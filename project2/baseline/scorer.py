#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判分器：按真值 schema 比对系统输出，分层出指标。
指标口径（与一页纸第 4 节对齐）：
- 字段准确率：6 字段 exact match（total 容差 0.01），清晰/模糊/全量分开报
- 判定准确率：verdict 四值 exact，常规/对抗分开报
- 转人工召回：真值 review/escalate 中预测同为转人工类的比例
- 条款正确率：reject 样本中 violated_clauses 集合精确匹配比例
- 成本：token 累计与单任务 p50/p95
"""
import json, statistics, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIELDS = ["invoice_code", "invoice_no", "date", "buyer_name", "seller", "total"]

def field_ok(pred, truth, name):
    pv, tv = (pred or {}).get(name), truth.get(name)
    if pv is None:
        return False
    if name == "total":
        try:
            return abs(float(pv) - float(tv)) <= 0.01
        except (TypeError, ValueError):
            return False
    return str(pv).strip() == str(tv).strip()

def main(results_path=None):
    results_path = Path(results_path) if results_path else ROOT / "baseline" / "baseline_results.jsonl"
    golden = {g["invoice_id"]: g for g in
              (json.loads(l) for l in (ROOT / "eval" / "dataset" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())}
    results = [json.loads(l) for l in Path(results_path).read_text(encoding="utf-8").splitlines() if l.strip()]

    per_item, failures = [], []
    for r in results:
        g = golden[r["invoice_id"]]
        pred = r.get("prediction", {})
        if pred.get("_parse_error") or not pred:
            failures.append(r["invoice_id"])
            continue
        pf = pred.get("fields", {})
        n_ok = sum(field_ok(pf, g["fields"], f) for f in FIELDS)
        verdict_ok = pred.get("verdict") == g["verdict"]
        clauses_ok = set(pred.get("violated_clauses") or []) == set(g["violated_clauses"])
        per_item.append({
            "id": r["invoice_id"], "stratum": g["stratum"], "atype": g["adversarial_type"],
            "field_acc": n_ok / len(FIELDS), "verdict_ok": verdict_ok, "clauses_ok": clauses_ok,
            "truth_verdict": g["verdict"], "pred_verdict": pred.get("verdict"),
            "tokens": r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"],
            "latency": r["latency_s"],
        })

    def agg(items, key):
        return round(sum(i[key] for i in items) / len(items), 4) if items else None

    def report(name, items):
        if not items:
            return
        print(f"[{name}] n={len(items)}  字段={agg(items,'field_acc')}  判定={agg(items,'verdict_ok')}  条款={agg(items,'clauses_ok')}")

    print(f"=== 判分报告（{results_path.name}）===")
    print(f"有效样本 {len(per_item)} / 失败 {len(failures)} {failures or ''}")
    for s in ["clear", "blur", "adversarial"]:
        report(f"分层:{s}", [i for i in per_item if i["stratum"] == s])
    regular = [i for i in per_item if i["stratum"] != "adversarial"]
    adv = [i for i in per_item if i["stratum"] == "adversarial"]
    report("常规合计", regular)
    report("对抗合计", adv)
    for t in "abcde":
        report(f"对抗:{t}", [i for i in adv if i["atype"] == t])
    # 转人工召回（能力边界 review 与权限边界 escalate 分开）
    for cls in ["review", "escalate"]:
        sub = [i for i in per_item if i["truth_verdict"] == cls]
        hit = sum(1 for i in sub if i["pred_verdict"] in ("review", "escalate"))
        print(f"转人工召回({cls}): {hit}/{len(sub)}" + (f" = {hit/len(sub):.2f}" if sub else ""))
    # 成本
    toks = [i["tokens"] for i in per_item]
    lats = [i["latency"] for i in per_item]
    if toks:
        print(f"成本: 总 token={sum(toks)}  单任务 p50={statistics.median(toks):.0f} p95={sorted(toks)[int(len(toks)*0.95)-1]}")
        print(f"延迟: p50={statistics.median(lats):.1f}s  p95={sorted(lats)[int(len(lats)*0.95)-1]}s")
    # 错误清单（排查用）
    wrong = [i["id"] for i in per_item if not i["verdict_ok"]]
    print(f"判定错误样本: {wrong or '无'}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
