"""职位与 JD 管理（PRD 3.2）。

权限：position:view_all / position:view_assigned（读）、position:edit（写）。
数据范围：role_data_scopes 的 position = all / assigned，面试官只看到被指派的职位。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_any_perm, require_perm
from app.core.errors import ErrorCode, bad_request
from app.models.position import Position
from app.models.user import User
from app.schemas.position import (
    JdIn,
    JdOut,
    JdVersionDetailOut,
    JdVersionOut,
    MessageOut,
    Paged,
    PositionCreateIn,
    PositionListItem,
    PositionOut,
    PositionUpdateIn,
    RoundOut,
    RoundsSaveIn,
    SavePreviewIn,
    SavePreviewOut,
)
from app.services import position as svc
from app.services.rbac import user_data_scopes, user_permissions

router = APIRouter(prefix="/positions", tags=["position"])

VIEW_PERMS = ("position:view_all", "position:view_assigned")


async def _names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.full_name).where(User.id.in_(ids))
    )
    return {r[0]: r[1] for r in rows.all()}


def _round_out(r, names: dict[int, str]) -> RoundOut:
    return RoundOut(
        id=r.id,
        seq=r.seq,
        name=r.name,
        type=r.type,
        interviewer_id=r.interviewer_id,
        interviewer_name=names.get(r.interviewer_id, "") if r.interviewer_id else "",
    )


def _jd_out(p: Position) -> JdOut:
    return JdOut(
        raw_text=p.jd_raw_text,
        hard_gates=list(p.jd_hard_gates or []),
        competencies=[dict(c) for c in (p.jd_competencies or [])],
        bonuses=list(p.jd_bonuses or []),
        status=p.jd_status,
        version=p.jd_version,
        draft=p.jd_draft_json,
    )


def _item(p: Position, names: dict[int, str], candidates: int = 0) -> PositionListItem:
    return PositionListItem(
        id=p.id,
        name=p.name,
        status=p.status,
        owner_id=p.owner_id,
        owner_name=names.get(p.owner_id, "") if p.owner_id else "",
        jd_version=p.jd_version,
        jd_status=p.jd_status,
        jd_completion=svc.jd_completion(p),
        candidate_count=candidates,
        round_count=len(p.rounds),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _status_out(p: Position) -> PositionOut:
    """状态流转类接口（发布 / 暂停 / 恢复 / 关闭 / 重新打开）共用出参。

    前端拿到结果后只提示 + 刷新列表，因此这里不回填 rounds / owner_name。
    """
    return PositionOut(
        id=p.id,
        name=p.name,
        status=p.status,
        owner_id=p.owner_id,
        jd_version=p.jd_version,
        jd_status=p.jd_status,
        jd_completion=svc.jd_completion(p),
        rounds=[],
        copied_from_id=p.copied_from_id,
        closed_at=p.closed_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
        jd=_jd_out(p),
    )


# ---------------------------------------------------------------- 列表与详情


@router.get("", response_model=Paged)
async def list_positions(
    status: str | None = Query(default=None, pattern="^(draft|open|paused|closed)$"),
    owner_id: int | None = None,
    keyword: str = "",
    include_closed: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> Paged:
    scopes = await user_data_scopes(db, user)
    rows, total = await svc.list_positions(
        db,
        user,
        scopes,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
        include_closed=include_closed,
        page=page,
        page_size=page_size,
    )
    ids = {p.owner_id for p in rows if p.owner_id}
    names = await _names(db, ids)
    return Paged(
        items=[_item(p, names) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{position_id}", response_model=PositionOut)
async def get_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> PositionOut:
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    names = await _names(
        db,
        {position.owner_id}
        | {r.interviewer_id for r in position.rounds if r.interviewer_id},
    )
    return PositionOut(
        id=position.id,
        name=position.name,
        status=position.status,
        owner_id=position.owner_id,
        owner_name=names.get(position.owner_id, "") if position.owner_id else "",
        jd_version=position.jd_version,
        jd_status=position.jd_status,
        jd_completion=svc.jd_completion(position),
        rounds=[_round_out(r, names) for r in sorted(position.rounds, key=lambda x: x.seq)],
        copied_from_id=position.copied_from_id,
        closed_at=position.closed_at,
        created_at=position.created_at,
        updated_at=position.updated_at,
        jd=_jd_out(position),
    )


# ---------------------------------------------------------------- 增删改


@router.post("", response_model=PositionOut, status_code=201)
async def create_position(
    payload: PositionCreateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    position = Position(
        name=payload.name.strip(),
        status="draft",
        owner_id=payload.owner_id or user.id,
    )
    db.add(position)
    await db.commit()
    await db.refresh(position)
    names = await _names(db, {position.owner_id} if position.owner_id else set())
    return PositionOut(
        id=position.id,
        name=position.name,
        status=position.status,
        owner_id=position.owner_id,
        owner_name=names.get(position.owner_id, "") if position.owner_id else "",
        jd_version=position.jd_version,
        jd_status=position.jd_status,
        jd_completion=0,
        rounds=[],
        created_at=position.created_at,
        updated_at=position.updated_at,
        jd=_jd_out(position),
    )


@router.patch("/{position_id}", response_model=PositionOut)
async def update_position(
    position_id: int,
    payload: PositionUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    if payload.name is not None:
        position.name = payload.name.strip()
    if payload.owner_id is not None:
        position.owner_id = payload.owner_id
    if payload.status is not None and payload.status != position.status:
        await svc.change_status(db, position, payload.status)
    await db.commit()
    await db.refresh(position)
    names = await _names(db, {position.owner_id} if position.owner_id else set())
    return PositionOut(
        id=position.id,
        name=position.name,
        status=position.status,
        owner_id=position.owner_id,
        owner_name=names.get(position.owner_id, "") if position.owner_id else "",
        jd_version=position.jd_version,
        jd_status=position.jd_status,
        jd_completion=svc.jd_completion(position),
        rounds=[_round_out(r, names) for r in sorted(position.rounds, key=lambda x: x.seq)],
        copied_from_id=position.copied_from_id,
        closed_at=position.closed_at,
        created_at=position.created_at,
        updated_at=position.updated_at,
        jd=_jd_out(position),
    )


@router.delete("/{position_id}", response_model=MessageOut)
async def delete_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> MessageOut:
    """仅草稿可真删除；非草稿一律只能关闭（2026-09-30 定）。"""
    position = await svc.get_position(db, position_id)
    ok, reason = await svc.can_delete(db, position)
    if not ok:
        raise bad_request(ErrorCode.POSITION_NOT_DELETABLE, reason)
    await db.delete(position)
    await db.commit()
    return MessageOut(message="职位已删除", id=position_id)


# ---------------------------------------------------------------- JD


@router.put("/{position_id}/jd", response_model=JdOut)
async def save_jd(
    position_id: int,
    payload: JdIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> JdOut:
    """保存 JD。confirm=False 只写草稿区；confirm=True 校验并生效（BR-01）。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    updated = await svc.save_jd(
        db,
        position,
        payload.hard_gates,
        payload.competencies,
        payload.bonuses,
        payload.raw_text,
        payload.confirm,
        user,
    )
    return _jd_out(updated)


