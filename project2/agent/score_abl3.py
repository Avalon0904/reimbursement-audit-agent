#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融③判分：与 full4 同口径（系统 verdict=挂起单的 gate 动作），分层/对抗分型/误转/召回/成本。"""
import json, statistics
from pathlib import Path

ROOT = Path(__file__).parent.parent
golden = {g["invoice_id"]: g for g in (json.loads(l) for l in
          (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
recs = [json.loads(l) for l in (ROOT / "agent" / "eval_results_abl3.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
assert len(recs) == 60, f"记录数 {len(recs)} != 60"

def acc(rows):
    return sum(1 for r, g in rows if r["prediction"]["verdict"] == g["verdict"]) / len(rows) if rows else 0

rows = [(r, golden[r["invoice_id"]]) for r in recs]
by_stratum = {}
for r, g in rows:
    by_stratum.setdefault(g["stratum"], []).append((r, g))

print("== 分层判定准确率 ==")
for s in ("clear", "blur", "adversarial"):
    if s in by_stratum:
        print(f"{s:12s} n={len(by_stratum[s]):2d}  acc={acc(by_stratum[s])*100:.1f}")
regular = [x for s in ("clear", "blur") for x in by_stratum.get(s, [])]
adv = by_stratum.get("adversarial", [])
print(f"{'常规合计':12s} n={len(regular):2d}  acc={acc(regular)*100:.1f}")

print("\n== 对抗分型 ==")
by_type = {}
for r, g in adv:
    by_type.setdefault(g["adversarial_type"], []).append((r, g))
names = {"a": "压线", "b": "连号", "c": "张冠李戴", "d": "超大额", "e": "伪造"}
for t in "abcde":
    rs = by_type.get(t, [])
    hit = sum(1 for r, g in rs if r["prediction"]["verdict"] == g["verdict"])
    detail = " ".join(f"{r['invoice_id'][4:]}:{'✓' if r['prediction']['verdict']==g['verdict'] else '✗'+r['prediction']['verdict']}" for r, g in rs)
    print(f"{t} {names[t]:5s} {hit}/{len(rs)}  {detail}")

print("\n== 转人工口径 ==")
need_human = [(r, g) for r, g in rows if g["verdict"] in ("escalate", "review")]
sent_human = [1 for r, g in need_human if r["prediction"]["verdict"] in ("escalate", "review")]
print(f"应转人工 {len(need_human)}，送对 {len(sent_human)}，召回 {len(sent_human)/len(need_human)*100:.1f}%")
false_gate = [(r, g) for r, g in regular if g["verdict"] == "pass" and r["prediction"]["verdict"] in ("escalate", "review")]
print(f"常规误转（真值pass却转人工）{len(false_gate)}: {[r['invoice_id'] for r, g in false_gate]}")

print("\n== 成本/延迟 ==")
toks = sorted(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in recs)
lat = sorted(r["latency_s"] for r in recs)
print(f"token p50={toks[30]} 均值={sum(toks)/60:.0f} max={toks[-1]}（硬顶50000）")
print(f"机器延迟 p50={lat[30]:.1f}s p95={lat[57]:.1f}s")

print("\n== 全部误判清单（附条款对账）==")
for r, g in rows:
    if r["prediction"]["verdict"] != g["verdict"]:
        print(f"{r['invoice_id']} [{g['stratum']}/{g.get('adversarial_type') or '-'}] "
              f"系统={r['prediction']['verdict']} 真值={g['verdict']} "
              f"系统条款={r['prediction']['violated_clauses']} 真值条款={g.get('violated_clauses')} "
              f"notes={g.get('notes','')[:24]}")
n_pass_reg = sum(1 for r, g in regular if g["verdict"] == "pass")
print(f"\n常规pass单 {n_pass_reg}，误转率={len(false_gate)}/{n_pass_reg}={len(false_gate)/n_pass_reg*100:.1f}%")
