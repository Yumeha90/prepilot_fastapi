"""面试会话出入参（PRD 3.3 第二段 / 3.4）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionOut(BaseModel):
    id: int
    application_id: int
    candidate_id: int
    candidate_name: str = ""
    position_id: int
    position_name: str = ""
    round_id: int
    # 派单那一刻的轮次快照，流程后续改动不影响本会话
    round_type: str
    round_name: str = ""
    interviewer_id: int
    interviewer_name: str = ""
    status: str
    submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
