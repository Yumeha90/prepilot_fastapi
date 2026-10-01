"""人岗匹配分（PRD §6.7 / §11.2）。

本期（3.2 第三段）只落表与状态位，算分链路在 3.3：

- JD 结构化变更确认后，该职位下所有 `CURRENT` 分数置为 `STALE`（岗位标准已变，需重算）
- **不自动重算**（PRD v1.6 定），由 HR 在 P08 / P18 手动触发
- 重算后**保留历史**：旧行留 STALE，新行插 CURRENT，因此没有
  `(position_id, candidate_id)` 唯一约束
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# CURRENT 有效 / STALE 岗位标准已变更
CURRENT = "CURRENT"
STALE = "STALE"


class MatchScore(Base):
    __tablename__ = "match_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 3.3 建 applications / candidates 后补外键，本期只存 id
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), index=True, nullable=False
    )
    # 算分当时用的 JD 版本（P18 回溯「按哪版标准算的分」）
    jd_version: Mapped[int] = mapped_column(Integer, nullable=False)

    score: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    algorithm_version: Mapped[str] = mapped_column(
        String(32), default="", nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CURRENT, nullable=False)

    vetoed_gates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 简历没写、无法核实的门槛：不否决，但要在 P18 提示人工确认
    unknown_gates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    breakdown: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    evidences: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    bonuses: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # AI 总结（BR-21）异步补写：pending 生成中 / ready 已生成 / failed 生成失败
    # 分数与构成是同步落定的，总结没回来不影响分数可用（PRD v1.11 的同步/异步边界）
    summary_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MatchFeedback(Base):
    """HR 对匹配分的异议（PRD §6.8 / BR-22）。

    **只追加、不可删改，也不回写原分数**：本期只落库做样本沉淀，
    不派生任何下游任务（不做周级聚合 / 归因流程 / 离线微调）。
    """

    __tablename__ = "match_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_scores.id", ondelete="SET NULL"), index=True, nullable=True
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), nullable=False
    )
    # higher 评分偏高 / lower 评分偏低 / wrong_quote 依据引用错误 /
    # missing_evidence 证据遗漏 / bad_weight 权重不合理 / other 其他
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 提交瞬间的评分快照（不可变），改分后也能回溯当时看到的是什么
    snapshot_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
