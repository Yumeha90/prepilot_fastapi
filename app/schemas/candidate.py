"""候选人 / 应聘记录 / 简历解析出入参（PRD 3.3）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------- 简历上传与解析 ----------


class ResumeExtractOut(BaseModel):
    filename: str
    text: str


class ResumeParseIn(BaseModel):
    raw_text: str = Field(default="")


class ResumeParseOut(BaseModel):
    """解析结果不落库：前端填充左原文 / 右结构化，人工纠错后点确认才写入（BR-04）。"""

    profile: dict[str, Any]
    # 长简历走了 L1 分块时为 >1，前端据此提示"已分段解析，请重点核对"
    chunks: int = 1
    notice: str = ""


# ---------- 应聘记录 ----------


class ApplicationOut(BaseModel):
    id: int
    candidate_id: int
    position_id: int
    position_name: str = ""
    stage: str
    created_at: datetime


# ---------- 候选人 ----------


class CandidateCreateIn(BaseModel):
    """上传简历（D1 必须选职位，一步建 candidate + application）。"""

    position_id: int
    name: str = Field(default="", max_length=100)
    # 用 str 而非 EmailStr：后者要额外装 email-validator，
    # 格式校验统一由服务层 `_norm_email` 的正则承担（同一处报错文案）
    contact_email: str = Field(max_length=200)
    contact_phone: str = Field(default="", max_length=50)
    # upload 文件上传 / paste 手动粘贴
    source: str = Field(default="upload", pattern="^(upload|paste)$")
    raw_text: str = Field(default="")
    file_name: str = Field(default="", max_length=255)
    # BR-09：未勾选《数据处理授权》拦截上传
    auth_tick: bool = False


class CandidateConfirmIn(BaseModel):
    """确认解析结果（BR-04 / BR-11）。profile 为前端纠错后的最终档案。"""

    profile: dict[str, Any] = Field(default_factory=dict)


class CandidateOut(BaseModel):
    id: int
    name: str
    contact_email: str
    contact_phone: str
    source: str
    resume_raw_text: str
    resume_file_name: str
    parsed_profile: dict[str, Any] | None = None
    profile_status: str
    auth_tick: bool
    auth_at: datetime | None = None
    confirmed_at: datetime | None = None
    purged_at: datetime | None = None
    created_by: int | None = None
    created_by_name: str = ""
    created_at: datetime
    updated_at: datetime
    applications: list[ApplicationOut] = Field(default_factory=list)


class CandidateListItem(BaseModel):
    id: int
    name: str
    contact_email: str
    profile_status: str
    # 本期一人一职位（D3），列表直接带上唯一的职位与阶段
    position_id: int | None = None
    position_name: str = ""
    stage: str = ""
    created_by_name: str = ""
    created_at: datetime
    updated_at: datetime


class CandidatePaged(BaseModel):
    items: list[CandidateListItem]
    total: int
    page: int
    page_size: int


class MessageOut(BaseModel):
    message: str
