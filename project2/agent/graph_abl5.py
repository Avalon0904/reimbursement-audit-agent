#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融⑤·去分层（2026-10-22）：OCR 首过从 k2.6 换 k3，全链路单模型旗舰档。
切除边界：只动 ocr_node 的模型选择；conf_gate/escalate_field(k3) 容错链原样保留
（escalation 本来就是 k3——双档变单档后，"疑难复核"变为同模型重抽，变量=模型分档本身）。
赛前预测（矩阵⑤）：判定≈②（k3 能力≥k2.6，上限封顶），成本回到~①水平。
看点：k2.6→k3 的字段准确率是否 100%→100%（若是，双档的纯收益=成本差）；
k3  OCR 的推理 token 会烧多少（k2.6 轻推理 ~248 token/单是省钱主因之一）。
"""
import base64, sqlite3, time
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import AuditState
import graph as G


def ocr_node_abl5(state: AuditState) -> dict:
    """同 G.ocr_node，唯一差别：模型 k2.6→k3（去模型分档）。"""
    t0 = time.time()
    img_b64 = base64.b64encode(Path(state["image_path"]).read_bytes()).decode()
    pred, usage = G._llm(img_b64, G.OCR_PROMPT, "kimi-k3")
    return {"fields": pred["fields"], "field_confidence": pred["field_confidence"],
            "ocr_model": "k3", **G._meter(state, "ocr", usage, t0, "kimi-k3")}


def get_app():
    g = StateGraph(AuditState)
    g.add_node("ocr", ocr_node_abl5)
    g.add_node("conf_gate", G.conf_gate_node)
    g.add_node("escalate_field", G.escalate_field_node)
    g.add_node("rule_check", G.rule_check_node)
    g.add_node("verify", G.verify_node)
    g.add_node("ledger_check", G.ledger_check_node)
    g.add_node("gate_escalate", G.set_gate("escalate"))
    g.add_node("gate_review", G.set_gate("review"))
    g.add_node("gate_lowconf", G.set_gate("lowconf"))
    g.add_node("gate_item", G.set_gate("item"))
    g.add_node("human_gate", G.human_gate_node)
    g.add_node("record", G.record_node)
    g.add_node("reject", G.reject_node)
    g.add_node("fuse", G.fuse_node)
    g.set_entry_point("ocr")
    g.add_edge("ocr", "conf_gate")
    g.add_conditional_edges("conf_gate", G.route_after_conf,
                            {"escalate_field": "escalate_field", "rule_check": "rule_check",
                             "gate_lowconf": "gate_lowconf", "fuse": "fuse"})
    g.add_edge("escalate_field", "conf_gate")
    g.add_conditional_edges("rule_check", G.route_after_rule,
                            {"reject": "reject", "verify": "verify", "gate_item": "gate_item"})
    g.add_conditional_edges("verify", G.route_after_verify,
                            {"reject": "reject", "gate_review": "gate_review", "ledger_check": "ledger_check"})
    g.add_conditional_edges("ledger_check", G.route_after_ledger,
                            {"reject": "reject", "gate_escalate": "gate_escalate", "record": "record"})
    for n in ("gate_escalate", "gate_review", "gate_lowconf", "gate_item"):
        g.add_edge(n, "human_gate")
    g.add_conditional_edges("human_gate", G.route_after_human, {"record": "record", "reject": "reject"})
    g.add_edge("record", END)
    g.add_edge("reject", END)
    g.add_edge("fuse", END)
    conn = sqlite3.connect(str(G.CKPT), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))
