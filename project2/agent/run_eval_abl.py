#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融通用评测 runner（④⑤⑥ 共用）：与 run_eval.py 同口径，三处增强：
1) --mod 指定图模块（graph_abl4/5/6），--results 指定独立结果文件（免疫无 tag 全局 done 雷）；
2) 补记 policy_citations 数与 reason 摘要（④去检索的可测增量全靠它）；
3) oracle HITL / 唯一 thread / b组顺序 / 并发，纪律不变。
用法：python run_eval_abl.py --mod graph_abl4 --tag abl4 --results eval_results_abl4.jsonl [--limit N] [--reset-ledger]
"""
import argparse, importlib, json, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from langgraph.types import Command
from graph import LEDGER, ROOT

_lock = threading.Lock()

def oracle_decide(payload, truth):
    if payload["gate_kind"] == "escalate":
        return "approve", "oracle: 大额审批通过"
    return ("approve", "oracle: 复核通过") if truth["verdict"] == "pass" \
        else ("reject", "oracle: 人工复核确认问题")

def run_one(app, g, tag, results, lock):
    thread_id = f"{g['invoice_id']}-{tag}"
    init = {"invoice_id": g["invoice_id"], "image_path": str(ROOT / "eval" / "dataset" / g["image"]),
            "claim": g["claim"]}
    cfg = {"configurable": {"thread_id": thread_id}}
    t0 = time.time()
    out = app.invoke(init, cfg)
    system_verdict = out.get("verdict")
    if "__interrupt__" in out:
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
           "citations": len(out.get("policy_citations") or []),
           "reason": (out.get("reason") or "")[:120],
           "final_verdict_after_oracle": out.get("verdict"),
           "stop_reason": out.get("stop_reason")}
    with lock:
        with open(results, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = "✓" if system_verdict == g["verdict"] else "✗"
    print(f"{ok} {g['invoice_id']}: {system_verdict} (真值 {g['verdict']}) 机器{rec['latency_s']}s", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--reset-ledger", action="store_true")
    a = ap.parse_args()
    results = ROOT / "agent" / a.results
    if a.reset_ledger and LEDGER.exists():
        LEDGER.unlink()
    golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
    done = set()
    if results.exists():
        done = {json.loads(l)["invoice_id"] for l in results.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [g for g in golden if g["invoice_id"] not in done][: a.limit]
    b_group = [g for g in todo if g["adversarial_type"] == "b"]
    rest = [g for g in todo if g["adversarial_type"] != "b"]
    app = importlib.import_module(a.mod).get_app()
    print(f"[{a.mod}/{a.tag}] 待跑 {len(todo)}（b组 {len(b_group)} 顺序 / 其余 {len(rest)} 并发{a.workers}）", flush=True)
    for g in b_group:
        run_one(app, g, a.tag, results, _lock)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(lambda g: run_one(app, g, a.tag, results, _lock), rest))

if __name__ == "__main__":
    main()
