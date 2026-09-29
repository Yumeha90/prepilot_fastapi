"""站内通知（PRD 3.1.2 头像下拉 + 铃铛）。

订阅事件类型：
- assignment           同岗位被派单
- stage_changed        候选人阶段变更
- evaluation_submitted 面评提交完成
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationSubscription

EVENT_TYPES: list[str] = ["assignment", "stage_changed", "evaluation_submitted"]


async def ensure_default_subscriptions(db: AsyncSession, user_id: int) -> None:
    """新用户默认订阅全部事件（幂等）。"""
    existing = set(
        await db.scalars(
            select(NotificationSubscription.event_type).where(
                NotificationSubscription.user_id == user_id
            )
        )
    )
    for event in EVENT_TYPES:
        if event not in existing:
            db.add(
                NotificationSubscription(
                    user_id=user_id, event_type=event, enabled=True
                )
            )
    if not existing:
        await db.commit()


async def create_notification(
    db: AsyncSession,
    user_id: int,
    type: str,
    title: str,
    body: str = "",
    payload: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def notify_subscribers(
    db: AsyncSession,
    event_type: str,
    title: str,
    body: str = "",
    payload: dict[str, Any] | None = None,
    user_ids: Iterable[int] | None = None,
) -> int:
    """按订阅关系群发通知，返回命中人数。

    user_ids 为空表示「所有订阅了该事件的用户」；指定时只取其中仍订阅着的人。
    """
    stmt = select(NotificationSubscription.user_id).where(
        NotificationSubscription.event_type == event_type,
        NotificationSubscription.enabled.is_(True),
    )
    if user_ids is not None:
        stmt = stmt.where(NotificationSubscription.user_id.in_(list(user_ids)))
    targets = list(await db.scalars(stmt))

    for uid in targets:
        db.add(
            Notification(
                user_id=uid,
                type=event_type,
                title=title,
                body=body,
                payload_json=json.dumps(payload or {}, ensure_ascii=False),
            )
        )
    if targets:
        await db.commit()
    return len(targets)


async def unread_count(db: AsyncSession, user_id: int) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    )
    return int(result or 0)
