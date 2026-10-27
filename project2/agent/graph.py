#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LangGraph 主干 v0.6（2026-10-22）：⑦异构双档升格为默认路线——OCR 首过 GLM-4V-Flash（免费），k3 旗舰兜底不变。
升格依据（五刀消融收官）：⑦判定与②逐项全等（常规100/对抗93.3、字段360/360、引用贯通100%），
token p50 1161=②的52%、延迟 p50 4.2s=②的1/3、OCR 账单归零 → 新 Pareto 最优点。
在案敞口：GLM 自评置信度恒1.0无区分度 → T2 escalation 结构性不触发；上生产前须补字段级校验和
（价税勾稽/号码位数）做客观置信信号替代自评置信度。
流水线：ocr → conf_gate ─┬→ escalate_field(k3 单字段重抽) ↺ 重试≤2、无进展即放弃
                        ├→ rule_check → verify(fail-closed) → ledger_check → record/reject
                        ├→ gate_review(低置信耗尽，fail-closed)
                        └→ fuse(步数25/token50K 熔断 → audit_report 优雅终态)
打转定义：escalation 重抽后字段值一字未变=无进展。
历史：v0.5=②完整系统（kimi-k2.6 首过），消融实验档案 graph_abl3/4/5/6/7.py 保持不动。
"""
import base64, csv, json, os, sqlite3, sys, time
from pathlib import Path
import requests
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, interrupt
from state import AuditState
from policy_store import PolicyStore

STORE = PolicyStore()
ROOT = Path(__file__).parent.parent
ENV = os.environ.get("AUDIT_CONFIG_FILE", str(Path(__file__).resolve().parent.parent / "config.local"))
LIMITS = {"住宿费": 500.0, "办公用品": 1000.0, "打车费": 100.0, "劳保用品": 2000.0}
CLAUSE_OF = {"住宿费": "2.1", "办公用品": "2.4", "打车费": "2.3", "劳保用品": "3.2"}
BUYER_FULL = "蓝丸化工科技有限公司"
LEDGER = ROOT / "agent" / "ledger.csv"
VERIFY_DB = ROOT / "eval" / "dataset" / "verify_db.json"
PENDING = ROOT / "agent" / "pending_queue.json"
CKPT = ROOT / "agent" / "checkpoints.sqlite"
CONF_THRESHOLD = 0.7      # 低于此置信度触发单字段 escalation
MAX_FIELD_RETRY = 2
FUSE_STEPS, FUSE_TOKENS = 25, 50000

def load_env(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

load_env(ENV)
BASE, KEY = os.environ["KIMI_BASE_URL"], os.environ["KIMI_API_KEY"]
ZBASE = os.environ["ZHIPU_BASE_URL"].rstrip("/")
ZKEY = os.environ["ZHIPU_API_KEY"]
ZMODEL = "glm-4v-flash"

OCR_PROMPT = """从发票图片提取字段，并对每个字段给出置信度(0~1)。
只输出 JSON：{"fields": {"invoice_code": "", "invoice_no": "", "date": "", "buyer_name": "", "seller": "",
"total": 0.0, "items": [{"name": "明细品名", "amount": 0.0}]},
"field_confidence": {"invoice_code": 0.0, "invoice_no": 0.0, "date": 0.0, "buyer_name": 0.0, "seller": 0.0, "total": 0.0}}"""

def _meter(state, node, usage=None, t0=None, model="python"):
    bucket = {"prompt": 0, "completion": 0, "model": model,
              "latency_s": round(time.time() - t0, 4) if t0 else 0}
    if usage:
        bucket.update({"prompt": usage.get("prompt_tokens", 0),
                       "completion": usage.get("completion_tokens", 0)})
    return {"token_usage": {**state.get("token_usage", {}), node: bucket},
            "step_count": state.get("step_count", 0) + 1}

def _total_tokens(state):
    return sum(v.get("prompt", 0) + v.get("completion", 0) for v in state.get("token_usage", {}).values())

def _llm(img_b64, prompt, model):
    r = requests.post(BASE.rstrip("/") + "/chat/completions",
                      headers={"Authorization": f"Bearer {KEY}"},
                      json={"model": model, "temperature": 1, "max_tokens": 4096,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                      timeout=180)
    d = r.json()
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    return json.loads(content[s:e + 1]), d.get("usage", {})

def _llm_zhipu(img_b64, prompt):
    """智谱免费层口径：4K 上下文 / max_tokens≤1024，实测载荷 ~1.2K 富余。"""
    r = requests.post(ZBASE + "/chat/completions",
                      headers={"Authorization": f"Bearer {ZKEY}"},
                      json={"model": ZMODEL, "temperature": 0.1, "max_tokens": 1024,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                      timeout=180)
    d = r.json()
    if "choices" not in d:
        raise RuntimeError(f"zhipu error: {json.dumps(d, ensure_ascii=False)[:200]}")
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    return json.loads(content[s:e + 1]), d.get("usage", {})

# ---------- 节点 ----------

def ocr_node(state: AuditState) -> dict:
    """v0.6：首过 GLM-4V-Flash（免费）；低置信字段由 escalate_field 交 k3 旗舰兜底。"""
    t0 = time.time()
    img_b64 = base64.b64encode(Path(state["image_path"]).read_bytes()).decode()
    pred, usage = _llm_zhipu(img_b64, OCR_PROMPT)
    return {"fields": pred["fields"], "field_confidence": pred["field_confidence"],
            "ocr_model": ZMODEL, **_meter(state, "ocr", usage, t0, ZMODEL)}

def escalate_field_node(state: AuditState) -> dict:
    """单字段 escalation：k3 只重抽低置信字段，好字段锁死；一字未变=无进展。"""
    t0 = time.time()
    low = [f for f, c in state["field_confidence"].items() if c < CONF_THRESHOLD]
    img_b64 = base64.b64encode(Path(state["image_path"]).read_bytes()).decode()
    prompt = (f"发票图片中以下字段此前识别置信度低：{low}。请只重新识别这些字段。"
              '只输出 JSON：{"fields": {"<字段名>": <值>}, "field_confidence": {"<字段名>": 0~1}}')
    pred, usage = _llm(img_b64, prompt, "kimi-k3")
    fields, conf = dict(state["fields"]), dict(state["field_confidence"])
    retries = dict(state.get("field_retries", {}))
    changed = False
    for f in low:
        retries[f] = retries.get(f, 0) + 1
        nv = pred.get("fields", {}).get(f)
        if nv is not None:
            if str(nv) != str(fields.get(f)):
                changed = True
            fields[f] = nv
            conf[f] = pred.get("field_confidence", {}).get(f, conf[f])
    return {"fields": fields, "field_confidence": conf, "field_retries": retries,
            "ocr_model": state.get("ocr_model", "") + "+k3",
            "stall_count": state.get("stall_count", 0) + (0 if changed else 1),
            **_meter(state, f"escalation_r{max(retries.values())}", usage, t0, "kimi-k3")}

def rule_check_node(state: AuditState) -> dict:
    t0 = time.time()
    f, c = state["fields"], state["claim"]
    v = []
    if f.get("buyer_name") != BUYER_FULL:
        v.append({"clause": "1.2", "detail": f"抬头非全称: {f.get('buyer_name')}"})
    d0 = time.strptime(f["date"], "%Y-%m-%d")
    d1 = time.strptime(c["submit_date"], "%Y-%m-%d")
    if (time.mktime(d1) - time.mktime(d0)) / 86400 > 30:
        v.append({"clause": "1.1", "detail": "超30天报销时限"})
    total = float(f.get("total", 0))
    cat = c["category"]
    if total < 5000 and cat in LIMITS and total > LIMITS[cat]:
        v.append({"clause": CLAUSE_OF[cat], "detail": f"{cat} {total} 超限额 {LIMITS[cat]}"})
    # 类目一致性（1.3）：票面明细与申报类目不符 → 存疑标记（review 级，不硬判驳回）
    item_cat = (f.get("items") or [{}])[0].get("name")
    if item_cat and item_cat != cat:
        v.append({"clause": "1.3", "review": True,
                  "detail": f"申报类目「{cat}」与票面明细「{item_cat}」不一致"})
    return {"rule_violations": v, **_meter(state, "rule_check", t0=t0)}

def verify_node(state: AuditState) -> dict:
    t0 = time.time()
    no = state["fields"]["invoice_no"]
    db = json.loads(VERIFY_DB.read_text(encoding="utf-8"))
    rec = db[no] if no in db else {"status": "unavailable", "total": None}
    v = list(state.get("rule_violations", []))
    if rec["status"] == "not_found":
        v.append({"clause": "1.6", "detail": "查验: 查无此票"})
    elif rec["status"] == "voided":
        v.append({"clause": "1.6", "detail": "查验: 发票已作废"})
    elif rec["status"] == "valid" and rec.get("total") is not None \
            and abs(float(state["fields"]["total"]) - float(rec["total"])) > 0.01:
        v.append({"clause": "1.6", "detail": f"查验金额不符: 票{state['fields']['total']} vs 底账{rec['total']}"})
    return {"verify_result": rec, "rule_violations": v, **_meter(state, "verify", t0=t0)}

def ledger_check_node(state: AuditState) -> dict:
    t0 = time.time()
    hits = []
    f = state["fields"]
    if LEDGER.exists():
        for row in csv.reader(LEDGER.read_text(encoding="utf-8").splitlines()):
            if not row:
                continue
            _id, l_no, l_date, l_seller, _total, _st = row
            if l_no == f["invoice_no"]:
                hits.append({"type": "重复报销", "ref_invoice_id": _id})
            elif (l_date == f["date"] and l_seller == f.get("seller")
                  and abs(int(l_no) - int(f["invoice_no"])) == 1):
                hits.append({"type": "连号", "ref_invoice_id": _id})
    v = list(state.get("rule_violations", []))
    for h in hits:
        v.append({"clause": "1.5", "detail": f"{h['type']}: 与 {h['ref_invoice_id']} 撞单"})
    return {"ledger_hits": hits, "rule_violations": v, **_meter(state, "ledger_check", t0=t0)}

def human_gate_node(state: AuditState) -> dict:
    t0 = time.time()
    f = state["fields"]
    decision = interrupt({
        "invoice_id": state["invoice_id"], "gate_kind": state["gate_kind"],
        "snapshot": {"invoice_no": f["invoice_no"], "date": f["date"],
                     "seller": f["seller"], "total": f["total"]},
        "system_reason": state.get("gate_reason", ""),
    })
    return {"human_decision": decision, **_meter(state, "human_gate", t0=t0)}

def record_node(state: AuditState) -> dict:
    f = state["fields"]
    with open(LEDGER, "a", encoding="utf-8") as fp:
        fp.write(f"{state['invoice_id']},{f['invoice_no']},{f['date']},{f.get('seller','')},{f['total']},pass\n")
    reason = "规则/查验/台账全过"
    hd = state.get("human_decision")
    if hd and hd.get("action") == "approve":
        reason += f"；人工审批通过({hd.get('reviewer','?')}: {hd.get('note','')})"
    return {"verdict": "pass", "reason": reason, "stop_reason": "completed",
            "step_count": state.get("step_count", 0) + 1}

def reject_node(state: AuditState) -> dict:
    v = state.get("rule_violations", [])
    reason = "; ".join(f"违反{x['clause']}({x['detail']})" for x in v) or "人工驳回"
    hd = state.get("human_decision")
    if hd and hd.get("action") == "reject":
        reason += f"；人工批复驳回({hd.get('reviewer','?')}: {hd.get('note','')})"
    exp_date = state["claim"].get("expense_date")
    citations = [c for x in v if (c := STORE.cite(x["clause"], exp_date))]
    if citations:
        c0 = citations[0]
        reason += f"｜依据{c0['version']} {c0['clause']}「{c0['text'][:36]}…」"
    return {"verdict": "reject", "reason": reason, "policy_citations": citations,
            "stop_reason": "completed", "step_count": state.get("step_count", 0) + 1}

def fuse_node(state: AuditState) -> dict:
    """熔断优雅终态：不是 crash，是带报告的退出。"""
    steps, toks = state.get("step_count", 0), _total_tokens(state)
    reason = "step_fuse" if steps >= FUSE_STEPS else "budget_fuse"
    report = {"stop_reason": reason, "steps": steps, "tokens": toks,
              "detail": f"触发熔断（{'步数' if reason=='step_fuse' else 'token预算'}），"
                        f"已完成节点: {list(state.get('token_usage', {}).keys())}，转人工兜底"}
    return {"verdict": "review", "stop_reason": reason, "audit_report": report,
            "reason": report["detail"], "step_count": steps + 1}

def conf_gate_node(state: AuditState) -> dict:
    return {}  # 纯路由节点：逻辑全在 route_after_conf

# ---------- 路由 ----------

def route_after_conf(state: AuditState) -> str:
    if state.get("step_count", 0) >= FUSE_STEPS or _total_tokens(state) >= FUSE_TOKENS:
        return "fuse"
    low = [f for f, c in state.get("field_confidence", {}).items() if c < CONF_THRESHOLD]
    if not low:
        return "rule_check"
    retries = state.get("field_retries", {})
    # 重试耗尽或连续打转（重抽一字未变）→ fail-closed 转人工，不静默放行
    if all(retries.get(f, 0) >= MAX_FIELD_RETRY for f in low) or state.get("stall_count", 0) >= 2:
        return "gate_lowconf"
    return "escalate_field"

def route_after_rule(state):
    v = state["rule_violations"]
    if any(x.get("review") for x in v):
        return "gate_item"      # 存疑级：类目不一致 → 转人工，不硬判
    return "reject" if v else "verify"

def route_after_verify(state):
    if state["verify_result"]["status"] == "unavailable":
        return "gate_review"
    if any(x["clause"] == "1.6" for x in state["rule_violations"]):
        return "reject"
    return "ledger_check"

def route_after_ledger(state):
    if state["ledger_hits"]:
        return "reject"
    if float(state["fields"].get("total", 0)) >= 5000:
        return "gate_escalate"
    return "record"

def route_after_human(state):
    return "record" if state["human_decision"].get("action") == "approve" else "reject"

def set_gate(kind):
    def _node(state):
        if kind == "lowconf":
            low = [f for f, c in state.get("field_confidence", {}).items() if c < CONF_THRESHOLD]
            return {"gate_kind": "review",
                    "gate_reason": f"OCR 低置信字段重试{MAX_FIELD_RETRY}次仍不清: {low}（fail-closed，不静默放行）"}
        if kind == "item":
            detail = next((x["detail"] for x in state.get("rule_violations", []) if x.get("review")), "")
            c = STORE.cite("1.3", state["claim"].get("expense_date"))
            out = {"gate_kind": "review", "gate_reason": f"{detail}（存疑转人工，不硬判驳回）"}
            if c:
                out["policy_citations"] = [c]
            return out
        reasons = {"escalate": "单笔≥5000，按1.4须人工审批",
                   "review": f"查验服务不可用(status={state.get('verify_result',{}).get('status')})，fail-closed"}
        out = {"gate_kind": kind, "gate_reason": reasons[kind]}
        if kind == "escalate":
            c = STORE.cite("1.4", state["claim"].get("expense_date"))
            if c:
                out["policy_citations"] = [c]
        return out
    return _node

def build_graph(checkpointer=None):
    g = StateGraph(AuditState)
    g.add_node("ocr", ocr_node)
    g.add_node("conf_gate", conf_gate_node)
    g.add_node("escalate_field", escalate_field_node)
    g.add_node("rule_check", rule_check_node)
    g.add_node("verify", verify_node)
    g.add_node("ledger_check", ledger_check_node)
    g.add_node("gate_escalate", set_gate("escalate"))
    g.add_node("gate_review", set_gate("review"))
    g.add_node("gate_lowconf", set_gate("lowconf"))
    g.add_node("gate_item", set_gate("item"))
    g.add_node("human_gate", human_gate_node)
    g.add_node("record", record_node)
    g.add_node("reject", reject_node)
    g.add_node("fuse", fuse_node)
    g.set_entry_point("ocr")
    g.add_edge("ocr", "conf_gate")
    g.add_conditional_edges("conf_gate", route_after_conf,
                            {"escalate_field": "escalate_field", "rule_check": "rule_check",
                             "gate_lowconf": "gate_lowconf", "fuse": "fuse"})
    g.add_edge("escalate_field", "conf_gate")
    g.add_conditional_edges("rule_check", route_after_rule,
                            {"reject": "reject", "verify": "verify", "gate_item": "gate_item"})
    g.add_conditional_edges("verify", route_after_verify,
                            {"reject": "reject", "gate_review": "gate_review", "ledger_check": "ledger_check"})
    g.add_conditional_edges("ledger_check", route_after_ledger,
                            {"reject": "reject", "gate_escalate": "gate_escalate", "record": "record"})
    for n in ("gate_escalate", "gate_review", "gate_lowconf", "gate_item"):
        g.add_edge(n, "human_gate")
    g.add_conditional_edges("human_gate", route_after_human, {"record": "record", "reject": "reject"})
    g.add_edge("record", END)
    g.add_edge("reject", END)
    g.add_edge("fuse", END)
    return g.compile(checkpointer=checkpointer)

# ---------- 运行器 ----------

def get_app():
    conn = sqlite3.connect(str(CKPT), check_same_thread=False)
    return build_graph(SqliteSaver(conn))

def submit(invoice_ids, run_tag="dev", reset_ledger=False):
    if reset_ledger and LEDGER.exists():
        LEDGER.unlink()
    golden = {g["invoice_id"]: g for g in
              (json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
    app = get_app()
    pending = json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else []
    for inv_id in invoice_ids:
        g = golden[inv_id]
        thread_id = f"{inv_id}-{run_tag}"
        init = {"invoice_id": inv_id, "image_path": str(ROOT / "eval" / "dataset" / g["image"]),
                "claim": g["claim"]}
        out = app.invoke(init, {"configurable": {"thread_id": thread_id}})
        if "__interrupt__" in out:
            payload = out["__interrupt__"][0].value
            pending.append({"thread_id": thread_id, **payload})
            PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"⏸ {inv_id}: 已挂起（{payload['gate_kind']}）| {payload['system_reason'][:50]} | thread={thread_id}")
        else:
            toks = _total_tokens(out)
            ok = "✓" if out["verdict"] == g["verdict"] else "✗"
            print(f"{ok} {inv_id}: {out['verdict']} (真值 {g['verdict']}) | {out['reason'][:56]} | token{toks}")

def decide(thread_id, action, note="", reviewer="财务-张三"):
    app = get_app()
    out = app.invoke(Command(resume={"action": action, "note": note, "reviewer": reviewer}),
                     {"configurable": {"thread_id": thread_id}})
    machine_s = sum(v.get("latency_s", 0) for v in out.get("token_usage", {}).values())
    print(f"▶ {thread_id}: 恢复续跑 → {out['verdict']} | {out['reason'][:60]} | 机器耗时{machine_s:.1f}s（人工等待不计）")
    if PENDING.exists():
        pending = [p for p in json.loads(PENDING.read_text(encoding="utf-8")) if p["thread_id"] != thread_id]
        PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--decide":
        decide(args[1], args[2], note=args[3] if len(args) > 3 else "")
    else:
        reset = "--reset-ledger" in args
        submit([a for a in args if not a.startswith("--")], reset_ledger=reset)
