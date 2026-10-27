#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融⑥·去容错链（2026-10-22）：砍 conf_gate + escalate_field，OCR(k2.6) 单遍直出进 rule_check。
切除边界：低置信字段不再触发 k3 单字段重抽、不再打转检测、不再 gate_lowconf 转人工——
OCR 给出什么就用什么，置信度照记但无人消费。fuse 节点保留（步数/token 硬顶是预算纪律不是容错链）。
赛前预测：模糊组（blur×15）是主考场——②里低置信字段靠重抽救回，⑥里错字段直接进规则校验，
预期误伤（错字段→误判 reject）或漏判（错字段→该抓没抓）；清晰组应无感。成本略降（无重抽调用）。
"""
import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import AuditState
import graph as G


def get_app():
    g = StateGraph(AuditState)
    g.add_node("ocr", G.ocr_node)
    g.add_node("rule_check", G.rule_check_node)
    g.add_node("verify", G.verify_node)
    g.add_node("ledger_check", G.ledger_check_node)
    g.add_node("gate_escalate", G.set_gate("escalate"))
    g.add_node("gate_review", G.set_gate("review"))
    g.add_node("gate_item", G.set_gate("item"))
    g.add_node("human_gate", G.human_gate_node)
    g.add_node("record", G.record_node)
    g.add_node("reject", G.reject_node)
    g.add_node("fuse", G.fuse_node)
    g.set_entry_point("ocr")
    g.add_edge("ocr", "rule_check")  # 容错链摘除：单遍直出，低置信无人兜底
    g.add_conditional_edges("rule_check", G.route_after_rule,
                            {"reject": "reject", "verify": "verify", "gate_item": "gate_item"})
    g.add_conditional_edges("verify", G.route_after_verify,
                            {"reject": "reject", "gate_review": "gate_review", "ledger_check": "ledger_check"})
    g.add_conditional_edges("ledger_check", G.route_after_ledger,
                            {"reject": "reject", "gate_escalate": "gate_escalate", "record": "record"})
    for n in ("gate_escalate", "gate_review", "gate_item"):
        g.add_edge(n, "human_gate")
    g.add_conditional_edges("human_gate", G.route_after_human, {"record": "record", "reject": "reject"})
    g.add_edge("record", END)
    g.add_edge("reject", END)
    g.add_edge("fuse", END)
    conn = sqlite3.connect(str(G.CKPT), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))
