#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""⑦号消融臂 · GLM-4.6V-Flash 探针：验证三件事——模型ID可用、JSON 可解析、逐字段置信度给得出。
单票调用（INV-R01），打印字段/置信度/usage/延迟。用法：python probe_glm.py [model]
"""
import base64, json, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).parent.parent
ENV = os.environ.get("AUDIT_CONFIG_FILE", str(Path(__file__).resolve().parent.parent / "config.local"))
for line in Path(ENV).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        import os
        os.environ[k.strip()] = v.strip()

import os
BASE = os.environ["ZHIPU_BASE_URL"].rstrip("/")
KEY = os.environ["ZHIPU_API_KEY"]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "glm-4.6v-flash"
MAX_TOK = int(sys.argv[2]) if len(sys.argv) > 2 else 4096

OCR_PROMPT = """从发票图片提取字段，并对每个字段给出置信度(0~1)。
只输出 JSON：{"fields": {"invoice_code": "", "invoice_no": "", "date": "", "buyer_name": "", "seller": "",
"total": 0.0, "items": [{"name": "明细品名", "amount": 0.0}]},
"field_confidence": {"invoice_code": 0.0, "invoice_no": 0.0, "date": 0.0, "buyer_name": 0.0, "seller": 0.0, "total": 0.0}}"""

golden = {g["invoice_id"]: g for g in (json.loads(l) for l in
          (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
g = golden["INV-R01"]
img_b64 = base64.b64encode((ROOT / "eval" / "dataset" / g["image"]).read_bytes()).decode()

t0 = time.time()
r = requests.post(BASE + "/chat/completions",
                  headers={"Authorization": f"Bearer {KEY}"},
                  json={"model": MODEL, "temperature": 0.1, "max_tokens": MAX_TOK,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": OCR_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                  timeout=180)
lat = time.time() - t0
d = r.json()
if "choices" not in d:
    print("API 错误:", json.dumps(d, ensure_ascii=False)[:400])
    sys.exit(1)
content = d["choices"][0]["message"].get("content") or ""
print(f"model={MODEL} 延迟={lat:.1f}s usage={d.get('usage')}")
print(f"原始输出前 200 字符: {content[:200]}")
s, e = content.find("{"), content.rfind("}")
pred = json.loads(content[s:e + 1])
print("\n提取字段:", json.dumps(pred["fields"], ensure_ascii=False))
print("\n置信度:", json.dumps(pred.get("field_confidence"), ensure_ascii=False))
print("\n真值:  ", json.dumps(g["fields"], ensure_ascii=False))
KEYS = ["invoice_code", "invoice_no", "date", "buyer_name", "seller"]
hit = sum(str(pred["fields"].get(k, "")) == str(g["fields"].get(k, "")) for k in KEYS)
hit += abs(float(pred["fields"].get("total", -1)) - float(g["fields"]["total"])) < 0.01
print(f"\n单票字段命中: {hit}/{len(KEYS)+1}")
