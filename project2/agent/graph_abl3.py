#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融③·去规则（2026-10-22）：rule_check 纯 Python 校验器 → k3 judge 节点（LLM+检索判合规）。
原 graph.py 一字不动；本文件独立建图，复用 graph.py 的节点/计量/常量/环境。
切除边界（术前确认）：
  - 切：rule_check（抬头1.2/时限1.1/限额2.x·3.2/类目1.3）
       + route_after_ledger 里 ≥5000 硬编码 escalate（1.4 交 judge 凭检索条款自决）
  - 留：verify（查验=外部工具维度）、ledger_check（连号=跨单记忆维度）——矩阵无对应刀
检索口径：第一章通用条款(1.1~1.7)每次必带 + BM25 search 类目专项 top-4（按费用发生日过滤版本）。
fail-closed：judge 输出解析失败重试 1 次，再败 → review 转人工，不静默放行。
"""
import json, sqlite3, time
import requests
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import AuditState
import graph as G  # import 即完成 load_env；复用节点与工具函数

JUDGE_PROMPT = """你是报销合规审核员。根据发票字段、报销申报和制度条款，给出审核结论。

【发票字段】{fields}
【报销申报】{claim}
【适用制度条款】（已按费用发生日过滤版本）
{clauses}

判定规则：
- 明确违反某条 → "reject"，violated_clauses 列出条款号
- 存疑/拿不准（如申报类目与票面明细不一致、条款适用有歧义）→ "review"（转人工，不硬判）
- 单笔金额达到大额审批线 → "escalate"（转人工审批，不是违规）
- 以上皆非 → "pass"

