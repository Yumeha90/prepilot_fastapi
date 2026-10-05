"""职位相关出入参（PRD 3.2）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------- 轮次 ----------

ROUND_TYPES = ("r1", "r2", "hr")
MAX_ROUNDS = 4


class RoundIn(BaseModel):
    """一轮面试。seq 由数组顺序决定，前端 ▲/▼ 上下移 = 调整数组顺序后整体提交。"""

    name: str = Field(default="", max_length=100)
    type: str = Field(pattern="^(r1|r2|hr)$")
    interviewer_id: int | None = None


class RoundOut(BaseModel):
    id: int
    seq: int
    name: str
    type: str
    interviewer_id: int | None
    interviewer_name: str = ""


class RoundsSaveIn(BaseModel):
    rounds: list[RoundIn] = Field(default_factory=list)


# ---------- JD ----------


class CompetencyIn(BaseModel):
    """核心能力项。id 由服务端生成稳定 slug，前端新增项可不传。"""

    id: str | None = None
    text: str = Field(min_length=1, max_length=500)
    weight: int = Field(default=0, ge=0, le=100)


class JdIn(BaseModel):
    """JD 保存。confirm=False 只写草稿区；confirm=True 校验并生效（BR-01）。"""

    raw_text: str = Field(default="")
    hard_gates: list[str] = Field(default_factory=list)
    competencies: list[CompetencyIn] = Field(default_factory=list)
    bonuses: list[str] = Field(default_factory=list)
    confirm: bool = False


class JdOut(BaseModel):
    raw_text: str
    hard_gates: list[str]
    competencies: list[dict[str, Any]]
    bonuses: list[str]
    status: str
    version: int
    # 未确认的草稿内容（AI 拆解结果或 HR 编辑中）
    draft: dict[str, Any] | None = None


# ---------- 职位 ----------


class PositionCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_id: int | None = None


class PositionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    owner_id: int | None = None
    status: str | None = Field(default=None, pattern="^(draft|open|paused|closed)$")


class PositionOut(BaseModel):
    id: int
    name: str
    status: str
    owner_id: int | None
    owner_name: str = ""
    jd_version: int
    jd_status: str
    jd_completion: int = 0
    rounds: list[RoundOut] = Field(default_factory=list)
    copied_from_id: int | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    jd: JdOut | None = None


class PositionListItem(BaseModel):
    id: int
    name: str
    status: str
    owner_id: int | None
    owner_name: str = ""
    jd_version: int
    jd_status: str
    jd_completion: int = 0
    # 本期无候选人模块，恒为 0；3.3 接入后改为真实计数
    candidate_count: int = 0
    round_count: int = 0
    created_at: datetime
    updated_at: datetime


class Paged(BaseModel):
    items: list[PositionListItem]
    total: int
    page: int
    page_size: int


class SavePreviewIn(BaseModel):
    """保存前的预检：把待保存的 JD 与轮次原样回传，由服务端判定影响。"""

    jd: JdIn | None = None
    rounds: list[RoundIn] = Field(default_factory=list)


class SavePreviewOut(BaseModel):
    """预检结果。前端据此决定是否弹二次确认。

    - `jd_changed`：结构化摘要与库内不同 → 会 bump 版本，存量匹配分置为待重算
    - `rounds_changed`：轮次顺序或人选有变 → 只对未来应聘记录生效
    - 错误信息为空表示校验通过；非空时前端禁止保存并原样展示原因
    """

    jd_changed: bool = False
    rounds_changed: bool = False
    affected_candidates: int = 0
    jd_error: str = ""
    rounds_error: str = ""


class JdVersionOut(BaseModel):
    """JD 版本列表项（编辑页「版本历史」抽屉 + P18 回溯入口）。"""

    version: int
    change_summary: str = ""
    changed_by: int | None = None
    changed_by_name: str = ""
    created_at: datetime


class JdVersionDetailOut(JdVersionOut):
    """某一版 JD 的完整快照（只读，用于「当年按哪版标准算的分」）。"""

    position_id: int
    hard_gates: list[str] = Field(default_factory=list)
    competencies: list[dict[str, Any]] = Field(default_factory=list)
    bonuses: list[str] = Field(default_factory=list)


class JdParseIn(BaseModel):
    """AI 拆解入参：原文来自前端输入框（可能是粘贴的，也可能是上传抽取回填的）。"""

    raw_text: str = Field(min_length=1)


class JdParseOut(BaseModel):
    """AI 拆解结果。**不落库**，前端填充表单后由 HR 确认保存（BR-01）。"""

    hard_gates: list[str]
    competencies: list[dict[str, Any]]
    bonuses: list[str]
    # 需要 HR 留意的提示（例如核心能力不足 3 项），为空表示无需提示
    notice: str = ""


class JdExtractOut(BaseModel):
    """文件上传抽取结果：只回纯文本，文件本身不留存。"""

    filename: str
    text: str


class MessageOut(BaseModel):
    message: str
    id: int | None = None
