"""人岗匹配（PRD §5.16 P18 / §6.7 / §6.8）。

两条硬口径：
- **BR-21 分数必须可解释**：只回一个数字视为不合格，必须同时给构成（权重代入）、
  证据（简历原文片段）与 AI 总结；且总分可由 `breakdown` 按公式复算。
- **BR-22 反馈只追加**：反馈不改原分数、不派生下游任务，绑定一张不可变评分快照。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MatchBreakdown(BaseModel):
    """得分构成一行（能力项 | 权重 | 得分 | 折算贡献）。"""

    competencyId: str = ""
    name: str = ""
    weight: float = 0
    score: int = 0
    contribution: float = 0
    # strong 证据充足 / verify 待核实 / weak 薄弱 / missing 缺失
    level: str = ""


class MatchEvidence(BaseModel):
    competencyId: str = ""
    # 简历原文定位（行号），quote 定位不到时两者都为空
    segmentId: str = ""
    quote: str = ""
    confidence: float = 0


class MatchBonus(BaseModel):
    id: str = ""
    text: str = ""
    delta: int = 0


class MatchGate(BaseModel):
    gateId: str = ""
    text: str = ""
    reason: str = ""


class MatchSummaryOut(BaseModel):
    conclusion: str = ""
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class MatchFeedbackItem(BaseModel):
    id: int
    kind: str
    expected_low: int | None = None
    expected_high: int | None = None
    comment: str = ""
    created_by_name: str = ""
    created_at: datetime


class MatchFeedbackIn(BaseModel):
    """BR-22 反馈表单。说明必填 ≥10 字 —— 空反馈等于噪声样本。"""

    kind: str = Field(
        pattern="^(higher|lower|wrong_quote|missing_evidence|bad_weight|other)$"
    )
    expected_low: int | None = Field(default=None, ge=0, le=100)
    expected_high: int | None = Field(default=None, ge=0, le=100)
    comment: str = Field(min_length=10, max_length=2000)


class MatchOut(BaseModel):
    """P18 一屏。没有 CURRENT 分时 `has_score=False`，页面给「计算」按钮。"""

    application_id: int
    candidate_id: int
    candidate_name: str = ""
    position_id: int
    position_name: str = ""
    stage: str = ""
    jd_version: int = 0
    jd_status: str = ""
    profile_status: str = ""

    has_score: bool = False
    score_id: int | None = None
    score: float | None = None
    tier: str = ""
    # CURRENT 有效 / STALE 岗位标准已变更，需重算
    status: str = ""
    algorithm_version: str = ""
    model_version: str = ""
    # pending 生成中 / ready 已生成 / failed 生成失败（不重试，等下次重算）
    summary_status: str = ""
    updated_at: datetime | None = None

    vetoed_gates: list[MatchGate] = Field(default_factory=list)
    unknown_gates: list[MatchGate] = Field(default_factory=list)
    breakdown: list[MatchBreakdown] = Field(default_factory=list)
    evidences: list[MatchEvidence] = Field(default_factory=list)
    bonuses: list[MatchBonus] = Field(default_factory=list)
    summary: MatchSummaryOut | dict[str, Any] | None = None

    feedbacks: list[MatchFeedbackItem] = Field(default_factory=list)
    # 计算前的前置提示（简历未确认 / JD 未确认），非空时页面阻断
    blocked_reason: str = ""


class MessageOut(BaseModel):
    ok: bool = True
    message: str = ""
