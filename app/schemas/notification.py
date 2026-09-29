"""通知与订阅 Schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    body: str
    payload_json: str = "{}"
    is_read: bool
    created_at: datetime


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    enabled: bool


class SubscriptionUpdate(BaseModel):
    """批量更新订阅开关。"""

    items: list[SubscriptionRead]
