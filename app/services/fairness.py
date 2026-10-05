"""Step 3 公平性检查（PRD §3.4.3 / §6.5 P11，AI 链路 C5）。

**两层判定：规则在前，模型在后**（PRD §6.5「规则库优先，LLM 补充」）。
五类受保护特征（婚育 / 年龄 / 性别 / 户籍地域民族宗教 / 健康残疾）是硬红线，
判断标准稳定、可穷举 —— 交给模型意味着「同一个问题这次合规、下次违规」。
所以规则命中即阻断（BLOCK 级零容忍）；模型只做两件规则做不了的事：
① 语义层面的诱导性 / 预设答案式提问（警告级，允许保留并记录原因）；
② 给每条命中**生成中性改写建议** —— 只说「不许这么问」而不给替代问法，
面试官只能自己憋，最后多半改成一句更空洞的套话。

**为什么检索合规案例库（C5 同样依赖 Milvus）**：判定依据必须能溯源到
「本公司制度第几条 / 哪部法第几条」，否则页面上一句「存在性别刻板印象」
只是模型的主观意见，面试官完全可以不服。

**但向量库连不上时不让整块扫描失败**（2026-10-05 改，BR-30）：五类红线是
关键词规则层，不依赖检索；拿不到资料只是「依据为空」，不是「判定失效」。
让基础设施抖动把整页公平性检查卡死，等于逼面试官跳过这一步 —— 那比没有
依据更糟。所以：检索异常（向量库不可用）→ 降级继续，但必须显式上报
`rag_degraded`，页面挂黄条说明「本次没有依据资料」。
**检索成功但结果为空不算降级**（这条本来就是允许的，不该报警）。

**为什么同步而不是异步**：P95 ≤ 10s（一次模型调用 + ≤6 次检索）。
异步 + 轮询会把「下一步 → 转圈 → 才看到结论」拆成两段等待，
而结论只有三态，没有中间产物需要流式呈现。

**模型给的建议要校验**：`snippet` 必须在原文里找得到，编造的片段会被丢弃
（与 C4 的 `rag_refs` 校验同源 —— 页面出现一条不存在的依据，比没有依据更糟）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ai import fairness_rules, rag
from app.ai.llm import structured_call
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, bad_request
from app.models.position import Position
from app.models.session import (
    DISPOSITION_ACCEPTED,
    DISPOSITION_PENDING,
    DISPOSITION_REWRITTEN,
    FAIRNESS_BLOCK,
    FAIRNESS_IDLE,
    FAIRNESS_PASS,
    FAIRNESS_WARN,
    LEVEL_BLOCK,
    LEVEL_INFO,
    LEVEL_WARN,
    InterviewSession,
)
from app.models.user import User
from app.observability import metrics, tracing
from app.schemas.workbench import (
    ChainRagHit,
    FairnessAcceptIn,
    FairnessCheckItem,
    FairnessFinding,
    FairnessOut,
    FairnessRewriteIn,
)
from app.services.workbench import ensure_can_edit, observation_text

logger = logging.getLogger(__name__)
settings = get_settings()

# 每个节点检索几条合规资料：1 条容易检索偏，3 条以上稀释 prompt
COMPLIANCE_TOP_K = 2
MAX_FINDINGS = 20
MAX_SUGGESTION_LEN = 60
# 卡片上展示的「原问题」上限：整句要能看全，但不能把一整条问题链塞进 JSONB
MAX_ORIGINAL_LEN = 400
# 采纳改写后的复检结果说明（直接显示在页面上，让人知道刚才那一下发生了什么）
NOTICE_REWRITTEN = "已采纳改写并复检通过：这一条不再计入结论。其他条目的状态未变动，需要整体复核可点「重新扫描」。"
NOTICE_STILL_HIT = "改写的问法仍命中同一风险，这一条回到待处置 —— 请再改一次或换一种问法。"
NOTICE_NEW_RISK = "改写后引入了新的风险项（已追加到列表），请一并处置。"
# 复检命中后要覆盖回原条目的字段（id / node_id / disposition 等不覆盖）
_RECHECK_KEYS = ("level", "field", "snippet", "original_text", "reason", "suggestion", "refs", "source")

# 规则层已覆盖的类别：这些命中一律阻断，模型无权降级
PROTECTED_CATEGORIES = tuple(r.category for r in fairness_rules.RULES)
# 检索时按类别补的关键词：让「婚育」真的检索到婚育那条制度，而不是最近的一条
CATEGORY_QUERY_HINT = {
    "marriage": "婚姻 生育 家庭计划 禁问",
    "age": "年龄 门槛 出生年代 差别对待",
    "gender": "性别 刻板印象 男性优先",
    "region": "户籍 地域 民族 宗教 歧视",
    "health": "健康 病史 残疾 乙肝 体检",
    "leading": "诱导性提问 预设答案 提问方式",
}


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 视图


def _findings_out(items: list[dict]) -> list[FairnessFinding]:
    out: list[FairnessFinding] = []
    for f in items:
        if not isinstance(f, dict):
            continue
        out.append(
            FairnessFinding(
                id=str(f.get("id") or ""),
                node_id=str(f.get("node_id") or ""),
                capability=str(f.get("capability") or ""),
                level=str(f.get("level") or LEVEL_INFO),
                category=str(f.get("category") or ""),
                field=str(f.get("field") or ""),
                original_text=str(f.get("original_text") or ""),
                snippet=str(f.get("snippet") or ""),
                reason=str(f.get("reason") or ""),
                suggestion=str(f.get("suggestion") or ""),
                source=str(f.get("source") or "rule"),
                refs=[str(r) for r in (f.get("refs") or [])],
                disposition=str(f.get("disposition") or DISPOSITION_PENDING),
                accepted_reason=str(f.get("accepted_reason") or ""),
                rewritten_to=str(f.get("rewritten_to") or ""),
            )
        )
    return out


def _hits_payload(data: dict | None) -> dict[str, list[ChainRagHit]]:
    raw = (data or {}).get("rag_hits")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[ChainRagHit]] = {}
    for key, items in raw.items():
        if not isinstance(items, list):
            continue
        out[str(key)] = [
            ChainRagHit(
                title=str(i.get("title") or ""),
                score=float(i.get("score") or 0),
                snippet=str(i.get("snippet") or ""),
            )
            for i in items
            if isinstance(i, dict)
        ]
    return out


def fairness_out(session: InterviewSession) -> FairnessOut:
    data = session.fairness_json or {}
    checks = [
        FairnessCheckItem(
            key=str(c.get("key") or ""),
            level=str(c.get("level") or LEVEL_INFO),
            count=int(c.get("count") or 0),
        )
        for c in (data.get("checks") or [])
        if isinstance(c, dict)
    ]
    findings = _findings_out([f for f in (data.get("findings") or []) if isinstance(f, dict)])
    return FairnessOut(
        result=str(data.get("result") or FAIRNESS_IDLE),
        scanned=bool(data.get("scanned_at")),
        scanned_at=str(data.get("scanned_at") or ""),
        model=str(data.get("model") or ""),
        nodes_scanned=int(data.get("nodes_scanned") or 0),
        checks=checks,
        findings=findings,
        revision=session.fairness_revision or 0,
        updated_at=session.fairness_updated_at,
        rag_hits=_hits_payload(data),
        notice=str(data.get("notice") or ""),
        rag_degraded=bool(data.get("rag_degraded")),
    )


# ---------------------------------------------------------------- 模型


class _FindingLLM(BaseModel):
    node_id: str = Field(default="", description="命中的节点 id，必须是给定节点之一")
    category: str = Field(default="", description="类别，只能取给定枚举")
    level: str = Field(default=LEVEL_WARN, description="block=阻断 / warn=警告")
    snippet: str = Field(default="", description="命中的原文片段，必须能在问题文本里找到")
    reason: str = Field(default="", description="为什么违规，一句话，尽量点出依据")
    suggestion: str = Field(default="", description="中性改写后的问法，不超过 30 字")
    refs: list[str] = Field(
        default_factory=list, description="依据的合规资料标题，必须是给定资料里的原文标题"
    )


class _ScanLLM(BaseModel):
    findings: list[_FindingLLM] = Field(default_factory=list)


FAIRNESS_SYSTEM_PROMPT = """你是招聘合规审查员，负责在面试开始**之前**，把问题链里的歧视性与不合规提问拦下来。

