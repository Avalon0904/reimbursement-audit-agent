#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评测 runner：完整系统跑 golden set，oracle HITL + 唯一 thread + 并发 + 判分器对接。
判分口径：挂起单的系统 verdict = gate 动作本身（escalate/review，"送对人"）；
oracle 批复（escalate→approve，其余按真值倾向）仅用于走完流程保持台账一致。
时序纪律：b 组连号必须顺序（共享台账），其余并发。
用法：python run_eval.py --tag full1 [--limit N] [--reset-ledger]
"""
import argparse, json, threading, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from langgraph.types import Command
from graph import get_app, LEDGER, ROOT

RESULTS = ROOT / "agent" / "eval_results.jsonl"
_lock = threading.Lock()

def oracle_decide(payload, truth):
    """脚本化策略人：按真值倾向批复。返回 (action, note)。"""
    if payload["gate_kind"] == "escalate":
        return "approve", "oracle: 大额审批通过"
    return ("approve", "oracle: 复核通过") if truth["verdict"] == "pass" \
        else ("reject", "oracle: 人工复核确认问题")

def run_one(app, g, tag):
    thread_id = f"{g['invoice_id']}-{tag}"
    init = {"invoice_id": g["invoice_id"], "image_path": str(ROOT / "eval" / "dataset" / g["image"]),
            "claim": g["claim"]}
    cfg = {"configurable": {"thread_id": thread_id}}
    t0 = time.time()
    out = app.invoke(init, cfg)
    system_verdict = out.get("verdict")
    if "__interrupt__" in out:  # 挂起：记 gate 动作为系统判定，oracle 批复走完流程
        payload = out["__interrupt__"][0].value
        system_verdict = payload["gate_kind"]
        action, note = oracle_decide(payload, g)
        out = app.invoke(Command(resume={"action": action, "note": note, "reviewer": "oracle"}), cfg)
    machine_s = sum(v.get("latency_s", 0) for v in out.get("token_usage", {}).values())
    p_toks = sum(v.get("prompt", 0) for v in out.get("token_usage", {}).values())
    c_toks = sum(v.get("completion", 0) for v in out.get("token_usage", {}).values())
    rec = {"invoice_id": g["invoice_id"],
           "prediction": {"fields": out.get("fields", {}),
                          "verdict": system_verdict,
                          "violated_clauses": [x["clause"] for x in out.get("rule_violations", [])]},
           "usage": {"prompt_tokens": p_toks, "completion_tokens": c_toks},
           "latency_s": round(machine_s, 2),
           "wall_s": round(time.time() - t0, 2),
           "final_verdict_after_oracle": out.get("verdict"),
           "stop_reason": out.get("stop_reason")}
    with _lock:
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = "✓" if system_verdict == g["verdict"] else "✗"
    print(f"{ok} {g['invoice_id']}: {system_verdict} (真值 {g['verdict']}) 机器{rec['latency_s']}s")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--reset-ledger", action="store_true")
    a = ap.parse_args()
    if a.reset_ledger and LEDGER.exists():
        LEDGER.unlink()
    golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
    done = set()
    if RESULTS.exists():
        done = {json.loads(l)["invoice_id"] for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [g for g in golden if g["invoice_id"] not in done][: a.limit]
    # 时序纪律：b 组顺序先行，其余并发
    b_group = [g for g in todo if g["adversarial_type"] == "b"]
    rest = [g for g in todo if g["adversarial_type"] != "b"]
    app = get_app()
    print(f"待跑 {len(todo)}（b组 {len(b_group)} 顺序 / 其余 {len(rest)} 并发{a.workers}）")
    for g in b_group:
        run_one(app, g, a.tag)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(lambda g: run_one(app, g, a.tag), rest))

if __name__ == "__main__":
    main()
