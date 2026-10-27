#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消融四刀 · 成本-成功率 Pareto（数据=各配置实测 token p50 / 判定准确率）。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# (label, token_p50, 对抗acc, 常规acc, color, 标签偏移)
cfgs = [
    ("① 裸基线",      3184, 53.3, 93.3, "#999999", (12, 8)),
    ("② 完整系统",     2218, 93.3, 100,  "#1f77b4", (-40, 16)),
    ("③ 去规则",       4073, 53.3, 82.2, "#d62728", (12, -18)),
    ("④ 去检索",       2125, 93.3, 100,  "#2ca02c", (-50, -36)),
    ("⑤ 去分层(全k3)", 1572, 93.3, 100,  "#ff7f0e", (-160, -4)),
    ("⑥ 去容错链",     2201, 93.3, 100,  "#9467bd", (18, -10)),
    ("⑦ 异构双档(GLM免费)", 1161, 93.3, 100, "#e377c2", (-30, 16)),
]
fig, ax = plt.subplots(figsize=(9, 6), dpi=140)
for label, tok, adv, reg, c, off in cfgs:
    ax.scatter(tok, adv, s=140, c=c, zorder=3)
    ax.annotate(f"{label}\n常规{reg}", (tok, adv), textcoords="offset points",
                xytext=off, fontsize=9.5)
ax.axhline(85, ls="--", lw=1, c="#d62728", alpha=0.6)
ax.text(1550, 86, "对抗验收线 ≥85", color="#d62728", fontsize=9)
ax.set_xlabel("成本：token p50（/单）")
ax.set_ylabel("对抗判定准确率（%）")
ax.set_title("消融五刀 · 成本-成功率 Pareto（60 单 golden set，2026-10-22）")
ax.set_xlim(950, 4500)
ax.set_ylim(40, 105)
ax.grid(alpha=0.3)
plt.tight_layout()
out = Path(__file__).parent / "report_pareto.png"
plt.savefig(out)
print("saved", out)