你会拿到：岗位与轮次、面试官准备的问题链（主问题 / 追问 / 评分观察点）、
以及**本公司合规案例库里检索出来的资料**（公司招聘制度 / 国家法律法规 / 公序良俗 / 司法与执法案例）。

判定分档：
- block（阻断）：涉及婚姻与生育计划、年龄、性别与性别刻板印象、户籍/地域/民族/宗教、
  健康/残疾/既往病史；以及任何把上述「先赋因素」当作评价依据的表述。
- warn（警告）：诱导性 / 预设答案式提问（如「你应该也认同……对吧」）、
  把团队成果预设为候选人个人成果、以压力面试为名的否定式提问等。
- 都不是就不要报。**宁可漏报，也不要把正常的专业提问报成违规** ——
  误报一多，面试官就会连真正的红线一起无视。

严格要求：
1. 只报**确实存在**的问题：`snippet` 必须是问题文本里的原文片段（可截取，但要能在原文里找到），
   编造或改写过的片段会被直接丢弃，等于没报。
2. `node_id` 必须是给定的节点 id；`category` 只能取给定的枚举值。
3. `suggestion` 是**中性改写**：保持原本想考察的能力维度，只问与岗位内在要求相关的客观问题，
   一句话问完、不超过 30 字。规则已命中的项也要给建议。
