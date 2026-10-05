"""P17 面评只读视图（PRD §5.15 / BR-17）。

**为什么单开一个服务而不是复用 workbench 那一屏**：工作台是**编辑态**（未提交、
能改、带轮询），P17 是**已成事实的只读快照**，两者的权限与字段都不一样。

**可见性（BR-17）**：
- `evaluation:view_all`（超管 / HR 主管 / HR）：全部已提交面评
- `evaluation:view_own`（面试官）：**仅本人撰写**的面评
- 两者都没有：直接 403，连列表都不给

面试官看他人面评 → 403（不是列表里过滤掉就算了，直接访问 URL 也要挡住，
PRD §10.1 验收明确写了这条）。

**只列已提交的**：没提交的是草稿，不是面评。面试官自己的草稿在 Step4 里看，
HR 也不该在 P17 里看到一份写着一半的评价。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, not_found
from app.models.candidate import Candidate
from app.models.position import Position
from app.models.session import SUBMITTED, InterviewSession
from app.models.user import User
from app.schemas.evaluation import EvaluationDetail, EvaluationSummary, FairnessSnapshot
from app.schemas.workbench import EvaluationFlag, EvaluationItem, EvidenceItem
from app.services.evaluation import evaluation_out
from app.services.rbac import user_permissions

logger = logging.getLogger(__name__)

VIEW_ALL = "evaluation:view_all"
VIEW_OWN = "evaluation:view_own"


def _can_view_all(user: User) -> bool:
    return VIEW_ALL in user_permissions(user)


def _can_view_own(user: User) -> bool:
    return VIEW_OWN in user_permissions(user)


def _visible_sessions_query(user: User):
    """可见的已提交会话查询。两个权限都没有时返回 None（调用方直接 403）。"""
    base = (
        select(InterviewSession)
        .where(InterviewSession.status == SUBMITTED)
        .order_by(InterviewSession.submitted_at.desc())
    )
    if _can_view_all(user):
        return base
    if _can_view_own(user):
        return base.where(InterviewSession.interviewer_id == user.id)
    return None


async def list_evaluations(
    db: AsyncSession,
    user: User,
    *,
    position_id: int | None = None,
    keyword: str | None = None,
    limit: int = 50,
) -> list[EvaluationSummary]:
    """面评列表（BR-17 过滤后的）。"""
    stmt = _visible_sessions_query(user)
    if stmt is None:
        raise forbidden("无权查看面评")
    if position_id:
        stmt = stmt.where(InterviewSession.position_id == position_id)
    rows = (await db.scalars(stmt.limit(max(1, min(limit, 200))))).all()
    # 职位名一次取齐，避免在循环里逐条查（N+1）
    position_ids = {s.position_id for s in rows}
    positions: dict[int, str] = {}
    if position_ids:
        for pid, pname in (
            await db.execute(
                select(Position.id, Position.name).where(Position.id.in_(position_ids))
            )
        ).all():
            positions[pid] = pname

    out: list[EvaluationSummary] = []
    for s in rows:
        name = s.candidate.name if s.candidate else ""
        if keyword and keyword.strip() and keyword.strip() not in name:
            continue
        out.append(_summary(s, name, positions.get(s.position_id, "")))
    return out


def _summary(
    s: InterviewSession, candidate_name: str, position_name: str
) -> EvaluationSummary:
    data = s.evaluation_json or {}
    return EvaluationSummary(
        session_id=s.id,
        candidate_id=s.candidate_id,
        candidate_name=candidate_name,
        position_id=s.position_id,
        position_name=position_name,
        round_type=s.round_type,
        round_name=s.round_name,
        interviewer_id=s.interviewer_id,
        interviewer_name=s.interviewer.full_name if s.interviewer else "",
        conclusion=str(s.conclusion or ""),
        submitted_at=s.submitted_at,
        content_purged=bool(data.get("content_purged")),
    )


async def evaluation_detail(
    db: AsyncSession, user: User, session_id: int
) -> EvaluationDetail:
    """P17 详情。他人面评一律 403（BR-17）。"""
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise not_found("面评不存在")
    if not _can_view_all(user):
        if not _can_view_own(user) or session.interviewer_id != user.id:
            raise forbidden("仅可查看本人提交的面评")
        if session.status != SUBMITTED:
            raise forbidden("该面评尚未提交，暂无可查看的内容")

    position = await db.get(Position, session.position_id)
    view = evaluation_out(session)
    data = session.evaluation_json or {}

    fairness_raw: dict[str, Any] = session.fairness_json or {}
    fairness = FairnessSnapshot(
        result=str(fairness_raw.get("result") or ""),
        scanned_at=str(fairness_raw.get("scanned_at") or ""),
        findings=[f for f in (fairness_raw.get("findings") or []) if isinstance(f, dict)],
    )

    # 已按保留策略粉碎：正文与证据都清了，只留评分与能力项名（BR-10 / v1.19）
    purged = bool(data.get("content_purged"))
    items: list[EvaluationItem] = []
    for it in view.items:
        items.append(
            EvaluationItem(
                row_id=it.row_id,
                capability=it.capability,
                score=it.score,
                evidences=[] if purged else it.evidences,
                note="" if purged else it.note,
                complete=it.complete,
                missing=it.missing,
            )
        )

    summary = "" if purged else view.summary
    summary_original = "" if purged else view.summary_original
    polished = "" if purged else view.polished

    return EvaluationDetail(
        session_id=session.id,
        candidate_id=session.candidate_id,
        candidate_name=session.candidate.name if session.candidate else "",
        position_id=session.position_id,
        position_name=position.name if position else "",
        round_type=session.round_type,
        round_name=session.round_name,
        interviewer_id=session.interviewer_id,
        interviewer_name=session.interviewer.full_name if session.interviewer else "",
        conclusion=str(session.conclusion or ""),
        submitted_at=session.submitted_at,
        duration_minutes=session.duration_minutes,
        items=items,
        summary=summary,
        summary_original=summary_original,
        polished=polished,
        polished_adopted=view.polished_adopted,
        polished_flags=[] if purged else view.polished_flags,
        recommendation=str(data.get("recommendation") or ""),
        fairness=fairness,
        content_purged=purged,
        read_only=True,
    )


__all__ = [
    "EvaluationDetail",
    "EvaluationFlag",
    "EvaluationItem",
    "EvaluationSummary",
    "EvidenceItem",
    "evaluation_detail",
    "list_evaluations",
]
