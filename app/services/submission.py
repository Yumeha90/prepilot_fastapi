"""Step 5 面评校准与提交（PRD §3.4.5 / §5.13 P13）。

四条口径：

**① 面试官只交结论与面评，不指定下一轮（BR-06）**
提交后**只把会话置 SUBMITTED**，`applications.stage` 一个字都不动 ——
阶段流转（下一轮 / Offer / 淘汰 / 人才库）一律由 HR 在 P08 处置。
会话终态 + 卡片上的「已提交」就是「此人已评估」的事实来源，
不需要再造一个 `evaluated` 阶段（造了它会从看板列里消失，HR 反而找不到人）。

**② 完整性在提交时才拦（BR-06）**
Step4 保存一律放行（写一半被拦＝逼面试官先编一条证据出来），
但**提交是终态动作**：每项都要有分 + 有证据或快记，综合评价不能为空。
拦的时候要说清缺的是**哪一项**（点名的清单比「还有 3 项没填完」有用）。
另有两条只在提交时生效的结论约束：**各项均分 < 2 时结论只能是「不通过」**（BR-06），
**结论为不通过时要有 ≥2 项评分 ≤ 2 且写明依据**（BR-07）——
差评是面试评价里最有争议的部分，没依据的差评既说服不了别人也说不清差在哪。

**③ 面评本身也要过合规检查（PRD §3.4.5「后台扫描」）**
「年纪太大」「看着不聪明」这类表述不会出现在题目里，只会出现在面评里 ——
题目拦住了、面评放过去，等于把风险换个地方落地。判定规则与 Step3 同源：
五类受保护特征走关键词规则（命中即阻断，模型无权降级），
语义层面的隐性歧视交给模型（警告级，不拦提交但常驻警示）。
**命中片段必须在原文里找得到**，模型编的片段一律丢弃。

**④ 提交要二次确认**
提交后连面试官本人都只能回看，是不可逆动作。前端弹窗之外后端再挡一道
（与粉碎的 `confirm=true` 同口径 —— 弹窗是 UI，不是凭证）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai import fairness_rules, rag
from app.ai.llm import structured_call
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request
from app.models.candidate import Candidate
from app.models.position import Position
from app.models.session import (
    CONCLUSIONS,
    CONCLUSION_FAIL,
    FAIRNESS_BLOCK,
    FAIRNESS_IDLE,
    FAIRNESS_PASS,
    FAIRNESS_WARN,
    LEVEL_BLOCK,
    LEVEL_INFO,
    LEVEL_WARN,
    SUBMITTED,
    InterviewSession,
)
from app.models.user import User
from app.observability import metrics, tracing
from app.schemas.workbench import (
    ChainRagHit,
    SubmissionFlag,
    SubmissionIn,
    SubmissionOut,
)
from app.services.workbench import ensure_can_edit

logger = logging.getLogger(__name__)
settings = get_settings()

# 检索几条合规资料：面评是一整块文本，比单个问题节点宽，多给一条才覆盖得住
COMPLIANCE_TOP_K = 3
MAX_FLAGS = 20
MAX_SUGGESTION_LEN = 60
# Step3 的「已放行」结论：阻断状态不允许提交
FAIRNESS_DONE_RESULTS = (FAIRNESS_PASS, FAIRNESS_WARN)
# 规则层已覆盖的类别：这些命中一律阻断，模型无权降级
PROTECTED_CATEGORIES = tuple(r.category for r in fairness_rules.RULES)
CATEGORY_QUERY_HINT = {
    "marriage": "婚姻 生育 家庭计划 禁问",
    "age": "年龄 门槛 出生年代 差别对待",
    "gender": "性别 刻板印象 男性优先",
    "region": "户籍 地域 民族 宗教 歧视",
    "health": "健康 病史 残疾 乙肝 体检",
    "leading": "诱导性提问 预设答案 提问方式",
}

CONCLUSION_TEXT = {"pass": "通过", "pending": "待定", "fail": "不通过"}

# BR-06：各项均分 < 2 时结论只能是「不通过」
FORCE_FAIL_AVG = 2.0
# BR-07：结论为不通过时，需 ≥2 个能力项评分 ≤ 2 且写明依据
FAIL_MIN_WEAK_ITEMS = 2
FAIL_WEAK_SCORE = 2


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 视图


def submission_out(session: InterviewSession) -> SubmissionOut:
    data = session.submission_json or {}
    flags = [
        SubmissionFlag(
            category=str(f.get("category") or ""),
            level=str(f.get("level") or LEVEL_INFO),
            field=str(f.get("field") or ""),
            snippet=str(f.get("snippet") or ""),
            reason=str(f.get("reason") or ""),
            suggestion=str(f.get("suggestion") or ""),
            source=str(f.get("source") or "rule"),
            refs=[str(r) for r in (f.get("refs") or [])],
        )
        for f in (data.get("flags") or [])
        if isinstance(f, dict)
    ]
    hits = [
        ChainRagHit(
            title=str(h.get("title") or ""),
            score=float(h.get("score") or 0),
            snippet=str(h.get("snippet") or ""),
        )
        for h in (data.get("rag_hits") or [])
        if isinstance(h, dict)
    ]
    return SubmissionOut(
        result=str(data.get("result") or FAIRNESS_IDLE),
        scanned=bool(data.get("scanned_at")),
        scanned_at=str(data.get("scanned_at") or ""),
        model=str(data.get("model") or ""),
        fields_scanned=int(data.get("fields_scanned") or 0),
        flags=flags,
        rag_hits=hits,
        conclusion=str(session.conclusion or ""),
        submitted_at=session.submitted_at,
    )


# ---------------------------------------------------------------- 待扫文本


def _text_fields(session: InterviewSession) -> list[tuple[str, str]]:
    """面评里所有**会被别人看到**的自由文本。

    逐条定位而不是拼成一大段：命中要能指回「是综合评价里的哪一句」，
    只给一句「面评里有问题」等于让面试官自己全文找。
    """
    data = session.evaluation_json or {}
    out: list[tuple[str, str]] = []
    summary = str(data.get("summary") or "").strip()
    if summary:
        out.append(("summary", summary))
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or "")
        for i, ev in enumerate(item.get("evidences") or []):
            if not isinstance(ev, dict):
                continue
            text = str(ev.get("text") or "").strip()
            if text:
                out.append((f"item:{row_id}:evidence:{i}", text))
        note = str(item.get("note") or "").strip()
        if note:
            out.append((f"item:{row_id}:note", note))
    return out


def _locate_field(fields: list[tuple[str, str]], snippet: str) -> str:
    """命中片段落在哪个字段（找不到就退回综合评价）。"""
    target = _norm(snippet)
    if not target:
        return "summary"
    for field, text in fields:
        if target in _norm(text):
            return field
    return "summary"


def _norm(text: str) -> str:
    """归一化后做包含判断：模型摘片段时会顺手去掉空格与标点。"""
    return re.sub(r"[\s，。？！、；：\"'“”‘’（）()《》【】\[\]?]", "", str(text or ""))


def _title_key(title: str) -> str:
    return re.sub(r"[\s《》〈〉「」\"'`*]", "", str(title or ""))


def _match_refs(refs: list[str], allowed: set[str]) -> list[str]:
    """只保留检索结果里真实存在的资料标题（与 Step3 同一规则：编的依据一律丢弃）。"""
    keys = {_title_key(t): t for t in allowed}
    out: list[str] = []
    for ref in refs:
        key = _title_key(ref)
        if not key:
            continue
        if key in keys:
            out.append(keys[key])
            continue
        hit = next(
            (real for k, real in keys.items() if len(key) >= 6 and (key in k or k in key)),
            None,
        )
        if hit and hit not in out:
            out.append(hit)
    return out


def _rule_flags(fields: list[tuple[str, str]]) -> list[dict]:
    """规则层：五类受保护特征，命中即阻断。**每个类别只报一条**（同类多条只会刷屏）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for field, text in fields:
        for rule in fairness_rules.RULES:
            if rule.category in seen:
                continue
            m = rule.pattern.search(text)
            if not m:
                continue
            seen.add(rule.category)
            out.append(
                {
                    "category": rule.category,
                    "level": rule.level,
                    "field": field,
                    "snippet": m.group(0),
                    "reason": rule.reason,
                    "suggestion": "",
                    "source": "rule",
                    "refs": [],
                }
            )
    return out


