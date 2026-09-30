"""候选人服务（PRD 3.3 第一段：上传 → 解析 → 确认）。

本阶段不做派单、看板、匹配分、粉碎 —— 确认后停在 `application.stage=pending`，
这本来就是看板的一列（P08 §3.3.3），是合法中间态，S2 再接派单。

两条本期唯一约束：
- D3 一人一职位：同邮箱候选人若已有任意 application 一律拦截
- D1 上传必须选职位，且该职位 JD 必须已确认（否则 S4 没有权重可算）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, bad_request, forbidden, not_found
from app.models.candidate import CONFIRMED, PENDING, Candidate
from app.models.candidate import Application
from app.models.position import Position
from app.models.user import User
from app.schemas.candidate import CandidateCreateIn

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    （S2 派单落地后，本函数再追加「存在 interviewer_id = 自己 的 session」条件。）
    """
    scope = scopes.get("candidate", "all")
    if scope == "all":
        return None
    rows = await db.scalars(select(Candidate.id).where(Candidate.created_by == user.id))
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
    else:
        candidate = Candidate(
            name=(payload.name or "").strip() or (payload.contact_email or "").split("@")[0],
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


async def confirm_candidate(
    db: AsyncSession, user: User, candidate: Candidate, profile: dict
) -> Candidate:
    """确认解析结果（BR-04 / BR-11）：只有确认后才允许进入流程。

    S2 会在这里挂钩派单，S4 挂钩首次算分 —— 本阶段只写档案。
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
    return candidate


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
