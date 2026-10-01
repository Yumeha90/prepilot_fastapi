"""候选人看板（PRD 3.3.3 / §5.8 P08）。

看板 = **阶段列 + 卡片**，卡片围绕「应聘记录（application）」而不是候选人：
本期一人一职位（D3），所以一张卡片就是一条 application；后续放开多职位时
同一候选人会在不同职位下各有一张卡，模型不用改。

两条硬口径：
- **BR-15 列可见性**：HR / HR 主管 / 超管 8 列；面试官只 r1 / r2。
  列是**后端**按权限下发的，前端不自己算 —— 否则面试官改个 JS 就能看到全列。
- **D9 不做拖拽**：阶段变更一律走「推进 / 退回 / 终结处置」按钮，
  与权重拉杆的水平拖动手势冲突，且拖拽容易误操作。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BoardCard(BaseModel):
    """看板卡片。字段按 §5.8：姓名 / 岗位 / 状态徽章 / 面试官 / 当轮进展。"""

    application_id: int
    candidate_id: int
    candidate_name: str = ""
    position_id: int
    position_name: str = ""
    stage: str
    # 当前轮次名与面试官（来自派单快照，未派单为空）
    current_round_name: str = ""
    interviewer_name: str = ""
    # 当轮进展：会话状态 s1_draft…s5_draft / submitted；无会话（pending、offer 列）为空
    session_status: str = ""
    # 简历侧状态：uploading / parsed / confirmed / archived（卡片上提示"待确认"）
    profile_status: str = ""
    # 最新人岗匹配分（BR-21 只显示分，解释在 P18）：无分 / 未参与计算时为 None
    match_score: float | None = None
    match_tier: str = ""
    # CURRENT 有效 / STALE 岗位标准已变更，徽章置灰提示需重算（§3.3.3）
    match_status: str = ""
    updated_at: datetime


class BoardColumn(BaseModel):
    stage: str
    cards: list[BoardCard] = Field(default_factory=list)


class BoardOut(BaseModel):
    """列顺序即前端渲染顺序；`summary` 是不成列的计数（如已录用）。"""

    columns: list[BoardColumn] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    # 当前用户能操作的动作（前端据此决定要不要渲染按钮）
    actions: list[str] = Field(default_factory=list)


class TransitionIn(BaseModel):
    """阶段流转。

    advance  推进到流程配置里的**下一轮**（pending → 首轮）
    rollback 退回上一轮
    accept / reject / pool / archive  终结处置（录用 / 淘汰 / 人才库 / 作废）
    """

    action: str = Field(pattern="^(advance|rollback|accept|reject|pool|archive)$")


class TransitionOut(BaseModel):
    application_id: int
    candidate_id: int
    candidate_name: str = ""
    stage: str
    # 推进需要派单而流程没配好时，处置本身已生效，这里回原因给前端提示
    notice: str = ""