# ---------------------------------------------------------------- 模型层


class _FlagLLM(BaseModel):
    category: str = Field(default="", description="类别，只能取给定枚举")
    level: str = Field(default=LEVEL_WARN, description="block=阻断 / warn=警告")
    snippet: str = Field(default="", description="命中的原文片段，必须能在面评文本里找到")
    reason: str = Field(default="", description="为什么违规，一句话，尽量点出依据")
    suggestion: str = Field(default="", description="改成什么更稳妥，不超过 30 字")
    refs: list[str] = Field(
        default_factory=list, description="依据的合规资料标题，必须是给定资料里的原文标题"
    )


class _ScanLLM(BaseModel):
    findings: list[_FlagLLM] = Field(default_factory=list)


SUBMISSION_SYSTEM_PROMPT = """你是招聘合规审查员，负责在面评**提交之前**，把评价文字里的歧视性与不合规表述拦下来。

你会拿到：岗位与轮次、面试官写好的面评（综合评价 + 各能力项的证据与快记），
以及**本公司合规案例库里检索出来的资料**（公司招聘制度 / 国家法律法规 / 公序良俗 / 司法与执法案例）。

判定分档：
- block（阻断）：把婚姻与生育、年龄、性别与性别刻板印象、户籍/地域/民族/宗教、
  健康与残疾这些「先赋因素」当作评价依据（例如"年纪偏大""女生扛不住""看着就不够机灵"）。
- warn（警告）：主观臆断、人格评价、与岗位能力无关的外貌或性格描述等，不改也能提交，但要提示。
- 都不是就不要报。**宁可漏报，也不要把正常的专业评价报成违规** ——
  误报一多，面试官会连真正的红线一起无视。

严格要求：
1. 只报**确实存在**的问题：`snippet` 必须是面评文本里的原文片段（可截取，但要能在原文里找到），
   编造或改写过的片段会被直接丢弃，等于没报。
2. `category` 只能取给定的枚举值；同一类别最多报 1 条。
3. `suggestion` 给出**中性、可直接使用**的替代表述：只描述与岗位内在要求相关的客观事实，
   不超过 30 字。规则已命中的项也要给建议。
4. `reason` 一句话说明为什么违规，能落到检索到的制度或法条就落
   （`refs` 照抄给定资料的标题原文，不要加《》，不要缩写）。
5. 不得改动任何评分或事实判断，你只判断**表述是否合规**。"""


