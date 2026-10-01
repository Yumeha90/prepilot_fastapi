"""AI 备面工作台（PRD 3.4 / §5.9 P09）出入参。

**矩阵为什么整块进出而不是逐行接口**：P09 的表格是「整体替换」的编辑模型 ——
面试官改完一堆行再点一次保存，逐行接口会把一次编辑拆成 N 个请求，
中途失败就是半截状态。整块提交 + `revision` 校验就够了：
revision 不匹配说明本地落后于服务端（比如另一个标签页刚生成过），前端提示刷新。

`revision` **不做乐观锁强制拦截**（返回 409 让面试官白改一次是灾难），
只做提示：以服务端最新为准覆盖，前端 toast 说明。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.session import (
    EVIDENCE_MISSING,
    ROW_SOURCE_JD,
)


class MatrixRow(BaseModel):
    """矩阵一行 = 一个能力要求项。

    - `source` 决定这一行是哪来的：JD 核心能力 / AI 从简历补的 / 面试官手加的。
      页面上三者的可操作性不同（JD 行不能删只能改考察重点，手加行可删），
      存下来才知道该怎么渲染。
    - `weight` 只来自 JD（展示用，让面试官知道这项占多少分）；
      简历补的行没有权重，前端留空而不是显示 0 —— 显示 0 会被误读成「这项不计分」。
    """

    id: str = ""
    source: str = ROW_SOURCE_JD
    capability: str = ""
    weight: float | None = None
    evidence: str = ""
    status: str = EVIDENCE_MISSING
    focus: str = ""
    # 「本轮重点」唯一入口在 P09（PRD §3.4.1：JD 编辑页不提供该勾选）
    is_key: bool = False


class MatrixOut(BaseModel):
    rows: list[MatrixRow] = Field(default_factory=list)
    generated: bool = False
    revision: int = 0
    updated_at: datetime | None = None
    generated_at: str = ""
    model: str = ""


class MatrixIn(BaseModel):
    rows: list[MatrixRow] = Field(default_factory=list)
    # 前端带上自己拿到的 revision；与服务端不一致时返回 `stale=True` 提示刷新
    revision: int | None = None


class MatrixSaveOut(BaseModel):
    matrix: MatrixOut
    # 本地 revision 落后于服务端（别处刚改过）：已按最新覆盖，前端提示一下
    stale: bool = False


class DurationIn(BaseModel):
    # 上下限在 service 里判（返回带 code 的 400），不放 pydantic 约束 ——
    # 校验失败会变成 422 + detail 数组，前端拿不到 code，只能弹一句含糊的"请求失败"
    duration_minutes: int = 45


class WorkbenchStep(BaseModel):
    """顶部步骤指示器。state: done / current / todo / disabled"""

    key: str
    state: str


class WorkbenchOut(BaseModel):
    session_id: int
    application_id: int
    candidate_id: int
    candidate_name: str = ""
    position_id: int
    position_name: str = ""
    round_type: str = ""
    round_name: str = ""
    interviewer_id: int
    interviewer_name: str = ""
    status: str = ""
    duration_minutes: int = 45

    # 只有被指派的面试官能编辑；HR / HR 主管一律只读（2026-10-01 拍板）。
    # 会话已提交时连面试官本人也只读。
    can_edit: bool = False
    # 只读的原因，前端据此给不同提示（read_only / submitted）
    read_only_reason: str = ""

    matrix: MatrixOut = Field(default_factory=MatrixOut)
    steps: list[WorkbenchStep] = Field(default_factory=list)
    # 前置没满足时页面阻断：jd_not_confirmed / resume_missing
    blocked_reason: str = ""
