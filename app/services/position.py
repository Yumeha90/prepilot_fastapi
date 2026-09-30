"""职位领域逻辑（PRD 3.2）。

三条核心规则落在这里：
- BR-01  JD 拆解必须人工确认才生效（未确认只写 `jd_draft_json`）
- BR-20  核心能力 ≥3 项、权重步进 5、合计必须 = 100
- BR-23  轮次 ≤4、每类型至多 1 个、末位须 hr/offer、允许跳过 HR 面

另有两个 2026-09-30 定下的实现口径：
- `jd_hash` 规范化摘要：只改原文/名称/状态**不 bump 版本**（避免空版本刷屏）
- 流程配置变更**只对未来应聘记录生效**，已派单会话不跟随（前端保存时显式提示）
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, bad_request, forbidden, not_found
from app.models.position import JdVersion, Position, PositionRound
from app.models.user import User
from app.schemas.position import CompetencyIn, JdIn, RoundIn

# 轮次类型与上限（BR-23）
ROUND_TYPES = ("r1", "r2", "hr", "offer")
MAX_ROUNDS = 4
TERMINAL_TYPES = ("hr", "offer")

# 轮次默认名称（前端走 i18n，这里只兜底）
ROUND_DEFAULT_NAME = {"r1": "技术一面", "r2": "技术二面", "hr": "HR 面试", "offer": "Offer 审批"}

STATUS_TRANSITIONS = {
    "draft": {"open", "closed"},
    "open": {"paused", "closed"},
    "paused": {"open", "closed"},
    "closed": {"open"},
}


# ---------------------------------------------------------------- JD 哈希与校验


# 模型输出常见的脏前缀：编号（1. / 1、）、项目符号（- * •）、
# 以及孤立的前导字母（实测出现过 `L本科及以上`、`LGo/Python`）。
# 匹配规则刻意保守：单个字母后必须紧跟中文或大写字母才算脏前缀，
# 因此 "Java" "Python" "R&D" "A/B 测试" 等正常条目不会被误伤。
_LEADING_JUNK_RE = re.compile(
    r"^(?:[0-9]+[.、)）:：]\s*|[-*•·▪◦]\s*|[A-Za-z](?=[\u4e00-\u9fff]|[A-Z]))"
)


def _norm_text(value: str) -> str:
    """规范化条目文本：去空白、去编号/项目符号/孤立前导脏字符。"""
    text = (value or "").strip()
    for _ in range(3):  # 可能叠多层，例如 "- 1. 本科及以上"
        stripped = _LEADING_JUNK_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return re.sub(r"\s+", " ", text).strip(" \t\"'「」『』")


def canonical_jd(
    hard_gates: list[str], competencies: list[CompetencyIn], bonuses: list[str]
) -> dict:
    """结构化字段的规范化表示。

    **刻意排序**：行序只影响展示，不影响权重与得分（PRD §3.2.2），
    因此 HR 用 ▲/▼ 调整顺序不应触发版本 bump。
    """
    return {
        "hard_gates": sorted(_norm_text(x) for x in hard_gates),
        "competencies": sorted(
            (_norm_text(c.text), int(c.weight)) for c in competencies
        ),
        "bonuses": sorted(_norm_text(x) for x in bonuses),
    }


def jd_hash_of(
    hard_gates: list[str], competencies: list[CompetencyIn], bonuses: list[str]
) -> str:
    payload = canonical_jd(hard_gates, competencies, bonuses)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _slug(text: str, used: set[str]) -> str:
    """为能力项生成稳定 slug（不能用数组下标，否则排序/删除会让 P09 矩阵行错位）。"""
    base = re.sub(r"[^a-z0-9]+", "-", _norm_text(text).lower()).strip("-") or "item"
    slug, i = base, 2
    while slug in used:
        slug = f"{base}-{i}"
        i += 1
    used.add(slug)
    return slug


def normalize_items(raw: list, kind: str) -> list[str]:
    """清洗条目列表：去空、去重、每类 ≤5 项（概要 C1「只拆不扩，每类 ≤ 5 项」）。"""
    seen: set[str] = set()
    out: list[str] = []
    for value in raw or []:
        if isinstance(value, dict):
            value = value.get("text", "")
        text = _norm_text(str(value))
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    if len(out) > 5:
        out = out[:5]
    return out


def normalize_competencies(raw: list[CompetencyIn]) -> list[dict]:
    """补齐 id，并把权重裁剪到合法范围。"""
    used: set[str] = set()
    items: list[dict] = []
    for c in raw or []:
        text = _norm_text(c.text)
        if not text:
            continue
        items.append(
            {
                "id": c.id or _slug(text, used),
                "text": text,
                "weight": max(0, min(100, int(c.weight))),
            }
        )
    return items


def normalize_bonuses(raw: list) -> list[str]:
    return normalize_items(raw, "bonus")


def validate_jd(hard_gates: list[str], competencies: list[CompetencyIn]) -> None:
    """BR-02 / BR-03 / BR-20。"""
    if len(hard_gates) < 1:
        raise bad_request(ErrorCode.JD_INVALID, "硬性门槛至少需要 1 项（BR-02）")
    if len(competencies) < 3:
        raise bad_request(ErrorCode.JD_INVALID, "核心能力至少需要 3 项（BR-03）")
    for c in competencies:
        if int(c.weight) % 5 != 0:
            raise bad_request(
                ErrorCode.JD_INVALID, f"权重必须为 5 的倍数：「{c.text}」当前 {c.weight}"
            )
    total = sum(int(c.weight) for c in competencies)
    if total != 100:
        raise bad_request(
            ErrorCode.JD_INVALID, f"核心能力权重合计必须等于 100，当前 {total}（BR-20）"
        )


def even_weights(count: int) -> list[int]:
    """一键均分：100 ÷ 项数，余数补给前 N 项。

    **必须落在 5 的倍数上**：BR-20 要求权重步进 5，而朴素的 `100 // count`
    在项数不是 2/4/5/10/20 时会产出非 5 倍数（3 项 → 34/33/33），
    前端点「一键均分」后直接点保存会被校验拦下。
    做法：先按「5 的份数」（100 ÷ 5 = 20 份）分配，再乘回 5。
    """
    if count <= 0:
        return []
    units, step = 20, 5  # 20 份 × 5 = 100
    base, remainder = divmod(units, count)
    return [(base + 1 if i < remainder else base) * step for i in range(count)]


# ---------------------------------------------------------------- 轮次校验（BR-23）


def validate_rounds(rounds: list[RoundIn]) -> None:
    """BR-23 六条约束。空列表允许（草稿阶段可先不配流程）。"""
    if not rounds:
        return
    if len(rounds) > MAX_ROUNDS:
        raise bad_request(
            ErrorCode.ROUNDS_INVALID, f"面试轮次最多 {MAX_ROUNDS} 轮，当前 {len(rounds)} 轮"
        )

    types = [r.type for r in rounds]
    if len(set(types)) != len(types):
        raise bad_request(ErrorCode.ROUNDS_INVALID, "每种轮次类型最多只能出现 1 次")
    if types[-1] not in TERMINAL_TYPES:
        raise bad_request(ErrorCode.ROUNDS_INVALID, "流程末位必须是 HR 面试或 Offer 轮")
    if not set(types) & set(TERMINAL_TYPES):
        raise bad_request(
            ErrorCode.ROUNDS_INVALID, "HR 面试与 Offer 轮至少需要存在一个（终止节点）"
        )
    if "r1" in types and "r2" in types and types.index("r1") > types.index("r2"):
        raise bad_request(ErrorCode.ROUNDS_INVALID, "技术一面（r1）必须排在二面（r2）之前")
    if "r2" in types and "r1" not in types:
        raise bad_request(ErrorCode.ROUNDS_INVALID, "配置了二面（r2）就必须先配一面（r1）")

    # PRD 只对 r1/r2/offer 强制要求责任人；此处对 hr 一并要求，
    # 否则 3.3 派单时会「无面试官可派」，属于提前收紧而非新增规则。
    for r in rounds:
        if r.interviewer_id is None:
            raise bad_request(
                ErrorCode.ROUNDS_INVALID,
                f"「{r.name or ROUND_DEFAULT_NAME[r.type]}」必须指定面试官",
            )


# ---------------------------------------------------------------- 可见性（数据范围）


async def visible_positions(
    db: AsyncSession, user: User, scopes: dict[str, str]
) -> list[int] | None:
    """返回该用户可见的职位 id 列表；None 表示不限制（all）。"""
    scope = scopes.get("position", "all")
    if scope == "all":
        return None
    rows = await db.scalars(
        select(PositionRound.position_id).where(
            PositionRound.interviewer_id == user.id
        )
    )
    return list(set(rows))


async def ensure_can_view(db: AsyncSession, user: User, position: Position) -> None:
    from app.services.rbac import user_data_scopes

    scopes = await user_data_scopes(db, user)
    allowed = await visible_positions(db, user, scopes)
    if allowed is not None and position.id not in allowed:
        raise forbidden("无权查看该职位")


# ---------------------------------------------------------------- 读写


async def get_position(db: AsyncSession, position_id: int) -> Position:
    position = await db.get(Position, position_id)
    if position is None:
        raise not_found("职位不存在")
    return position


def jd_completion(position: Position) -> int:
    """JD 完成度（0–100）：原文 40% + 门槛 20% + 能力权重 40%。"""
    score = 0
    if position.jd_raw_text.strip():
        score += 40
    if position.jd_hard_gates:
        score += 20
    comps = position.jd_competencies or []
    total = sum(int(c.get("weight", 0)) for c in comps)
    if comps and total == 100:
        score += 40
    elif comps:
        score += int(40 * min(total, 100) / 100)
    return score


async def count_active_candidates(db: AsyncSession, position_id: int) -> int:
    """在流程候选人数。3.3 引入 applications 表后改为真实查询。"""
    return 0


async def save_rounds(db: AsyncSession, position: Position, rounds: list[RoundIn]) -> None:
    """整体替换轮次（前端按 ▲/▼ 排好序后一次性提交）。"""
    validate_rounds(rounds)
    for row in list(position.rounds):
        await db.delete(row)
    await db.flush()

    for seq, r in enumerate(rounds, start=1):
        db.add(
            PositionRound(
                position_id=position.id,
                seq=seq,
                name=r.name or ROUND_DEFAULT_NAME.get(r.type, ""),
                type=r.type,
                interviewer_id=r.interviewer_id,
            )
        )
    position.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(position)


async def save_jd(
    db: AsyncSession,
    position: Position,
    hard_gates: list[str],
    competencies: list[CompetencyIn],
    bonuses: list[str],
    raw_text: str,
    confirm: bool,
    user: User,
) -> Position:
    """保存 JD。

    confirm=False → 只写草稿区（jd_draft_json），不生效、不 bump；
    confirm=True  → 校验（BR-02/03/20）后写正式字段，
                    结构化摘要变化时 jd_version+1 并落 jd_versions 快照。
    """
    gates = normalize_items(hard_gates, "hard_gate")
    comps_raw = normalize_competencies(competencies)
    bonus = normalize_bonuses(bonuses)

    if not confirm:
        position.jd_raw_text = raw_text or position.jd_raw_text
        position.jd_draft_json = {
            "hard_gates": gates,
            "competencies": comps_raw,
            "bonuses": bonus,
        }
        position.jd_status = "draft" if (gates or comps_raw or bonus) else "empty"
        position.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(position)
        return position

    comps_in = [CompetencyIn(**c) for c in comps_raw]
    validate_jd(gates, comps_in)

    # had_hash 必须在覆写 jd_hash 之前取：库里没有摘要说明是**首次确认**，
    # 此时应保持 jd_version = 1 而不是 +1（否则列表会显示「已确认 v2」但只确认过一次）。
    had_hash = bool(position.jd_hash)
    new_hash = jd_hash_of(gates, comps_in, bonus)
    changed = new_hash != position.jd_hash

    position.jd_raw_text = raw_text or position.jd_raw_text
    position.jd_hard_gates = gates
    position.jd_competencies = comps_raw
    position.jd_bonuses = bonus
    position.jd_draft_json = None
    position.jd_status = "confirmed"

    if changed:
        position.jd_hash = new_hash
        if had_hash:
            position.jd_version = int(position.jd_version) + 1
        db.add(
            JdVersion(
                position_id=position.id,
                version=position.jd_version,
                snapshot_json={
                    "hard_gates": gates,
                    "competencies": comps_raw,
                    "bonuses": bonus,
                },
                change_summary="JD 结构化变更" if had_hash else "JD 首次确认",
                changed_by=user.id,
            )
        )
        # 3.3/P18 接入后在此把存量匹配分置 STALE：
        # UPDATE match_scores SET stale = true WHERE position_id = :id
    position.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(position)
    return position


async def duplicate_position(db: AsyncSession, position: Position, user: User) -> Position:
    """BR-24 复制职位：带 JD 与轮次骨架，**面试官人选一律清空**，须重新指派。

    不带：候选人 / 面评 / 会话 / 匹配结果 / jdVersion 历史。
    """
    copy = Position(
        name=f"{position.name} - 副本",
        status="draft",
        owner_id=user.id,
        jd_raw_text=position.jd_raw_text,
        jd_hard_gates=list(position.jd_hard_gates or []),
        jd_competencies=[dict(c) for c in (position.jd_competencies or [])],
        jd_bonuses=list(position.jd_bonuses or []),
        jd_status=position.jd_status,
        jd_version=1,
        jd_hash=position.jd_hash,
        copied_from_id=position.id,
    )
    db.add(copy)
    await db.flush()

    # 轮次骨架：保留顺序与类型，清空面试官
    for r in sorted(position.rounds, key=lambda x: x.seq):
        db.add(
            PositionRound(
                position_id=copy.id,
                seq=r.seq,
                name=r.name,
                type=r.type,
                interviewer_id=None,
            )
        )

    if position.jd_status == "confirmed":
        db.add(
            JdVersion(
                position_id=copy.id,
                version=1,
                snapshot_json={
                    "hard_gates": copy.jd_hard_gates,
                    "competencies": copy.jd_competencies,
                    "bonuses": copy.jd_bonuses,
                },
                change_summary="由职位复制创建",
                changed_by=user.id,
            )
        )
    await db.commit()
    await db.refresh(copy)
    return copy


async def preview_save(
    db: AsyncSession, position: Position, jd: JdIn | None, rounds: list[RoundIn]
) -> dict:
    """保存前预检：判定 JD / 轮次是否实质变更，并顺带做一次校验。

    与真正保存走同一套规范化与校验，避免「前端判一遍、后端判一遍」结果不一致。
    """
    jd_changed = False
    jd_error = ""

    if jd is not None:
        gates = normalize_items(jd.hard_gates, "hard_gate")
        comps_raw = normalize_competencies(jd.competencies)
        bonus = normalize_bonuses(jd.bonuses)
        try:
            validate_jd(gates, [CompetencyIn(**c) for c in comps_raw])
        except AppError as exc:
            jd_error = str(exc.detail.get("message", ""))
        else:
            jd_changed = (
                jd_hash_of(gates, [CompetencyIn(**c) for c in comps_raw], bonus)
                != position.jd_hash
            )

    rounds_error = ""
    rounds_changed = False
    try:
        validate_rounds(rounds)
    except AppError as exc:
        rounds_error = str(exc.detail.get("message", ""))
    else:
        current = [
            (r.type, r.interviewer_id)
            for r in sorted(position.rounds, key=lambda x: x.seq)
        ]
        incoming = [(r.type, r.interviewer_id) for r in rounds]
        rounds_changed = current != incoming

    return {
        "jd_changed": jd_changed,
        "rounds_changed": rounds_changed,
        "affected_candidates": await count_active_candidates(db, position.id),
        "jd_error": jd_error,
        "rounds_error": rounds_error,
    }


async def can_delete(db: AsyncSession, position: Position) -> tuple[bool, str]:
    """仅草稿可真删除；非草稿一律只能关闭（2026-09-30 定）。"""
    if position.status != "draft":
        return False, "只有草稿状态的职位可以删除，其余请改为「关闭」"
    return True, ""


async def change_status(db: AsyncSession, position: Position, target: str) -> Position:
    allowed = STATUS_TRANSITIONS.get(position.status, set())
    if target not in allowed:
        raise bad_request(
            ErrorCode.POSITION_STATUS_INVALID,
            f"不允许从「{position.status}」变更为「{target}」",
        )
    position.status = target
    position.closed_at = (
        datetime.now(timezone.utc) if target == "closed" else None
    )
    position.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(position)
    return position


async def check_publish_ready(db: AsyncSession, position: Position) -> None:
    """发布（草稿 → 招聘中）前置校验。"""
    if position.jd_status != "confirmed":
        raise bad_request(
            ErrorCode.PUBLISH_BLOCKED, "请先完成 JD 拆解并点击「确认」（BR-01）"
        )
    gates = list(position.jd_hard_gates or [])
    comps = [CompetencyIn(**c) for c in (position.jd_competencies or [])]
    validate_jd(gates, comps)

    # 草稿阶段允许先不配流程（validate_rounds 放行空列表），但「发布」意味着
    # 开始接收候选人并派单，没有流程会直接卡住 3.3 派单 —— 这里额外收紧。
    if not position.rounds:
        raise bad_request(
            ErrorCode.ROUNDS_INVALID, "发布前请先配置面试流程，末位须为 HR 面试或 Offer 审批"
        )
    validate_rounds(
        [
            RoundIn(name=r.name, type=r.type, interviewer_id=r.interviewer_id)
            for r in sorted(position.rounds, key=lambda x: x.seq)
        ]
    )


async def list_positions(
    db: AsyncSession,
    user: User,
    scopes: dict[str, str],
    *,
    status: str | None = None,
    owner_id: int | None = None,
    keyword: str = "",
    include_closed: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Position], int]:
    stmt = select(Position)
    count_stmt = select(func.count()).select_from(Position)

    allowed = await visible_positions(db, user, scopes)
    if allowed is not None:
        stmt = stmt.where(Position.id.in_(allowed))
        count_stmt = count_stmt.where(Position.id.in_(allowed))

    if status:
        stmt = stmt.where(Position.status == status)
        count_stmt = count_stmt.where(Position.status == status)
    elif not include_closed:
        stmt = stmt.where(Position.status != "closed")
        count_stmt = count_stmt.where(Position.status != "closed")

    if owner_id:
        stmt = stmt.where(Position.owner_id == owner_id)
        count_stmt = count_stmt.where(Position.owner_id == owner_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Position.name.ilike(like))
        count_stmt = count_stmt.where(Position.name.ilike(like))

    total = int(await db.scalar(count_stmt) or 0)
    rows = list(
        await db.scalars(
            stmt.order_by(Position.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total