def _prompt(
    *,
    position: Position,
    round_name: str,
    fields: list[tuple[str, str]],
    hits: list[rag.AssetHit],
    rule_flags: list[dict],
) -> str:
    lines = [f"- [{field}] {text}" for field, text in fields]
    rule_lines = [
        f"- 类别 {f['category']}｜命中片段：{f['snippet']}" for f in rule_flags
    ]
    compliance = (
        "\n".join(f"- 《{h.title}》：{h.text[:300]}" for h in hits)
        if hits
        else "（未检索到合规资料）"
    )
    return (
        f"岗位：{position.name}｜轮次：{round_name or '本轮'}\n\n"
        f"【待检查的面评】\n" + ("\n".join(lines) or "（无）") + "\n\n"
        f"【检索到的合规资料】\n{compliance}\n\n"
        "【规则层已经命中的项】（这些一律按阻断处理，请为它们各给一条中性表述建议）\n"
        + ("\n".join(rule_lines) if rule_lines else "（无）")
        + "\n\n类别枚举：marriage / age / gender / region / health / leading。请输出 findings。"
    )


async def _call_llm(prompt: str) -> _ScanLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _ScanLLM,
        SUBMISSION_SYSTEM_PROMPT,
        prompt,
        timeout=120,
    )


def _plain(text: str) -> str:
    s = str(text or "").strip().replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[*#]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _merge(
    fields: list[tuple[str, str]],
    hits: list[rag.AssetHit],
    rule_flags: list[dict],
    llm: _ScanLLM,
) -> list[dict]:
    """规则命中打底，模型只补建议与语义问题（与 Step3 同一分层）。"""
    allowed = {h.title for h in hits}
    texts = [t for _, t in fields]
    flags: list[dict] = [dict(f) for f in rule_flags]
    taken = {str(f["category"]) for f in flags}

    for f in llm.findings:
        if len(flags) >= MAX_FLAGS:
            break
        category = str(f.category or "").strip().lower()
        if category not in CATEGORY_QUERY_HINT or category in taken:
            continue
        snippet = _plain(f.snippet)
        # 事实锚点：片段必须在原文里找得到，找不到就是模型编的
        if snippet and not any(_norm(snippet) in _norm(t) for t in texts):
            logger.warning("提交前扫描丢弃找不到原文的命中：%s / %s", category, snippet[:30])
            continue
        # 受保护特征一律按阻断处理：模型无权把红线降级成警告
        level = (
            LEVEL_BLOCK
            if category in PROTECTED_CATEGORIES or str(f.level).lower() == LEVEL_BLOCK
            else LEVEL_WARN
        )
        taken.add(category)
        flags.append(
            {
                "category": category,
                "level": level,
                "field": _locate_field(fields, snippet),
                "snippet": snippet,
                "reason": _plain(f.reason),
                "suggestion": _plain(f.suggestion)[:MAX_SUGGESTION_LEN],
                "source": "llm",
                "refs": _match_refs(f.refs, allowed),
            }
        )
    return flags


