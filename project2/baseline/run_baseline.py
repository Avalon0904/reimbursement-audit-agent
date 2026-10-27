#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""裸 VLM 基线（对照矩阵配置①）：强模型一把梭，制度全文塞 prompt，单轮出 JSON。
对照纪律：无工具、无重试链、无分层——架构贡献全部留给完整 Agent 去证明。
断点续跑：输出文件里已完成的 invoice_id 自动跳过（应对长批次分块执行）。
"""
import argparse, base64, json, os, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).parent.parent
ENV = os.environ.get("AUDIT_CONFIG_FILE", str(Path(__file__).resolve().parent.parent / "config.local"))
POLICY = (ROOT / "policy" / "报销制度_v1.0.md").read_text(encoding="utf-8")

PROMPT = """你是报销审核员。根据下面的《报销制度》、报销申报信息和发票图片，完成审核。

【报销制度】
{policy}

【申报信息】
申报类目: {category} | 申请人: {applicant} | 费用发生日: {expense_date} | 提交日: {submit_date} | 城市档: {city_tier}线

【任务】
1. 从发票图片提取字段；
2. 按制度判定：pass（通过）/ reject（驳回，须列违反条款号）/ review（存疑转人工复核）/ escalate（大额转人工审批）。

只输出 JSON，不要任何其他文字：
{{"fields": {{"invoice_code": "", "invoice_no": "", "date": "", "buyer_name": "", "seller": "", "total": 0.0}},
 "verdict": "", "violated_clauses": [], "confidence": 0.0}}"""

def load_env(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()  # override=True，防系统变量注入（踩坑#8）

def call_k3(base, key, img_b64, prompt):
    """temperature=1（思考模型硬约束）；空输出=可重试故障（踩坑#9），重试计入成本。"""
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    for attempt in range(3):
        r = requests.post(base.rstrip("/") + "/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": "kimi-k3", "temperature": 1, "max_tokens": 4096,
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                          timeout=180)
        if r.status_code != 200:
            time.sleep(2 ** attempt)
            continue
        data = r.json()
        u = data.get("usage", {})
        usage_total["prompt_tokens"] += u.get("prompt_tokens", 0)
        usage_total["completion_tokens"] += u.get("completion_tokens", 0)
        content = data["choices"][0]["message"].get("content") or ""
        if content.strip():
            return content, usage_total
    return "", usage_total  # 三次皆空 → 记为失败样本

def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1]) if s >= 0 else {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "baseline" / "baseline_results.jsonl"))
    a = ap.parse_args()
    load_env(ENV)
    base, key = os.environ["KIMI_BASE_URL"], os.environ["KIMI_API_KEY"]
    golden = [json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["invoice_id"] for l in Path(a.out).read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [g for g in golden if g["invoice_id"] not in done][: a.limit]
    print(f"待跑 {len(todo)} 单（已完成 {len(done)}，并发 {a.workers}）")

    def process(g):
        img_b64 = base64.b64encode((ROOT / "eval" / "dataset" / g["image"]).read_bytes()).decode()
        c = g["claim"]
        prompt = PROMPT.format(policy=POLICY, category=c["category"], applicant=c["applicant"],
                               expense_date=c["expense_date"], submit_date=c["submit_date"], city_tier=c["city_tier"])
        t0 = time.time()
        raw, usage = call_k3(base, key, img_b64, prompt)
        try:
            pred = parse_json(raw)
        except Exception:
            pred = {"_parse_error": True, "_raw": raw[:500]}
        return {"invoice_id": g["invoice_id"], "prediction": pred, "usage": usage,
                "latency_s": round(time.time() - t0, 2)}

    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    with open(a.out, "a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=a.workers) as ex:
        for rec in ex.map(process, todo):
            with lock:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
            p = rec["prediction"]
            print(f"{rec['invoice_id']}: verdict={p.get('verdict')} tokens={rec['usage']['prompt_tokens']}+{rec['usage']['completion_tokens']} {rec['latency_s']}s")

if __name__ == "__main__":
    main()
