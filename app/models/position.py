"""职位 / 面试轮次 / JD 版本快照（PRD 3.2）。

要点：
- `Position` 同时持有「已确认生效」的 JD 结构化字段与「未确认」的 `jd_draft_json`
  —— BR-01 要求 AI 拆解必须人工确认后才可用，未确认的内容不参与匹配计算。
- `jd_hash` 是结构化字段的规范化摘要，用于判定是否实质变更（只改原文/名称不 bump）。
- `PositionRound` 拆子表：面试官视角要按 `interviewer_id` 反查被指派职位，
  且 BR-23「每类型至多 1 个」由 `UNIQUE(position_id, type)` 保证。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # draft 草稿 / open 招聘中 / paused 已暂停 / closed 已关闭
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )

    jd_raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # competencies 元素形如 {"id": "slug", "text": "...", "weight": 40}
    jd_hard_gates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    jd_competencies: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    jd_bonuses: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 未确认的 AI 拆解 / HR 编辑中内容，形如 {"hard_gates": [...], ...}
    jd_draft_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # empty 未录入 / draft 有草稿未确认 / confirmed 已确认生效
    jd_status: Mapped[str] = mapped_column(String(16), default="empty", nullable=False)
    jd_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    jd_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    copied_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rounds: Mapped[list["PositionRound"]] = relationship(
        "PositionRound",
        back_populates="position",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PositionRound.seq",
    )


class PositionRound(Base):
    __tablename__ = "position_rounds"
    __table_args__ = (
        UniqueConstraint("position_id", "type", name="uq_position_round_type"),
        UniqueConstraint("position_id", "seq", name="uq_position_round_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # r1 技术一面 / r2 技术二面 / hr HR 面（v1.26 起无 offer 轮）
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    interviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    position: Mapped["Position"] = relationship(
        "Position", back_populates="rounds", lazy="selectin"
    )


class JdVersion(Base):
    """JD 结构化快照历史（每次 bump 落一行，供 P18 回溯「当年按哪版标准算的分」）。"""

    __tablename__ = "jd_versions"
    __table_args__ = (UniqueConstraint("position_id", "version", name="uq_jd_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
