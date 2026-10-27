#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""制度检索 mini RAG（policy_store v1.0）
两个岗位：① 条款号→原文指针（可审计引用）；② 自由查询的语义入口（BM25 腿，13 条规模裁剪版）。
版本化：每条带 version/effective_from/effective_to(None=现行)；retrieve 按费用发生日过滤（4.2 条）。
被否方案：Milvus+向量腿——13 条规模过度设计；方法论口径=混合检索按规模裁剪。
"""
import re
from pathlib import Path
import jieba
from rank_bm25 import BM25Okapi

POLICY_MD = Path(__file__).parent.parent / "policy" / "报销制度_v1.0.md"

def _tokenize(text):
    return [w for w in jieba.lcut(text) if w.strip() and not re.fullmatch(r"[\W_]+", w)]

def load_clauses(path=POLICY_MD):
    """把制度 markdown 切成条款记录。每行 '- **x.y** 内容' 为一条。"""
    clauses = []
    chapter = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        m_ch = re.match(r"##\s+第(.+?)章\s*(.*)", line)
        if m_ch:
            chapter = line.lstrip("# ").strip()
        m = re.match(r"-\s+\*\*(\d+\.\d+)\s*([^*]*)\*\*\s*(.*)", line)
        if m:
            no, title, body = m.group(1), m.group(2).strip(), m.group(3).strip().lstrip("：:")
            clauses.append({
                "clause": no, "title": title, "chapter": chapter,
                "text": f"{title}：{body}" if title else body,
                "version": "v1.0", "effective_from": "2026-01-01", "effective_to": None,
            })
    return clauses

# 口语↔制度语的同义词映射（检索工程的标配：关键词扩展，把行话鸿沟显式编码）
KEYWORDS = {
    "1.1": "逾期 过期 超期 一个月 30天 时限  deadline",
    "1.2": "抬头 税号 公司名 简称 全称",
    "1.3": "张冠李戴 类目不符 虚假申报 开成 冒充 不一致",
    "1.4": "大额 5000 审批 总经理 签字 谁批 超大额",
    "1.5": "连号 相邻号码 同一天 合并计算",
    "1.6": "假发票 伪造 真伪 查验 核验 作废 查无此票 是假的",
    "1.7": "查验失败 服务不可用 转人工",
    "2.1": "出差 酒店 住宿 一晚 房价",
    "2.2": "请客 吃饭 招待 餐费 人均",
    "2.3": "打车 高铁 飞机 交通 车票 机票",
    "2.4": "办公用品 文具 明细清单",
    "3.1": "危化品 化学品 采购审批 双人验收",
    "3.2": "劳保 防护 安全帽 手套 额度",
    "3.3": "驻厂 偏远 补贴 出差补助",
    "4.1": "存疑 拿不准 复核 人工",
    "4.2": "版本 生效 变更 旧制度 适用",
}

class PolicyStore:
    def __init__(self, path=POLICY_MD):
        self.clauses = load_clauses(path)
        self.by_no = {c["clause"]: c for c in self.clauses}
        self.bm25 = BM25Okapi([_tokenize(c["text"] + c["chapter"] + KEYWORDS.get(c["clause"], ""))
                               for c in self.clauses])

    def _effective(self, c, expense_date=None):
        if not expense_date:
            return c["effective_to"] is None  # 默认只看现行版
        return c["effective_from"] <= expense_date and \
               (c["effective_to"] is None or expense_date < c["effective_to"])

    def cite(self, clause_no, expense_date=None):
        """岗位①：条款号→原文指针。带版本生效期过滤，找不到返回 None（不许裸引用）。"""
        c = self.by_no.get(clause_no)
        if c and self._effective(c, expense_date):
            return {"clause": c["clause"], "title": c["title"], "text": c["text"],
                    "version": c["version"]}
        return None

    def search(self, query, k=3, expense_date=None):
        """岗位②：自由查询→BM25 top-k，带生效期过滤。"""
        scores = self.bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self.clauses, scores), key=lambda x: -x[1])
        return [{"clause": c["clause"], "title": c["title"], "text": c["text"],
                 "version": c["version"], "score": round(s, 3)}
                for c, s in ranked if self._effective(c, expense_date)][:k]

if __name__ == "__main__":
    store = PolicyStore()
    print(f"加载条款 {len(store.clauses)} 条")
    # 语义入口自测：查询→期望条款（mini 评估，检索质量一眼可见）
    cases = [("出差住酒店超标怎么办", "2.1"), ("发票是假的能报吗", "1.6"),
             ("两张连号的发票", "1.5"), ("报销超过一个月了", "1.1"),
             ("买危化品要附什么", "3.1"), ("大额报销要谁批", "1.4"),
             ("劳保用品额度多少", "3.2"), ("餐饮开成办公用品", "1.3")]
    hit = 0
    for q, expect in cases:
        top = store.search(q, k=1)[0]
        ok = top["clause"] == expect
        hit += ok
        print(f"{'✓' if ok else '✗'} 「{q}」→ {top['clause']} {top['title']} (期望 {expect}, score {top['score']})")
    print(f"语义入口自测: {hit}/{len(cases)}")
    # 岗位①自测
    print("cite 2.1:", store.cite("2.1", expense_date="2026-03-10"))