def result_of(flags: list[dict]) -> str:
    """三态结论：有阻断级 → block；否则有警告级 → warn；都没有 → pass。"""
    if any(str(f.get("level")) == LEVEL_BLOCK for f in flags):
        return FAIRNESS_BLOCK
    if any(str(f.get("level")) == LEVEL_WARN for f in flags):
        return FAIRNESS_WARN
    return FAIRNESS_PASS


# ---------------------------------------------------------------- 扫描


def _query_of(fields: list[tuple[str, str]], rule_flags: list[dict]) -> str:
    """检索词：规则命中了就按命中的类别检索（要拿到那条制度的原文），否则按面评正文。"""
    hint = " ".join(CATEGORY_QUERY_HINT.get(str(f["category"]), "") for f in rule_flags)
    body = " ".join(t for _, t in fields)[:600]
    return " ".join(x for x in (hint, body) if x).strip()


def retrieve(fields: list[tuple[str, str]], rule_flags: list[dict]) -> list[rag.AssetHit]:
    """检索合规案例库（可 mock：测试锁的是编排，不是向量库）。"""
    return rag.search_compliance(_query_of(fields, rule_flags), top_k=COMPLIANCE_TOP_K)


@tracing.ai_step("submission.scan")
async def scan(db, user: User, session: InterviewSession) -> SubmissionOut:
    """提交前的合规扫描（可单独触发，让面试官在正式提交之前就看到命中）。

    为什么要有「先扫描、后提交」两步：只做一个提交按钮的话，面试官要等到
    点了提交才知道被拦 —— 而被拦意味着他得回 Step4 改完再走一遍流程。
    """
    ensure_can_edit(user, session)
    fields = _text_fields(session)
    if not fields:
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_EMPTY, "还没有面评内容，没有可检查的文本"
        )

    position = await db.get(Position, session.position_id)
    rule_flags = _rule_flags(fields)
    hits = retrieve(fields, rule_flags)

    prompt = _prompt(
        position=position,
        round_name=session.round_name,
        fields=fields,
        hits=hits,
        rule_flags=rule_flags,
    )
    flags = _merge(fields, hits, rule_flags, await _call_llm(prompt))
    return await _persist(
        db,
        session,
        flags,
        rag_hits=[
            {"title": h.title, "score": round(float(h.score), 4), "snippet": h.text[:160]}
            for h in hits
        ],
        fields_scanned=len(fields),
        model=settings.LLM_MODEL,
    )


