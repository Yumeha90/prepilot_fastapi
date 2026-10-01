"""数据生命周期 P15（PRD 3.5.1 / §5.14 / §7.7）。

权限分两码，与 §2.3 矩阵一致：
- `system:lifecycle` 查看与编辑策略（本期只有超管有）
- `system:purge`   执行粉碎（扫描预览也归它：预览是粉碎动作的前一步）

手动粉碎必须带 `confirm=true` —— 弹窗确认只在前端做是不够的，
接口层面也得挡一道，否则脚本误触就能删数据。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_perm
from app.core.errors import ErrorCode, bad_request
from app.models.user import User
from app.schemas.lifecycle import (
    AutoPurgeOut,
    LifecycleOut,
    PolicyOut,
    PolicyUpdateIn,
    PurgeIn,
    PurgeOut,
    ScanOut,
)
from app.services import lifecycle as svc

router = APIRouter(prefix="/system/lifecycle", tags=["lifecycle"])


@router.get("", response_model=LifecycleOut)
async def get_lifecycle(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("system:lifecycle")),
) -> LifecycleOut:
    """当前策略 + 历史策略 + 待粉碎 / 已粉碎计数。"""
    return await svc.lifecycle_data(db)


@router.put("/policy", response_model=PolicyOut)
async def update_policy(
    payload: PolicyUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("system:lifecycle")),
) -> PolicyOut:
    """编辑保留策略：旧版本转历史，新版本生效。"""
    policy = await svc.update_policy(
        db, user, name=payload.name, days=payload.days, enabled=payload.enabled
    )
    return svc.policy_out(policy)


@router.get("/scan", response_model=ScanOut)
async def scan(
    days: int | None = Query(default=None, ge=svc.SCAN_MIN_DAYS, le=svc.MAX_DAYS),
    limit: int = Query(default=svc.SCAN_LIMIT, le=svc.SCAN_LIMIT),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("system:purge")),
) -> ScanOut:
    """扫出「已到期且未入职」的候选人（只预览，不清除）。"""
    return await svc.scan(db, days=days, limit=limit)


@router.post("/purge", response_model=PurgeOut)
async def purge_manual(
    payload: PurgeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("system:purge")),
) -> PurgeOut:
    """手动粉碎：清空简历内容，行保留。已录用 / 已粉碎自动跳过。"""
    if not payload.confirm:
        raise bad_request(ErrorCode.LIFECYCLE_CONFIRM_REQUIRED, "请先确认粉碎操作")
    return await svc.manual_purge(db, user, payload.candidate_ids)


@router.post("/purge/auto", response_model=AutoPurgeOut)
async def purge_auto(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("system:purge")),
) -> AutoPurgeOut:
    """立即执行一次定时扫描（不等下个整点）。用于验收与运维。"""
    return await svc.run_auto_purge(db)
