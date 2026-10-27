#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针·阿里 qwen3-vl-flash 交叉验证 k2.6 置信度校准（2026-10-22）
设计：qwen3-vl-flash 作为独立第二读者，分层抽样 golden set（低置信字段所在单/模糊组/对抗组/清晰对照），
三方对照 golden 真值 vs k2.6（②full4 存档） vs qwen（现跑），检验：
  - k2.6 打低分（0.97/0.98）的字段是否真的更容易错（校准性）
  - k2.6 的抽取值与独立模型是否一致（交叉共识）
纪律：独立脚本、不动评估集、不动 graph.py；qwen 只读图不入库。
"""
import base64, json, time
from pathlib import Path
import requests

import graph as G  # 仅为 load_env + 读 full4 存档

KEY = G.os.environ["ALI_API_KEY"]
BASE = G.os.environ["ALI_BASE_URL"].rstrip("/")
ROOT = Path(__file__).parent.parent

def ocr_aliyun(img_path):
    img_b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
    t0 = time.time()
    r = requests.post(BASE + "/chat/completions",
                      headers={"Authorization": f"Bearer {KEY}"},
                      json={"model": "qwen3-vl-plus", "temperature": 0.1, "max_tokens": 2048,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": G.OCR_PROMPT},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                      timeout=180)
    d = r.json()
    if "choices" not in d:
        raise RuntimeError(f"aliyun error: {json.dumps(d, ensure_ascii=False)[:200]}")
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    return json.loads(content[s:e + 1]), d.get("usage", {}), round(time.time() - t0, 2)

# --- 1. 扫描②存档，找 k2.6 置信度最低的字段所在单 ---
app = G.get_app()
golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
low_records = []
for g in golden:
    st = app.get_state({"configurable": {"thread_id": f"{g['invoice_id']}-full4"}})
    fc = (st.values or {}).get("field_confidence")
    if fc:
        m = min(fc.values())
        if m <= 0.98:
            low_records.append((g["invoice_id"], m, [f for f, c in fc.items() if c == m]))
low_records.sort(key=lambda x: x[1])
print("k2.6 低置信（≤0.98）单：", low_records)

# --- 2. 分层抽样：低置信2 + 模糊组1 + 对抗组1 + 清晰对照1 ---
sample = [x[0] for x in low_records[:2]] + ["INV-R00", "INV-E01", "INV-R01"]
sample = list(dict.fromkeys(sample))
print("分层抽检样本：", sample, "\n")

FIELDS = ["invoice_code", "invoice_no", "date", "buyer_name", "seller", "total"]
gm = {g["invoice_id"]: g for g in golden}
for inv in sample:
    g = gm[inv]
    st = app.get_state({"configurable": {"thread_id": f"{inv}-full4"}}).values
    k26, kconf = st["fields"], st["field_confidence"]
    pred, usage, lat = ocr_aliyun(ROOT / "eval" / "dataset" / g["image"])
    qw = pred.get("fields", {})
    print(f"=== {inv}（stratum={g.get('stratum')}）qwen: {usage.get('prompt_tokens',0)}+{usage.get('completion_tokens',0)} tok, {lat}s")
    print(f"{'字段':<14}{'真值':<22}{'k2.6(置信)':<26}{'qwen3-vl-flash':<22}三方")
    for f in FIELDS:
        tv = g["fields"].get(f); kv = k26.get(f); qv = qw.get(f)
        agree = "✓✓" if (str(tv) == str(kv) == str(qv)) else ("k2.6✗" if str(tv) != str(kv) else "qwen✗")
        print(f"{f:<14}{str(tv):<22}{str(kv)+'('+str(kconf.get(f))+')':<26}{str(qv):<22}{agree}")
    it_t = (g["fields"].get("items") or [{}])[0].get("name")
    it_k = (k26.get("items") or [{}])[0].get("name")
    it_q = (qw.get("items") or [{}])[0].get("name")
    agree = "✓✓" if it_t == it_k == it_q else ("k2.6✗" if it_t != it_k else "qwen✗")
    print(f"{'items[0].name':<14}{str(it_t):<22}{str(it_k):<26}{str(it_q):<22}{agree}")
    print()
