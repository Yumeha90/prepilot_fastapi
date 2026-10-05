"""候选人与应聘记录（PRD 3.3）。

要点：
- `Candidate` 存简历主体，`Application` 存「某人投了某职位」的关联。
  看板 / 派单 / 会话全部围绕 Application 转，因此不能把职位挂在 Candidate 上。
- `contact_email` 必填且唯一（D14）：没有稳定的识别键，「一人一职位」（D3）落不了地。
- `profile_status` 与 `Application.stage` 是两条独立的状态线：
  前者是**简历侧**（上传 → 解析 → 确认 → 粉碎），后者是**流程侧**（pending → r1 → …）。
  BR-11 要求简历确认后才能推进流程，但两者不同步也不冲突。
- 粉碎只清 `resume_raw_text` / `parsed_profile`，**行保留**（D7），
  否则看板与历史会话会出现空洞外键。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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

# 简历侧状态
UPLOADING = "uploading"
PARSED = "parsed"
CONFIRMED = "confirmed"
ARCHIVED = "archived"

# 流程侧状态
PENDING = "pending"
IN_R1 = "in_r1"
STAGES = (PENDING, IN_R1, "in_r2", "in_hr", "accepted", "rejected", "in_pool", "archived")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    contact_email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    contact_phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # upload 文件上传 / paste 手动粘贴
    source: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)
    resume_raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resume_file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    parsed_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    profile_status: Mapped[str] = mapped_column(String(16), default=UPLOADING, nullable=False)

    auth_tick: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 粉碎来源 auto 定时 / manual 超管手动，以及手动时的操作人（定时任务无操作人）
    purge_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    purged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    applications: Mapped[list["Application"]] = relationship(
        "Application",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_purged(self) -> bool:
        return self.purged_at is not None


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "position_id", name="uq_application_candidate_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id"), index=True, nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(16), default=PENDING, nullable=False)
    current_round_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_rounds.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped["Candidate"] = relationship(
        "Candidate", back_populates="applications", lazy="selectin"
    )
