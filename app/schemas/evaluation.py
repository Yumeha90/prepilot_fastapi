"""P17 面评详情与列表（PRD §5.15 / BR-17）出入参。

与 Step4 的 `EvaluationOut` 分开定义：那边是**编辑态**（带 revision、润色稿、
完整性提示），这边是**只读快照**——提交成事实之后不该再被编辑态字段污染，
而且 P17 要额外给出「谁写的、什么时候交的、结论是什么、合规扫出了什么」。

可见性（BR-17）：HR / HR 主管 / 超管看全部；面试官**只能看本人撰写**的面评，
他人面评一律 403（不是列表里过滤掉——直接访问 URL 也要挡住）。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.workbench import EvaluationFlag, EvaluationItem


class EvaluationSummary(BaseModel):
    """列表一行：一眼能认出「谁、哪一轮、什么结论」。"""

    session_id: int = 0
    candidate_id: int = 0
    candidate_name: str = ""
    position_id: int = 0
    position_name: str = ""
    round_type: str = ""
    round_name: str = ""
    interviewer_id: int = 0
    interviewer_name: str = ""
    # 结论（pass / pending / fail）+ 中文文案，前端直接展示
    conclusion: str = ""
    submitted_at: datetime | None = None
    # 90 天保留策略清过正文：行还在，内容没了（P17 要显式说明，不能显示成一片空白）
    content_purged: bool = False


class FairnessSnapshot(BaseModel):
    """提交那一刻的公平性结论（P17 要展示「题目扫过没有、结果是什么」）。"""

    result: str = ""
    scanned_at: str = ""
    findings: list[dict] = Field(default_factory=list)


class EvaluationDetail(BaseModel):
    """P17 详情：候选人 + 岗位 + 轮次 + 提交时间 + 评分表 + 面评正文 + 合规结论。"""

    session_id: int = 0
    candidate_id: int = 0
    candidate_name: str = ""
    position_id: int = 0
    position_name: str = ""
    round_type: str = ""
    round_name: str = ""
    interviewer_id: int = 0
    interviewer_name: str = ""
    conclusion: str = ""
    submitted_at: datetime | None = None
    duration_minutes: int = 0

    items: list[EvaluationItem] = Field(default_factory=list)
    summary: str = ""
    # 采纳润色稿前的原文：P17 做双栏对比（BR-08）
    summary_original: str = ""
    polished: str = ""
    polished_adopted: bool = False
    polished_flags: list[EvaluationFlag] = Field(default_factory=list)
    recommendation: str = ""

    fairness: FairnessSnapshot = Field(default_factory=FairnessSnapshot)
    content_purged: bool = False
    # 只读视图：面试官看本人面评时置 true，前端据此隐藏一切编辑入口
    read_only: bool = True
