#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""escalation + 熔断 单元验证（现有模糊度压不出低置信，故注入状态直测——纪律：不动评估集）。
1. escalate_field_node：低置信字段交给 k3 单字段重抽，好字段必须原样锁死；
2. route_after_conf：四种处境的路由判定；
3. fuse_node：熔断报告生成。
"""
import json
from pathlib import Path
from graph import escalate_field_node, route_after_conf, fuse_node, FUSE_STEPS

ROOT = Path(__file__).parent.parent
img = str(ROOT / "eval" / "dataset" / "images" / "INV-R00.jpg")  # 模糊件

# --- 1. escalation 节点直测：invoice_code 置信度 0.4，其余 0.95 ---
fake_state = {
    "invoice_id": "TEST-ESC", "image_path": img,
    "fields": {"invoice_code": "000000000000", "invoice_no": "60000000",
               "date": "2026-03-01", "buyer_name": "蓝丸化工科技有限公司",
               "seller": "未知", "total": 100.0},
    "field_confidence": {"invoice_code": 0.4, "invoice_no": 0.95, "date": 0.95,
                         "buyer_name": 0.95, "seller": 0.95, "total": 0.95},
}
out = escalate_field_node(fake_state)
good_locked = all(out["fields"][f] == fake_state["fields"][f]
                  for f in ["invoice_no", "date", "buyer_name", "total"])
print("① k3 单字段重抽: invoice_code", fake_state["fields"]["invoice_code"], "→", out["fields"]["invoice_code"],
      "| 新置信度:", round(out["field_confidence"]["invoice_code"], 2),
      "| 好字段锁死:", "✓" if good_locked else "✗", "| retries:", out["field_retries"])

# --- 2. 路由四处境 ---
cases = [
    ("全高清→rule_check", {"field_confidence": {"a": 0.9}, "step_count": 2, "token_usage": {}}, "rule_check"),
    ("低置信首圈→escalate", {"field_confidence": {"a": 0.4}, "step_count": 2, "token_usage": {}}, "escalate_field"),
    ("重试耗尽→gate_lowconf", {"field_confidence": {"a": 0.4}, "field_retries": {"a": 2}, "step_count": 6, "token_usage": {}}, "gate_lowconf"),
    ("步数到顶→fuse", {"field_confidence": {"a": 0.9}, "step_count": FUSE_STEPS, "token_usage": {}}, "fuse"),
    ("token到顶→fuse", {"field_confidence": {"a": 0.9}, "step_count": 5,
                        "token_usage": {"ocr": {"prompt": 40000, "completion": 12000}}}, "fuse"),
]
for name, st, expect in cases:
    got = route_after_conf(st)
    print(f"② {'✓' if got == expect else '✗'} {name} → {got}")

# --- 3. 熔断报告 ---
fz = fuse_node({"step_count": FUSE_STEPS, "token_usage": {"ocr": {"prompt": 1, "completion": 1}}})
print("③ fuse:", fz["stop_reason"], "|", fz["audit_report"]["detail"][:50])
