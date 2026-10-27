#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v0.6 升格冒烟（2026-10-22）：⑦升默认后验证新主线，三单三路径（pass/规则reject/查验reject）。
纪律：独立脚本、不动评测台账（LEDGER 指向临时演示文件）、不动 golden set。
"""
import json, time
from pathlib import Path

import graph as G

G.LEDGER = Path(__file__).parent / "ledger_v06smoke.csv"  # 独立台账，防 golden 号码撞单（踩坑#11）
if G.LEDGER.exists():
    G.LEDGER.unlink()

ROOT = Path(__file__).parent.parent
golden = {g["invoice_id"]: g for g in
          (json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}

CASES = ["INV-R01", "INV-A02", "INV-E01"]  # pass / 规则层reject(2.1) / 查验层reject(1.6)
app = G.get_app()
ok_all = True
for inv in CASES:
    g = golden[inv]
    tid = f"{inv}-v06smoke-{int(time.time())}"
    init = {"invoice_id": inv, "image_path": str(ROOT / "eval" / "dataset" / g["image"]),
            "claim": g["claim"]}
    out = app.invoke(init, {"configurable": {"thread_id": tid}})
    ok = out.get("verdict") == g["verdict"]
    ok_all &= ok
    tu = out.get("token_usage", {})
    toks = sum(b["prompt"] + b["completion"] for b in tu.values())
    lat = sum(b["latency_s"] for b in tu.values())
    models = {b["model"] for b in tu.values()}
    print(f"{'✓' if ok else '✗'} {inv}: {out.get('verdict')}（真值 {g['verdict']}）"
          f" | {toks} tok / {lat:.2f}s | 模型 {models or '{}'}")
    print(f"   reason: {(out.get('reason') or '')[:70]}")

print("\n冒烟", "全过 ✓" if ok_all else "有失败 ✗")
