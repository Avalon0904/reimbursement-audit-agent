#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融⑦·异构双档（2026-10-22）：OCR 首过换外部免费 VLM（智谱 glm-4v-flash），k3 旗舰兜底不变。
背景：⑤证明 k2.6/k3 同宗双档在 token 口径无收益（前提1价差不存在）；本刀把前提1变成真的——
glm-4v-flash 免费（探针单票 1162 token、5.7s、字段 6/6）。4.6V-Flash 因免费层 1305 拥堵未上岗，
退一代 4V-Flash（HANDOVER 预留备胎型号）。口径：4K 上下文/max_tokens≤1024，实测载荷 ~1.2K 富余。
看点（赛前写死三结局）：字段≥98% 上岗 / 95~98% T2 容错链首次真实触发 / <95% 不上岗。
其余组件与②全等（规则/查验/台账/人工门/k3 escalation），变量唯一=OCR 模型。
"""
import base64, json, sqlite3, time
from pathlib import Path
import requests
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import AuditState
import graph as G
import os

ZBASE = os.environ["ZHIPU_BASE_URL"].rstrip("/")
ZKEY = os.environ["ZHIPU_API_KEY"]
ZMODEL = "glm-4v-flash"


def _llm_zhipu(img_b64, prompt):
    r = requests.post(ZBASE + "/chat/completions",
                      headers={"Authorization": f"Bearer {ZKEY}"},
                      json={"model": ZMODEL, "temperature": 0.1, "max_tokens": 1024,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                      timeout=180)
    d = r.json()
    if "choices" not in d:
        raise RuntimeError(f"zhipu error: {json.dumps(d, ensure_ascii=False)[:200]}")
    content = d["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    return json.loads(content[s:e + 1]), d.get("usage", {})


def ocr_node_abl7(state: AuditState) -> dict:
    """同 G.ocr_node，唯一差别：首过模型 k2.6 → glm-4v-flash（免费外部 VLM）。"""
    t0 = time.time()
    img_b64 = base64.b64encode(Path(state["image_path"]).read_bytes()).decode()
    pred, usage = _llm_zhipu(img_b64, G.OCR_PROMPT)
    return {"fields": pred["fields"], "field_confidence": pred["field_confidence"],
            "ocr_model": ZMODEL, **G._meter(state, "ocr", usage, t0, ZMODEL)}


def get_app():
    g = StateGraph(AuditState)
    g.add_node("ocr", ocr_node_abl7)
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
