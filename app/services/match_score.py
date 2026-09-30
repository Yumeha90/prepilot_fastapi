"""匹配分的 STALE 埋点（PRD §6.7 / §8.2，3.2 第三段）。

口径（PRD v1.6 定）：
- JD 结构化**实质变更**并确认后，该职位下所有 `CURRENT` 分数置 `STALE`；
- **不自动重算**，由 HR 在 P08 看板 / P18 详情手动触发；
- 重算不删历史：旧行留 STALE，新行插 CURRENT。

本期候选人与应聘记录表还没建（属 3.3），因此这里只做「按职位批量置位 + 计数」，
不会去查候选人的在流程状态 —— 3.3 接入后只需把 `count_current_scores` 的口径
换成「在流程且有当前分」，接口与调用点都不用动。
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_score import CURRENT, STALE, MatchScore


async def count_current_scores(db: AsyncSession, position_id: int) -> int:
    """该职位当前有效的匹配分条数 = JD 变更后会被置 STALE 的数量。"""
    res = await db.execute(
        select(func.count())
        .select_from(MatchScore)
        .where(
            MatchScore.position_id == position_id,
            MatchScore.status == CURRENT,
        )
    )
    return int(res.scalar_one())


async def mark_scores_stale(db: AsyncSession, position_id: int) -> int:
    """把该职位所有 CURRENT 分数置为 STALE，返回被置位的条数。

    只 execute 不 commit —— 由调用方（save_jd）与 JD 字段一起落库，
    避免「JD 版本 bump 了但分数没置位」这类半截状态。
    """
    res = await db.execute(
        update(MatchScore)
        .where(
            MatchScore.position_id == position_id,
            MatchScore.status == CURRENT,
        )
        .values(status=STALE)
    )
    return int(res.rowcount or 0)
