#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""escalation 带计量演示（2026-10-22）：注入式触发单字段重抽，全图跑通，逐节点记 token/延迟。

纪律声明：
- 不动评估集、不动 graph.py：OCR 注入用运行时 monkeypatch（仅 k2.6 首过被替换，k3 重抽走真实 API）；
- 台账指向独立演示文件 ledger_escalation_demo.csv，不污染评测台账（踩坑#11 纪律）；
- 注入内容：INV-R00（模糊件）真值字段，唯独 invoice_code 置错 + 置信度 0.4 → 触发 T2 链。
- OCR 首过为 mock（0 token 仅为占位），escalation_r1 是真实 k3 调用（真 token、真延迟）。
"""
import json, time
from pathlib import Path

import graph as G

ROOT = Path(__file__).parent.parent
IMG = ROOT / "eval" / "dataset" / "images" / "INV-R00.jpg"
G.LEDGER = Path(__file__).parent / "ledger_escalation_demo.csv"  # 演示台账独立

# --- golden 真值（INV-R00），注入时仅 invoice_code 置错 ---
TRUTH = {
    "invoice_code": "011002510851", "invoice_no": "60000000", "date": "2026-03-09",
    "buyer_name": "蓝丸化工科技有限公司", "seller": "晨光文具华东分公司",
    "items": [{"name": "办公用品", "qty": 1, "price": 714.22, "tax_rate": 0.13, "amount": 807.07}],
    "total": 807.07,
}
INJECTED_FIELDS = {**TRUTH, "invoice_code": "000000000000"}  # 识别错的字段
INJECTED_CONF = {k: 0.95 for k in TRUTH} | {"invoice_code": 0.4}

_real_llm = G._llm
def fake_ocr_llm(img_b64, prompt, model):
    if model == "kimi-k2.6":  # 只 mock 首过；k3 重抽放行到真实 API
        return {"fields": INJECTED_FIELDS, "field_confidence": INJECTED_CONF}, \
               {"prompt_tokens": 0, "completion_tokens": 0}
    return _real_llm(img_b64, prompt, model)
G._llm = fake_ocr_llm

# --- 跑全图 ---
if G.LEDGER.exists():
    G.LEDGER.unlink()
app = G.get_app()
tid = f"TEST-ESC-METER-{int(time.time())}"
init = {"invoice_id": "INV-R00-DEMO", "image_path": str(IMG),
        "claim": {"category": "办公用品", "city_tier": 1, "applicant": "张三",
                  "expense_date": "2026-03-09", "submit_date": "2026-04-01"}}
t0 = time.time()
out = app.invoke(init, {"configurable": {"thread_id": tid}})
wall = time.time() - t0

# --- 逐节点账单 ---
print(f"thread={tid}")
print(f"verdict={out.get('verdict')} | stop={out.get('stop_reason')} | reason={(out.get('reason') or '')[:60]}")
print(f"invoice_code: 000000000000 → {out['fields']['invoice_code']} "
      f"(真值 011002510851) | retries={out.get('field_retries')}")
print()
print(f"{'节点':<16}{'model':<12}{'prompt':>7}{'completion':>11}{'合计':>7}{'延迟':>8}")
tt = tl = 0.0
for node, b in out.get("token_usage", {}).items():
    t = b["prompt"] + b["completion"]; tt += t; tl += b["latency_s"]
    tag = " (mock)" if node == "ocr" else ""
    print(f"{node:<16}{b['model']:<12}{b['prompt']:>7}{b['completion']:>11}{t:>7}{b['latency_s']:>7.2f}s{tag}")
print(f"{'—合计':<39}{tt:>7}{tl:>7.2f}s  wall={wall:.2f}s")
