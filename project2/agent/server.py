#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段6 · 报销合规审核 Agent 演示服务（FastAPI，零新依赖，2026-10-22）。
单页三功能区：审单台（SSE 逐节点进度）/ 待审队列（pending_queue 数据源，批复→恢复续跑）/ 台账。
设计要点：
- 图执行走 LangGraph stream(stream_mode="updates")，节点事件即 SSE 事件——"进度可订阅"是架构红利不是 UI 技巧；
- interrupt 挂起检测：stream 结束后 get_state，snapshot.next 非空即挂起，写 pending_queue（与 CLI 同格式）；
- 上传走 base64 JSON（规避 python-multipart 依赖）；示例单直接调 golden set 图+申报（演示免找图）；
- 图调用全局串行（INVOKE_LOCK）：SqliteSaver 单连接，演示规模不引入连接池。
运行：cd agent && python server.py  →  http://localhost:8010
"""
import base64, json, threading, time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

import graph as G

# 演示台账独立：评测台账全是 golden 号码，示例单/上传图一录账必"撞单"误伤（验收实测复现）
G.LEDGER = G.ROOT / "agent" / "ledger_demo.csv"

ROOT = G.ROOT
STATIC = Path(__file__).parent / "static"
AUDIT_LOG = ROOT / "agent" / "audit_log.jsonl"

app = FastAPI(title="报销合规审核 Agent · 演示")
_app_graph = G.get_app()
INVOKE_LOCK = threading.Lock()
SESSIONS = {}          # thread_id -> {"events": [...], "done": bool}
GOLDEN = {g["invoice_id"]: g for g in (json.loads(l) for l in
          (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}


class AuditReq(BaseModel):
    image_b64: str
    category: str = "住宿费"
    submit_date: str = "2026-04-15"
    expense_date: str = "2026-04-01"
    applicant: str = "演示用户"


class DecideReq(BaseModel):
    thread_id: str
    action: str           # approve / reject
    note: str = ""


def _push(tid, event):
    SESSIONS[tid]["events"].append(event)


def _tokens_of(values):
    return sum(v.get("prompt", 0) + v.get("completion", 0)
               for v in values.get("token_usage", {}).values())


def _run_audit(tid, init):
    """后台线程：跑图，逐节点推事件，挂起写 pending，完成写 audit_log。"""
    cfg = {"configurable": {"thread_id": tid}}
    try:
        with INVOKE_LOCK:
            for chunk in _app_graph.stream(init, cfg, stream_mode="updates"):
                for node, upd in chunk.items():
                    note = ""
                    if node == "rule_check" and upd.get("rule_violations"):
                        note = f"违规 {len(upd['rule_violations'])} 条: " + "; ".join(
                            x["detail"] for x in upd["rule_violations"])[:60]
                    if node == "ledger_check" and upd.get("ledger_hits"):
                        note = f"台账命中: {upd['ledger_hits'][0]['type']}"
                    _push(tid, {"node": node, "note": note,
                                "t": round(time.time(), 1)})
            snap = _app_graph.get_state(cfg)
            values, reason, verdict = snap.values, "", None
            if snap.next:  # 挂起：interrupt 载荷进 pending_queue（与 CLI 同格式）
                payload = snap.tasks[0].interrupts[0].value
                pending = json.loads(G.PENDING.read_text(encoding="utf-8")) if G.PENDING.exists() else []
                pending.append({"thread_id": tid, **payload})
                G.PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
                _push(tid, {"node": "__interrupt__", "gate_kind": payload["gate_kind"],
                            "note": payload["system_reason"]})
            else:
                verdict = values.get("verdict")
                reason = values.get("reason", "")
                _push(tid, {"node": "__done__", "verdict": verdict, "note": reason,
                            "tokens": _tokens_of(values)})
            _append_log(tid, init["invoice_id"], verdict or "pending", reason,
                        _tokens_of(values))
    except Exception as e:  # 演示服务也要 fail-visible
        _push(tid, {"node": "__error__", "note": str(e)[:200]})
    finally:
        SESSIONS[tid]["done"] = True


def _append_log(tid, invoice_id, verdict, reason, tokens):
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"thread_id": tid, "invoice_id": invoice_id, "verdict": verdict,
                            "reason": reason, "tokens": tokens,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")


@app.post("/audit")
def audit(req: AuditReq):
    tid = f"web-{int(time.time() * 1000)}"
    img = ROOT / "agent" / "uploads" / f"{tid}.jpg"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(base64.b64decode(req.image_b64))
    invoice_id = f"W{tid[-6:]}"
    init = {"invoice_id": invoice_id, "image_path": str(img),
            "claim": {"category": req.category, "submit_date": req.submit_date,
                      "expense_date": req.expense_date, "applicant": req.applicant}}
    SESSIONS[tid] = {"events": [], "done": False}
    threading.Thread(target=_run_audit, args=(tid, init), daemon=True).start()
    return {"thread_id": tid, "invoice_id": invoice_id}


@app.post("/demo/{invoice_id}")
def demo(invoice_id: str):
    """示例单：直接调 golden set 的图与申报（演示免上传）。"""
    g = GOLDEN[invoice_id]
    req = AuditReq(image_b64=base64.b64encode(
        (ROOT / "eval" / "dataset" / g["image"]).read_bytes()).decode(),
        category=g["claim"]["category"], submit_date=g["claim"]["submit_date"],
        expense_date=g["claim"].get("expense_date", g["claim"]["submit_date"]),
        applicant=g["claim"].get("applicant", "demo"))
    return audit(req)


@app.get("/audit/{tid}/stream")
def stream(tid: str):
    def gen():
        idx = 0
        while True:
            evs = SESSIONS[tid]["events"]
            while idx < len(evs):
                yield f"data: {json.dumps(evs[idx], ensure_ascii=False)}\n\n"
                idx += 1
            if SESSIONS[tid]["done"]:
                break
            time.sleep(0.3)
            yield ": hb\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/pending")
def pending():
    return json.loads(G.PENDING.read_text(encoding="utf-8")) if G.PENDING.exists() else []


@app.post("/decide")
def decide(req: DecideReq):
    cfg = {"configurable": {"thread_id": req.thread_id}}
    with INVOKE_LOCK:
        out = _app_graph.invoke(Command(resume={"action": req.action, "note": req.note,
                                                "reviewer": "演示-财务"}), cfg)
    if G.PENDING.exists():
        rest = [p for p in json.loads(G.PENDING.read_text(encoding="utf-8"))
                if p["thread_id"] != req.thread_id]
        G.PENDING.write_text(json.dumps(rest, ensure_ascii=False, indent=2), encoding="utf-8")
    verdict, reason = out.get("verdict"), out.get("reason", "")
    _append_log(req.thread_id, out.get("invoice_id", "?"), verdict, reason, _tokens_of(out))
    return {"verdict": verdict, "reason": reason}


@app.get("/ledger")
def ledger():
    rows = []
    if G.LEDGER.exists():
        for r in [l.split(",") for l in G.LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]:
            rows.append({"invoice_id": r[0], "invoice_no": r[1], "date": r[2],
                         "seller": r[3], "total": r[4], "status": r[5]})
    logs = []
    if AUDIT_LOG.exists():
        logs = [json.loads(l) for l in AUDIT_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"ledger": rows, "audit_log": logs[-30:]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
