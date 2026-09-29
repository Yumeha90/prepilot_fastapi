"""当前用户的通知与订阅（头像下拉 / 铃铛，PRD 3.1.2）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.notification import Notification, NotificationSubscription
from app.models.user import User
from app.schemas.notification import (
    NotificationRead,
    SubscriptionRead,
    SubscriptionUpdate,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    unread: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return list(await db.scalars(stmt))


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    record = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if record is not None:
        record.is_read = True
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Response:
    records = await db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.is_read.is_(False)
        )
    )
    for record in records:
        record.is_read = True
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notification-subscriptions", response_model=list[SubscriptionRead])
async def list_subscriptions(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[NotificationSubscription]:
    return list(
        await db.scalars(
            select(NotificationSubscription).where(
                NotificationSubscription.user_id == user.id
            )
        )
    )


@router.put("/notification-subscriptions", response_model=list[SubscriptionRead])
async def update_subscriptions(
    payload: SubscriptionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationSubscription]:
    existing = {
        row.event_type: row
        for row in await db.scalars(
            select(NotificationSubscription).where(
                NotificationSubscription.user_id == user.id
            )
        )
    }
    for item in payload.items:
        row = existing.get(item.event_type)
        if row is None:
            db.add(
                NotificationSubscription(
                    user_id=user.id,
                    event_type=item.event_type,
                    enabled=item.enabled,
                )
            )
        else:
            row.enabled = item.enabled
    await db.commit()
    return list(
        await db.scalars(
            select(NotificationSubscription).where(
                NotificationSubscription.user_id == user.id
            )
        )
    )
