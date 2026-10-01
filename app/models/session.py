"""面试会话（PRD 3.4 工作台的载体，S2 派单时创建）。

要点：
- **会话围绕 application 转，不是 candidate**：一人一职位本期成立（D3），
  但模型不锁死；后续放开多职位时，会话天然挂在那条应聘记录上。
- **轮次信息做快照**（round_type / round_name）：流程配置变更只对未来的应聘记录生效，
  已派单的会话不跟随。只留 round_id 外键会读到「改过之后」的值。
- `candidate_id` / `position_id` 冗余：面试官按 interviewer_id 直接反查、看板按
  position_id 过滤，省掉每次 join application。
- 状态机取自 PRD §11.1：S1_DRAFT → … → S5_DRAFT → SUBMITTED。
  Step1–Step5 的产物（矩阵、问题链、评分、面评）随各自阶段加字段，骨架里不预铺空列。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 会话状态（PRD §11.1）
S1_DRAFT = "s1_draft"
S2_DRAFT = "s2_draft"
S3_DRAFT = "s3_draft"
S4_DRAFT = "s4_draft"
S5_DRAFT = "s5_draft"
SUBMITTED = "submitted"
SESSION_STATUSES = (S1_DRAFT, S2_DRAFT, S3_DRAFT, S4_DRAFT, S5_DRAFT, SUBMITTED)

# 轮次类型 → 应聘记录阶段
ROUND_STAGE = {
    "r1": "in_r1",
    "r2": "in_r2",
    "hr": "in_hr",
    "offer": "in_offer",
}

# Offer 轮是终结处置节点，不是面试（PRD 3.2）：不产生面评、不进工作台
NON_INTERVIEW_ROUNDS = ("offer",)

# 本轮默认时长（分钟）—— P09 顶部信息条可改，改的是会话不是职位
DEFAULT_DURATION_MINUTES = 45
MIN_DURATION_MINUTES = 10
MAX_DURATION_MINUTES = 240

# 矩阵行的证据状态（PRD §3.4.1：证据充足 / 待核实 / 缺失）
EVIDENCE_SUFFICIENT = "sufficient"
EVIDENCE_VERIFY = "verify"
EVIDENCE_MISSING = "missing"
EVIDENCE_STATUSES = (EVIDENCE_SUFFICIENT, EVIDENCE_VERIFY, EVIDENCE_MISSING)

# 矩阵行的来源：JD 核心能力 / AI 从简历补充 / 面试官手动新增
ROW_SOURCE_JD = "jd"
ROW_SOURCE_RESUME = "resume"
ROW_SOURCE_MANUAL = "manual"
ROW_SOURCES = (ROW_SOURCE_JD, ROW_SOURCE_RESUME, ROW_SOURCE_MANUAL)

# Step2 问题链的生成状态（异步 Celery + 前端轮询，BR-12）
CHAIN_IDLE = "idle"
CHAIN_RUNNING = "running"
CHAIN_READY = "ready"
CHAIN_FAILED = "failed"
CHAIN_STATUSES = (CHAIN_IDLE, CHAIN_RUNNING, CHAIN_READY, CHAIN_FAILED)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id"), index=True, nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), index=True, nullable=False
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("position_rounds.id"), nullable=False
    )
    # 派单那一刻的轮次快照
    round_type: Mapped[str] = mapped_column(String(8), nullable=False)
    round_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    interviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default=S1_DRAFT, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ---- 3.4 工作台产物（逐阶段加：Step1 矩阵 → Step2 问题链 → …）----
    # 本轮时长（分钟）：同一职位一面 / 二面时长不同，且与轮次快照同源，
    # 派单那一刻定下来，流程后续改动不追溯
    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_DURATION_MINUTES, nullable=False
    )
    # Step1 能力-证据矩阵整块（结构见 migrations/009_workbench.sql 注释）
    matrix_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 每次生成 / 保存 +1，用于前端判断「本地编辑是否落后于服务端」
    matrix_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matrix_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ---- Step2 问题链（异步生成，PRD §3.4.2 / §6.4）----
    chain_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chain_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chain_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chain_status: Mapped[str] = mapped_column(
        String(16), default=CHAIN_IDLE, nullable=False
    )
    chain_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 失败原因落库：页面上要能区分「向量库不可用」与「AI 没产出内容」
    chain_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped["Candidate"] = relationship(  # noqa: F821
        "Candidate", lazy="selectin"
    )
    interviewer: Mapped["User"] = relationship(  # noqa: F821
        "User", lazy="selectin"
    )

    @property
    def is_submitted(self) -> bool:
        return self.status == SUBMITTED
