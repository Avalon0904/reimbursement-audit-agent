#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融通用判分器（④⑤⑥ 共用）：与 score_abl3.py 同口径 + 引用贯通率。
用法：python score_ablation.py eval_results_abl4.jsonl
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
golden = {g["invoice_id"]: g for g in (json.loads(l) for l in
          (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
path = ROOT / "agent" / sys.argv[1]
recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
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
sent = sum(1 for r, g in need_human if r["prediction"]["verdict"] in ("escalate", "review"))
print(f"应转人工 {len(need_human)}，送对 {sent}，召回 {sent/len(need_human)*100:.1f}%")
false_gate = [(r, g) for r, g in regular if g["verdict"] == "pass" and r["prediction"]["verdict"] in ("escalate", "review")]
n_pass_reg = sum(1 for r, g in regular if g["verdict"] == "pass")
print(f"常规误转 {len(false_gate)}/{n_pass_reg} = {len(false_gate)/n_pass_reg*100:.1f}%  {[r['invoice_id'] for r, g in false_gate]}")

print("\n== 成本/延迟 ==")
toks = sorted(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in recs)
lat = sorted(r["latency_s"] for r in recs)
print(f"token p50={toks[30]} 均值={sum(toks)/60:.0f} max={toks[-1]}")
print(f"机器延迟 p50={lat[30]:.1f}s p95={lat[57]:.1f}s")

if "citations" in recs[0]:
    decided = [r for r in recs if r["prediction"]["verdict"] in ("reject", "escalate", "review")]
    cited = sum(1 for r in decided if r["citations"] > 0)
    print(f"\n== 引用贯通率 ==\n判否/转人工单 {len(decided)}，带原文引用 {cited} = {cited/len(decided)*100:.1f}%")

print("\n== 全部误判清单 ==")
for r, g in rows:
    if r["prediction"]["verdict"] != g["verdict"]:
        print(f"{r['invoice_id']} [{g['stratum']}/{g.get('adversarial_type') or '-'}] "
              f"系统={r['prediction']['verdict']} 真值={g['verdict']} "
              f"系统条款={r['prediction']['violated_clauses']} 真值条款={g.get('violated_clauses')}")
