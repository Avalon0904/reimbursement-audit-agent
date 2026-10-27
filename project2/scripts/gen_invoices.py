#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
golden set 合成生成器 v0.1（项目二 · 报销合规审核 Agent）
产出：images/*.jpg + golden_set.jsonl + verify_db.json
纪律：真值只许由本脚本生成，禁止手改 jsonl——图像与真值不一致时改脚本重生成。
用法：python gen_invoices.py --out ../eval/dataset --seed 42
"""
import argparse, json, math, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1000, 620
BUYER = ("蓝丸化工科技有限公司", "91330100MA27XT2P4A")
SELLERS = ["杭州云栖酒店有限公司", "沪上宴餐饮管理有限公司", "晨光文具华东分公司",
           "京城差旅服务社", "苏州恒安劳保用品厂", "华东危化品供应链有限公司"]
CATEGORIES = {"住宿费": 0.06, "办公用品": 0.13, "打车费": 0.09, "劳保用品": 0.13}
# 类目→限额（与制度 v1.0 一致；餐饮按人头规则复杂，常规单不出餐饮违规题，避免真值歧义）
LIMITS = {"住宿费": 500.0, "办公用品": 1000.0, "打车费": 100.0, "劳保用品": 2000.0}
CAT_SELLER = {"住宿费": "杭州云栖酒店有限公司", "办公用品": "晨光文具华东分公司",
              "打车费": "京城差旅服务社", "劳保用品": "苏州恒安劳保用品厂", "餐饮费": "沪上宴餐饮管理有限公司"}


def load_font(size):
    for p in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render(spec):
    """把发票 spec 渲染成仿增值税普票版式的图像（带'合成票样'水印，防误用）。"""
    img = Image.new("RGB", (W, H), "#fffdf5")
    d = ImageDraw.Draw(img)
    f_t, f, f_s = load_font(34), load_font(22), load_font(18)
    d.rectangle([20, 20, W - 20, H - 20], outline="#b03030", width=3)
    d.text((W // 2 - 170, 35), "增值税普通发票（合成票样）", font=f_t, fill="#b03030")
    d.text((60, 95), f"发票代码: {spec['invoice_code']}", font=f, fill="black")
    d.text((550, 95), f"发票号码: {spec['invoice_no']}", font=f, fill="black")
    d.text((60, 130), f"开票日期: {spec['date']}", font=f, fill="black")
    d.text((60, 175), f"购买方: {spec['buyer_name']}", font=f, fill="black")
    d.text((60, 205), f"税号: {spec['buyer_tax_id']}", font=f, fill="black")
    d.text((60, 250), f"销售方: {spec['seller']}", font=f, fill="black")
    y = 300
    d.line([40, y - 10, W - 40, y - 10], fill="#999", width=1)
    for it in spec["items"]:
        d.text((60, y), f"{it['name']}  数量{it['qty']}  单价{it['price']:.2f}  税率{int(it['tax_rate']*100)}%  金额{it['amount']:.2f}",
               font=f, fill="black")
        y += 40
    d.line([40, y, W - 40, y], fill="#999", width=1)
    d.text((60, y + 15), f"价税合计(小写): ¥{spec['total']:.2f}", font=f, fill="#b03030")
    d.text((60, y + 50), f"(大写): {spec['total_cn']}", font=f_s, fill="black")
    return img


CN_NUM = "零壹贰叁肆伍陆柒捌玖"
def amount_cn(x):
    return f"人民币{x:.2f}元整"  # 简化：评估不考大写转换正确性，只要求图上存在


def degrade(img, rng, level):
    """模糊件：旋转+模糊+压缩+亮度，'难但非不可读'。"""
    img = img.rotate(rng.uniform(-3, 3) * level, expand=False, fillcolor="#e8e4d8")
    img = img.filter(ImageFilter.GaussianBlur(radius=0.8 * level))
    img = img.point(lambda p: int(p * rng.uniform(0.85, 1.0)))
    return img.resize((W, H))


SUBMIT_DATE = "2026-04-01"  # 统一报销提交日，时限违规（1.1）以此为基准


def make_spec(rng, inv_id, category, total, date, no, stratum, verdict, violated, atype=None,
              claim_cat=None, note="", seller=None, tax_rate=None, buyer=None):
    rate = tax_rate if tax_rate is not None else CATEGORIES.get(category, 0.06)
    net = round(total / (1 + rate), 2)
    return {
        "invoice_id": inv_id, "stratum": stratum, "adversarial_type": atype,
        "invoice_code": f"0110025{rng.randint(10000, 99999)}", "invoice_no": f"{no:08d}",
        "date": date, "buyer_name": buyer or BUYER[0], "buyer_tax_id": BUYER[1],
        "seller": seller or CAT_SELLER.get(category, rng.choice(SELLERS)),
        "items": [{"name": category, "qty": 1, "price": net, "tax_rate": rate, "amount": total}],
        "total": total, "total_cn": amount_cn(total),
        "claim": {"category": claim_cat or category, "city_tier": 1, "applicant": "张三",
                  "expense_date": date, "submit_date": SUBMIT_DATE},
        "verdict": verdict, "violated_clauses": violated, "review_reason": "", "notes": note,
    }


def build_specs(rng):
    """五种对抗样例 + 常规填充。扩到 60 单：补 regular 列表即可，对抗五种已齐。"""
    specs = [
        # a 压线：498 合规 / 502 违规×2（限 500，条款 2.1）
        make_spec(rng, "INV-A01", "住宿费", 498.00, "2026-03-10", 10000001, "adversarial", "pass", [], "a", note="压线合规"),
        make_spec(rng, "INV-A02", "住宿费", 502.00, "2026-03-11", 10000002, "adversarial", "reject", ["2.1"], "a", note="压线违规"),
        make_spec(rng, "INV-A03", "住宿费", 502.00, "2026-03-15", 10000003, "adversarial", "reject", ["2.1"], "a", note="压线违规"),
        # b 连号：同日相邻号码两张
        make_spec(rng, "INV-B01", "餐饮费", 260.00, "2026-03-12", 20000001, "adversarial", "reject", ["1.5"], "b", note="连号第1张"),
        make_spec(rng, "INV-B02", "餐饮费", 240.00, "2026-03-12", 20000002, "adversarial", "reject", ["1.5"], "b", note="连号第2张"),
        # c 张冠李戴：餐饮票申报办公用品 → review
        make_spec(rng, "INV-C01", "餐饮费", 380.00, "2026-03-13", 30000001, "adversarial", "review", ["1.3", "4.1"], "c", claim_cat="办公用品"),
        make_spec(rng, "INV-C02", "餐饮费", 420.00, "2026-03-14", 30000002, "adversarial", "review", ["1.3", "4.1"], "c", claim_cat="办公用品"),
        make_spec(rng, "INV-C03", "餐饮费", 150.00, "2026-03-16", 30000003, "adversarial", "review", ["1.3", "4.1"], "c", claim_cat="办公用品"),
        # d 超大额 6800 → escalate（其余完全合规）
        make_spec(rng, "INV-D01", "办公用品", 6800.00, "2026-03-17", 40000001, "adversarial", "escalate", ["1.4"], "d"),
        make_spec(rng, "INV-D02", "劳保用品", 6800.00, "2026-03-18", 40000002, "adversarial", "escalate", ["1.4"], "d", seller="苏州恒安劳保用品厂"),
        # e 伪造三档（图像自洽，伪造信息在查验库）
        make_spec(rng, "INV-E01", "住宿费", 300.00, "2026-03-19", 50000001, "adversarial", "reject", ["1.6"], "e", note="查无此票"),
        make_spec(rng, "INV-E02", "住宿费", 300.00, "2026-03-19", 50000002, "adversarial", "reject", ["1.6"], "e", note="查无此票"),
        make_spec(rng, "INV-E03", "餐饮费", 200.00, "2026-03-20", 50000003, "adversarial", "reject", ["1.6"], "e", note="金额不符"),
        make_spec(rng, "INV-E04", "餐饮费", 200.00, "2026-03-20", 50000004, "adversarial", "reject", ["1.6"], "e", note="金额不符"),
        make_spec(rng, "INV-E05", "住宿费", 450.00, "2026-03-21", 50000005, "adversarial", "reject", ["1.6"], "e", note="已作废"),
    ]
    # 常规填充 45 单：合规 / 超限额 / 超时限(1.1) / 抬头不符(1.2)
    CLAUSE_OF = {"住宿费": "2.1", "办公用品": "2.4", "打车费": "2.3", "劳保用品": "3.2"}
    for i in range(45):
        cat = rng.choice(list(CATEGORIES))
        lim = LIMITS[cat]
        kind = rng.choices(["pass", "over_limit", "expired", "bad_title"],
                           weights=[62, 22, 8, 8])[0]
        stratum = "clear" if i % 3 else "blur"
        buyer, date, note = None, f"2026-03-{rng.randint(1,28):02d}", ""
        if kind == "pass":     # 限额的 20%~95%（避开压线区，留给对抗集考）
            total, verdict, violated = round(rng.uniform(0.2, 0.95) * lim, 2), "pass", []
        elif kind == "over_limit":  # 限额的 105%~160%（远离压线区，真值无歧义）
            total, verdict, violated = round(rng.uniform(1.05, 1.6) * lim, 2), "reject", [CLAUSE_OF[cat]]
        elif kind == "expired":     # 费用发生日早于提交日 30 天以上
            total = round(rng.uniform(0.2, 0.95) * lim, 2)
            date = f"2026-01-{rng.randint(5,28):02d}"
            verdict, violated, note = "reject", ["1.1"], "超30天时限"
        else:                        # 抬头用简称，不符 1.2
            total = round(rng.uniform(0.2, 0.95) * lim, 2)
            buyer = "蓝丸化工公司"
            verdict, violated, note = "reject", ["1.2"], "抬头非全称"
        specs.append(make_spec(rng, f"INV-R{i:02d}", cat, total, date,
                               60000000 + i, stratum, verdict, violated, buyer=buyer, note=note))
    return specs


def build_verify_db(specs, rng):
    """查验库：默认 valid；按 e 类 notes 制造伪造记录。"""
    db = {}
    for s in specs:
        rec = {"status": "valid", "total": s["total"]}
        if s["notes"] == "查无此票":
            rec = {"status": "not_found", "total": None}
        elif s["notes"] == "金额不符":
            rec = {"status": "valid", "total": round(s["total"] + rng.choice([50, 100]), 2)}
        elif s["notes"] == "已作废":
            rec = {"status": "voided", "total": s["total"]}
        db[s["invoice_no"]] = rec
    return db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../eval/dataset")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = Path(a.out); (out / "images").mkdir(parents=True, exist_ok=True)
    specs = build_specs(rng)
    rows = []
    for s in specs:
        img = render(s)
        if s["stratum"] == "blur":
            img = degrade(img, rng, level=rng.choice([1, 2]))
        rel = f"images/{s['invoice_id']}.jpg"
        img.save(out / rel, quality=75 if s["stratum"] == "blur" else 92)
        row = {
            "invoice_id": s["invoice_id"], "image": rel, "stratum": s["stratum"],
            "adversarial_type": s["adversarial_type"],
            "fields": {k: s[k] for k in ("invoice_code", "invoice_no", "date", "buyer_name",
                                          "buyer_tax_id", "seller", "items", "total")},
            "claim": s["claim"], "verdict": s["verdict"],
            "violated_clauses": s["violated_clauses"], "review_reason": s["review_reason"],
            "notes": s["notes"],
        }
        rows.append(row)
    (out / "golden_set.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (out / "verify_db.json").write_text(
        json.dumps(build_verify_db(specs, rng), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成 {len(rows)} 单 → {out}")


if __name__ == "__main__":
    main()
