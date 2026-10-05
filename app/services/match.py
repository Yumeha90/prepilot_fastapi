"""人岗匹配评分（PRD §6.7）与解释生成（BR-21）。

**为什么打分不用 LLM**：BR-21 要求「总分可由依据复算得出」，LLM 给不了这个保证 ——
同一个输入两次调用可能给出不同分数，HR 追问"凭什么是 72 分"时无法回答。
所以**判定与加权全部走规则**（同步、可复算、零 token），LLM 只做**解释**
（把已经算好的中间结果翻译成人话），且**异步补写**（PRD v1.11 定的边界）：
分数先落库可用，总结没回来只显示"生成中"，不影响分数本身。

规则的三处语义判断（硬性门槛 / 能力项证据强度 / 加分项）都基于**关键词定位**：
- 命中越长的短语，证据越强（完整短语 > 3 字片段 > 2 字片段）
- 命中位置决定强度上限：技能栏 / 工作经历 ≥ 简历原文（后者只算"待核实"）
- 学历与年限走数值比较，简历没写则判"待核实"而不是"不满足"——
  信息缺失 ≠ 不满足，一票否决只在有明确反证时开火。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import structured_call
from app.core.config import get_settings
from app.models.candidate import CONFIRMED, Application, Candidate
from app.models.match_score import CURRENT, STALE, MatchScore
from app.models.position import Position
from app.observability import metrics, tracing

logger = logging.getLogger(__name__)
settings = get_settings()

# 算法版本写进每一行分数，反馈快照回溯时才知道当年按哪套规则算的
ALGORITHM_VERSION = "rule-v1"

# 证据强度 → 能力项得分（§6.7 第 2 步）
STRONG, VERIFY, WEAK, MISSING = 5, 3, 2, 0
LEVEL_NAME = {STRONG: "strong", VERIFY: "verify", WEAK: "weak", MISSING: "missing"}

# 加分项：命中 +2 / 项，加成上限 +10，总分封顶 100（§6.7 第 4 步）
BONUS_DELTA = 2
BONUS_CAP = 10
SCORE_CAP = 100

# 等级映射（§6.7 第 5 步）
def _tier_of(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "low"


# ---------------------------------------------------------------- 文本工具

_EN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,20}")
_ZH_TOKEN = re.compile(r"[\u4e00-\u9fa5]{2,}")
_YEAR_REQ = re.compile(r"(\d{1,2})\s*年以上")
_YEAR_ANY = re.compile(r"(\d{1,2})\s*年")
_DEGREES = (
    ("博士", 4),
    ("硕士", 3),
    ("研究生", 3),
    ("本科", 2),
    ("学士", 2),
    ("学士", 2),
    ("大专", 1),
    ("专科", 1),
)
# 门槛 / 能力项里的修饰词不参与检索，否则「熟悉」「优先」会命中任何简历
_STOPWORDS = {
    "熟悉",
    "精通",
    "掌握",
    "了解",
    "具备",
    "具有",
    "熟练",
    "以上",
    "以下",
    "优先",
    "经验",
    "能力",
    "良好",
    "较强",
    "扎实",
    "丰富",
    "相关",
    "能够",
    "可以",
    "负责",
    "参与",
    "使用",
    "运用",
    "以及",
    "或者",
    "并且",
    "要求",
    "需要",
}
# 技术词别名（只在直接检索失败时兜底，命中算"薄弱"）
_SYNONYMS = {
    "golang": ("go",),
    "go": ("golang",),
    "k8s": ("kubernetes",),
    "kubernetes": ("k8s",),
    "javascript": ("js", "es6"),
    "js": ("javascript",),
    "typescript": ("ts",),
    "ts": ("typescript",),
    "postgresql": ("postgres", "pg"),
    "postgres": ("postgresql",),
    "mongodb": ("mongo",),
    "mongo": ("mongodb",),
    "elasticsearch": ("es",),
    "redis": ("缓存",),
    "kafka": ("消息队列", "mq"),
    "rabbitmq": ("消息队列", "mq"),
    "mysql": ("关系型数据库", "sql"),
    "分布式": ("微服务",),
    "微服务": ("分布式",),
}


def _norm(text: str) -> str:
    return (text or "").strip()


def _phrases(text: str) -> list[str]:
    """把一条能力项 / 门槛拆成可检索的短语，**长短语在前**。

    中文没有分词，用滑窗生成 4 / 3 / 2 字片段；越长命中说明越是"整词命中"，
    证据强度越高。2 字片段最容易误伤（"设计""开发"到处都是），只给最低分。
    """
    out: list[str] = []
    for raw in _ZH_TOKEN.findall(text):
        if len(raw) >= 4:
            out.append(raw)
        for size in (4, 3, 2):
            for i in range(len(raw) - size + 1):
                piece = raw[i : i + size]
                if piece not in _STOPWORDS:
                    out.append(piece)
    for token in _EN_TOKEN.findall(text):
        if len(token) >= 2 and token.lower() not in {"and", "the", "with"}:
            out.append(token)
    # 去重并保持长短语优先
    seen: set[str] = set()
    dedup: list[str] = []
    for p in sorted(out, key=len, reverse=True):
        key = p.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(p)
    return dedup


def _level_of_hit(phrase: str) -> int:
    if _EN_TOKEN.fullmatch(phrase):
        return STRONG
    if len(phrase) >= 4:
        return STRONG
    if len(phrase) == 3:
        return VERIFY
    return WEAK


def _locate(raw: str, phrase: str) -> tuple[str, str]:
    """在简历原文里定位命中片段，返回 `(quote, segmentId)`。

    **定位不到就返回空**（§6.7 硬约束：quote 必须能在原文中找到，否则不写入）。
    segmentId 用行号 `L12`，前端可据此跳回 P05 对应段落。
    """
    idx = raw.lower().find(phrase.lower())
    if idx < 0:
        return "", ""
    line_no = raw.count("\n", 0, idx) + 1
    start = raw.rfind("\n", 0, idx) + 1
    end = raw.find("\n", idx)
    end = len(raw) if end < 0 else end
    line = raw[start:end].strip()
    quote = line if len(line) <= 120 else line[:117] + "…"
    return quote, f"L{line_no}"


def _profile_texts(profile: dict) -> dict[str, str]:
    """结构化档案按区块拼文本 —— 命中位置决定证据强度。"""
    p = profile or {}
    skills = " ".join(str(x) for x in (p.get("skills") or []))
    work = " ".join(
        f"{w.get('title', '')} {w.get('desc', '')} {w.get('company', '')}"
        for w in (p.get("work") or [])
        if isinstance(w, dict)
    )
    projects = " ".join(
        f"{x.get('name', '')} {x.get('desc', '')} {x.get('role', '')}"
        for x in (p.get("projects") or [])
        if isinstance(x, dict)
    )
    education = " ".join(
        f"{e.get('school', '')} {e.get('major', '')} {e.get('degree', '')}"
        for e in (p.get("education") or [])
        if isinstance(e, dict)
    )
    return {
        "skills": skills,
        "work": f"{work} {projects}",
        "education": education,
        "basic": str((p.get("basic") or {}).get("years", "")),
    }


# ---------------------------------------------------------------- 能力项打分


def _score_competency(
    comp: dict, texts: dict[str, str], raw: str
) -> tuple[int, str, str, float, str]:
    """返回 `(得分, 等级, quote, 置信度, segmentId)`。"""
    name = _norm(str(comp.get("text") or ""))
    phrases = _phrases(name)
    # 命中区域按强度从高到低：技能栏 / 工作经历 → 充足；简历原文 → 待核实
    for hay, level_cap in ((texts["skills"], STRONG), (texts["work"], STRONG), (raw, VERIFY)):
        if not hay:
            continue
        for phrase in phrases:
            if phrase.lower() in hay.lower():
                level = min(_level_of_hit(phrase), level_cap)
                quote, segment = _locate(raw, phrase)
                conf = {STRONG: 0.9, VERIFY: 0.6, WEAK: 0.5}[level]
                return level, LEVEL_NAME[level], quote, conf, segment

    # 直接检索没命中 → 试别名，命中算"薄弱"
    for phrase in phrases:
        for alias in _SYNONYMS.get(phrase.lower(), ()):  # type: ignore[arg-type]
            for hay in (texts["skills"], texts["work"], raw):
                if hay and alias.lower() in hay.lower():
                    quote, segment = _locate(raw, alias)
                    return WEAK, LEVEL_NAME[WEAK], quote, 0.5, segment
    return MISSING, LEVEL_NAME[MISSING], "", 0.0, ""


# ---------------------------------------------------------------- 硬性门槛


def _degree_level(text: str) -> tuple[int, str]:
    for name, level in _DEGREES:
        if name in text:
            return level, name
    return 0, ""


def _check_gate(gate: str, texts: dict[str, str], raw: str) -> tuple[str, str]:
    """判定一条硬性门槛。返回 `(pass|fail|unknown, 判定理由)`。

    unknown = 简历没写、无法核实 —— **不算不满足**（信息缺失 ≠ 不达标），
    只在页面标注"待人工核实"。
    """
    # 学历：比较等级
    req_level, req_name = _degree_level(gate)
    if req_level:
        got_level, got_name = _degree_level(texts["education"])
        if not got_level:
            return "unknown", f"简历未体现学历信息，无法核实「{gate}」"
        if got_level >= req_level:
            return "pass", f"简历学历为{got_name}，满足「{gate}」"
        return "fail", f"要求{req_name}及以上，简历学历为{got_name}"

    # 年限：数值比较
    m = _YEAR_REQ.search(gate)
    if m:
        required = int(m.group(1))
        found = _YEAR_ANY.search(texts["basic"] or "")
        if not found:
            return "unknown", f"简历未体现工作年限，无法核实「{gate}」"
        years = int(found.group(1))
        if years >= required:
            return "pass", f"简历工作年限 {years} 年，满足「{gate}」"
        return "fail", f"要求 {required} 年以上，简历为 {years} 年"

    # 其余（技能 / 资质）：关键词定位
    hay_all = f"{texts['skills']} {texts['work']} {raw}"
    for phrase in _phrases(gate):
        if phrase.lower() in hay_all.lower():
            return "pass", f"简历提到「{phrase}」，满足「{gate}」"
        for alias in _SYNONYMS.get(phrase.lower(), ()):  # type: ignore[arg-type]
            if alias.lower() in hay_all.lower():
                return "pass", f"简历提到「{alias}」，满足「{gate}」"
    return "fail", f"简历未提及「{gate}」"


# ---------------------------------------------------------------- 评分主流程


def _weighted(breakdown: list[dict]) -> float:
    """`score = 100 × Σ(wᵢ × sᵢ) / (5 × Σwᵢ)`（§6.7 第 3 步）。"""
    total_w = sum(float(b["weight"]) for b in breakdown)
    if total_w <= 0:
        return 0.0
    acc = sum(float(b["weight"]) * float(b["score"]) for b in breakdown)
    return round(100 * acc / (5 * total_w), 2)


def evaluate(candidate: Candidate, position: Position) -> dict[str, Any]:
    """纯函数：算一套匹配结果（不落库），便于测试与复算校验。"""
    profile = candidate.parsed_profile or {}
    raw = candidate.resume_raw_text or ""
    texts = _profile_texts(profile)

    comps = [c for c in (position.jd_competencies or []) if isinstance(c, dict)]
    gates = [str(g) for g in (position.jd_hard_gates or []) if str(g).strip()]
    bonuses_cfg = [str(b) for b in (position.jd_bonuses or []) if str(b).strip()]

    # 1) 一票否决前置（BR-19）：每条门槛只判一次，结果三处复用
    gates_checked = [(g, *_check_gate(g, texts, raw)) for g in gates]
    unknown = [
        {"gateId": f"g{i + 1}", "text": g, "reason": reason}
        for i, (g, state, reason) in enumerate(gates_checked)
        if state == "unknown"
    ]
    vetoed = [
        {"gateId": f"g{i + 1}", "text": g, "reason": reason}
        for i, (g, state, reason) in enumerate(gates_checked)
        if state == "fail"
    ]
    if vetoed:
        return {
            "score": 0.0,
            "tier": "vetoed",
            "vetoed_gates": vetoed,
            "unknown_gates": unknown,
            "breakdown": [],
            "evidences": [],
            "bonuses": [],
            "summary": veto_summary(vetoed, unknown),
            "summary_ready": True,
        }

    # 2) 能力项打分 + 3) 加权
    breakdown: list[dict] = []
    evidences: list[dict] = []
    for c in comps:
        cid = str(c.get("id") or "")
        name = _norm(str(c.get("text") or ""))
        weight = float(c.get("weight") or 0)
        s, level, quote, conf, segment = _score_competency(c, texts, raw)
        contribution = 0.0
        breakdown.append(
            {
                "competencyId": cid,
                "name": name,
                "weight": weight,
                "score": s,
                "contribution": contribution,
                "level": level,
            }
        )
        evidences.append(
            {
                "competencyId": cid,
                "segmentId": segment,
                "quote": quote,
                "confidence": conf,
            }
        )

    base = _weighted(breakdown)
    total_w = sum(float(b["weight"]) for b in breakdown) or 1.0
    for b in breakdown:
        b["contribution"] = round(
            100 * float(b["weight"]) * float(b["score"]) / (5 * total_w), 2
        )

    # 4) 加分项
    bonuses: list[dict] = []
    bonus_sum = 0
    hay_all = f"{texts['skills']} {texts['work']} {raw}"
    for i, b in enumerate(bonuses_cfg):
        # 与能力项同口径：先直接检索，再试别名（Golang ↔ Go）
        hit = any(p.lower() in hay_all.lower() for p in _phrases(b))
        if not hit:
            for p in _phrases(b):
                if any(
                    a.lower() in hay_all.lower() for a in _SYNONYMS.get(p.lower(), ())
                ):
                    hit = True
                    break
        if not hit:
            continue
        if bonus_sum >= BONUS_CAP:
            break
        delta = min(BONUS_DELTA, BONUS_CAP - bonus_sum)
        bonus_sum += delta
        bonuses.append({"id": f"b{i + 1}", "text": b, "delta": delta})

    score = round(min(base + bonus_sum, float(SCORE_CAP)), 2)
    return {
        "score": score,
        "tier": _tier_of(score),
        "vetoed_gates": [],
        "unknown_gates": unknown,
        "breakdown": breakdown,
        "evidences": evidences,
        "bonuses": bonuses,
        "summary": {},
        "summary_ready": False,
        "base_score": base,
        "bonus_sum": bonus_sum,
    }


def veto_summary(vetoed: list[dict], unknown: list[dict]) -> dict:
    """一票否决时**不调模型**：未满足项就是结论，规则直接产出（§6.7 硬约束）。"""
    names = "、".join(v["text"] for v in vetoed)
    conclusion = f"未满足硬性门槛 {len(vetoed)} 项（{names}），按规则判定为不匹配，不计算加权得分。"
    reasons = [f"{v['text']}：{v['reason']}" for v in vetoed]
    gaps = [v["text"] for v in vetoed]
    if unknown:
        reasons.append(
            "另有 " + "、".join(u["text"] for u in unknown) + " 无法从简历核实，建议人工确认"
        )
    return {
        "conclusion": conclusion,
        "reasons": reasons,
        "gaps": gaps,
        "suggestions": ["如需继续推进，请先与 HR 确认硬性门槛是否可放宽"],
    }


# ---------------------------------------------------------------- 落库


def _snapshot(row: MatchScore) -> dict:
    """反馈绑定的不可变快照（BR-22）。"""
    return {
        "score": float(row.score),
        "tier": row.tier,
        "jd_version": row.jd_version,
        "algorithm_version": row.algorithm_version,
        "model_version": row.model_version,
        "breakdown": row.breakdown or [],
        "evidences": row.evidences or [],
        "bonuses": row.bonuses or [],
        "vetoed_gates": row.vetoed_gates or [],
        "unknown_gates": row.unknown_gates or [],
        "summary": row.summary or {},
    }


@tracing.ai_step("match.compute")
async def compute(
    db: AsyncSession, application: Application, *, enqueue_explain: bool = True
) -> MatchScore:
    """算一次分并落库（同步部分）。旧行置 STALE 保留历史，新行插 CURRENT。

    解释默认**异步**补写（Celery），失败不影响分数可用。
    """
    candidate = await db.get(Candidate, application.candidate_id)
    if candidate is None:
        raise ValueError("候选人不存在")
    position = await db.get(Position, application.position_id)
    if position is None:
        raise ValueError("职位不存在")

    result = evaluate(candidate, position)

    # 旧行置 STALE：重算不删历史（§8.2），P18 才能回答"当年按哪版标准算的分"
    old = await db.scalars(
        select(MatchScore).where(
            MatchScore.application_id == application.id,
            MatchScore.status == CURRENT,
        )
    )
    for row in old:
        row.status = STALE

    row = MatchScore(
        application_id=application.id,
        candidate_id=candidate.id,
        position_id=position.id,
        jd_version=position.jd_version,
        score=result["score"],
        tier=result["tier"],
        algorithm_version=ALGORITHM_VERSION,
        model_version=settings.LLM_MODEL,
        status=CURRENT,
        vetoed_gates=result["vetoed_gates"],
        unknown_gates=result.get("unknown_gates", []),
        breakdown=result["breakdown"],
        evidences=result["evidences"],
        bonuses=result["bonuses"],
        summary=result.get("summary") or {},
        summary_status="ready" if result.get("summary_ready") else "pending",
    )
    db.add(row)
    # 自己 commit：调用方（确认流程 / 重算接口）不该记得替算分提交，
    # 否则"分数算出来了但事务回滚了"这种半截状态极难排查
    await db.commit()
    await db.refresh(row)

    if enqueue_explain and row.summary_status == "pending":
        _enqueue_explain(row.id)
    return row


def _enqueue_explain(match_score_id: int) -> None:
    """丢给 Celery。**派发失败只记日志** —— 分数已经落库，不能因为队列不通让整次算分失败。"""
    try:
        from app.tasks.tasks import explain_match

        explain_match.delay(match_score_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("匹配分 %s 的解释任务派发失败：%s", match_score_id, exc)


def _explain_payload(row: MatchScore, candidate: Candidate, position: Position) -> str:
    return (
        f"岗位：{position.name}\n"
        f"JD 版本：v{row.jd_version}\n"
        f"总分：{row.score}（{row.tier}）\n"
        f"能力项构成：{row.breakdown}\n"
        f"证据引用：{row.evidences}\n"
        f"加分项：{row.bonuses}\n"
        f"未满足硬性门槛：{row.vetoed_gates}\n"
        f"简历原文（节选）：{(candidate.resume_raw_text or '')[:3000]}"
    )


class _Summary(BaseModel):
    conclusion: str = Field(default="", description="1-2 句结论，含主要加分项与扣分项")
    reasons: list[str] = Field(default_factory=list, description="3-5 条依据，须引用简历证据")
    gaps: list[str] = Field(default_factory=list, description="缺失/薄弱能力项及其影响")
    suggestions: list[str] = Field(default_factory=list, description="本轮优先验证的能力项")


_EXPLAIN_SYSTEM = (
    "你是资深 HRBP，负责把已经算好的岗位匹配结果解释给 HR 听。\n"
    "硬性要求：\n"
    "1. 分数与各能力项得分已由规则算好，**严禁修改、重新估算或四舍五入任何数字**，只能原样引用；\n"
    "2. conclusion 1-2 句，必须点出主要加分项与主要扣分项；\n"
    "3. reasons 3-5 条，每条都要落到具体证据（能力项名称或简历片段）；\n"
    "4. gaps 只写确实缺失或薄弱的能力项，并说明它对总分的影响；\n"
    "5. suggestions 是本轮面试应优先验证的能力项，2-4 条；\n"
    "6. 全部用中文，不要复述字段名，不要输出 Markdown。"
)


@tracing.ai_step("match.explain")
async def generate_summary(db: AsyncSession, match_score_id: int) -> bool:
    """异步补写 AI 总结。返回是否成功。失败只置 failed，不动分数。"""
    row = await db.get(MatchScore, match_score_id)
    if row is None:
        return False
    if row.summary_status == "ready":
        return True

    candidate = await db.get(Candidate, row.candidate_id)
    position = await db.get(Position, row.position_id)
    if candidate is None or position is None:
        row.summary_status = "failed"
        await db.commit()
        return False

    try:
        data = await asyncio.to_thread(
            structured_call,
            _Summary,
            _EXPLAIN_SYSTEM,
            _explain_payload(row, candidate, position),
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("匹配分 %s 的 AI 总结生成失败：%s", match_score_id, exc)
        row.summary_status = "failed"
        await db.commit()
        return False

    row.summary = data.model_dump()
    row.summary_status = "ready"
    await db.commit()
    return True


async def ensure_confirmed(candidate: Candidate) -> None:
    """BR-18：简历没确认就不算分（草稿不参与匹配）。"""
    if candidate.profile_status != CONFIRMED:
        from app.core.errors import ErrorCode, bad_request

        raise bad_request(
            ErrorCode.CANDIDATE_NOT_CONFIRMED, "该候选人简历尚未确认，无法计算人岗匹配分"
        )


# ---------------------------------------------------------------- P18 详情


async def current_score(db: AsyncSession, application_id: int) -> MatchScore | None:
    # 当前分数：CURRENT 优先；全是 STALE 时也要回最后一条 ——
    # P18 要在这条上显示黄条「岗位标准已变更，请重新计算」，回空就看不到分数了
    rows = list(
        await db.scalars(
            select(MatchScore)
            .where(MatchScore.application_id == application_id)
            .order_by(MatchScore.id.desc())
        )
    )
    if not rows:
        return None
    return next((r for r in rows if r.status == CURRENT), rows[0])


async def detail_payload(db: AsyncSession, application: Application) -> dict[str, Any]:
    """P18 一屏所需的数据（PRD §5.16）。

    没有 CURRENT 分时返回 `has_score=False` —— 页面显示「尚未计算」+ 计算按钮，
    **不自动算**（JD 未确认、简历未确认的情况下算出来也是错的）。
    """
    candidate = await db.get(Candidate, application.candidate_id)
    position = await db.get(Position, application.position_id)
    row = await current_score(db, application.id)

    base = {
        "application_id": application.id,
        "candidate_id": application.candidate_id,
        "candidate_name": candidate.name if candidate else "",
        "position_id": application.position_id,
        "position_name": position.name if position else "",
        "stage": application.stage,
        "jd_version": position.jd_version if position else 0,
        "jd_status": position.jd_status if position else "",
        "profile_status": candidate.profile_status if candidate else "",
        "has_score": row is not None,
        "score_id": row.id if row else None,
        "score": float(row.score) if row else None,
        "tier": row.tier if row else "",
        "status": row.status if row else "",
        "algorithm_version": row.algorithm_version if row else "",
        "model_version": row.model_version if row else "",
        "summary_status": row.summary_status if row else "",
        "updated_at": row.updated_at if row else None,
        "vetoed_gates": row.vetoed_gates if row else [],
        "unknown_gates": row.unknown_gates if row else [],
        "breakdown": row.breakdown if row else [],
        "evidences": row.evidences if row else [],
        "bonuses": row.bonuses if row else [],
        "summary": row.summary if row else {},
        # 反馈按 application 查：即便当时没有分（或分还没算）也留过异议，不能丢
        "feedbacks": await feedback_payload(db, application.id),
    }
    return base


async def feedback_payload(db: AsyncSession, application_id: int) -> list[dict]:
    from app.models.match_score import MatchFeedback
    from app.models.user import User

    rows = await db.scalars(
        select(MatchFeedback)
        .where(MatchFeedback.application_id == application_id)
        .order_by(MatchFeedback.id.desc())
    )
    rows = list(rows)
    if not rows:
        return []
    ids = {r.created_by for r in rows if r.created_by}
    names: dict[int, str] = {}
    if ids:
        users = await db.scalars(select(User).where(User.id.in_(ids)))
        names = {u.id: (u.full_name or u.email) for u in users}
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "expected_low": r.expected_low,
            "expected_high": r.expected_high,
            "comment": r.comment,
            "created_by_name": names.get(r.created_by, "") if r.created_by else "",
            "created_at": r.created_at,
        }
        for r in rows
    ]


async def add_feedback(
    db: AsyncSession,
    user: Any,
    application: Application,
    *,
    kind: str,
    comment: str,
    expected_low: int | None,
    expected_high: int | None,
) -> dict:
    """BR-22：落库一条反馈 + 一张不可变评分快照。**不改原分数**。"""
    from app.models.match_score import MatchFeedback

    row = await current_score(db, application.id)
    feedback = MatchFeedback(
        match_score_id=row.id if row else None,
        application_id=application.id,
        candidate_id=application.candidate_id,
        position_id=application.position_id,
        kind=kind,
        expected_low=expected_low,
        expected_high=expected_high,
        comment=comment,
        snapshot_json=_snapshot(row) if row else {},
        created_by=user.id,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return {
        "id": feedback.id,
        "kind": feedback.kind,
        "expected_low": feedback.expected_low,
        "expected_high": feedback.expected_high,
        "comment": feedback.comment,
        "created_by_name": getattr(user, "full_name", "") or getattr(user, "email", ""),
        "created_at": feedback.created_at,
    }