只输出 JSON：{{"verdict": "pass|reject|review|escalate", "violated_clauses": ["x.y"], "reason": "一句话依据"}}"""


def _llm_text(prompt, model="kimi-k3"):
    """纯文本调用：judge 不需要图——字段已在手，不再为图像付费。"""
    r = requests.post(G.BASE.rstrip("/") + "/chat/completions",
                      headers={"Authorization": f"Bearer {G.KEY}"},
                      json={"model": model, "temperature": 1, "max_tokens": 4096,
                            "messages": [{"role": "user", "content": prompt}]},
                      timeout=180)
    d = r.json()
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    return json.loads(content[s:e + 1]), d.get("usage", {})


def _judge_clauses(claim, fields):
    """通用条款全带 + 检索专项：第一章人人适用，类目条款靠 BM25 找出来。"""
    exp = claim.get("expense_date")
    general = [c for c in G.STORE.clauses if c["clause"].startswith("1.") and G.STORE._effective(c, exp)]
    item = (fields.get("items") or [{}])[0].get("name", "")
    q = f"{claim.get('category', '')} {item} 报销 金额{fields.get('total', 0)}元"
    spec = G.STORE.search(q, k=4, expense_date=exp)
    seen, out = set(), []
    for c in general + spec:
        if c["clause"] not in seen:
            seen.add(c["clause"])
            out.append(c)
    return out


def judge_node(state: AuditState) -> dict:
    """k3 拿检索到的条款做合规判断——替代 rule_check 的确定性 if。
    输出借 rule_violations 管道流动：reject=违规条目 / review=存疑条目 / escalate=1.4标记，
    下游 set_gate/reject_node 的引用挂载因此零改动复用。"""
    t0 = time.time()
    fields, claim = state["fields"], state["claim"]
    clauses = _judge_clauses(claim, fields)
    ctext = "\n".join(f"- {c['clause']}（{c['version']}）{c['text']}" for c in clauses)
    prompt = JUDGE_PROMPT.format(fields=json.dumps(fields, ensure_ascii=False),
                                 claim=json.dumps(claim, ensure_ascii=False), clauses=ctext)
    tot_p = tot_c = 0
    pred = None
    for attempt in range(2):  # 踩坑#3纪律：空输出/JSON坏=可重试故障，重试计入成本
        try:
            pred, usage = _llm_text(prompt)
            tot_p += usage.get("prompt_tokens", 0)
            tot_c += usage.get("completion_tokens", 0)
            break
        except Exception:
            pred = None
    usage = {"prompt_tokens": tot_p, "completion_tokens": tot_c}
    if pred is None:  # fail-closed：判不出来转人工，绝不静默放行
        v = [{"clause": "4.1", "review": True,
              "detail": "judge 输出解析失败（重试1次仍败），fail-closed 转人工"}]
        return {"rule_violations": v, **G._meter(state, "judge", usage, t0, "kimi-k3")}
    verdict, reason = pred.get("verdict", "review"), pred.get("reason", "")
    bad = pred.get("violated_clauses") or []
    if verdict == "reject":
        v = [{"clause": c, "detail": reason} for c in bad] or [{"clause": "?", "detail": reason}]
    elif verdict == "review":
        v = [{"clause": bad[0] if bad else "4.1", "review": True, "detail": reason}]
    elif verdict == "escalate":
        v = [{"clause": "1.4", "escalate": True, "detail": reason}]
    else:
        v = []
    return {"rule_violations": v, **G._meter(state, "judge", usage, t0, "kimi-k3")}


def route_after_judge(state):
    v = state["rule_violations"]
    if any(x.get("review") for x in v):
        return "gate_item"
    if any(x.get("escalate") for x in v):
        return "gate_escalate"
    return "reject" if v else "verify"


def route_after_conf_abl3(state):
    """同 route_after_conf，唯一差别：字段全清晰时去 judge 而非 rule_check。"""
    if state.get("step_count", 0) >= G.FUSE_STEPS or G._total_tokens(state) >= G.FUSE_TOKENS:
        return "fuse"
    low = [f for f, c in state.get("field_confidence", {}).items() if c < G.CONF_THRESHOLD]
    if not low:
        return "judge"
    retries = state.get("field_retries", {})
    if all(retries.get(f, 0) >= G.MAX_FIELD_RETRY for f in low) or state.get("stall_count", 0) >= 2:
        return "gate_lowconf"
    return "escalate_field"


def route_after_ledger_abl3(state):
    """去规则版：≥5000 escalate 硬编码已切（交 judge），此处只剩台账证据路由。"""
    return "reject" if state["ledger_hits"] else "record"


def get_app():
    g = StateGraph(AuditState)
    g.add_node("ocr", G.ocr_node)
    g.add_node("conf_gate", G.conf_gate_node)
    g.add_node("escalate_field", G.escalate_field_node)
    g.add_node("judge", judge_node)
    g.add_node("verify", G.verify_node)
    g.add_node("ledger_check", G.ledger_check_node)
    g.add_node("gate_escalate", G.set_gate("escalate"))
    g.add_node("gate_review", G.set_gate("review"))
    g.add_node("gate_lowconf", G.set_gate("lowconf"))
    g.add_node("gate_item", G.set_gate("item"))
    g.add_node("human_gate", G.human_gate_node)
    g.add_node("record", G.record_node)
    g.add_node("reject", G.reject_node)
    g.add_node("fuse", G.fuse_node)
    g.set_entry_point("ocr")
    g.add_edge("ocr", "conf_gate")
    g.add_conditional_edges("conf_gate", route_after_conf_abl3,
                            {"escalate_field": "escalate_field", "judge": "judge",
                             "gate_lowconf": "gate_lowconf", "fuse": "fuse"})
    g.add_edge("escalate_field", "conf_gate")
    g.add_conditional_edges("judge", route_after_judge,
                            {"reject": "reject", "verify": "verify",
                             "gate_item": "gate_item", "gate_escalate": "gate_escalate"})
    g.add_conditional_edges("verify", G.route_after_verify,
                            {"reject": "reject", "gate_review": "gate_review",
                             "ledger_check": "ledger_check"})
    g.add_conditional_edges("ledger_check", route_after_ledger_abl3,
                            {"reject": "reject", "record": "record"})
    for n in ("gate_escalate", "gate_review", "gate_lowconf", "gate_item"):
        g.add_edge(n, "human_gate")
    g.add_conditional_edges("human_gate", G.route_after_human, {"record": "record", "reject": "reject"})
    g.add_edge("record", END)
    g.add_edge("reject", END)
    g.add_edge("fuse", END)
    conn = sqlite3.connect(str(G.CKPT), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))


if __name__ == "__main__":
    # 单票试跑：python graph_abl3.py INV-A02（oracle 自动批复，仅冒烟用）
    import sys
    from langgraph.types import Command
    inv_id = sys.argv[1] if len(sys.argv) > 1 else "INV-A02"
    golden = {g["invoice_id"]: g for g in (json.loads(l) for l in
              (G.ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
    gd = golden[inv_id]
    app = get_app()
    cfg = {"configurable": {"thread_id": f"{inv_id}-abl3smoke"}}
    out = app.invoke({"invoice_id": inv_id,
                      "image_path": str(G.ROOT / "eval" / "dataset" / gd["image"]),
                      "claim": gd["claim"]}, cfg)
    if "__interrupt__" in out:
        p = out["__interrupt__"][0].value
        print(f"⏸ 挂起 gate={p['gate_kind']} | {p['system_reason']}")
        out = app.invoke(Command(resume={"action": "approve", "note": "smoke", "reviewer": "oracle"}), cfg)
    print(f"verdict={out.get('verdict')} | reason={out.get('reason', '')[:80]} | tokens={G._total_tokens(out)}")
