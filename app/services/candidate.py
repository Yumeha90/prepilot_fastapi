"""候选人服务（PRD 3.3：上传 → 解析 → 确认 → 派单）。

D5 确认即派单：确认写档案后立刻按流程配置派到首轮面试官并建会话（见 confirm_candidate）。
派单失败不回滚确认，候选人停在 `pending` 等配置补齐。

两条本期唯一约束：
- D3 一人一职位：同邮箱候选人若已有任意 application 一律拦截
- D1 上传必须选职位，且该职位 JD 必须已确认（否则没有权重可算匹配分）
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, bad_request, forbidden, not_found
from app.models.candidate import CONFIRMED, PARSED, PENDING, Candidate
from app.models.candidate import Application
from app.models.position import Position
from app.models.session import InterviewSession
from app.models.user import User
from app.schemas.candidate import CandidateCreateIn

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_email(value: str) -> str:
    """邮箱是 D14 的唯一识别键，必须归一化后再比较，否则大小写会绕过唯一约束。"""
    email = (value or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise bad_request(ErrorCode.CANDIDATE_EMAIL_TAKEN, "邮箱格式不正确")
    return email


# ---------------------------------------------------------------- 可见性（数据范围）


async def visible_candidate_ids(
    db: AsyncSession, user: User, scopes: dict[str, str]
) -> list[int] | None:
    """返回该用户可见的候选人 id 列表；None 表示不限制（all）。

    方案 a（2026-09-30 拍板）：面试官的数据范围是 `assigned`，但 D5 派单派的是
    **首轮**面试官 —— 若上传者是 r2 面试官，派单后他自己反而看不到了。
    因此这里额外放开 `created_by = 自己`，让上传者能跟踪自己提交的候选人。

    S2 起再并上「存在 interviewer_id = 自己 的会话」：派给谁的面试，谁就能看到
    这个候选人（否则面试官登录后列表是空的，等于派了个看不见的单）。
    """
    scope = scopes.get("candidate", "all")
    if scope == "all":
        return None
    assigned = select(InterviewSession.candidate_id).where(
        InterviewSession.interviewer_id == user.id
    )
    rows = await db.scalars(
        select(Candidate.id).where(
            or_(Candidate.created_by == user.id, Candidate.id.in_(assigned))
        )
    )
    return list(set(rows))


async def ensure_can_view(db: AsyncSession, user: User, candidate: Candidate) -> None:
    from app.services.rbac import user_data_scopes

    scopes = await user_data_scopes(db, user)
    allowed = await visible_candidate_ids(db, user, scopes)
    if allowed is not None and candidate.id not in allowed:
        raise forbidden("无权查看该候选人")


async def ensure_can_upload_to_position(
    db: AsyncSession, user: User, position: Position
) -> None:
    """上传前的职位校验：JD 必须已确认 + 职位在该用户可见范围内。"""
    if position.jd_status != "confirmed":
        raise bad_request(
            ErrorCode.CANDIDATE_JD_NOT_CONFIRMED,
            "该职位的 JD 尚未确认，无法上传候选人（确认后才能计算匹配分）",
        )
    from app.services.position import ensure_can_view as ensure_can_view_position

    await ensure_can_view_position(db, user, position)


# ---------------------------------------------------------------- 读写


async def get_candidate(db: AsyncSession, candidate_id: int) -> Candidate:
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise not_found("候选人不存在")
    return candidate


async def create_candidate(
    db: AsyncSession, user: User, payload: CandidateCreateIn
) -> Candidate:
    """上传简历：授权门禁 → 职位校验 → 按邮箱复用或新建 → 建 pending 应聘记录。"""
    if not payload.auth_tick:
        raise bad_request(
            ErrorCode.CANDIDATE_AUTH_REQUIRED, "请先阅读并勾选《数据处理授权》"
        )

    # 姓名是列表与看板的主标识，不允许为空（也不再用邮箱前缀兜底）
    name = (payload.name or "").strip()
    if not name:
        raise bad_request(ErrorCode.CANDIDATE_NAME_REQUIRED, "请填写候选人姓名")

    position = await db.get(Position, payload.position_id)
    if position is None:
        raise not_found("职位不存在")
    await ensure_can_upload_to_position(db, user, position)

    email = _norm_email(payload.contact_email)
    raw_text = (payload.raw_text or "").strip()
    if len(raw_text) < 30:
        raise bad_request(
            ErrorCode.RESUME_TEXT_TOO_SHORT,
            "简历内容为空或过短，请重新上传文件或粘贴完整内容",
        )

    existing = await db.scalar(select(Candidate).where(Candidate.contact_email == email))
    if existing is not None:
        # D3：一人一职位。已有任意应聘记录就拦截，并说清投的是哪个职位
        applied = list(existing.applications or [])
        if applied:
            app = applied[0]
            pos_name = (
                (await db.get(Position, app.position_id)).name if app.position_id else ""
            )
            if app.position_id == payload.position_id:
                raise bad_request(
                    ErrorCode.CANDIDATE_ALREADY_APPLIED,
                    f"该候选人已投递过职位「{pos_name}」，不能重复投递",
                )
            raise bad_request(
                ErrorCode.CANDIDATE_ALREADY_APPLIED,
                f"该候选人已投递过职位「{pos_name}」，本期不支持一人投递多个职位",
            )
        candidate = existing
        # 复用旧候选人时补上本次填写的姓名与电话（旧值可能为空）
        candidate.name = name
        if (payload.contact_phone or "").strip():
            candidate.contact_phone = payload.contact_phone.strip()
    else:
        candidate = Candidate(
            name=name,
            contact_email=email,
            contact_phone=(payload.contact_phone or "").strip(),
            source=payload.source,
            created_by=user.id,
        )
        db.add(candidate)
        await db.flush()

    candidate.resume_raw_text = raw_text
    candidate.resume_file_name = (payload.file_name or "").strip()
    candidate.auth_tick = True
    candidate.auth_at = _now()
    candidate.profile_status = "uploading"
    candidate.parsed_profile = None
    candidate.confirmed_at = None
    candidate.updated_at = _now()

    db.add(
        Application(
            candidate_id=candidate.id,
            position_id=position.id,
            stage=PENDING,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await db.commit()
    await db.refresh(candidate)
    return candidate


def ensure_can_parse(candidate: Candidate) -> None:
    """解析前置校验：**必须放在调用模型之前**。

    一次解析云端 17~23s 且要烧 token，先跑模型再拒绝等于白烧一次。
    """
    if candidate.profile_status == CONFIRMED:
        raise bad_request(
            ErrorCode.CANDIDATE_ALREADY_CONFIRMED, "该候选人已确认，不能覆盖其档案"
        )
    if candidate.purged_at is not None:
        raise bad_request(ErrorCode.CANDIDATE_PURGED, "该候选人简历已粉碎，无法解析")


async def save_parsed_profile(
    db: AsyncSession, candidate: Candidate, profile: dict
) -> Candidate:
    """把 AI 解析结果存成**草稿**（`profile_status=parsed`），确认时才置 confirmed。

    为什么落库（2026-10-01 改口径）：一次解析云端实测 17~23s，原「结果不落库」
    导致每次进解析页都要重跑一遍 —— 既重复烧 token，又让 HR 每次干等。
    改成先存草稿后，重复进入直接读库里的结果，只有点「重新解析」才再调模型。

    草稿**不等于生效数据**：不推进流程、不改姓名/邮箱/手机、不给匹配分用，
    BR-04「人工确认后才写入」的口径没变，变的只是把「写在哪一步」从确认时
    提前到解析后存草稿、确认时转正。
    """
    ensure_can_parse(candidate)

    candidate.parsed_profile = profile or {}
    candidate.profile_status = PARSED
    candidate.updated_at = _now()
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def confirm_candidate(
    db: AsyncSession, user: User, candidate: Candidate, profile: dict
) -> tuple[Candidate, str]:
    """确认解析结果并派单到首轮（D5）。

    返回 `(candidate, dispatch_notice)`：notice 非空表示派单没成（确认仍已生效），
    由调用方提示 HR 去补齐流程配置。

    **派单失败不回滚确认** —— 确认是已经完成的业务动作，回滚它是错的；
    候选人停在 `pending`，等配置补齐后由看板的手动派单补上。
    """
    if candidate.profile_status == CONFIRMED:
        raise bad_request(ErrorCode.CANDIDATE_ALREADY_CONFIRMED, "该候选人已确认，无需重复操作")

    profile = profile or {}
    basic = profile.get("basic") or {}
    name = str(basic.get("name") or "").strip()
    if not name:
        raise bad_request(
            ErrorCode.CANDIDATE_NOT_CONFIRMED, "请先补全候选人姓名再确认"
        )

    candidate.name = name or candidate.name
    if str(basic.get("email") or "").strip():
        candidate.contact_email = _norm_email(str(basic["email"]))
    if str(basic.get("phone") or "").strip():
        candidate.contact_phone = str(basic["phone"]).strip()
    candidate.parsed_profile = profile
    candidate.profile_status = CONFIRMED
    candidate.confirmed_at = _now()
    candidate.updated_at = _now()
    await db.commit()
    await db.refresh(candidate)

    # D5 确认即派单：独立的一步，失败只记原因，不动已经落库的确认
    from app.core.errors import AppError
    from app.services import dispatch as dispatch_svc

    notice = ""
    try:
        dispatched = False
        for app in list(candidate.applications or []):
            if app.stage == PENDING:
                await dispatch_svc.dispatch_first_round(db, app)
                dispatched = True
        if dispatched:
            await db.commit()
    except AppError as exc:
        # 失败路径上**不要再触发任何数据库 IO**：rollback / expire_all + refresh 都会让
        # 连接在归还时炸 MissingGreenlet（SQLAlchemy 异步会话的老问题）。
        # 反正派单这一步要么全成、要么一个字节都没写，不需要回滚。
        notice = str(exc.detail.get("message", "派单失败"))
        logger.warning("候选人 %s 确认后派单失败：%s", candidate.id, notice)

    if notice:
        # 派单没成就没有新数据要读回来，跳过 refresh（失败路径上少一次 IO 更稳）
        return candidate, notice
    await db.refresh(candidate)
    return candidate, notice


async def list_candidates(
    db: AsyncSession,
    user: User,
    *,
    keyword: str = "",
    position_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Candidate], int]:
    """候选人列表。数据范围：all 全量 / assigned 仅自己上传的（方案 a）。"""
    from app.services.rbac import user_data_scopes

    scopes = await user_data_scopes(db, user)
    allowed = await visible_candidate_ids(db, user, scopes)

    stmt = select(Candidate)
    if allowed is not None:
        stmt = stmt.where(Candidate.id.in_(allowed) if allowed else Candidate.id == -1)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(Candidate.name.ilike(like), Candidate.contact_email.ilike(like))
        )
    if status:
        stmt = stmt.where(Candidate.profile_status == status)
    if position_id:
        stmt = stmt.where(
            Candidate.id.in_(
                select(Application.candidate_id).where(
                    Application.position_id == position_id
                )
            )
        )

    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0
    rows = await db.scalars(
        stmt.order_by(Candidate.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows), int(total)
