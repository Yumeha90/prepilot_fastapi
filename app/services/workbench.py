"""AI 备面工作台（PRD 3.4）—— 地基 + Step 1 能力-证据矩阵（C3）。

**权限口径（2026-10-01 拍板）**：只有**被指派的面试官**能写；HR 与 HR 主管
（`workbench:enter_all` / `enter_view`）进来只能看。理由：面评与考察重点由面试官撰写，
HR 代填会让「谁是这份面评的责任人」变得含糊，也不符合 BR-17 的分角色可见性。
会话 `SUBMITTED` 之后连面试官本人都只能回看 —— 提交是终态动作。

**矩阵生成走纯 LLM（C3），不检索任何词表**：PRD §6.6 给 C3 的定位就是
「JD 能力 × 简历证据 → 考察重点」，没有外部知识可检索；上 RAG 只会引入噪声。
（对比：C4 问题链要检索组织技术资产库，C5 公平性要检索合规案例库 —— 那两条才需要 Milvus。）

**失败策略**：LLM 失败直接抛错，不做「假装成功」的兜底（与全项目同一口径）。
矩阵是备面的起点，给一份编造的矩阵比报错更糟。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import structured_call
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request, not_found
from app.models.candidate import Candidate
from app.models.position import Position
from app.models.session import (
    EVIDENCE_MISSING,
    EVIDENCE_STATUSES,
    EVIDENCE_VERIFY,
    ROW_SOURCE_JD,
    ROW_SOURCE_MANUAL,
    ROW_SOURCE_RESUME,
    ROW_SOURCES,
    SUBMITTED,
    S1_DRAFT,
    S2_DRAFT,
    S3_DRAFT,
    S4_DRAFT,
    S5_DRAFT,
    InterviewSession,
)
from app.models.user import User
from app.schemas.workbench import (
    DurationIn,
    MatrixIn,
    MatrixOut,
    MatrixRow,
    MatrixSaveOut,
    WorkbenchOut,
    WorkbenchStep,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# 矩阵行数上限：JD 能力项一般 3–6 条，给 AI 从简历补充留一倍余量。
# 超过这个数面试官在 45 分钟里根本问不完，矩阵就失去「聚焦」的意义。
MAX_ROWS = 12
MAX_CAPABILITY_LEN = 100
MAX_EVIDENCE_LEN = 300
MAX_FOCUS_LEN = 200

# 喂给模型的简历原文上限：一次结构化调用装得下即可，
# 超长简历本来已在 C2 分块解析过，这里用解析结果 + 原文前段足够定位证据
RESUME_SNIPPET_CHARS = 6_000

# Step1–Step5 的步骤指示器：本期只实现第 1 步，
# 后面几步标记为 disabled 而不是 todo —— 点了报错比点了没反应更糟
STEP_KEYS = ("step1", "step2", "step3", "step4", "step5")
IMPLEMENTED_STEPS = 1
STATUS_STEP_INDEX = {
    S1_DRAFT: 0,
    S2_DRAFT: 1,
    S3_DRAFT: 2,
    S4_DRAFT: 3,
    S5_DRAFT: 4,
}


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 权限


def can_edit(user: User, session: InterviewSession) -> bool:
    """只有被指派给本人的面试官、且会话未提交时才能写。"""
    return session.interviewer_id == user.id and session.status != SUBMITTED


def read_only_reason(user: User, session: InterviewSession) -> str:
    if can_edit(user, session):
        return ""
    if session.status == SUBMITTED:
        return "submitted"
    return "read_only"


def ensure_can_edit(user: User, session: InterviewSession) -> None:
    if session.status == SUBMITTED:
        raise bad_request(ErrorCode.WORKBENCH_SUBMITTED, "该面评已提交，只能查看不能修改")
    if session.interviewer_id != user.id:
        raise bad_request(
            ErrorCode.WORKBENCH_READ_ONLY,
            "只有被指派的面试官可以编辑工作台内容（其他角色只读）",
        )


# ---------------------------------------------------------------- 视图


def _clean(text: str) -> str:
    """去编号 / 项目符号 / 多余空白（模型偶发吐出 `1. ` 这类前缀）。"""
    s = re.sub(r"^(?:[0-9]+[.、)）:：]\s*|[-*•·▪◦]\s*)", "", str(text or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def _rows_from_json(session: InterviewSession) -> tuple[list[dict], bool, str, str]:
    data = session.matrix_json or {}
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], False, "", ""
    out = [r for r in rows if isinstance(r, dict)]
    generated = bool(data.get("generated"))
    return out, generated, str(data.get("generated_at") or ""), str(data.get("model") or "")


def _matrix_out(session: InterviewSession) -> MatrixOut:
    raw_rows, generated, generated_at, model = _rows_from_json(session)
    rows: list[MatrixRow] = []
    for i, r in enumerate(raw_rows):
        rows.append(
            MatrixRow(
                id=str(r.get("id") or f"m{i + 1}"),
                source=str(r.get("source") or ROW_SOURCE_JD),
                capability=str(r.get("capability") or ""),
                weight=_as_weight(r.get("weight")),
                evidence=str(r.get("evidence") or ""),
                status=str(r.get("status") or EVIDENCE_MISSING),
                focus=str(r.get("focus") or ""),
                is_key=bool(r.get("is_key")),
            )
        )
    return MatrixOut(
        rows=rows,
        generated=generated,
        revision=session.matrix_revision or 0,
        updated_at=session.matrix_updated_at,
        generated_at=generated_at,
        model=model,
    )


def _as_weight(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _steps(session: InterviewSession) -> list[WorkbenchStep]:
    if session.status == SUBMITTED:
        return [WorkbenchStep(key=k, state="done") for k in STEP_KEYS]
    current = STATUS_STEP_INDEX.get(session.status, 0)
    out: list[WorkbenchStep] = []
    for i, key in enumerate(STEP_KEYS):
        if i < current:
            state = "done"
        elif i == current:
            state = "current"
        elif i < IMPLEMENTED_STEPS:
            state = "todo"
        else:
            state = "disabled"
        out.append(WorkbenchStep(key=key, state=state))
    return out


def _blocked_reason(candidate: Candidate | None, position: Position | None) -> str:
    """生成矩阵的前置：JD 已确认 + 简历有内容。缺任一都不该让面试官对着空气备面。"""
    if position is None or position.jd_status != "confirmed":
        return "jd_not_confirmed"
    if candidate is None:
        return "resume_missing"
    if not (candidate.resume_raw_text or "").strip() and not candidate.parsed_profile:
        return "resume_missing"
    return ""


async def workbench_view(
    db: AsyncSession, user: User, session_id: int
) -> WorkbenchOut:
    """P09 一屏数据：顶部信息条 + 矩阵 + 步骤指示器 + 可编辑标志。"""
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise not_found("会话不存在")
    from app.services import dispatch as dispatch_svc

    await dispatch_svc.ensure_can_view_session(db, user, session)

    candidate = await db.get(Candidate, session.candidate_id)
    position = await db.get(Position, session.position_id)
    interviewer = await db.get(User, session.interviewer_id)

    return WorkbenchOut(
        session_id=session.id,
        application_id=session.application_id,
        candidate_id=session.candidate_id,
        candidate_name=candidate.name if candidate else "",
        position_id=session.position_id,
        position_name=position.name if position else "",
        round_type=session.round_type,
        round_name=session.round_name,
        interviewer_id=session.interviewer_id,
        interviewer_name=(interviewer.full_name or interviewer.email) if interviewer else "",
        status=session.status,
        duration_minutes=session.duration_minutes,
        can_edit=can_edit(user, session),
        read_only_reason=read_only_reason(user, session),
        matrix=_matrix_out(session),
        steps=_steps(session),
        blocked_reason=_blocked_reason(candidate, position),
    )


# ---------------------------------------------------------------- C3 矩阵生成


class _RowLLM(BaseModel):
    capability: str = Field(default="", description="能力要求项（简短短语，不超过 20 字）")
    source: str = Field(default="jd", description="jd=来自岗位核心能力；resume=简历中体现但 JD 未列")
    evidence: str = Field(default="", description="简历中的证据摘要，尽量引用原话短句；没有依据则留空")
    status: str = Field(default="missing", description="sufficient=证据充足 / verify=待核实 / missing=缺失")
    focus: str = Field(default="", description="本轮考察重点：这一项在面试里具体要问出什么")


class _MatrixLLM(BaseModel):
    rows: list[_RowLLM] = Field(default_factory=list)


MATRIX_SYSTEM_PROMPT = """你是资深面试官，负责为一场面试准备「能力-证据矩阵」。

