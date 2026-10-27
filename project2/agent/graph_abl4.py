#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融④·去检索（2026-10-22）：policy_store 两个岗位（cite/search）全摘，图结构一刀不动。
切除边界：reject_node 不再挂"依据v1.0 x.y「原文」"、set_gate 不再附 policy_citations；
驳回仍有裸条款号（条款号来自 rule_check 的确定性输出，非检索产物）。
赛前判断：verdict 类指标应与②全等（检索从不参与判定），可测增量=引用贯通率与可审计性。
"""
import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import AuditState
import graph as G


def reject_node_abl4(state: AuditState) -> dict:
    """同 G.reject_node，摘掉 STORE.cite：理由只剩裸条款号，无版本指针、无原文。"""
    v = state.get("rule_violations", [])
    reason = "; ".join(f"违反{x['clause']}({x['detail']})" for x in v) or "人工驳回"
    hd = state.get("human_decision")
    if hd and hd.get("action") == "reject":
        reason += f"；人工批复驳回({hd.get('reviewer', '?')}: {hd.get('note', '')})"
    return {"verdict": "reject", "reason": reason,
            "stop_reason": "completed", "step_count": state.get("step_count", 0) + 1}


def set_gate_abl4(kind):
    """同 G.set_gate，摘掉 STORE.cite：gate 不带条款原文指针。"""
    def _node(state):
        if kind == "lowconf":
            low = [f for f, c in state.get("field_confidence", {}).items() if c < G.CONF_THRESHOLD]
            return {"gate_kind": "review",
                    "gate_reason": f"OCR 低置信字段重试{G.MAX_FIELD_RETRY}次仍不清: {low}（fail-closed，不静默放行）"}
        if kind == "item":
            detail = next((x["detail"] for x in state.get("rule_violations", []) if x.get("review")), "")
            return {"gate_kind": "review", "gate_reason": f"{detail}（存疑转人工，不硬判驳回）"}
        reasons = {"escalate": "单笔≥5000，按1.4须人工审批",
                   "review": f"查验服务不可用(status={state.get('verify_result', {}).get('status')})，fail-closed"}
        return {"gate_kind": kind, "gate_reason": reasons[kind]}
    return _node


def get_app():
    g = StateGraph(AuditState)
    g.add_node("ocr", G.ocr_node)
    g.add_node("conf_gate", G.conf_gate_node)
    g.add_node("escalate_field", G.escalate_field_node)
    g.add_node("rule_check", G.rule_check_node)
    g.add_node("verify", G.verify_node)
    g.add_node("ledger_check", G.ledger_check_node)
    g.add_node("gate_escalate", set_gate_abl4("escalate"))
    g.add_node("gate_review", set_gate_abl4("review"))
    g.add_node("gate_lowconf", set_gate_abl4("lowconf"))
    g.add_node("gate_item", set_gate_abl4("item"))
    g.add_node("human_gate", G.human_gate_node)
    g.add_node("record", G.record_node)
    g.add_node("reject", reject_node_abl4)
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