async def _persist(
    db,
    session: InterviewSession,
    flags: list[dict],
    *,
    rag_hits: list[dict],
    fields_scanned: int,
    model: str,
) -> SubmissionOut:
    now = _now()
    session.submission_json = {
        "result": result_of(flags),
        "scanned_at": now.isoformat(),
        "model": model,
        "fields_scanned": fields_scanned,
        "flags": flags,
        "rag_hits": rag_hits,
    }
    session.updated_at = now
    await db.commit()
    await db.refresh(session)
    return submission_out(session)


# ---------------------------------------------------------------- 提交


def _missing_names(session: InterviewSession) -> list[str]:
    """还没填完的能力项名称：点名比报数字有用。"""
    from app.services import evaluation as evaluation_svc

    out = evaluation_svc.evaluation_out(session)
    return [it.capability or "—" for it in out.items if not it.complete]


def average_score(session: InterviewSession) -> float | None:
    """已打分项的算术平均（BR-06）。一项都没打分时返回 None —— 那是完整性问题，
    由 `complete` 拦，不在这里重复判。"""
    from app.services import evaluation as evaluation_svc

    scores = [
        it.score
        for it in evaluation_svc.evaluation_out(session).items
        if it.score is not None
    ]
    return sum(scores) / len(scores) if scores else None


def weak_evidenced_count(session: InterviewSession) -> int:
    """评分 ≤ 2 且写明依据（证据或快记）的能力项数（BR-07）。

    「有低分」还不够：**低分也要有依据**。面试评价里最有争议的就是差评，
    没依据的差评在复盘时既说服不了别人，也说不清到底差在哪。
    """
    from app.services import evaluation as evaluation_svc

    return sum(
        1
        for it in evaluation_svc.evaluation_out(session).items
        if it.score is not None
        and it.score <= FAIL_WEAK_SCORE
        and ([e for e in it.evidences if e.text.strip()] or it.note.strip())
    )


def _blocked_summary(flags: list[SubmissionFlag]) -> str:
    """阻断项的一句话摘要：提交被拦时必须说清命中了什么，不然面试官无从下手。"""
    parts: list[str] = []
    for f in flags:
        if f.level != LEVEL_BLOCK:
            continue
        snippet = f.snippet.strip()
        parts.append(
            CATEGORY_TEXT.get(f.category, f.category) + (f"：{snippet}" if snippet else "")
        )
    return "、".join(parts) or "受保护特征相关表述"


CATEGORY_TEXT = {
    "marriage": "婚育",
    "age": "年龄",
    "gender": "性别",
    "region": "户籍地域民族宗教",
    "health": "健康残疾",
    "leading": "诱导性表述",
}


