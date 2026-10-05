"""Step 4 行为化评分与面评（PRD §3.4.4 / §6.6 P12，AI 链路 C6）。

四条口径：

**① 自动保存而不是「记得点保存」（BR-13）**
面试结束后面试官还要赶下一场，让他记得点保存是不现实的。
所以草稿是「整块提交 + 定时落库」，保存失败也要能被看见（不能假装成功）。
`revision` 与矩阵同一口径：**只提示不拦截** —— 自动保存被 409 拦下来，
面试官会发现自己写了半小时的东西根本没存上，那是最坏的结果。

**② 完整性只提示，不拦截保存（BR-07）**
「每能力项至少 1 条证据或 1 条快记笔记」是**提交前**的要求（Step5 才拦），
Step4 中途保存时必须放行 —— 面试官是先记重点、后补证据的，
写一半就被拦在门外等于逼他先编一条证据出来。所以这里只把缺哪些项算出来给前端提示。

**③ AI 润色不覆盖原文（BR-08）**
润色稿与原文并存，面试官显式「采纳」才覆写；采纳时把原文留进
`summary_original`，P17 要做「原文 / 润色稿双栏对比」，覆写后原文就没了。

**④ 润色稿要过二次合规扫描（PRD §6.6）**
面试题拦住了、面评里却写出「年纪偏大」，等于把风险换个地方落地。
命中落进 `polished_flags` 常驻警示，采纳由面试官确认（AI 也会犯错，
但把改写权完全收回也不合理 —— 他自己改一句就合规了）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ai import fairness_rules
from app.ai.llm import structured_call
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request
from app.models.position import Position
from app.models.session import (
    MAX_EVIDENCES_PER_ITEM,
    MAX_EVIDENCE_LEN,
    MAX_NOTE_LEN,
    MAX_SUMMARY_LEN,
    SCORE_MAX,
    SCORE_MIN,
    S3_DRAFT,
    S4_DRAFT,
    InterviewSession,
)
from app.models.user import User
from app.observability import metrics, tracing
from app.schemas.workbench import (
    EvaluationFlag,
    EvaluationIn,
    EvaluationItem,
    EvaluationOut,
    EvaluationSaveOut,
    EvidenceItem,
)
from app.services.workbench import clean_text, ensure_can_edit

logger = logging.getLogger(__name__)
settings = get_settings()

# 缺什么：前端要能说清是「没打分」还是「没写依据」，一句话混着说面试官不知道该补哪个
MISSING_SCORE = "no_score"
MISSING_EVIDENCE = "no_evidence"

# 综合建议三态（PRD §6.6「建议推进 / 不推进 / 待定」）
RECOMMEND_PROCEED = "proceed"
RECOMMEND_HOLD = "hold"
RECOMMEND_REJECT = "reject"
RECOMMENDATIONS = (RECOMMEND_PROCEED, RECOMMEND_HOLD, RECOMMEND_REJECT)

MAX_POLISHED_LEN = 4_000


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 视图


def _items_from_json(session: InterviewSession) -> list[dict]:
    data = session.evaluation_json or {}
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


def _matrix_rows(session: InterviewSession) -> list[dict]:
    data = session.matrix_json or {}
    rows = data.get("rows") if isinstance(data, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def _item_out(raw: dict) -> EvaluationItem:
    evidences = [
        EvidenceItem(
            id=str(e.get("id") or f"e{i + 1}"),
            text=str(e.get("text") or ""),
            quote=bool(e.get("quote")),
        )
        for i, e in enumerate(raw.get("evidences") or [])
        if isinstance(e, dict)
    ]
    score = raw.get("score")
    missing: list[str] = []
    if score is None:
        missing.append(MISSING_SCORE)
    if not [e for e in evidences if e.text.strip()] and not str(raw.get("note") or "").strip():
        missing.append(MISSING_EVIDENCE)
    return EvaluationItem(
        row_id=str(raw.get("row_id") or ""),
        capability=str(raw.get("capability") or ""),
        score=int(score) if isinstance(score, int) else None,
        evidences=evidences,
        note=str(raw.get("note") or ""),
        complete=not missing,
        missing=missing,
    )


def _merged_items(session: InterviewSession) -> list[dict]:
    """矩阵行 ∪ 已存草稿：矩阵行变了要跟着变，但不能把已填的内容冲掉。

    以矩阵行为准排序（页面顺序与 Step1 一致），孤儿草稿项（矩阵里已删的行）
    仍然保留在末尾 —— 面试官写过的内容不因为矩阵调整就凭空消失。
    """
    stored = {str(i.get("row_id") or ""): i for i in _items_from_json(session)}
    out: list[dict] = []
    used: set[str] = set()
    for r in _matrix_rows(session):
        row_id = str(r.get("id") or "")
        item = dict(stored.get(row_id) or {})
        # 能力项名称以矩阵为准：矩阵里改了名字，评分表不该继续显示旧名字
        item["row_id"] = row_id
        item["capability"] = str(r.get("capability") or item.get("capability") or "")
        out.append(item)
        used.add(row_id)
    for row_id, item in stored.items():
        if row_id not in used:
            out.append(dict(item))
    return out


def evaluation_out(session: InterviewSession) -> EvaluationOut:
    data = session.evaluation_json or {}
    raw_items = _merged_items(session)
    items = [_item_out(i) for i in raw_items]
    incomplete = [i for i in items if not i.complete]
    flags = [
        EvaluationFlag(
            category=str(f.get("category") or ""),
            snippet=str(f.get("snippet") or ""),
            reason=str(f.get("reason") or ""),
        )
        for f in (data.get("polished_flags") or [])
        if isinstance(f, dict)
    ]
    return EvaluationOut(
        items=items,
        summary=str(data.get("summary") or ""),
        summary_original=str(data.get("summary_original") or ""),
        polished=str(data.get("polished") or ""),
        polished_at=str(data.get("polished_at") or ""),
        polished_model=str(data.get("polished_model") or ""),
        polished_adopted=bool(data.get("polished_adopted")),
        polished_flags=flags,
        recommendation=str(data.get("recommendation") or ""),
        revision=session.evaluation_revision or 0,
        updated_at=session.evaluation_updated_at,
        complete=bool(items) and not incomplete,
        incomplete_count=len(incomplete),
    )


# ---------------------------------------------------------------- 保存


def _normalize_item(raw: EvaluationItem) -> dict:
    evidences: list[dict] = []
    for i, e in enumerate(raw.evidences or []):
        text = str(e.text or "").strip()
        if not text:
            continue
        evidences.append(
            {
                "id": str(e.id or f"e{i + 1}"),
                "text": text[:MAX_EVIDENCE_LEN],
                "quote": bool(e.quote),
            }
        )
        if len(evidences) >= MAX_EVIDENCES_PER_ITEM:
            break
    score: int | None = None
    if raw.score is not None:
        value = int(raw.score)
        # 越界的评分直接丢弃而不是夹到边界：夹出来的是一个「看起来有效」的假分数
        if SCORE_MIN <= value <= SCORE_MAX:
            score = value
    return {
        "row_id": str(raw.row_id or ""),
        "capability": clean_text(raw.capability)[:100],
        "score": score,
        "evidences": evidences,
        "note": str(raw.note or "").strip()[:MAX_NOTE_LEN],
    }


@tracing.ai_step("evaluation.draft")
async def save_draft(
    db, user: User, session: InterviewSession, payload: EvaluationIn
) -> EvaluationSaveOut:
    """整块保存草稿（自动 / 手动共用）。

    **刻意不做完整性拦截**：写一半就被拦在门外，等于逼面试官先编一条证据出来。
    """
    ensure_can_edit(user, session)
    if not _matrix_rows(session):
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_NO_MATRIX, "还没有能力-证据矩阵，无法评分"
        )

    items = [_normalize_item(i) for i in payload.items]
    # 只保留能对上矩阵行的项：孤儿 row_id 存进去再也渲染不出来
    rows = {str(r.get("id") or "") for r in _matrix_rows(session)}
    items = [i for i in items if i["row_id"] in rows]

    stale = payload.revision is not None and payload.revision != (
        session.evaluation_revision or 0
    )
    return await _persist(db, session, items, str(payload.summary or ""), stale=stale)


def _complete(items: list[dict]) -> bool:
    """全部能力项都「有分 + 有证据或笔记」才算填完（BR-07 的口径）。

    落库而不是每次读的时候现算：Step5 提交时要拿它做校验，
    而「当时是否完整」是一个事实，不是视图 —— 现算会随矩阵变动而变化。
    """
    if not items:
        return False
    for i in items:
        if i.get("score") is None:
            return False
        has_evidence = any(
            str(e.get("text") or "").strip() for e in (i.get("evidences") or [])
        )
        if not has_evidence and not str(i.get("note") or "").strip():
            return False
    return True


async def _persist(
    db,
    session: InterviewSession,
    items: list[dict],
    summary: str,
    *,
    stale: bool = False,
    **extra: Any,
) -> EvaluationSaveOut:
    now = _now()
    data: dict[str, Any] = dict(session.evaluation_json or {})
    data["items"] = items
    data["summary"] = summary[:MAX_SUMMARY_LEN]
    data["complete"] = _complete(items)
    data.update(extra)
    session.evaluation_json = data
    session.evaluation_revision = (session.evaluation_revision or 0) + 1
    session.evaluation_updated_at = now
    session.updated_at = now
    # 有评分产物即推进 s3 → s4。只前进不回退：回头改评分不该把 Step4 打回未完成
    if session.status == S3_DRAFT:
        session.status = S4_DRAFT
    await db.commit()
    await db.refresh(session)
    return EvaluationSaveOut(evaluation=evaluation_out(session), stale=stale)


# ---------------------------------------------------------------- C6 面评润色


class _ItemLLM(BaseModel):
    capability: str = Field(default="", description="能力项名称，必须是给定能力项之一")
    score: int = Field(default=0, description="该能力项的评分 1–5，未评分填 0")
    evidence: str = Field(default="", description="支撑这个评分的关键证据，不超过 60 字")


class _PolishLLM(BaseModel):
    summary: str = Field(default="", description="总评：1–2 句定性，不超过 80 字")
    items: list[_ItemLLM] = Field(default_factory=list)
    recommendation: str = Field(default="hold", description="proceed=建议推进 / hold=待定 / reject=不推进")
    risks: list[str] = Field(
        default_factory=list, description="风险提示：跨能力项的矛盾或明显短板，没有就留空"
    )


POLISH_SYSTEM_PROMPT = """你是资深面试官的写作助手，负责把面试当场的口语化笔记整理成结构化面评。

