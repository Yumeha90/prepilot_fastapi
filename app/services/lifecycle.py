"""数据生命周期服务（PRD 3.5.1 P15 / BR-10 / §7.7）。

四条口径：
1. **粉碎只清内容、行保留**（D7）：清空简历原文、解析结果与文件名，置
   `purged_at` 与 `profile_status=archived`。候选人的应聘记录、会话、匹配分全部不动 ——
   流程事实不是隐私数据，删了就看板空洞、P18 无法回答「当年按哪版标准算的分」。
2. **已录用不自动粉碎**（BR-10 原文是「未入职」）：`stage=accepted` 表示已入职并离开
   招聘流程，自动扫描与手动粉碎都跳过它，否则等于把在职员工简历删了。
3. **定时任务与手动粉碎共用一条清除逻辑**，只是 `purge_source` 不同：
   两条路径各写一遍迟早会不一致（比如一边清了文件名一边没清）。
4. **面评原文同属粉碎范围**（§10 待办，3.4 落地后补齐）：简历清了、面评里却
   留着「候选人说…」的证据原文与整段综合评价，等于没清 —— 面评是对这个人的
   **评价性**个人信息，比简历更敏感。清法与简历同口径：**只清正文、保留结构**，
   评分与能力项名留着（那是流程事实），证据与笔记正文清空。
   矩阵里的「简历证据」摘要同此口径：它是从简历原文摘来的片段。

停用的语义：策略 `enabled=false` 时定时任务照跑（更新 `last_run_at` 让页面看得到
「它确实在跑」），但一条都不粉碎 —— 与「干脆不跑」相比，更好排查。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, bad_request
from app.models.candidate import ARCHIVED, Candidate
from app.models.candidate import Application
from app.models.lifecycle import CURRENT, HISTORY, PURGE_AUTO, PURGE_MANUAL, RetentionPolicy
from app.models.lifecycle import RESOURCE_RESUME
from app.models.position import Position
from app.models.session import InterviewSession
from app.models.user import User
from app.observability import metrics, tracing
from app.schemas.lifecycle import (
    AutoPurgeOut,
    LifecycleOut,
    PolicyOut,
    PurgeOut,
    ScanItem,
    ScanOut,
)

logger = logging.getLogger(__name__)

DEFAULT_POLICY_NAME = "默认保留策略"
DEFAULT_DAYS = 90
MIN_DAYS = 1
MAX_DAYS = 3650
# 扫描允许 0 天：表示「此刻即到期」。策略不允许 0 天（等于关掉保留期），
# 但验收时要能当场看到「自动粉碎到底会删谁」，所以预览口径比策略宽一档。
SCAN_MIN_DAYS = 0
# 单次扫描上限：防止策略被改成 1 天后一次捞全库
SCAN_LIMIT = 500
# 已录用 = 已入职（BR-10 的「未入职」判定：没有 accepted 应聘记录）
ACCEPTED = "accepted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def policy_out(policy: RetentionPolicy) -> PolicyOut:
    return PolicyOut(
        id=policy.id,
        name=policy.name,
        resource=policy.resource,
        days=policy.days,
        enabled=policy.enabled,
        status=policy.status,
        last_run_at=policy.last_run_at,
        last_purged=policy.last_purged,
        created_at=policy.created_at,
    )


# ---------------------------------------------------------------- 策略


async def current_policy(db: AsyncSession) -> RetentionPolicy:
    """取生效中的策略；库里没有就按默认值建一条。

    为什么不放在迁移里插：迁移跑在 seed 之前，`created_by` 还不知道是谁；
    而且本地测试库的策略可能被用例改过，懒创建比「迁移保证有」更稳。
    """
    row = await db.scalar(
        select(RetentionPolicy).where(
            RetentionPolicy.resource == RESOURCE_RESUME,
            RetentionPolicy.status == CURRENT,
        )
    )
    if row is not None:
        return row
    row = RetentionPolicy(
        name=DEFAULT_POLICY_NAME,
        resource=RESOURCE_RESUME,
        days=DEFAULT_DAYS,
        enabled=True,
        status=CURRENT,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def lifecycle_data(db: AsyncSession) -> LifecycleOut:
    policy = await current_policy(db)
    history = list(
        await db.scalars(
            select(RetentionPolicy)
            .where(RetentionPolicy.status == HISTORY)
            .order_by(RetentionPolicy.id.desc())
        )
    )
    pending = await _pending_count(db, policy.days)
    purged = await db.scalar(
        select(func.count()).select_from(Candidate).where(Candidate.purged_at.isnot(None))
    )
    return LifecycleOut(
        policy=policy_out(policy),
        history=[policy_out(p) for p in history],
        pending=pending or 0,
        purged_total=int(purged or 0),
    )


async def update_policy(
    db: AsyncSession,
    user: User,
    *,
    name: str | None = None,
    days: int | None = None,
    enabled: bool | None = None,
) -> RetentionPolicy:
    """编辑当前策略：**旧版本转历史**，再插一条新的生效策略。

    不能直接 update 原行：页面要能回答「这个人被粉碎时生效的策略是多少天」。
    """
    policy = await current_policy(db)
    if days is not None and not (MIN_DAYS <= days <= MAX_DAYS):
        raise bad_request(
            ErrorCode.LIFECYCLE_DAYS_INVALID,
            f"保留天数需在 {MIN_DAYS}~{MAX_DAYS} 天之间",
        )

    # 唯一的 current 约束在 (resource) WHERE status='current' 上，
    # 必须先把旧的置为 history 再插新的，否则撞唯一索引。
    policy.status = HISTORY
    await db.flush()

    new_policy = RetentionPolicy(
        name=(name or "").strip() or policy.name,
        resource=policy.resource,
        days=days if days is not None else policy.days,
        enabled=policy.enabled if enabled is None else enabled,
        status=CURRENT,
        created_by=user.id,
        # 继承最近一次执行情况：换策略不该让「定时任务跑没跑」这条信息消失
        last_run_at=policy.last_run_at,
        last_purged=policy.last_purged,
    )
    db.add(new_policy)
    await db.commit()
    await db.refresh(new_policy)
    return new_policy


# ---------------------------------------------------------------- 扫描


def _accepted_ids_stmt():
    return select(Application.candidate_id).where(Application.stage == ACCEPTED)


def _since(candidate: Candidate) -> datetime:
    """保留期起算点（D6）：确认时间，未确认的草稿退回入库时间。

    为什么不能只用 `created_at`：D6 定的是「从确认起算」，确认才是简历正式入库、
    开始计保留期的时点；上传后没确认的草稿记 created_at 会让它在满 90 天前就被判到期，
    HR 还没确认就先被清了。
    为什么不能只用 `confirmed_at`：未确认的草稿同样存了简历原文，`confirmed_at` 为 NULL
    会让它们永远不命中扫描 —— 合规上等于给「传了就不管」留了个洞，所以兜底到 created_at。
    """
    return _as_utc(candidate.confirmed_at or candidate.created_at)


def _due_stmt(days: int):
    cutoff = _now() - timedelta(days=days)
    return (
        select(Candidate)
        .where(
            Candidate.purged_at.is_(None),
            func.coalesce(Candidate.confirmed_at, Candidate.created_at) <= cutoff,
            ~Candidate.id.in_(_accepted_ids_stmt()),
        )
        .order_by(Candidate.created_at)
    )


async def _pending_count(db: AsyncSession, days: int) -> int:
    rows = await db.scalars(_due_stmt(days).with_only_columns(Candidate.id))
    return len(list(rows))


async def scan(db: AsyncSession, days: int | None = None, limit: int = SCAN_LIMIT) -> ScanOut:
    """按保留天数扫出待粉碎候选人。`days` 为空时取当前策略。"""
    policy = await current_policy(db)
    effective_days = days if days is not None else policy.days
    if not (SCAN_MIN_DAYS <= effective_days <= MAX_DAYS):
        raise bad_request(
            ErrorCode.LIFECYCLE_DAYS_INVALID,
            f"保留天数需在 {SCAN_MIN_DAYS}~{MAX_DAYS} 天之间",
        )

    candidates = list(await db.scalars(_due_stmt(effective_days).limit(limit)))
    total = await _pending_count(db, effective_days)
    if not candidates:
        return ScanOut(days=effective_days, total=total, items=[])

    applications = list(
        await db.scalars(
            select(Application).where(
                Application.candidate_id.in_([c.id for c in candidates])
            )
        )
    )
    positions = {
        p.id: p.name
        for p in await db.scalars(
            select(Position).where(
                Position.id.in_({a.position_id for a in applications})
            )
        )
    }
    apps_by_candidate: dict[int, list[Application]] = {}
    for a in applications:
        apps_by_candidate.setdefault(a.candidate_id, []).append(a)

    items: list[ScanItem] = []
    for c in candidates:
        apps = apps_by_candidate.get(c.id) or []
        held = (_now() - _since(c)).days
        items.append(
            ScanItem(
                candidate_id=c.id,
                name=c.name,
                email=c.contact_email,
                position_names=[positions.get(a.position_id, "") for a in apps],
                stage=apps[0].stage if apps else "",
                since_at=_since(c),
                days_overdue=max(held - effective_days, 0),
            )
        )
    return ScanOut(days=effective_days, total=total, items=items)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- 粉碎：面评与简历证据


def purge_evaluation(session: InterviewSession) -> bool:
    """清掉一个会话里的面评正文（行与结构保留）。

    **保留什么**：能力项名、评分（`score`）、证据的 quote 标记之外的结构 ——
    这些是「当年按哪版标准、给了几分」的流程事实，也是 P17 回看的骨架。
    **清掉什么**：综合评价正文、采纳前的原文、润色稿、逐条证据文本与快记 ——
    这些是对这个人的**描述与评价**，是 §10 粉碎范围的主体。
    """
    # 刻意 copy 一份再改：SQLAlchemy 按**对象身份**判定 JSONB 是否变过，
    # 原地改完再赋回去（`data = session.evaluation_json` → 改 → 赋回）会被判成没变，
    # UPDATE 根本不发 —— 页面显示已粉碎，库里原文还在，是最难查的那种 bug。
    data = dict(session.evaluation_json or {})
    if not data:
        return False
    items: list[dict] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["evidences"] = []
        item["note"] = ""
        items.append(item)
    data["items"] = items
    data["summary"] = ""
    data["summary_original"] = ""
    data["polished"] = ""
    data["polished_flags"] = []
    # 留痕：P17 要能显示「内容已按保留策略粉碎」，而不是显示一片空白让人以为没写
    data["content_purged"] = True
    data["purged_at"] = _now().isoformat()
    session.evaluation_json = data

    # 提交快照里的命中片段同样来自面评正文
    submission = dict(session.submission_json or {})
    if submission:
        submission["flags"] = []
        session.submission_json = submission
    return True


def purge_matrix_evidence(session: InterviewSession) -> None:
    """矩阵里的「简历证据」是从简历原文摘的片段 —— 简历清了它也要清。

    考察重点（`focus`）与能力项名留下：那是面试官写的备面思路，不含个人信息。
    """
    # 同上：整体换新对象，行也逐条 copy，避免把共享的嵌套 dict 一起改脏
    data = dict(session.matrix_json or {})
    rows = data.get("rows")
    if not isinstance(rows, list):
        return
    new_rows: list = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
            item["evidence"] = ""
            new_rows.append(item)
        else:
            new_rows.append(row)
    data["rows"] = new_rows
    session.matrix_json = data


async def purge_evaluations(db: AsyncSession, candidate_id: int) -> int:
    """清掉该候选人**所有会话**（含草稿与已提交）里的面评正文，返回清了几个会话。"""
    sessions = list(
        await db.scalars(
            select(InterviewSession).where(
                InterviewSession.candidate_id == candidate_id
            )
        )
    )
    touched = 0
    for session in sessions:
        purge_matrix_evidence(session)
        if purge_evaluation(session):
            touched += 1
        session.updated_at = _now()
    return touched


async def purge(
    db: AsyncSession,
    candidate_ids: list[int],
    *,
    actor: User | None,
    source: str,
) -> PurgeOut:
    """清除简历内容（行保留）。已粉碎 / 已录用 / 不存在一律跳过而不是报错。

    跳过而非报错的理由：定时任务是一次批量作业，中间夹着「已录用」和「上轮刚粉碎」
    是常态，抛异常会让整批失败；手动粉碎时前端看到的是计数，也够用。
    """
    result = PurgeOut(requested=len(candidate_ids))
    for candidate_id in candidate_ids:
        candidate = await db.get(Candidate, candidate_id)
        if candidate is None:
            result.skipped_missing += 1
            continue
        if candidate.purged_at is not None:
            result.skipped_purged += 1
            continue
        apps = list(
            await db.scalars(
                select(Application).where(Application.candidate_id == candidate.id)
            )
        )
        if any(a.stage == ACCEPTED for a in apps):
            result.skipped_accepted += 1
            continue

        candidate.resume_raw_text = ""
        candidate.resume_file_name = ""
        candidate.parsed_profile = None
        candidate.profile_status = ARCHIVED
        candidate.purged_at = _now()
        candidate.purge_source = source
        candidate.purged_by = actor.id if actor is not None else None
        candidate.updated_at = _now()
        result.purged += 1
        result.purged_ids.append(candidate.id)
        # §10：面评原文与矩阵里的简历证据摘要同属粉碎范围
        result.evaluations_purged += await purge_evaluations(db, candidate.id)

    await db.commit()
    # 按「实际粉碎的人数」计数（不是按请求数），否则面板上的数字对不上合规记录
    metrics.BUSINESS_OPS.labels(op=f"lifecycle.purge.{source}", status="ok").inc(
        result.purged
    )
    tracing.add_metadata(
        requested=result.requested,
        purged=result.purged,
        skipped_accepted=result.skipped_accepted,
        skipped_missing=result.skipped_missing,
        skipped_purged=result.skipped_purged,
        evaluations_purged=result.evaluations_purged,
    )
    return result


async def manual_purge(
    db: AsyncSession, user: User, candidate_ids: list[int]
) -> PurgeOut:
    if not candidate_ids:
        raise bad_request(ErrorCode.LIFECYCLE_NOTHING_SELECTED, "请先选择要粉碎的候选人")
    logger.info("超管 %s 手动粉碎 %s 位候选人", user.id, len(candidate_ids))
    return await purge(db, candidate_ids, actor=user, source=PURGE_MANUAL)


async def run_auto_purge(db: AsyncSession) -> AutoPurgeOut:
    """定时任务入口：按当前策略扫描并粉碎。

    为什么停用时也要更新 `last_run_at`：页面「最近执行」一直没有动静时，
    运维分不清是「beat 没起来」还是「策略停用所以没删人」。
    """
    policy = await current_policy(db)
    policy.last_run_at = _now()
    if not policy.enabled:
        policy.last_purged = 0
        await db.commit()
        await db.refresh(policy)
        metrics.record_op("lifecycle.auto_purge", "skipped_disabled")
        return AutoPurgeOut(enabled=False, days=policy.days, scanned=0, purged=0,
                            ran_at=policy.last_run_at)

    result = await scan(db, policy.days, limit=SCAN_LIMIT)
    purged = await purge(db, [i.candidate_id for i in result.items], actor=None,
                         source=PURGE_AUTO)
    policy.last_purged = purged.purged
    await db.commit()
    await db.refresh(policy)
    # 粉碎是不可逆的合规动作，每一次都必须留下计数，运维要能对得上「扫描到 / 已粉碎」
    metrics.record_op("lifecycle.auto_purge", "ok")
    tracing.add_metadata(scanned=len(result.items), purged=purged.purged, days=policy.days)
    if purged.purged:
        logger.info("定时粉碎：到期 %s 人，已粉碎 %s 人", result.total, purged.purged)
    return AutoPurgeOut(
        enabled=True,
        days=policy.days,
        scanned=len(result.items),
        purged=purged.purged,
        ran_at=policy.last_run_at,
    )