@tracing.ai_step("submission.submit")
async def submit(
    db, user: User, session: InterviewSession, payload: SubmissionIn
) -> SubmissionOut:
    """提交面评：校验 → 扫描 → 落结论 → 会话置终态 → 通知。"""
    ensure_can_edit(user, session)
    if not payload.confirm:
        raise bad_request(
            ErrorCode.WORKBENCH_CONFIRM_REQUIRED,
            "提交后面评不可再修改，请确认后再提交",
        )

    conclusion = str(payload.conclusion or "").strip().lower()
    if conclusion not in CONCLUSIONS:
        raise bad_request(
            ErrorCode.WORKBENCH_CONCLUSION_INVALID, "请选择本轮面试结论（通过 / 待定 / 不通过）"
        )

    data = session.evaluation_json or {}
    # BR-07 的完整性要求**只在提交时拦**：Step4 保存一律放行，提交是终态动作
    if not data.get("complete"):
        names = _missing_names(session)
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_INCOMPLETE,
            "还有能力项没填完（每项都要有评分，且有证据或快记）："
            + ("、".join(names) if names else "请先完成评分"),
        )
    if not str(data.get("summary") or "").strip():
        raise bad_request(
            ErrorCode.WORKBENCH_EVAL_NO_SUMMARY, "综合评价不能为空，请先写一段整体判断"
        )

    # BR-06：均分 < 2 时结论只能是「不通过」。
    # 「强制」落成拦截而不是静默改写结论：提交是不可逆动作，
    # 面试官选了通过、系统悄悄改成不通过却不告诉他，比让他多改一次糟糕得多。
    avg = average_score(session)
    if avg is not None and avg < FORCE_FAIL_AVG and conclusion != CONCLUSION_FAIL:
        raise bad_request(
            ErrorCode.WORKBENCH_CONCLUSION_FORCED_FAIL,
            f"各项均分 {avg:.1f}（< {FORCE_FAIL_AVG:.0f}），结论只能为「不通过」，请改选后再提交",
        )

    # BR-07：差评要有依据 —— 至少 2 项评分 ≤ 2 且写明证据或快记
    if conclusion == CONCLUSION_FAIL:
        weak = weak_evidenced_count(session)
        if weak < FAIL_MIN_WEAK_ITEMS:
            raise bad_request(
                ErrorCode.WORKBENCH_FAIL_NEEDS_EVIDENCE,
                f"结论为「不通过」时，需至少 {FAIL_MIN_WEAK_ITEMS} 个能力项评分 ≤ "
                f"{FAIL_WEAK_SCORE} 且写明依据（当前 {weak} 项）",
            )

    # 题目还没过合规就交面评 = 跳过闸门：Step3 必须已经扫过且不是阻断状态
    fairness_result = str((session.fairness_json or {}).get("result") or "")
    if fairness_result not in FAIRNESS_DONE_RESULTS:
        raise bad_request(
            ErrorCode.WORKBENCH_FAIRNESS_REQUIRED,
            "问题链还有阻断级问题没处置，请回到公平性检查处理后再提交"
            if fairness_result == FAIRNESS_BLOCK
            else "请先完成公平性检查，再提交面评",
        )

    out = await scan(db, user, session)
    if out.result == FAIRNESS_BLOCK:
        raise bad_request(
            ErrorCode.WORKBENCH_SUBMISSION_BLOCKED,
            f"面评命中阻断级合规问题（{_blocked_summary(out.flags)}），请修改后再提交",
        )

    now = _now()
    session.conclusion = conclusion
    session.status = SUBMITTED
    session.submitted_at = now
    session.updated_at = now
    data = dict(session.submission_json or {})
    data["conclusion"] = conclusion
    data["submitted_at"] = now.isoformat()
    session.submission_json = data
    await db.commit()
    await db.refresh(session)
    logger.info("会话 %s 面评已提交，结论 %s（面试官 %s）", session.id, conclusion, user.id)

    await _notify_submitted(db, session, user)
    return submission_out(session)


async def _notify_submitted(db, session: InterviewSession, actor: User) -> int:
    """通知订阅了「面评提交」的人（HR 侧）—— 提交完就没人知道等于没人处置。

    通知对象不含提交者本人：自己刚做的事再弹一条提醒是噪音。
    """
    from app.models.notification import NotificationSubscription
    from app.services import notification as notify_svc

    subscribed = list(
        await db.scalars(
            select(NotificationSubscription.user_id).where(
                NotificationSubscription.event_type == "evaluation_submitted",
                NotificationSubscription.enabled.is_(True),
            )
        )
    )
    targets = [uid for uid in subscribed if uid != actor.id]
    if not targets:
        return 0

    candidate = await db.get(Candidate, session.candidate_id)
    position = await db.get(Position, session.position_id)
    name = candidate.name if candidate else ""
    text = CONCLUSION_TEXT.get(str(session.conclusion or ""), str(session.conclusion or ""))
    return await notify_svc.notify_subscribers(
        db,
        "evaluation_submitted",
        title="面评已提交，待处置",
        body=f"「{position.name if position else ''}」{name} 的"
        f"{session.round_name or '本轮'}面评已提交（结论：{text}），请在候选人看板处置",
        payload={
            "candidateId": session.candidate_id,
            "applicationId": session.application_id,
            "sessionId": session.id,
        },
        user_ids=targets,
    )