你会拿到：岗位与轮次、各能力项的评分与证据（含面试官的快记笔记）、以及综合评价草稿。

输出结构：
1. `summary`：总评，1–2 句定性，不超过 80 字。
2. `items`：逐能力项给出评分与**支撑这个评分的关键证据**（每项不超过 60 字）。
3. `recommendation`：proceed（建议推进）/ hold（待定）/ reject（不推进）。
4. `risks`：跨能力项的矛盾或明显短板（例如"沟通项 4 分但系统设计 2 分"），没有就留空。

严格要求：
1. **不引入笔记里没有的事实**：不得编造经历、数字、年限、职级；
   笔记里没写的一律不写，宁可留空也不要"合理推断"。
2. **不使用主观臆断与受保护特征**：不得出现对婚育、年龄、性别、户籍地域民族宗教、
   健康与残疾的评价或猜测（例如"年纪偏大""女生扛不住"）—— 那不是面评，是风险。
3. `score` 照抄给定评分，未评分的填 0；不要自己调整分数，评分是面试官的判断。
4. 只做**表达层面**的整理：把口语转成书面语、把零散要点归到对应能力项下，
   不改变原意、不夸大也不淡化。
5. 语言跟随笔记原文（中文笔记就输出中文）。"""


def _score_label(score: int | None) -> str:
    return "未评分" if score is None else f"{score}/5"


def _polish_prompt(
    *,
    position: Position,
    round_name: str,
    items: list[EvaluationItem],
    summary: str,
) -> str:
    lines = []
    for i, it in enumerate(items, start=1):
        ev = "；".join(
            f"{'（原话）' if e.quote else ''}{e.text.strip()}"
            for e in it.evidences
            if e.text.strip()
        )
        lines.append(
            f"{i}. {it.capability or '—'}｜评分：{_score_label(it.score)}"
            f"｜证据：{ev or '（无）'}｜快记：{it.note.strip() or '（无）'}"
        )
    return (
        f"岗位：{position.name}｜轮次：{round_name or '本轮'}\n\n"
        f"【各能力项评分与笔记】\n" + ("\n".join(lines) or "（无）") + "\n\n"
        f"【综合评价草稿】\n{summary.strip() or '（无）'}\n\n"
        "请输出结构化面评（summary / items / recommendation / risks）。"
    )


async def _call_llm(prompt: str) -> _PolishLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _PolishLLM,
        POLISH_SYSTEM_PROMPT,
        prompt,
        timeout=120,
    )


def _plain(text: str) -> str:
    s = str(text or "").strip().replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[*#]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


RECOMMEND_TEXT = {
    RECOMMEND_PROCEED: "建议推进",
    RECOMMEND_HOLD: "待定",
    RECOMMEND_REJECT: "不推进",
}


def render_polished(data: _PolishLLM, items: list[EvaluationItem]) -> str:
    """结构化输出 → 一段可直接采纳的面评正文。

    渲染成文本而不是存 JSON：面试官要的是「一段能直接提交的面评」，
    再让他从 JSON 里自己拼一遍，等于没润色。
    """
    known = {str(it.capability or "").strip().lower(): it for it in items}
    blocks: list[str] = []
    summary = _plain(data.summary)
    if summary:
        blocks.append(f"【总评】{summary}")
    item_lines: list[str] = []
    for it in data.items:
        name = _plain(it.capability)
        if not name:
            continue
        source = known.get(name.lower())
        # 模型偶尔会把能力项改名（"系统设计能力" vs "系统设计"）—— 认不出就按模型给的写，
        # 但评分以面试官给的为准（评分是他的判断，模型无权改）
        score = source.score if source is not None and source.score is not None else None
        if score is None:
            try:
                value = int(it.score or 0)
                score = value if SCORE_MIN <= value <= SCORE_MAX else None
            except (TypeError, ValueError):
                score = None
        evidence = _plain(it.evidence)
        item_lines.append(
            f"- {name}（{_score_label(score)}）"
            + (f"：{evidence}" if evidence else "")
        )
    if item_lines:
        blocks.append("【能力项】\n" + "\n".join(item_lines))
    rec = RECOMMEND_TEXT.get(str(data.recommendation or "").strip().lower(), "")
    if rec:
        blocks.append(f"【综合建议】{rec}")
    risks = [_plain(r) for r in (data.risks or []) if _plain(r)]
    if risks:
        blocks.append("【风险提示】\n" + "\n".join(f"- {r}" for r in risks))
    return "\n\n".join(blocks)[:MAX_POLISHED_LEN]


@tracing.ai_step("evaluation.polish")
async def polish(db, user: User, session: InterviewSession) -> EvaluationOut:
    """C6 润色（同步，一次模型调用）。润色稿**不覆盖**原文（BR-08）。"""
    ensure_can_edit(user, session)
    items = evaluation_out(session).items
    if not items:
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_NO_MATRIX, "还没有可评分的能力项，无法润色"
        )
    if not any(
        it.score is not None or it.note.strip() or [e for e in it.evidences if e.text.strip()]
        for it in items
    ):
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_EMPTY, "还没有任何评分或笔记，没有可润色的内容"
        )

    position = await db.get(Position, session.position_id)
    prompt = _polish_prompt(
        position=position,
        round_name=session.round_name,
        items=items,
        summary=str((session.evaluation_json or {}).get("summary") or ""),
    )
    data = await _call_llm(prompt)
    text = render_polished(data, items)
    if not text.strip():
        raise bad_request(ErrorCode.LLM_FAILED, "AI 未产出有效的面评内容，请重试")

    recommendation = str(data.recommendation or "").strip().lower()
    if recommendation not in RECOMMENDATIONS:
        recommendation = RECOMMEND_HOLD

    # 二次合规扫描（PRD §6.6）：润色稿同样可能踩线，命中落库常驻警示
    flags = [
        {"category": h.category, "snippet": h.snippet, "reason": h.reason}
        for h in fairness_rules.scan_text(text)
    ]
    if flags:
        logger.warning("面评润色稿命中合规红线 %s 条：%s", len(flags), [f["category"] for f in flags])

    now = _now()
    await _persist(
        db,
        session,
        [i for i in _items_from_json(session)],
        str((session.evaluation_json or {}).get("summary") or ""),
        polished=text,
        polished_at=now.isoformat(),
        polished_model=settings.LLM_MODEL,
        polished_adopted=False,
        polished_flags=flags,
        recommendation=recommendation,
    )
    return evaluation_out(session)


async def adopt_polished(db, user: User, session: InterviewSession) -> EvaluationOut:
    """采纳润色稿（BR-08：只有显式采纳才覆写原文）。

    采纳时把正文留进 `summary_original` —— P17 要做「原文 / 润色稿双栏对比」，
    覆写完原文就再也拿不回来了。
    """
    ensure_can_edit(user, session)
    data = session.evaluation_json or {}
    text = str(data.get("polished") or "").strip()
    if not text:
        raise bad_request(ErrorCode.WORKBENCH_EVAL_NO_POLISHED, "还没有润色稿，请先生成")
    summary = str(data.get("summary") or "")
    original = str(data.get("summary_original") or "")
    if summary and not original:
        original = summary[:MAX_SUMMARY_LEN]
    await _persist(
        db,
        session,
        _items_from_json(session),
        text,
        summary_original=original,
        polished_adopted=True,
    )
    return evaluation_out(session)