4. `reason` 一句话说明为什么违规，能落到检索到的制度或法条就落
   （`refs` 照抄给定资料的标题原文，不要加《》，不要缩写）。
5. 一个节点同一类别最多报 1 条。"""


def _node_lines(nodes: list[dict]) -> str:
    lines = []
    for n in nodes:
        lines.append(f"节点 {n.get('id')}｜能力项：{n.get('capability') or '—'}")
        lines.append(f"  主问题：{n.get('main_question') or '—'}")
        for f in n.get("followups") or []:
            if not isinstance(f, dict):
                continue
            lines.append(f"  追问{f.get('level')}（答得模糊时）：{f.get('vague') or '—'}")
            lines.append(f"  追问{f.get('level')}（防伪验证）：{f.get('anti_fake') or '—'}")
        for o in n.get("observations") or []:
            lines.append(f"  观察点：{o}")
    return "\n".join(lines)


def _compliance_block(hits: list[rag.AssetHit]) -> str:
    if not hits:
        return "（本节点未检索到合规资料）"
    return "\n".join(f"- 《{h.title}》：{h.text[:300]}" for h in hits)


def _prompt(
    *,
    position: Position,
    round_name: str,
    nodes: list[dict],
    hits: dict[str, list[rag.AssetHit]],
    rule_hits: list[fairness_rules.Hit],
) -> str:
    rule_lines = [
        f"- 节点 {h.node_id}｜类别 {h.category}｜命中片段：{h.snippet}" for h in rule_hits
    ]
    return (
        f"岗位：{position.name}｜轮次：{round_name or '本轮'}\n\n"
        f"【待检查的问题链】\n{_node_lines(nodes)}\n\n"
        "【各节点检索到的合规资料】\n"
        + "\n".join(
            f"节点 {n.get('id')}：\n{_compliance_block(hits.get(str(n.get('id')), []))}"
            for n in nodes
        )
        + "\n\n"
        "【规则层已经命中的项】（这些一律按阻断处理，请为它们各给一条中性改写建议）\n"
        + ("\n".join(rule_lines) if rule_lines else "（无）")
        + "\n\n类别枚举：marriage / age / gender / region / health / leading。请输出 findings。"
    )


async def _call_llm(prompt: str) -> _ScanLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _ScanLLM,
        FAIRNESS_SYSTEM_PROMPT,
        prompt,
        timeout=120,
    )


def _plain(text: str) -> str:
    s = str(text or "").strip().replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[*#]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm(text: str) -> str:
    """归一化后做包含判断：模型摘片段时会顺手去掉空格与标点。"""
    return re.sub(r"[\s，。？！、；：\"'“”‘’（）()《》【】\[\]?]", "", str(text or ""))


def _title_key(title: str) -> str:
    return re.sub(r"[\s《》〈〉「」\"'`*]", "", str(title or ""))


def _match_refs(refs: list[str], allowed: set[str]) -> list[str]:
    """只保留检索结果里真实存在的资料标题 —— 编的依据比没依据更糟。

    （与 C4 问题链的 `rag_refs` 校验同一规则：书名号 / 空格造成的形变允许通过，
    完全对不上的一律丢弃。）
    """
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


def _node_texts(node: dict) -> list[str]:
    texts = [str(node.get("main_question") or "")]
    for f in node.get("followups") or []:
        if isinstance(f, dict):
            texts.append(str(f.get("vague") or ""))
            texts.append(str(f.get("anti_fake") or ""))
    texts.extend(str(o) for o in (node.get("observations") or []))
    return [t for t in texts if t]


def _field_text(node: dict, field: str) -> str:
    """取命中字段的**整句原文**（`_set_node_text` 的反向）。

    只给命中片段的话，面试官得自己回到问题链里把整句话找出来才能改 ——
    「改的是哪一句」必须由系统给全，尤其是追问和观察点这种短文本。
    """
    if not node:
        return ""
    if not field or field == "main_question":
        return str(node.get("main_question") or "")[:MAX_ORIGINAL_LEN]
    if field.startswith("followup:"):
        parts = field.split(":", 2)
        if len(parts) == 3:
            _, level, key = parts
            for f in node.get("followups") or []:
                if isinstance(f, dict) and str(f.get("level")) == str(level):
                    return str(f.get(key) or "")[:MAX_ORIGINAL_LEN]
        return ""
    if field.startswith("observation:"):
        try:
            index = int(field.split(":", 1)[1])
        except (TypeError, ValueError):
            return ""
        observations = node.get("observations") or []
        if 0 <= index < len(observations):
            return str(observations[index] or "")[:MAX_ORIGINAL_LEN]
        return ""
    return str(node.get("main_question") or "")[:MAX_ORIGINAL_LEN]


def _locate_field(node: dict, snippet: str) -> str:
    """定位片段落在哪个字段（前端要高亮到那一句）。找不到就退回主问题。"""
    target = _norm(snippet)
    if not target:
        return "main_question"
    if target in _norm(str(node.get("main_question") or "")):
        return "main_question"
    for f in node.get("followups") or []:
        if not isinstance(f, dict):
            continue
        level = f.get("level") or 1
        for key in ("vague", "anti_fake"):
            if target in _norm(str(f.get(key) or "")):
                return f"followup:{level}:{key}"
    for i, o in enumerate(node.get("observations") or []):
        if target in _norm(str(o or "")):
            return f"observation:{i}"
    return "main_question"


def _merge(
    nodes: list[dict],
    hits: dict[str, list[rag.AssetHit]],
    rule_hits: list[fairness_rules.Hit],
    llm: _ScanLLM,
) -> list[dict]:
    """规则命中打底（依据来自法条与制度），模型只补建议与语义问题。"""
    by_id = {str(n.get("id") or ""): n for n in nodes}
    findings: list[dict] = []
    taken: set[tuple[str, str]] = set()

    for h in rule_hits:
        allowed = {x.title for x in hits.get(h.node_id, [])}
        match = next(
            (
                f
                for f in llm.findings
                if str(f.node_id) == h.node_id and str(f.category) == h.category
            ),
            None,
        )
        findings.append(
            {
                "id": f"f{len(findings) + 1}",
                "node_id": h.node_id,
                "capability": h.capability,
                "level": h.level,
                "category": h.category,
                "field": h.field,
                "snippet": h.snippet,
                "original_text": _field_text(by_id.get(h.node_id), h.field),
                "reason": h.reason,
                "suggestion": _plain(match.suggestion)[:MAX_SUGGESTION_LEN] if match else "",
                "source": "rule",
                "refs": _match_refs(match.refs, allowed) if match else [],
                "disposition": DISPOSITION_PENDING,
                "accepted_reason": "",
            }
        )
        taken.add((h.node_id, h.category))

    for f in llm.findings:
        if len(findings) >= MAX_FINDINGS:
            break
        node_id = str(f.node_id or "").strip()
        node = by_id.get(node_id)
        if node is None:
            continue
        category = str(f.category or "").strip().lower()
        if category not in CATEGORY_QUERY_HINT:
            continue
        if (node_id, category) in taken:
            continue
        snippet = _plain(f.snippet)
        # 事实锚点：片段必须在原文里找得到，找不到就是模型编的
        if snippet and not any(_norm(snippet) in _norm(t) for t in _node_texts(node)):
            logger.warning("公平性检查丢弃找不到原文的命中：%s / %s", node_id, snippet[:30])
            continue
        # 受保护特征一律按阻断处理：模型无权把红线降级成警告
        level = (
            LEVEL_BLOCK
            if category in PROTECTED_CATEGORIES or str(f.level).lower() == LEVEL_BLOCK
            else LEVEL_WARN
        )
        taken.add((node_id, category))
        findings.append(
            {
                "id": f"f{len(findings) + 1}",
                "node_id": node_id,
                "capability": str(node.get("capability") or ""),
                "level": level,
                "category": category,
                "field": _locate_field(node, snippet),
                "snippet": snippet,
                "original_text": _field_text(node, _locate_field(node, snippet)),
                "reason": _plain(f.reason),
                "suggestion": _plain(f.suggestion)[:MAX_SUGGESTION_LEN],
                "source": "llm",
                "refs": _match_refs(f.refs, {x.title for x in hits.get(node_id, [])}),
                "disposition": DISPOSITION_PENDING,
                "accepted_reason": "",
            }
        )
    return findings


def _checks(findings: list[dict]) -> list[dict]:
    """六行检查表（PRD §5.5）：没命中的类别也要列出来，否则看不出查了什么。"""
    rank = {LEVEL_INFO: 0, LEVEL_WARN: 1, LEVEL_BLOCK: 2}
    agg: dict[str, dict] = {}
    for f in findings:
        if f.get("disposition") != DISPOSITION_PENDING:
            continue
        cat = str(f.get("category") or "")
        item = agg.setdefault(cat, {"key": cat, "level": LEVEL_INFO, "count": 0})
        item["count"] += 1
        if rank.get(str(f.get("level")), 0) > rank.get(item["level"], 0):
            item["level"] = str(f.get("level"))
    return [agg.get(k, {"key": k, "level": LEVEL_INFO, "count": 0}) for k in fairness_rules.CHECK_ORDER]


def result_of(findings: list[dict]) -> str:
    """三态结论：有未处置的阻断 → block；有未处置的警告 → warn；否则 pass。"""
    pending = [f for f in findings if f.get("disposition") == DISPOSITION_PENDING]
    if any(str(f.get("level")) == LEVEL_BLOCK for f in pending):
        return FAIRNESS_BLOCK
    if any(str(f.get("level")) == LEVEL_WARN for f in pending):
        return FAIRNESS_WARN
    return FAIRNESS_PASS


# ---------------------------------------------------------------- 扫描


def _chain_nodes(session: InterviewSession) -> list[dict]:
    data = session.chain_json or {}
    nodes = data.get("nodes") if isinstance(data, dict) else None
    out: list[dict] = []
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        node = dict(n)
        # 观察点可能存成了 JSON 串（模型偶发），扫描前先还原成正文 ——
        # 否则词面扫描与 LLM 看到的都是 `{"type": "bad", ...}`，命中片段也对不上原句。
        node["observations"] = [observation_text(o) for o in (n.get("observations") or [])]
        out.append(node)
    return out


def retrieve(
    node: dict, category: str = "", *, degraded: set[str] | None = None
) -> list[rag.AssetHit]:
    """检索合规案例库（可 mock：测试锁的是编排，不是向量库）。

    **向量库连不上时降级为空，不整块失败**（2026-10-05 改）：五类红线是关键词规则层，
    不依赖检索；拿不到依据资料只是「引用为空」，不是「查不出来」。
    让基础设施抖动把整页公平性检查卡死，等于逼面试官跳过这一步 —— 那比没有依据更糟。
    降级必须显式上报（`degraded`），由调用方写进结论里提示用户。
    检索成功但结果为空不属降级：那只是这条没有对应资料，本来就是允许的。
    """
    hint = CATEGORY_QUERY_HINT.get(category, "")
    query = " ".join(
        x
        for x in (hint, str(node.get("capability") or ""), str(node.get("main_question") or ""))
        if x
    ).strip()
    try:
        return rag.search_compliance(query, top_k=COMPLIANCE_TOP_K)
    except AppError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if str(detail.get("code") or "") != ErrorCode.WORKBENCH_RAG_UNAVAILABLE:
            raise
        logger.warning("合规资料库不可用，本次公平性检查无依据资料：%s", detail.get("message"))
        if degraded is not None:
            degraded.add("compliance")
        return []


@tracing.ai_step("fairness.scan")
async def scan(
    db,
    user: User,
    session: InterviewSession,
    *,
    inherit_disposition: bool = True,
    use_llm: bool = True,
) -> FairnessOut:
    """C5 公平性扫描（同步）。失败直接抛错，不做「假装通过」的兜底。"""
    ensure_can_edit(user, session)
    nodes = _chain_nodes(session)
    if not nodes:
        raise bad_request(
            ErrorCode.WORKBENCH_FAIRNESS_NO_CHAIN, "请先生成问题链，再做公平性检查"
        )

    position = await db.get(Position, session.position_id)

    rule_hits = fairness_rules.scan_nodes(nodes)
    hit_category = {h.node_id: h.category for h in rule_hits}
    hits: dict[str, list[rag.AssetHit]] = {}
    degraded: set[str] = set()
    for n in nodes:
        hits[str(n.get("id") or "")] = retrieve(
            n, hit_category.get(str(n.get("id") or ""), ""), degraded=degraded
        )

    if use_llm:
        prompt = _prompt(
            position=position,
            round_name=session.round_name,
            nodes=nodes,
            hits=hits,
            rule_hits=rule_hits,
        )
        findings = _merge(nodes, hits, rule_hits, await _call_llm(prompt))
    else:
        findings = _merge(nodes, hits, rule_hits, _ScanLLM())

    if inherit_disposition:
        findings = _inherit(findings, session)

    return await _persist(
        db,
        session,
        findings,
        rag_hits={
            k: [
                {"title": h.title, "score": round(float(h.score), 4), "snippet": h.text[:160]}
                for h in items
            ]
            for k, items in hits.items()
        },
        nodes_scanned=len(nodes),
        model=settings.LLM_MODEL if use_llm else "",
        rag_degraded=bool(degraded),
    )


def _inherit(findings: list[dict], session: InterviewSession) -> list[dict]:
    """重新扫描后继承「已保留」的处置。

    面试官对某条警告级命中写过保留理由，改了别的问题再扫描一次，
    那条理由不该被冲掉 —— 否则「保留并记录原因」这个动作等于白做。
    按 (node_id, category) 继承，且只对非阻断项生效。
    """
    data = session.fairness_json or {}
    old = {
        (str(f.get("node_id") or ""), str(f.get("category") or "")): f
        for f in (data.get("findings") or [])
        if isinstance(f, dict) and f.get("disposition") == DISPOSITION_ACCEPTED
    }
    if not old:
        return findings
    for f in findings:
        prev = old.get((str(f.get("node_id") or ""), str(f.get("category") or "")))
        if prev and str(f.get("level")) != LEVEL_BLOCK:
            f["disposition"] = DISPOSITION_ACCEPTED
            f["accepted_reason"] = str(prev.get("accepted_reason") or "")
    return findings


async def _persist(
    db,
    session: InterviewSession,
    findings: list[dict],
    *,
    rag_hits: dict,
    nodes_scanned: int,
    model: str,
    notice: str = "",
    revision: int | None = None,
    rag_degraded: bool | None = None,
) -> FairnessOut:
    now = _now()
    # 降级标记：本次没重新检索就沿用上次的值，别把「上次降级」的提示擦掉
    if rag_degraded is None:
        rag_degraded = bool((session.fairness_json or {}).get("rag_degraded"))
    session.fairness_json = {
        "result": result_of(findings),
        "scanned_at": now.isoformat(),
        "model": model,
        "nodes_scanned": nodes_scanned,
        "checks": _checks(findings),
        "findings": findings,
        "rag_hits": rag_hits,
        "notice": notice,
        "rag_degraded": bool(rag_degraded),
    }
    session.fairness_revision = (
        (session.fairness_revision or 0) + 1 if revision is None else revision
    )
    session.fairness_updated_at = now
    session.updated_at = now
    await db.commit()
    await db.refresh(session)
    return fairness_out(session)


# ---------------------------------------------------------------- 处置


def _findings_or_raise(session: InterviewSession, finding_id: str) -> list[dict]:
    data = session.fairness_json or {}
    findings = [f for f in (data.get("findings") or []) if isinstance(f, dict)]
    if not any(str(f.get("id")) == finding_id for f in findings):
        raise bad_request(ErrorCode.WORKBENCH_FINDING_NOT_FOUND, "要处置的命中项不存在，请重新扫描")
    return findings


@tracing.ai_step("fairness.accept")
async def accept_finding(
    db, user: User, session: InterviewSession, finding_id: str, payload: FairnessAcceptIn
) -> FairnessOut:
    """「保留并记录原因」：只放行警告级，且必须写清理由。

    阻断级不给这个口子（BR-05：命中阻断级必须改写），写了也得留痕 ——
    否则「这条风险是谁放行的」事后无从追溯。
    """
    ensure_can_edit(user, session)
    findings = _findings_or_raise(session, finding_id)
    reason = str(payload.reason or "").strip()
    if not reason:
        raise bad_request(ErrorCode.WORKBENCH_FAIRNESS_REASON_REQUIRED, "保留风险项必须写明原因")
    for f in findings:
        if str(f.get("id")) != finding_id:
            continue
        if str(f.get("level")) == LEVEL_BLOCK:
            raise bad_request(
                ErrorCode.WORKBENCH_FAIRNESS_BLOCK_REQUIRED,
                "阻断级问题必须改写后才能继续，不能保留",
            )
        f["disposition"] = DISPOSITION_ACCEPTED
        f["accepted_reason"] = reason[:300]
    data = session.fairness_json or {}
    return await _persist(
        db,
        session,
        findings,
        rag_hits=data.get("rag_hits") or {},
        nodes_scanned=int(data.get("nodes_scanned") or 0),
        model=str(data.get("model") or ""),
    )


@tracing.ai_step("fairness.recheck")
async def _recheck_node(
    db, session: InterviewSession, node_id: str
) -> tuple[list[dict], dict[str, list[rag.AssetHit]], bool]:
    """只复检**一个节点**（采纳改写后用）：其余节点的结论一个字都不动。

    为什么不整链重扫（2026-10-05 修）：整链重扫会把上一次的全部命中重新生成一遍 ——
    面试官点了其中一条「采纳建议」，页面上其他条目的等级、片段、顺序一起变，
    甚至因为模型这次没报出来而整页变绿。那是「我改了一处，系统把整份结论洗了一遍」，
    没人敢信这份结论。采纳只处置**被点名的那一条**，范围必须收在这一条上。
    """
    node = next(
        (n for n in _chain_nodes(session) if str(n.get("id") or "") == node_id), None
    )
    if node is None:
        return [], {}, False
    rule_hits = fairness_rules.scan_nodes([node])
    degraded: set[str] = set()
    hits = {
        node_id: retrieve(node, rule_hits[0].category if rule_hits else "", degraded=degraded),
    }
    position = await db.get(Position, session.position_id)
    prompt = _prompt(
        position=position,
        round_name=session.round_name,
        nodes=[node],
        hits=hits,
        rule_hits=rule_hits,
    )
    findings = _merge([node], hits, rule_hits, await _call_llm(prompt))
    return findings, hits, bool(degraded)


def _next_finding_id(findings: list[dict]) -> str:
    """新 id 不能撞上已有的 f1/f2…（追加「改写引入的新风险」时用）。"""
    nums = [
        int(m.group(1))
        for m in (re.fullmatch(r"f(\d+)", str(f.get("id") or "")) for f in findings)
        if m
    ]
    return f"f{max(nums, default=0) + 1}"


@tracing.ai_step("fairness.rewrite")
async def apply_rewrite(
    db,
    user: User,
    session: InterviewSession,
    finding_id: str,
    payload: FairnessRewriteIn | None = None,
) -> FairnessOut:
    """采纳改写：替换**这一条**命中的原文 → **只复检这一条** → 只改这一条的状态。

    三条口径：
    1. **不动其他条目**：其他命中的等级 / 片段 / 处置状态原样保留，页面不会整体洗牌。
    2. **改写必须复检**：AI 给的建议本身也可能仍踩线 —— 不复检就放行等于绿灯造假。
       复检只针对被改写的那个节点（规则层 + 模型层），不是整链重扫。
    3. **改写引入的新风险要报**：同一节点若冒出**别的类别**的命中，追加为新条目，
       不能因为「这条处置完了」就顺手放过新冒出来的问题。
    """
    ensure_can_edit(user, session)
    data = dict(session.fairness_json or {})
    old = [dict(f) for f in (data.get("findings") or []) if isinstance(f, dict)]
    if not any(str(f.get("id")) == finding_id for f in old):
        raise bad_request(ErrorCode.WORKBENCH_FINDING_NOT_FOUND, "要处置的命中项不存在，请重新扫描")
    target = next(f for f in old if str(f.get("id")) == finding_id)
    text = str((payload.suggestion if payload else "") or "").strip() or str(
        target.get("suggestion") or ""
    ).strip()
    if not text:
        raise bad_request(
            ErrorCode.WORKBENCH_FAIRNESS_NO_SUGGESTION,
            "还没有可用的改写建议，请手动修改问题后重新扫描",
        )

    node_id = str(target.get("node_id") or "")
    category = str(target.get("category") or "")
    prev_revision = session.fairness_revision or 0

    from app.services import question_chain as qc

    # 写回文本（内部会作废旧结论 —— 题目变了，旧结论一律不算数，BR-25）
    await qc.patch_node_text(db, session, node_id, str(target.get("field") or ""), text)
    # 只复检被改写的这一个节点
    fresh, hits, degraded = await _recheck_node(db, session, node_id)

    still = [f for f in fresh if str(f.get("category")) == category]
    new_risk = [f for f in fresh if str(f.get("category")) != category]

    findings: list[dict] = []
    notice = ""
    for f in old:
        if str(f.get("id")) != finding_id:
            findings.append(dict(f))
            continue
        nf = dict(f)
        nf["rewritten_to"] = text
        nf["accepted_reason"] = ""
        if still:
            # 改写后仍踩同一条红线：这条不算处置完，回到待处置并换成新的命中信息
            nf.update({k: still[0][k] for k in _RECHECK_KEYS if k in still[0]})
            nf["disposition"] = DISPOSITION_PENDING
            notice = NOTICE_STILL_HIT
        else:
            nf["disposition"] = DISPOSITION_REWRITTEN
            # original_text 保留**改写前**的那句，与 rewritten_to 对照着看：
            # 「原来问的是什么 / 现在改成什么」要能一眼看出来
            notice = NOTICE_REWRITTEN
        findings.append(nf)

    for f in new_risk:
        nf = dict(f)
        nf["id"] = _next_finding_id(findings)
        findings.append(nf)
        notice = NOTICE_NEW_RISK

    rag_hits = dict(data.get("rag_hits") or {})
    if hits.get(node_id):
        rag_hits[node_id] = [
            {"title": h.title, "score": round(float(h.score), 4), "snippet": h.text[:160]}
            for h in hits[node_id]
        ]
    return await _persist(
        db,
        session,
        findings,
        rag_hits=rag_hits,
        nodes_scanned=int(data.get("nodes_scanned") or 0),
        model=str(data.get("model") or ""),
        notice=notice,
        revision=prev_revision + 1,
        # 复检重新检索过，以这次的结果为准（True 覆盖上次 False，反之也一样）
        rag_degraded=degraded,
    )
