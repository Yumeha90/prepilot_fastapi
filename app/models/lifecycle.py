"""数据保留策略（PRD 3.5.1 P15 / BR-10）。

为什么策略要成表、还要留历史版本：
- 保留天数是要能调的（不同客户/不同时期合规口径不同），写死在代码里改一次要发版；
- 「当年按 90 天粉碎的」必须可追溯 —— 有人质疑「凭什么删我简历」时，
  回答得靠「那时候生效的策略是 90 天」，所以编辑后旧版本转 history 而不是原地改。
- 默认策略**不给删除入口**（PRD §5.14）：只能改天数或停用，避免把合规基线删掉。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 生效中 / 历史版本（只读）
CURRENT = "current"
HISTORY = "history"

# 本期只有简历一类需要定时粉碎的数据
RESOURCE_RESUME = "resume"

# 粉碎来源：定时任务 / 超管手动
PURGE_AUTO = "auto"
PURGE_MANUAL = "manual"


class RetentionPolicy(Base):
    """一条生效中或已失效的保留策略。"""

    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(32), default=RESOURCE_RESUME, nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CURRENT, nullable=False)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 最近一次定时扫描（页面要能回答「定时任务跑没跑」）
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_purged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
