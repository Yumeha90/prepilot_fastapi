"""派单与会话（PRD 3.3 第二段 / 3.4 工作台载体）。

口径：
- **D5 确认即派单**：简历确认后按「流程配置」派到首轮面试官，并建首轮会话。
- **首轮 = seq 最小的轮次**（`NON_INTERVIEW_ROUNDS` 里的非面试节点不当首轮；
  v1.26 删掉 offer 轮后该集合为空，判断保留是为了将来加非面试节点时不用改调用方）。
- **轮次信息做快照**：流程配置变更只对未来的应聘记录生效，已派单的会话不跟随。
- 派单失败（没配轮次 / 轮次没面试官）**不回滚确认**：确认是已经完成的业务动作，
  回滚它是错的。调用方拿到失败原因后自行提示，候选人停在 `pending` 等补齐配置。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, bad_request, forbidden, not_found
from app.models.candidate import PENDING, Application, Candidate
from app.models.position import Position, PositionRound
from app.models.session import NON_INTERVIEW_ROUNDS, ROUND_STAGE, S1_DRAFT, InterviewSession
from app.models.user import User
from app.services.rbac import user_permissions

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now()


def pick_first_round(rounds: list[PositionRound]) -> PositionRound:
    """首轮 = seq 最小且不是非面试轮次的轮次（见 `NON_INTERVIEW_ROUNDS`）。"""
    candidates = [r for r in rounds if r.type not in NON_INTERVIEW_ROUNDS]
    if not candidates:
        raise bad_request(
            ErrorCode.SESSION_NO_ROUND,
            "该职位还没有配置面试轮次，无法派单（请在职位里先配好面试流程）",
        )
    return min(candidates, key=lambda r: r.seq)


async def dispatch_to_round(
    db: AsyncSession,
    application: Application,
    round: PositionRound,
    *,
    notify: bool = True,
) -> InterviewSession:
    """按指定轮次建会话并推进阶段。返回新建的会话。

    本函数只负责「派一次」，幂等性由调用方保证。
    """
    if round.interviewer_id is None:
        raise bad_request(
            ErrorCode.SESSION_NO_INTERVIEWER,
            f"轮次「{round.name or round.type}」还没有指派面试官，无法派单",
        )

    stage = ROUND_STAGE.get(round.type)
    if stage is None:
        raise bad_request(ErrorCode.SESSION_NO_ROUND, f"未知轮次类型：{round.type}")

    session = InterviewSession(
        application_id=application.id,
        candidate_id=application.candidate_id,
        position_id=application.position_id,
        round_id=round.id,
        round_type=round.type,
        round_name=round.name or "",
        interviewer_id=round.interviewer_id,
        status=S1_DRAFT,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(session)

    application.stage = stage
    application.current_round_id = round.id
    application.updated_at = _now()

    await db.flush()

    if notify:
        await _notify_assignment(db, session, application)

    return session


async def _notify_assignment(
    db: AsyncSession, session: InterviewSession, application: Application
) -> None:
    """给面试官发一条「面试指派」站内信；没订阅该事件的人不会被打扰。"""
    from app.services import notification as notify_svc

    candidate = await db.get(Candidate, application.candidate_id)
    position = await db.get(Position, application.position_id)
    name = candidate.name if candidate else "候选人"
    pos_name = position.name if position else ""
    round_label = session.round_name or session.round_type

    await notify_svc.notify_subscribers(
        db,
        event_type="assignment",
        title="新的面试指派",
        body=f"「{pos_name}」的候选人 {name} 已进入 {round_label}，请进入工作台备面",
        payload={"candidateId": application.candidate_id, "sessionId": session.id},
        user_ids=[session.interviewer_id],
    )


async def dispatch_first_round(
    db: AsyncSession, application: Application, *, notify: bool = True
) -> InterviewSession:
    """把某条应聘记录派到首轮（D5）。"""
    if application.stage != PENDING:
        raise bad_request(
            ErrorCode.SESSION_ALREADY_DISPATCHED, "该候选人已派单，无需重复操作"
        )
    position = await db.get(Position, application.position_id)
    if position is None:
        raise not_found("职位不存在")
    first = pick_first_round(list(position.rounds or []))
    return await dispatch_to_round(db, application, first, notify=notify)


# ---------------------------------------------------------------- 可见性


async def visible_session_ids(
    db: AsyncSession, user: User, *, current_round_only: bool = True
) -> list[int] | None:
    """返回该用户可见的会话 id；None 表示不限制。

    用**权限码**而不是数据范围来判：`workbench:enter_all`（HR 主管）/ `enter_view`（HR，只读）
    能看全部；`workbench:enter_assigned`（面试官）只看派给自己的（BR-16）。
    数据范围表里没有 workbench 这个 resource，硬加一项还得重新播种云端库，不划算。

    **`current_round_only`（默认开）**：一条应聘记录每轮一条会话，HR 推进/退回都可能
    提前建出下一轮的会话。只按 `interviewer_id` 过滤，二面面试官会在 HR 还没推进时
    就看到候选人的二面记录（一面还没结束，他点进去是空的），流程顺序被打乱。
    所以「我的备面」列表再卡一道 `round_id == 应聘记录当前轮次`。

    **为什么查看（ensure_can_view_session）要传 False**：面试官提交完一轮后，HR 一推进
    他就得能回看自己写过的那份面评（P17）。列表里不出现是「没轮到你别来备面」，
    点进去还能看是「你写的东西你得看得见」——两件事，不是一个口径。
    此时 `can_edit` 另按当前轮次判 false，进去是只读，写不了。
    """
    perms = user_permissions(user)
    if "workbench:enter_all" in perms or "workbench:enter_view" in perms:
        return None
    stmt = select(InterviewSession.id).where(InterviewSession.interviewer_id == user.id)
    if current_round_only:
        stmt = (
            stmt.outerjoin(Application, Application.id == InterviewSession.application_id).where(
                or_(
                    Application.id.is_(None),  # 没有应聘记录的会话（历史数据）按老口径放行
                    Application.current_round_id.is_(None),
                    Application.current_round_id == InterviewSession.round_id,
                )
            )
        )
    rows = await db.scalars(stmt)
    return list(set(rows))


async def ensure_can_view_session(
    db: AsyncSession, user: User, session: InterviewSession
) -> None:
    # 只要派给过自己就允许查看（历史轮次留给 P17 回看），能不能写在 can_edit 里判
    allowed = await visible_session_ids(db, user, current_round_only=False)
    if allowed is not None and session.id not in allowed:
        raise forbidden("无权查看该会话")
