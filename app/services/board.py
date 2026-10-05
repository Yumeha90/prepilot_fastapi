"""看板数据与阶段流转（PRD 3.3.3 P08）。

**为什么卡片是 application 不是 candidate**：本期一人一职位（D3），
但看板语义是「这条应聘记录现在在哪个阶段」，写死了 candidate 以后放开多职位就得重构。

**流转口径**：
- 推进（advance）按**流程配置**走，不让人手选下一轮 —— 面试官不指定下一轮（BR-06），
  HR 也只是"往下推一步"，下一轮是谁由职位配置决定。
- 推进需要派单（建会话 / 通知负责人），派单失败则**推进整体失败**（stage 不变）；
  这与 D5「确认即派单，派单失败不回滚确认」不冲突：确认是已完成的业务动作，
  而"推进到下一轮"这件事本身就包含"派出去"，没派成就不算推进。
- **终结处置（录用 / 淘汰 / 人才库 / 作废）只是改 stage，不需要派单，无条件生效。
  录用 / 淘汰等一律由 HR 在流程任意阶段直接处置**（v1.26：不再有 offer 轮这一中间态 ——
  它不建会话也不进工作台，而且 HR 本来就能直接录用，这一步必然被绕过）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ErrorCode, bad_request, forbidden, not_found
from app.models.candidate import CONFIRMED, PENDING, Application, Candidate
from app.models.match_score import CURRENT, MatchScore
from app.models.position import Position, PositionRound
from app.models.session import (
    NON_INTERVIEW_ROUNDS,
    ROUND_STAGE,
    SUBMITTED,
    InterviewSession,
)
from app.models.user import User
from app.observability import metrics, tracing
from app.schemas.board import BoardCard, BoardColumn, BoardOut
from app.services import candidate as candidate_svc
from app.services import dispatch as dispatch_svc
from app.services.rbac import user_permissions as _user_permissions

logger = logging.getLogger(__name__)

# 在流程的阶段（按推进顺序）。v1.26：删掉 in_offer（offer 轮已删除）
FLOW_STAGES = ("pending", "in_r1", "in_r2", "in_hr")
# 终结处置
TERMINAL_ACTION_STAGE = {
    "accept": "accepted",
    "reject": "rejected",
    "pool": "in_pool",
    "archive": "archived",
}
TERMINAL_STAGES = tuple(TERMINAL_ACTION_STAGE.values())

# BR-15：HR 8 列（v1.26 删掉 in_offer；v1.24 起 accepted 成列 —— 录用后人在看板上
# 彻底消失，HR 找不到人、查不了简历与面评、误点也没法撤回，而淘汰/人才库/作废都有列，
# 唯独录用没有，口径不一致。录用只是离开招聘流程，不等于从系统里消失）
HR_COLUMNS = (
    "pending",
    "in_r1",
    "in_r2",
    "in_hr",
    "accepted",
    "rejected",
    "in_pool",
    "archived",
)
INTERVIEWER_COLUMNS = ("in_r1", "in_r2")


def _now() -> datetime:
    return datetime.now()


def visible_columns(user: User) -> tuple[str, ...]:
    """列可见性由后端判定（BR-15）：有 `candidate:view_all` 才是 HR 视角。"""
    perms = _user_permissions(user)
    if "candidate:view_all" in perms:
        return HR_COLUMNS
    return INTERVIEWER_COLUMNS


def available_actions(user: User) -> list[str]:
    perms = _user_permissions(user)
    if "candidate:dispose" in perms:
        return ["advance", "rollback", "accept", "reject", "pool", "archive", "reopen"]
    return []


# ---------------------------------------------------------------- 看板数据


async def board_data(
    db: AsyncSession,
    user: User,
    *,
    position_id: int | None = None,
    keyword: str = "",
) -> BoardOut:
    columns = visible_columns(user)
    allowed = await candidate_svc.visible_candidate_ids(
        db, user, await _scopes(db, user)
    )
    if allowed is not None and not allowed:
        return BoardOut(columns=[BoardColumn(stage=s) for s in columns],
                        summary={}, actions=available_actions(user))

    stmt = (
        select(Application)
        .options(selectinload(Application.candidate))
        .join(Candidate, Candidate.id == Application.candidate_id)
        # BR-10 / §7.7：简历已粉碎的候选人不再出现在看板上（行还在，只是没内容可推进）
        .where(Candidate.purged_at.is_(None))
    )
    if allowed is not None:
        stmt = stmt.where(Application.candidate_id.in_(allowed))
    if position_id is not None:
        stmt = stmt.where(Application.position_id == position_id)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Candidate.name.ilike(like) | Candidate.contact_email.ilike(like))

    apps = list(await db.scalars(stmt.order_by(Application.id.desc())))
    if not apps:
        return BoardOut(columns=[BoardColumn(stage=s) for s in columns],
                        summary={}, actions=available_actions(user))

    # 一次性取全，避免 N+1：职位名 / 用户名 / 最新会话
    position_ids = {a.position_id for a in apps}
    positions = {
        p.id: p
        for p in await db.scalars(select(Position).where(Position.id.in_(position_ids)))
    }
    round_ids = {a.current_round_id for a in apps if a.current_round_id}
    rounds = {
        r.id: r
        for r in await db.scalars(
            select(PositionRound).where(PositionRound.id.in_(round_ids))
        )
    } if round_ids else {}
    sessions = await db.scalars(
        select(InterviewSession).where(
            InterviewSession.application_id.in_([a.id for a in apps])
        )
    )
    # 按应聘记录分组。用列表而不是「只留 id 最大的那条」：卡片要显示的是
    # **当前所在轮次**的会话，退回再推进、或提前建过下一轮会话时，id 最大的那条
    # 可能是别的轮次（见 _current_session）
    by_app: dict[int, list[InterviewSession]] = {}
    for s in sessions:
        by_app.setdefault(s.application_id, []).append(s)
    user_ids = {s.interviewer_id for s in sessions}
    user_ids |= {r.interviewer_id for r in rounds.values() if r.interviewer_id}
    names = await _names(db, user_ids)

    # 匹配分：一次捞全，避免每张卡片一条 SQL
    scores = await _current_scores(db, [a.id for a in apps])

    cards: dict[str, list[BoardCard]] = {s: [] for s in columns}
    # 面试官视角只给 r1 / r2 两列，若照样统计其它阶段，等于变相把
    # 「库里一共淘汰了几个、录用了几个」透给他 —— BR-15 只放行他自己的两列
    count_summary = columns != INTERVIEWER_COLUMNS
    summary: dict[str, int] = {}
    for a in apps:
        if a.stage not in cards:
            # 不成列的阶段（如超管视角之外的情况）只计数，不占看板列
            if count_summary:
                summary[a.stage] = summary.get(a.stage, 0) + 1
            continue
        cand = a.candidate
        rnd = rounds.get(a.current_round_id) if a.current_round_id else None
        sess = _current_session(by_app.get(a.id) or [], a.current_round_id)
        interviewer = (
            names.get(sess.interviewer_id, "")
            if sess
            else (names.get(rnd.interviewer_id, "") if rnd and rnd.interviewer_id else "")
        )
        cards[a.stage].append(
            BoardCard(
                application_id=a.id,
                candidate_id=a.candidate_id,
                candidate_name=cand.name if cand else "",
                position_id=a.position_id,
                position_name=positions.get(a.position_id).name
                if positions.get(a.position_id)
                else "",
                stage=a.stage,
                current_round_name=(sess.round_name if sess else (rnd.name if rnd else "")),
                interviewer_name=interviewer,
                session_status=sess.status if sess else "",
                session_id=sess.id if sess else None,
                can_prepare=_can_prepare(user, sess),
                profile_status=cand.profile_status if cand else "",
                history_evaluation_session_id=_history_evaluation(
                    user, by_app.get(a.id) or [], a.current_round_id
                ),
                match_score=float(scores[a.id].score) if a.id in scores else None,
                match_tier=scores[a.id].tier if a.id in scores else "",
                match_status=scores[a.id].status if a.id in scores else "",
                updated_at=a.updated_at,
            )
        )
    return BoardOut(
        columns=[BoardColumn(stage=s, cards=cards[s]) for s in columns],
        summary=summary,
        actions=available_actions(user),
    )


def _current_session(
    rows: list[InterviewSession], current_round_id: int | None
) -> InterviewSession | None:
    """卡片要显示「候选人当前所在轮次」的会话，不是 id 最大的那条。

    一个应聘记录可以有多条会话（每轮一条）。退回再推进、或提前建过下一轮会话时，
    id 最大的那条属于**别的轮次** —— 用它就会出这种事：候选人一面面评已提交，
    卡片却显示二面会话的「待备面」，HR 据此判断等于看错了一轮。
    找不到当前轮次的会话（比如已终结、或非面试轮）才退回最近一条。
    """
    if not rows:
        return None
    if current_round_id:
        for s in rows:
            if s.round_id == current_round_id:
                return s
    return max(rows, key=lambda s: s.id)


def _history_evaluation(
    user: User, rows: list[InterviewSession], current_round_id: int | None
) -> int | None:
    """历史轮次里最近一条「看得见」的已提交面评（BR-16 + BR-17）。

    卡片只展示**当前轮次**会话：HR 把人推进到二面后，卡片上的 session 变成二面的
    「待备面」，一面那份已提交的面评在卡片上就消失了。HR 因此问「我刚才的面评呢」。

    消失是**对的**（看板回答的是"这个人现在卡在哪"），但面评不能真的找不到 ——
    这里把历史面评的入口单独下发：
    - HR / HR 主管 / 超管（`evaluation:view_all`）：任何人的历史面评
    - 面试官（`evaluation:view_own`）：只有**本人撰写**的历史面评
      （自己面过一轮、后面轮次交给别人时，仍要能回看自己写的那份）
    """
    perms = _user_permissions(user)
    view_all = "evaluation:view_all" in perms
    view_own = "evaluation:view_own" in perms
    if not (view_all or view_own):
        return None
    past = [
        s
        for s in rows
        if s.status == SUBMITTED
        and (not current_round_id or s.round_id != current_round_id)
        and (view_all or s.interviewer_id == user.id)
    ]
    return max(past, key=lambda s: s.id).id if past else None


def _can_prepare(user: User, sess: InterviewSession | None) -> bool:
    """「开始备面」入口：被指派给本人的面试官 + 会话未提交（PRD §5.8）。

    只看 interviewer_id，不看权限码 —— 权限码决定能不能进 /workbench（路由层已拦），
    这里回答的是「这张卡片的会话是不是你的」。已提交的会话只能回看面评，不再备面。
    """
    return sess is not None and sess.interviewer_id == user.id and sess.status != SUBMITTED


async def _scopes(db: AsyncSession, user: User) -> dict[str, str]:
    from app.services.rbac import user_data_scopes

    return await user_data_scopes(db, user)


async def _current_scores(
    db: AsyncSession, application_ids: list[int]
) -> dict[int, MatchScore]:
    """每张卡片的最新有效匹配分（CURRENT 优先，没有则取最后一条 STALE 提示需重算）。"""
    if not application_ids:
        return {}
    rows = await db.scalars(
        select(MatchScore)
        .where(MatchScore.application_id.in_(application_ids))
        .order_by(MatchScore.id.desc())
    )
    out: dict[int, MatchScore] = {}
    for row in rows:
        prev = out.get(row.application_id or 0)
        if prev is None or (prev.status != CURRENT and row.status == CURRENT):
            out[row.application_id or 0] = row
    return out


async def _names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.scalars(select(User).where(User.id.in_(ids)))
    return {u.id: (u.full_name or u.email) for u in rows}


# ---------------------------------------------------------------- 流转


async def _get_application(db: AsyncSession, application_id: int) -> Application:
    app = await db.get(Application, application_id)
    if app is None:
        raise not_found("应聘记录不存在")
    return app


async def _rounds_of(db: AsyncSession, position_id: int) -> list[PositionRound]:
    rows = await db.scalars(
        select(PositionRound).where(PositionRound.position_id == position_id)
    )
    return sorted(rows, key=lambda r: r.seq)


async def _notify_round_owner(
    db: AsyncSession, application: Application, rnd: PositionRound
) -> None:
    """推进到非面试轮次时让负责人知道（v1.26 起无此类轮次，仅作兜底）。"""
    from app.services import notification as notify_svc

    candidate = await db.get(Candidate, application.candidate_id)
    position = await db.get(Position, application.position_id)
    await notify_svc.notify_subscribers(
        db,
        event_type="assignment",
        title="新的面试指派",
        body=f"「{position.name if position else ''}」的候选人 "
        f"{candidate.name if candidate else ''} 已进入 {rnd.name or rnd.type}，请及时处理",
        payload={"candidateId": application.candidate_id},
        user_ids=[rnd.interviewer_id] if rnd.interviewer_id else [],
    )


async def _dispatch_or_reuse(
    db: AsyncSession, application: Application, rnd: PositionRound
) -> str:
    """派到指定轮次：已有该轮会话就复用（退回再推进不重复建），否则新建。

    返回 notice（正常为空）。
    """
    existing = await db.scalars(
        select(InterviewSession).where(
            InterviewSession.application_id == application.id,
            InterviewSession.round_id == rnd.id,
        )
    )
    if list(existing):
        application.stage = ROUND_STAGE[rnd.type]
        application.current_round_id = rnd.id
        application.updated_at = _now()
        return ""

    if rnd.type in NON_INTERVIEW_ROUNDS:
        # 非面试节点（v1.26 起已无非面试轮次，留着以防将来再加）
        application.stage = ROUND_STAGE[rnd.type]
        application.current_round_id = rnd.id
        application.updated_at = _now()
        await db.flush()
        await _notify_round_owner(db, application, rnd)
        return ""

    await dispatch_svc.dispatch_to_round(db, application, rnd)
    return ""


async def transition(
    db: AsyncSession, user: User, application_id: int, action: str
) -> tuple[Application, str]:
    """阶段流转。返回 `(application, notice)`。"""
    perms = _user_permissions(user)
    if "candidate:dispose" not in perms:
        metrics.record_op(f"board.{action}", "forbidden")
        raise forbidden("无候选人处置权限")

    application = await _get_application(db, application_id)
    candidate = await db.get(Candidate, application.candidate_id)
    if candidate is None:
        raise not_found("候选人不存在")
    await candidate_svc.ensure_can_view(db, user, candidate)

    if action in TERMINAL_ACTION_STAGE:
        return await _settle(db, application, action)
    if action == "reopen":
        return await _reopen(db, application)

    if action == "advance":
        return await _advance(db, application)
    return await _rollback(db, application)


async def _settle(
    db: AsyncSession, application: Application, action: str
) -> tuple[Application, str]:
    application.stage = TERMINAL_ACTION_STAGE[action]
    application.closed_at = _now()
    application.updated_at = _now()
    await db.commit()
    await db.refresh(application)
    # 终结处置是业务漏斗的出口，Grafana 上要看「录用了几个 / 淘汰了几个」就靠它
    metrics.record_op(f"board.{action}", "ok")
    tracing.add_metadata(application_id=application.id, action=action)
    return application, ""


async def _reopen(db: AsyncSession, application: Application) -> tuple[Application, str]:
    """撤销终结处置：回到终结前所在的轮次。

    终结处置只是改 stage，**不动 current_round_id**（派单还在、面试官也没变），
    所以撤销不需要另存快照 —— 照当前轮次把 stage 算回去即可；没派过单的回待派单。
    不这么留口子的后果是：HR 手滑点错一个「录用 / 淘汰」，这条应聘记录就永久钉死，
    只能改库。
    """
    if application.stage not in TERMINAL_STAGES:
        raise bad_request(ErrorCode.STAGE_INVALID, "该候选人还在流程中，无需撤销处置")

    stage = PENDING
    round_obj = None
    if application.current_round_id:
        round_obj = await db.get(PositionRound, application.current_round_id)
    if round_obj is not None:
        stage = ROUND_STAGE.get(round_obj.type, PENDING)

    application.stage = stage
    application.closed_at = None
    application.updated_at = _now()
    await db.commit()
    await db.refresh(application)
    metrics.record_op("board.reopen", "ok")
    return application, ""


async def _advance(db: AsyncSession, application: Application) -> tuple[Application, str]:
    candidate = await db.get(Candidate, application.candidate_id)
    if candidate is None:
        raise not_found("候选人不存在")
    if candidate.profile_status != CONFIRMED:
        metrics.record_op("board.advance", "rejected")
        raise bad_request(
            ErrorCode.CANDIDATE_NOT_CONFIRMED, "该候选人简历尚未确认，不能推进流程"
        )
    if application.stage not in FLOW_STAGES:
        raise bad_request(ErrorCode.STAGE_INVALID, "该候选人已终结处置，不能继续推进")

    rounds = await _rounds_of(db, application.position_id)
    if not rounds:
        raise bad_request(
            ErrorCode.SESSION_NO_ROUND, "该职位还没有配置面试流程，无法推进"
        )

    if application.stage == PENDING:
        current_seq = -1
    else:
        current = next(
            (r for r in rounds if r.id == application.current_round_id), None
        )
        current_seq = current.seq if current else max(r.seq for r in rounds)

    nxt = next((r for r in rounds if r.seq > current_seq), None)
    if nxt is None:
        raise bad_request(
            ErrorCode.STAGE_INVALID, "已是流程最后一轮，请直接做终结处置（录用 / 淘汰 / 人才库）"
        )

    notice = await _dispatch_or_reuse(db, application, nxt)
    await db.commit()
    await db.refresh(application)
    metrics.record_op("board.advance", "ok")
    tracing.add_metadata(
        application_id=application.id, to_stage=application.stage, notice=bool(notice)
    )
    return application, notice


async def _rollback(db: AsyncSession, application: Application) -> tuple[Application, str]:
    """退回上一轮。

    **成功出口有三处**（找不到当前轮次 / 已在第一轮 / 正常退回上一轮），逐个写埋点
    迟早会漏一个，所以把校验与记账放在外层，成功统一记一次 `ok`。
    """
    if application.stage not in FLOW_STAGES:
        metrics.record_op("board.rollback", "rejected")
        raise bad_request(ErrorCode.STAGE_INVALID, "该候选人已终结处置，不能退回")
    if application.stage == PENDING:
        metrics.record_op("board.rollback", "rejected")
        raise bad_request(ErrorCode.STAGE_INVALID, "候选人还没派单，无需退回")

    result = await _rollback_impl(db, application)
    metrics.record_op("board.rollback", "ok")
    tracing.add_metadata(
        action="rollback",
        application_id=application.id,
        to_stage=application.stage,
    )
    return result


async def _rollback_impl(
    db: AsyncSession, application: Application
) -> tuple[Application, str]:
    rounds = await _rounds_of(db, application.position_id)
    current = next((r for r in rounds if r.id == application.current_round_id), None)
    if current is None:
        # 配置被改过、找不到当前轮次：退回待派单，由 HR 重新推进
        application.stage = PENDING
        application.current_round_id = None
        application.updated_at = _now()
        await db.commit()
        await db.refresh(application)
        return application, ""

    prev = [r for r in rounds if r.seq < current.seq]
    if not prev:
        application.stage = PENDING
        application.current_round_id = None
        application.updated_at = _now()
        await db.commit()
        await db.refresh(application)
        return application, ""

    target = max(prev, key=lambda r: r.seq)
    notice = await _dispatch_or_reuse(db, application, target)
    await db.commit()
    await db.refresh(application)
    return application, notice


async def count_active_candidates(db: AsyncSession, position_id: int) -> int:
    """在流程（未终结处置）的应聘记录数 —— 关闭职位时的二次确认用。"""
    rows = await db.scalars(
        select(Application.id).where(
            Application.position_id == position_id,
            Application.stage.in_(list(FLOW_STAGES)),
        )
    )
    return len(list(rows))
