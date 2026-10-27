#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AuditState：报销合规审核 Agent 的共享状态（LangGraph State schema v1.0）

设计原则：
1. State 是"处境的全景"，不是全量对话历史——每个字段回答"哪个节点写、哪个节点读"。
2. 数据结构决定系统能力上限：缺字段 = 某种优化原理性消失（逐字段置信度→单字段重试；
   台账读数→连号检测；人工批复→interrupt 续跑）。
3. 克制：只放图流转必需的信息，大块原文（制度条款全文等）走指针，防上下文膨胀。
"""
from typing import TypedDict, Optional


class AuditState(TypedDict, total=False):
    # ---------- 输入（调度器投喂，全流程只读） ----------
    invoice_id: str
    image_path: str
    claim: dict            # {category, applicant, expense_date, submit_date, city_tier}

    # ---------- OCR 层（k2.6 写；低置信字段由 k3 单字段覆写） ----------
    fields: dict           # {invoice_code, invoice_no, date, buyer_name, seller, total, items}
    field_confidence: dict # {字段名: 0~1}——逐字段，单字段 escalation 的依据
    ocr_model: str         # 实际出结果的模型（k2.6 / k2.6+k3 混合），消融第四刀读数用

    # ---------- 工具层（规则校验器/查验/台账/检索 写） ----------
    rule_violations: list  # [{clause, detail}] 规则校验器命中（确定性，零 LLM）
    verify_result: dict    # 查验库返回 {status: valid|not_found|voided, total}；服务异常={status:"unavailable"}
    ledger_hits: list      # 台账查询结果 [{type: "连号"|"重复报销", ref_invoice_id}]——跨单记忆的载体
    policy_citations: list # [{clause, text}] 检索回的条款指针+原文（驳回/存疑必须挂此，可审计）

    # ---------- 决策层 ----------
    verdict: str           # pass | reject | review | escalate
    reason: str            # 判定理由（引用 policy_citations 的条款号）
    confidence_tier: str   # high | low——低置信判定的整体标记，防"悄悄放行"

    # ---------- 人机协作（interrupt 的进出通道） ----------
    gate_kind: str         # escalate(权限边界) | review(能力边界)——进人工门的来源，分开统计
    gate_reason: str       # 进人工门的理由（挂起时给审批人看的快照说明）
    human_decision: Optional[dict]  # None=未介入；{action: approve|reject, note, reviewer} 恢复时由外部写入

    # ---------- 计量与控制（每个节点累加；Stop 条件三件套的载体） ----------
    token_usage: dict      # {节点名: {prompt, completion, model, latency_s}}——成本与延迟双维度的
                           # 节点级计量，成本报告/延迟归因/消融归因三份报告的共同原料
    step_count: int        # 熔断：上限 25
    stall_count: int       # 无进展计数：连续 3 步 State 无实质变化 → 判打转
    field_retries: dict    # {字段名: 已重试次数}——单字段重试 ≤2
    stop_reason: str       # 优雅退出原因：completed | step_fuse | stall_fuse | budget_fuse
    audit_report: dict     # 终态输出（含触发熔断时的"为什么没办成"报告）