你会拿到：① 岗位的核心能力项（含权重）② 候选人的简历（结构化档案 + 原文片段）。
请逐项判断简历里的证据，并给出本轮的考察重点。

严格要求：
1. **只依据给定材料**，禁止编造简历里没有的经历、数字或技能。
2. `capability` 优先沿用岗位能力项的原文表述；不要改写岗位方已经定好的说法。
   最多补充 2 项「简历里有明确亮点、但岗位能力项没覆盖」的能力，标 `source=resume`。
3. `evidence` 是**证据摘要**，尽量引用简历原话中的短句（不超过 60 字），
   让面试官一眼看到依据在哪。判定为 missing 时留空字符串，不要写"无""未提及"之类的占位词。
4. `status` 三选一：
   - sufficient：简历中有明确、可直接采信的证据（项目/职责/技能栏直接命中）
   - verify：提到了但不足以采信（只出现关键词、或表述模糊、或需要面试中确认）
   - missing：简历中找不到依据
5. `focus` 写**这一项在面试里具体要问出什么**（STAR 式的追问方向，不超过 80 字），
   不要写"重点考察沟通能力"这种空话。证据充足时写"如何验证深度/边界"，
   缺失时写"如何判断是否具备/如何降低风险"。
6. 输出不超过 12 行，按权重从高到低排列（source=resume 的行排在最后）。"""


def _competency_lines(position: Position) -> str:
    comps = [c for c in (position.jd_competencies or []) if isinstance(c, dict)]
    if not comps:
        return ""
    lines = []
    for i, c in enumerate(comps, start=1):
        text = _clean(str(c.get("text") or ""))
        if not text:
            continue
        lines.append(f"{i}. {text}（权重 {c.get('weight') or 0}）")
    return "\n".join(lines)


def _resume_digest(candidate: Candidate) -> str:
    """把解析结果压成文本；解析结果缺失时退回简历原文前段。"""
    profile = candidate.parsed_profile or {}
    parts: list[str] = []
    if profile:
        basic = profile.get("basic") or {}
        bits = [
            f"姓名：{basic.get('name') or ''}",
            f"年限：{basic.get('years') or ''}",
            f"城市：{basic.get('location') or ''}",
        ]
        parts.append("【基本信息】" + "；".join(b for b in bits if b.split("：")[1]))
        skills = [str(s) for s in (profile.get("skills") or []) if str(s).strip()]
        if skills:
            parts.append("【技能】" + "、".join(skills[:30]))
        for w in (profile.get("work") or [])[:10]:
            line = " / ".join(
                x
                for x in (
                    str(w.get("company") or ""),
                    str(w.get("title") or ""),
                    str(w.get("period") or ""),
                )
                if x
            )
            desc = str(w.get("desc") or "")
            if line or desc:
                parts.append(f"【经历】{line}：{desc[:300]}")
        for p in (profile.get("projects") or [])[:10]:
            name = str(p.get("name") or "")
            desc = str(p.get("desc") or "")
            if name or desc:
                parts.append(f"【项目】{name}：{desc[:300]}")
        for e in (profile.get("education") or [])[:5]:
            line = " ".join(
                x
                for x in (
                    str(e.get("school") or ""),
                    str(e.get("major") or ""),
                    str(e.get("degree") or ""),
                )
                if x
            )
            if line:
                parts.append(f"【教育】{line}")
    raw = (candidate.resume_raw_text or "").strip()
    if raw:
        parts.append("【简历原文片段】" + raw[:RESUME_SNIPPET_CHARS])
    return "\n".join(parts)


def _normalize_rows(rows: list[_RowLLM], position: Position) -> list[dict]:
    """模型输出 → 存储结构。去重、截断、状态兜底、按 JD 权重补 weight。"""
    weight_by_text: dict[str, float] = {}
    for c in position.jd_competencies or []:
        if not isinstance(c, dict):
            continue
        text = _clean(str(c.get("text") or ""))
        if text:
            try:
                weight_by_text[text.lower()] = float(c.get("weight") or 0)
            except (TypeError, ValueError):
                weight_by_text[text.lower()] = 0.0

    out: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        capability = _clean(r.capability)[:MAX_CAPABILITY_LEN]
        if not capability:
            continue
        key = capability.lower()
        if key in seen:
            continue
        seen.add(key)

        status = str(r.status or "").strip().lower()
        if status not in EVIDENCE_STATUSES:
            # 未知取值一律降级为「待核实」：宁可让面试官再看一眼，
            # 也不能自信地判成充足或缺失
            status = EVIDENCE_VERIFY
        source = str(r.source or "").strip().lower()
        if source not in (ROW_SOURCE_JD, ROW_SOURCE_RESUME):
            source = ROW_SOURCE_JD
        out.append(
            {
                "id": f"m{i + 1}",
                "source": source,
                "capability": capability,
                "weight": weight_by_text.get(key),
                "evidence": _clean(r.evidence)[:MAX_EVIDENCE_LEN],
                "status": status,
                "focus": _clean(r.focus)[:MAX_FOCUS_LEN],
                "is_key": False,
            }
        )
        if len(out) >= MAX_ROWS:
            break
    return out


def _merge_keys(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    """重新生成时保留面试官已经勾过的「本轮重点」。

    按能力项名匹配（不按 id：id 每次生成都重排）。不保留的话，
    面试官勾完重点、想再生成一次看看有没有漏项，勾选项就被冲掉了 ——
    那是纯属工具设计问题造成的返工。
    """
    key_by_name = {
        str(r.get("capability") or "").strip().lower(): bool(r.get("is_key"))
        for r in old_rows
    }
    for r in new_rows:
        name = str(r.get("capability") or "").strip().lower()
        if name in key_by_name:
            r["is_key"] = key_by_name[name]
    return new_rows


async def generate_matrix(
    db: AsyncSession, user: User, session: InterviewSession
) -> MatrixSaveOut:
    """调用 C3 生成矩阵并落库（覆盖旧矩阵，保留已勾选的本轮重点）。"""
    ensure_can_edit(user, session)
    candidate = await db.get(Candidate, session.candidate_id)
    position = await db.get(Position, session.position_id)
    reason = _blocked_reason(candidate, position)
    if reason == "jd_not_confirmed":
        raise bad_request(
            ErrorCode.WORKBENCH_NO_JD, "该职位的 JD 还未确认，无法生成能力-证据矩阵"
        )
    if reason == "resume_missing":
        raise bad_request(
            ErrorCode.WORKBENCH_NO_RESUME, "候选人简历为空或已粉碎，无法生成能力-证据矩阵"
        )

    assert candidate is not None and position is not None  # _blocked_reason 已保证
    comp_lines = _competency_lines(position)
    if not comp_lines:
        raise bad_request(
            ErrorCode.WORKBENCH_NO_JD, "该职位还没有确认的核心能力项，无法生成能力-证据矩阵"
        )

    prompt = (
        f"岗位：{position.name}\n\n"
        f"【岗位核心能力项】\n{comp_lines}\n\n"
        f"【候选人简历】\n{_resume_digest(candidate)}"
    )
    data = await _call_llm(prompt)
    rows = _normalize_rows(list(data.rows), position)
    if not rows:
        raise bad_request(ErrorCode.LLM_FAILED, "AI 未产出有效的能力项，请重试")

    old_rows, _, _, _ = _rows_from_json(session)
    rows = _merge_keys(old_rows, rows)
    return await _persist(db, session, rows, generated=True)


async def _call_llm(prompt: str) -> _MatrixLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _MatrixLLM,
        MATRIX_SYSTEM_PROMPT,
        prompt,
        timeout=120,
    )


# ---------------------------------------------------------------- 保存


async def save_matrix(
    db: AsyncSession, user: User, session: InterviewSession, payload: MatrixIn
) -> MatrixSaveOut:
    """人工编辑后的整块保存。"""
    ensure_can_edit(user, session)
    if not payload.rows:
        raise bad_request(ErrorCode.WORKBENCH_MATRIX_INVALID, "矩阵不能为空")

    rows: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(payload.rows):
        capability = _clean(r.capability)[:MAX_CAPABILITY_LEN]
        if not capability:
            continue
        if capability.lower() in seen:
            continue
        seen.add(capability.lower())
        source = r.source if r.source in ROW_SOURCES else ROW_SOURCE_MANUAL
        status = r.status if r.status in EVIDENCE_STATUSES else EVIDENCE_VERIFY
        rows.append(
            {
                "id": _clean(r.id) or f"m{i + 1}",
                "source": source,
                "capability": capability,
                "weight": _as_weight(r.weight),
                "evidence": _clean(r.evidence)[:MAX_EVIDENCE_LEN],
                "status": status,
                "focus": _clean(r.focus)[:MAX_FOCUS_LEN],
                "is_key": bool(r.is_key),
            }
        )
        if len(rows) >= MAX_ROWS:
            break
    if not rows:
        raise bad_request(ErrorCode.WORKBENCH_MATRIX_INVALID, "矩阵至少要有 1 个能力项")

    stale = payload.revision is not None and payload.revision != (session.matrix_revision or 0)
    return await _persist(db, session, rows, generated=bool(session.matrix_json), stale=stale)


async def update_duration(
    db: AsyncSession, user: User, session: InterviewSession, payload: DurationIn
) -> int:
    ensure_can_edit(user, session)
    minutes = int(payload.duration_minutes)
    if not (10 <= minutes <= 240):
        raise bad_request(ErrorCode.WORKBENCH_DURATION_INVALID, "面试时长需在 10~240 分钟之间")
    session.duration_minutes = minutes
    session.updated_at = _now()
    await db.commit()
    return minutes


async def _persist(
    db: AsyncSession,
    session: InterviewSession,
    rows: list[dict],
    *,
    generated: bool,
    stale: bool = False,
) -> MatrixSaveOut:
    """落库并 commit —— 与 match.compute 同一口径：写库这件事服务自己负责，
    路由只管编排。之前踩过「flush 后回滚，重算返回 CURRENT 但库里只有旧 STALE」。
    """
    now = _now()
    data: dict[str, Any] = dict(session.matrix_json or {})
    data["rows"] = rows
    data["generated"] = generated
    if generated:
        data["generated_at"] = now.isoformat()
        data["model"] = settings.LLM_MODEL
    session.matrix_json = data
    session.matrix_revision = (session.matrix_revision or 0) + 1
    session.matrix_updated_at = now
    session.updated_at = now
    await db.commit()
    await db.refresh(session)
    return MatrixSaveOut(matrix=_matrix_out(session), stale=stale)


async def get_session_or_404(db: AsyncSession, session_id: int) -> InterviewSession:
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise not_found("会话不存在")
    return session


async def sessions_of(db: AsyncSession, ids: list[int]) -> list[InterviewSession]:
    if not ids:
        return []
    rows = await db.scalars(
        select(InterviewSession).where(InterviewSession.id.in_(ids)).order_by(
            InterviewSession.id.desc()
        )
    )
    return list(rows)
