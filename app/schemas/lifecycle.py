"""数据生命周期 P15（PRD 3.5.1 / §5.14 / §7.7）。

粒度刻意做小：
- 策略只有「保留天数 + 是否启用 + 名称」，不做按职位/按角色的分策略（本期无此需求）；
- 扫描返回的是**待粉碎候选人的预览**，真正清除要走 `/purge` 且必须带 `confirm=true`
  —— 二次确认不能只做在前端，后端必须挡一道（PRD 3.5.1「操作需二次确认」）。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PolicyOut(BaseModel):
    id: int
    name: str = ""
    resource: str = "resume"
    days: int = 90
    enabled: bool = True
    status: str = "current"
    last_run_at: datetime | None = None
    last_purged: int = 0
    created_at: datetime


class PolicyUpdateIn(BaseModel):
    name: str | None = None
    days: int | None = None
    enabled: bool | None = None


class LifecycleOut(BaseModel):
    """页面首屏：当前策略 + 历史策略 + 两个计数（待粉碎 / 已粉碎）。"""

    policy: PolicyOut
    history: list[PolicyOut] = Field(default_factory=list)
    # 按当前策略已到期、等待粉碎的人数
    pending: int = 0
    # 已粉碎人数（含自动与手动）
    purged_total: int = 0


class ScanItem(BaseModel):
    candidate_id: int
    name: str = ""
    email: str = ""
    # 投过的职位名（本期一人一职位，通常只有一个）
    position_names: list[str] = Field(default_factory=list)
    stage: str = ""
    # 保留期起算点（D6：确认时间，未确认退回入库时间）—— 页面按它算超期天数
    since_at: datetime
    days_overdue: int = 0


class ScanOut(BaseModel):
    days: int
    total: int = 0
    items: list[ScanItem] = Field(default_factory=list)


class PurgeIn(BaseModel):
    candidate_ids: list[int] = Field(default_factory=list)
    # 二次确认：前端弹窗还不够，后端必须再挡一道
    confirm: bool = False


class PurgeOut(BaseModel):
    requested: int = 0
    purged: int = 0
    # 已粉碎过 / 已录用受保护 / 不存在，分别计数便于前端提示
    skipped_purged: int = 0
    skipped_accepted: int = 0
    skipped_missing: int = 0
    purged_ids: list[int] = Field(default_factory=list)


class AutoPurgeOut(BaseModel):
    """立即执行一次定时扫描（不等下个整点）。"""

    enabled: bool = True
    days: int = 90
    scanned: int = 0
    purged: int = 0
    ran_at: datetime | None = None