@router.get("/{position_id}/jd/versions", response_model=list[JdVersionOut])
async def list_jd_versions(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> list[JdVersionOut]:
    """JD 版本历史（倒序）。P18 用它回答「当年按哪版标准算的分」。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    rows = await svc.list_jd_versions(db, position_id)
    names = await _names(db, {r.changed_by for r in rows if r.changed_by})
    return [
        JdVersionOut(
            version=r.version,
            change_summary=r.change_summary,
            changed_by=r.changed_by,
            changed_by_name=names.get(r.changed_by, "") if r.changed_by else "",
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/{position_id}/jd/versions/{version}", response_model=JdVersionDetailOut)
async def get_jd_version(
    position_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> JdVersionDetailOut:
    """某一版 JD 的完整快照（只读）。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    row = await svc.get_jd_version(db, position_id, version)
    snap = row.snapshot_json or {}
    names = await _names(db, {row.changed_by} if row.changed_by else set())
    return JdVersionDetailOut(
        version=row.version,
        change_summary=row.change_summary,
        changed_by=row.changed_by,
        changed_by_name=names.get(row.changed_by, "") if row.changed_by else "",
        created_at=row.created_at,
        position_id=row.position_id,
        hard_gates=list(snap.get("hard_gates") or []),
        competencies=[dict(c) for c in (snap.get("competencies") or [])],
        bonuses=list(snap.get("bonuses") or []),
    )


@router.post("/{position_id}/save-preview", response_model=SavePreviewOut)
async def save_preview(
    position_id: int,
    payload: SavePreviewIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> SavePreviewOut:
    """保存前预检：JD 是否会 bump 版本、轮次是否变更、波及多少在流程候选人。

    前端据此决定要不要弹二次确认；真正保存仍会再校验一次（不信任前端）。
    """
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    return SavePreviewOut(**await svc.preview_save(db, position, payload.jd, payload.rounds))




# ---------------------------------------------------------------- 流程配置


@router.put("/{position_id}/rounds", response_model=list[RoundOut])
async def save_rounds(
    position_id: int,
    payload: RoundsSaveIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> list[RoundOut]:
    """整体替换轮次。变更只对未来的应聘记录生效，已派单会话不跟随。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.save_rounds(db, position, payload.rounds)
    await db.refresh(position)
    names = await _names(db, {r.interviewer_id for r in position.rounds if r.interviewer_id})
    return [_round_out(r, names) for r in sorted(position.rounds, key=lambda x: x.seq)]


# ---------------------------------------------------------------- 状态流转 / 复制


@router.post("/{position_id}/publish", response_model=PositionOut)
async def publish_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    """草稿 → 招聘中：要求 JD 已确认且 JD / 轮次校验全部通过。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.check_publish_ready(db, position)
    await svc.change_status(db, position, "open")
    names = await _names(
        db,
        {position.owner_id}
        | {r.interviewer_id for r in position.rounds if r.interviewer_id},
    )
    return PositionOut(
        id=position.id,
        name=position.name,
        status=position.status,
        owner_id=position.owner_id,
        owner_name=names.get(position.owner_id, "") if position.owner_id else "",
        jd_version=position.jd_version,
        jd_status=position.jd_status,
        jd_completion=svc.jd_completion(position),
        rounds=[_round_out(r, names) for r in sorted(position.rounds, key=lambda x: x.seq)],
        copied_from_id=position.copied_from_id,
        closed_at=position.closed_at,
        created_at=position.created_at,
        updated_at=position.updated_at,
        jd=_jd_out(position),
    )


@router.post("/{position_id}/pause", response_model=PositionOut)
async def pause_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    """招聘中 → 已暂停：停止接收新候选人，在流程候选人不受影响。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.change_status(db, position, "paused")
    return _status_out(position)


@router.post("/{position_id}/resume", response_model=PositionOut)
async def resume_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    """已暂停 → 招聘中：恢复接收新候选人。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.change_status(db, position, "open")
    return _status_out(position)


@router.get("/{position_id}/active-candidates", response_model=dict)
async def active_candidates(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> dict:
    """在流程（未终结处置）的候选人数量 —— 关闭职位前的二次确认要显示这个数。

    3.2 落地时这里恒返回 0（当时还没有 applications），S3 接上真实统计。
    """
    from app.services import board as board_svc

    count = await board_svc.count_active_candidates(db, position_id)
    return {"count": count}


@router.post("/{position_id}/close", response_model=PositionOut)
async def close_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.change_status(db, position, "closed")
    return _status_out(position)


@router.post("/{position_id}/reopen", response_model=PositionOut)
async def reopen_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    await svc.change_status(db, position, "open")
    return _status_out(position)


@router.post("/{position_id}/duplicate", response_model=PositionOut, status_code=201)
async def duplicate_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("position:edit")),
) -> PositionOut:
    """BR-24：带 JD 与轮次骨架复制，面试官人选清空须重新指派。"""
    position = await svc.get_position(db, position_id)
    await svc.ensure_can_view(db, user, position)
    copy = await svc.duplicate_position(db, position, user)
    names = await _names(db, {copy.owner_id} if copy.owner_id else set())
    return PositionOut(
        id=copy.id,
        name=copy.name,
        status=copy.status,
        owner_id=copy.owner_id,
        owner_name=names.get(copy.owner_id, "") if copy.owner_id else "",
        jd_version=copy.jd_version,
        jd_status=copy.jd_status,
        jd_completion=svc.jd_completion(copy),
        rounds=[_round_out(r, names) for r in sorted(copy.rounds, key=lambda x: x.seq)],
        copied_from_id=copy.copied_from_id,
        created_at=copy.created_at,
        updated_at=copy.updated_at,
        jd=_jd_out(copy),
    )
