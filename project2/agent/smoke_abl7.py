#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""⑦号消融臂 · glm-4v-flash 上岗冒烟：12 张清晰单，字段准确率 ≥98% 才上岗（沿用 k2.6 上岗纪律）。
顺带统计：置信度分布（T2 容错链会不会触发的前瞻）、token/延迟。"""
import base64, json, time
from pathlib import Path
from graph_abl7 import _llm_zhipu, ZMODEL
import graph as G

ROOT = G.ROOT
golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
clear12 = [g for g in golden if g["stratum"] == "clear"][:12]
KEYS = ["invoice_code", "invoice_no", "date", "buyer_name", "seller"]

tot = ok = 0
toks, lats, lows = [], [], []
for g in clear12:
    img_b64 = base64.b64encode((ROOT / "eval" / "dataset" / g["image"]).read_bytes()).decode()
    t0 = time.time()
    pred, usage = _llm_zhipu(img_b64, G.OCR_PROMPT)
    lat = time.time() - t0
    f, conf = pred["fields"], pred.get("field_confidence", {})
    hit = sum(str(f.get(k, "")) == str(g["fields"].get(k, "")) for k in KEYS)
    hit += abs(float(f.get("total", -1)) - float(g["fields"]["total"])) < 0.01
    tot += len(KEYS) + 1
    ok += hit
    toks.append(usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
    lats.append(lat)
    low = [k for k, c in conf.items() if c < G.CONF_THRESHOLD]
    lows.extend(low)
    print(f"{'✓' if hit == len(KEYS)+1 else '✗'} {g['invoice_id']}: {hit}/{len(KEYS)+1} "
          f"conf_min={min(conf.values()):.2f} low={low} tok={toks[-1]} {lat:.1f}s")

print(f"\n== 冒烟结果（{ZMODEL}）==")
print(f"字段准确率: {ok}/{tot} = {ok/tot*100:.1f}%（上岗线 ≥98% = 至多错 {int(tot*0.02)} 个）")
print(f"token 单均={sum(toks)/len(toks):.0f}  延迟 p50={sorted(lats)[len(lats)//2]:.1f}s")
print(f"低置信字段(<0.7)出现次数: {len(lows)}（T2 触发前瞻）")
