#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""k2.6 OCR 上岗冒烟：12 张清晰单，只考字段抽取（含逐字段置信度输出）。
过关线：字段准确率 ≥98%（6 字段 × 12 单 = 72 个字段，错 ≤1 个）。
不过关 → 退方案②（外部便宜 VLM），记录在案不硬上岗。
"""
import base64, json, os, time
from pathlib import Path
import requests
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).parent.parent
ENV = os.environ.get("AUDIT_CONFIG_FILE", str(Path(__file__).resolve().parent.parent / "config.local"))
OUT = ROOT / "agent" / "smoke_k26_results.jsonl"

PROMPT = """从发票图片提取字段，并对每个字段给出置信度(0~1，看不清就给低分)。
只输出 JSON：{"fields": {"invoice_code": "", "invoice_no": "", "date": "", "buyer_name": "", "seller": "", "total": 0.0},
"field_confidence": {"invoice_code": 0.0, "invoice_no": 0.0, "date": 0.0, "buyer_name": 0.0, "seller": 0.0, "total": 0.0}}"""

def load_env(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def extract(base, key, img_b64):
    r = requests.post(base.rstrip("/") + "/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": "kimi-k2.6", "temperature": 1, "max_tokens": 4096,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": PROMPT},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                      timeout=180)
    if r.status_code != 200:
        return None, {}
    d = r.json()
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    try:
        return json.loads(content[s:e + 1]), d.get("usage", {})
    except Exception:
        return None, d.get("usage", {})

def field_ok(pv, tv, name):
    if pv is None:
        return False
    if name == "total":
        try:
            return abs(float(pv) - float(tv)) <= 0.01
        except (TypeError, ValueError):
            return False
    return str(pv).strip() == str(tv).strip()

def main():
    load_env(ENV)
    base, key = os.environ["KIMI_BASE_URL"], os.environ["KIMI_API_KEY"]
    golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
    clear = [g for g in golden if g["stratum"] == "clear"][:12]
    FIELDS = ["invoice_code", "invoice_no", "date", "buyer_name", "seller", "total"]

    def process(g):
        img_b64 = base64.b64encode((ROOT / "eval" / "dataset" / g["image"]).read_bytes()).decode()
        t0 = time.time()
        pred, usage = extract(base, key, img_b64)
        lat = round(time.time() - t0, 2)
        if pred is None:
            return {"invoice_id": g["invoice_id"], "ok": 0, "err": True, "latency_s": lat}
        pf = pred.get("fields", {})
        bad = [f for f in FIELDS if not field_ok(pf.get(f), g["fields"].get(f), f)]
        return {"invoice_id": g["invoice_id"], "ok": len(FIELDS) - len(bad), "bad": bad,
                "conf": pred.get("field_confidence"), "usage": usage, "latency_s": lat}

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(process, clear))
    with open(OUT, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total_f = len(clear) * len(FIELDS)
    got = sum(r["ok"] for r in results)
    print(f"字段准确率: {got}/{total_f} = {got/total_f:.4f}（过关线 0.98）")
    for r in results:
        if r.get("err") or r["ok"] < len(FIELDS):
            print("  失误:", r)
    lats = sorted(r["latency_s"] for r in results)
    toks = [r.get("usage", {}).get("total_tokens", 0) for r in results]
    print(f"延迟 p50={lats[len(lats)//2]}s max={lats[-1]}s | token/单 均值={sum(toks)/len(toks):.0f}")

if __name__ == "__main__":
    main()
