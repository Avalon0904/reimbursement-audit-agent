#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目二 · LangGraph 主干流程图（含四刀消融落点标注）。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_LLM, C_DET, C_HUMAN, C_TERM, C_FUSE = "#ffe8cc", "#d3f9d8", "#fff3bf", "#dbe4ff", "#f8d7da"

fig, ax = plt.subplots(figsize=(13, 9), dpi=140)
ax.set_xlim(0, 13); ax.set_ylim(0, 10); ax.axis("off")
ax.set_title("报销合规审核 Agent · LangGraph 主干（v0.5，60 单实测：常规 100 / 对抗 93.3 / token p50 2218）",
             fontsize=13, pad=14)

def box(x, y, w, h, text, fc, fs=10, bold=False):
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
                 boxstyle="round,pad=0.08", fc=fc, ec="#495057", lw=1.2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal")

def arrow(x1, y1, x2, y2, label="", rad=0.0, color="#495057", ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), connectionstyle=f"arc3,rad={rad}",
                 arrowstyle="-|>", mutation_scale=14, color=color, lw=1.2, linestyle=ls))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx + 0.12, my + 0.06, label, fontsize=8.5, color=color)

# 主链
box(3.0, 9.1, 3.2, 0.85, "OCR 字段提取（k2.6）\n逐字段置信度 ⑤刀：换k3判定不变", C_LLM, 9)
box(3.0, 7.9, 3.2, 0.7, "置信度门 conf_gate（0 token）", C_DET, 9.5)
box(3.0, 6.5, 3.4, 0.95, "规则校验 rule_check（0 token）\n抬头/时限/限额/类目 ③刀：-40pp", C_DET, 9, True)
box(3.0, 5.1, 3.4, 0.85, "发票查验 verify（fail-closed）\n查无此票/作废/金额不符", C_DET, 9)
box(3.0, 3.8, 3.4, 0.85, "台账 ledger_check（0 token）\n跨单记忆：连号/重复报销", C_DET, 9)

# 容错链与熔断
box(7.4, 8.6, 3.4, 0.9, "单字段重抽 escalate_field（k3）\n重试≤2次·无进展即弃 ⑥刀：零触发", C_LLM, 9)
box(11.2, 8.6, 2.9, 0.9, "熔断 fuse\n步数25 / token 50K\n→ audit_report 优雅终态", C_FUSE, 8.5)

# 人工门四个入口
box(7.4, 6.9, 3.2, 0.62, "gate_lowconf：重试耗尽（fail-closed）", C_HUMAN, 8.5)
box(7.4, 6.1, 3.2, 0.62, "gate_item：类目存疑不硬判", C_HUMAN, 8.5)
box(7.4, 5.1, 3.2, 0.62, "gate_review：查验不可用", C_HUMAN, 8.5)
box(7.4, 4.1, 3.2, 0.62, "gate_escalate：≥5000 按1.4审批", C_HUMAN, 8.5)
box(7.4, 2.7, 3.6, 0.95, "人工门 human_gate\ninterrupt 挂起 → 批复 → 续跑\n（checkpointer 真恢复）", C_HUMAN, 9, True)

# 终态
box(1.7, 1.4, 2.6, 0.9, "录入台账 record\n（CSV/MCP 预留）", C_TERM, 9)
box(5.6, 1.4, 3.4, 0.9, "驳回 reject\n依据 v1.0 x.y「原文」④刀：引用贯通100→0", "#ffd6d6", 8.5)

# 边
arrow(3.0, 8.66, 3.0, 8.28)
arrow(3.0, 7.55, 3.0, 7.0, "字段全清晰")
arrow(4.6, 7.9, 5.7, 8.5, "低置信<0.7", rad=-0.15)
arrow(5.7, 8.75, 4.62, 8.12, "", rad=-0.15)
arrow(9.12, 8.85, 9.75, 8.7, "每轮回门检查")
arrow(4.6, 7.7, 5.8, 7.0, "耗尽/打转", rad=-0.1)
arrow(3.0, 6.02, 3.0, 5.55, "无违规")
arrow(4.72, 6.35, 5.8, 6.15, "存疑", rad=-0.1)
arrow(3.0, 4.67, 3.0, 4.25)
arrow(4.72, 5.1, 5.8, 5.1, "不可用")
arrow(4.72, 3.95, 5.8, 4.05, "≥5000", rad=0.1)
arrow(7.4, 6.58, 7.4, 6.42); arrow(7.4, 5.8, 7.4, 5.42)
arrow(7.4, 4.78, 7.4, 4.42); arrow(7.4, 3.78, 7.4, 3.2)
arrow(2.2, 3.4, 1.8, 1.9, "全过", rad=0.25)
arrow(3.8, 3.4, 5.2, 1.9, "违规/命中", rad=-0.2, color="#b91c1c")
arrow(3.6, 6.3, 5.4, 1.7, "", rad=0.45, color="#b91c1c")
arrow(6.0, 2.6, 3.0, 1.7, "批准", rad=0.15, color="#15803d")
arrow(8.0, 2.25, 7.2, 1.75, "驳回", rad=0.1, color="#b91c1c")

# 图例
ax.text(10.4, 6.2, "图例", fontsize=10, fontweight="bold")
for i, (c, t) in enumerate([(C_LLM, "LLM 节点（只为读图付费）"),
                            (C_DET, "确定性组件（0 token）"),
                            (C_HUMAN, "人工门（HITL 真挂起）"),
                            (C_FUSE, "熔断兜底"),
                            (C_TERM, "终态 / 审计输出")]):
    ax.add_patch(FancyBboxPatch((10.4, 5.75 - i*0.42), 0.5, 0.26, boxstyle="round,pad=0.05", fc=c, ec="#495057"))
    ax.text(11.05, 5.88 - i*0.42, t, fontsize=9, va="center")
ax.text(10.4, 2.7, "四刀消融落点：\n③ 规则层=命中率第一支柱\n④ 检索=纯可审计性\n⑤ 双档省钱被证伪\n⑥ 容错链=零触发保险费", fontsize=9,
        bbox=dict(boxstyle="round", fc="#f1f3f5", ec="#adb5bd"))

out = Path(__file__).parent / "report_pipeline.png"
plt.tight_layout()
plt.savefig(out)
print("saved", out)
