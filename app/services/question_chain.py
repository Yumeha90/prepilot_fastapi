"""Step 2 问题链生成（PRD §3.4.2 / §6.4 P10，AI 链路 C4）。

**这一步和 C3 矩阵最大的区别是要检索**：矩阵是「JD 能力 × 简历证据」的对齐，
材料全在库里；问题链要出题，题目质量取决于**这家公司真正在意什么** ——
那就得去组织技术资产库里捞（内部技术文档 / 架构规范 / 故障复盘 / 历史优秀面评 /
Code Review 清单）。检索不到就编，编出来的题看着专业但对本公司毫无针对性，
RAG 就变成了摆设，出了问题还查不出来。所以 **Milvus 不可用直接失败，不降级**。

**为什么走异步 Celery + 前端轮询（BR-12）**：一次生成要检索 3~6 个能力项再交给模型，
云端实测 15~30s。同步等待会撞 nginx 的 120s 上限，而且期间页面完全无反馈。
与 S4「匹配分同步落库、AI 总结异步补写」是同一套路。

**幻觉检测（PRD §6.4 质量门禁）**：追问里出现的「具体数字 / 项目名」必须能在简历或
检索到的资料里找到。实现上只查**两位及以上**的数字（"3 年"这种单位性数字太常见，
查了全是误报），命中就重写一次；重写后仍命中，就给节点打 `flagged` 让面试官自己确认 ——
**不静默通过（等于没做门禁），也不无限循环烧 token**。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ai import rag
from app.ai.llm import structured_call
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request
from app.models.candidate import Candidate
from app.models.position import Position
from app.models.session import (
    CHAIN_FAILED,
    CHAIN_IDLE,
    CHAIN_READY,
    CHAIN_RUNNING,
    EVIDENCE_MISSING,
    EVIDENCE_VERIFY,
    S1_DRAFT,
    S2_DRAFT,
    S3_DRAFT,
    InterviewSession,
)
from app.models.user import User
from app.schemas.workbench import (
    ChainIn,
    ChainNode,
    ChainOut,
    ChainRagHit,
    ChainSaveOut,
)
from app.services.workbench import (
    blocked_reason,
    clean_text,
    ensure_can_edit,
    resume_digest,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# 开场自我介绍 + 收尾「你有什么想问我的」：这部分不进题目预算
RESERVE_MINUTES = 5
MIN_BUDGET_MINUTES = 10
MIN_NODE_MINUTES = 3
# 单题耗时上限：一场 4 小时的面试只问 3 组题时，每组能分到 78 分钟 ——
# 上限卡在 30 会让长面试的预算怎么也填不满（时长是面试官自己定的，不该由算法硬凑）
MAX_NODE_MINUTES = 90
# 45 分钟最多问 6 组题目：再多每组只能分到 5 分钟，问不出深度
MAX_NODES = 6
# 主问题上限（PRD 要求 ≤30 字）。这里放宽到 60 才硬截 ——
# 硬截到 30 会产出半截句子，比「稍长但完整」更糟，长度主要靠 prompt 约束
MAX_MAIN_QUESTION = 60
MAX_OBSERVATIONS = 4
MAX_FOLLOWUP_LEVELS = 3
# 人工保存的节点上限：比生成上限宽一点，允许面试官自己加题
MAX_SAVE_NODES = 12

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 视图


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


def chain_out(session: InterviewSession) -> ChainOut:
    data = session.chain_json or {}
    nodes_raw = data.get("nodes") if isinstance(data, dict) else None
    nodes: list[ChainNode] = []
    if isinstance(nodes_raw, list):
        for n in nodes_raw:
            if not isinstance(n, dict):
                continue
            nodes.append(
                ChainNode(
                    id=str(n.get("id") or ""),
                    row_id=str(n.get("row_id") or ""),
                    capability=str(n.get("capability") or ""),
                    status=str(n.get("status") or EVIDENCE_MISSING),
                    main_question=str(n.get("main_question") or ""),
                    followups=n.get("followups") or [],
                    observations=[str(o) for o in (n.get("observations") or [])],
                    minutes=int(n.get("minutes") or 0),
                    rag_refs=[str(r) for r in (n.get("rag_refs") or [])],
                    flagged=bool(n.get("flagged")),
                )
            )
    return ChainOut(
        nodes=nodes,
        generated=bool(data.get("generated")),
        revision=session.chain_revision or 0,
        updated_at=session.chain_updated_at,
        generated_at=str(data.get("generated_at") or ""),
        model=str(data.get("model") or ""),
        status=session.chain_status or CHAIN_IDLE,
        error=session.chain_error or "",
        budget_minutes=int(data.get("budget_minutes") or 0),
        rag_hits=_hits_payload(data),
    )


# ---------------------------------------------------------------- 生成


class _FollowupLLM(BaseModel):
    level: int = Field(default=1, description="第几层追问，从 1 开始")
    vague: str = Field(default="", description="候选人答得模糊时，怎么追问拿到行为证据")
    anti_fake: str = Field(default="", description="怀疑是背书/包装时，怎么验证真伪")


class _NodeLLM(BaseModel):
    capability: str = Field(default="", description="对应的能力项，必须与输入一致")
    main_question: str = Field(default="", description="主问题，不超过 30 字，一句话问完")
    followups: list[_FollowupLLM] = Field(default_factory=list, description="1-3 层追问")
    observations: list[str] = Field(default_factory=list, description="评分观察点，至少 2 条")
    minutes: int = Field(default=8, description="这一组题预计耗时（分钟）")
    rag_refs: list[str] = Field(
        default_factory=list, description="用到的内部资料标题，必须是给定资料里的原文标题"
    )


class _ChainLLM(BaseModel):
    nodes: list[_NodeLLM] = Field(default_factory=list)


CHAIN_SYSTEM_PROMPT = """你是资深面试官，要为一场真实面试设计「问题链」。

你会拿到：岗位与轮次、本轮时长、能力-证据矩阵、候选人简历摘要，
以及**本公司组织技术资产库里检索出来的内部资料**（内部技术文档 / 架构规范 /
故障复盘 / 历史优秀面评 / Code Review 清单）。

严格要求：
1. **必须结合内部资料出题**。本公司踩过的坑、写进规范的硬性要求、优秀面评里体现的
   评分口径，才是这轮面试真正的考察点。只用通用八股文的题目视为不合格。
2. **禁止编造事实**。追问里的具体数字、项目名、故障时间，必须能在简历或给定的内部
   资料里找到；找不到就不要写数字，改成让候选人自己给出量化结果。
3. `main_question` 一句话问完，不超过 30 字；不要写成一段背景介绍。
4. `followups` 每层给两条，分工不同：
   - `vague`：候选人答得模糊、只讲概念时，怎么追问拿到**行为证据**
   - `anti_fake`：怀疑是背书或团队成果包装成个人成果时，怎么**验证真伪**
   证据充足的能力项，追问重点放在「挖深度与边界」；证据缺失的，重点放在
   「判断是否真的具备」。
5. `observations` 至少 2 条，写**可判分的观察点**（答出什么算好、答成什么样算差），
   不要写「考察其沟通能力」这种无法判分的话。
6. `rag_refs` 只填你在给定内部资料里真正用到的标题，**照抄给定资料里的标题原文**
   （不要加《》，不要改写、不要缩写）。没用到就留空。
7. 每个能力项只输出 1 个节点。"""


def focus_rows(rows: list[dict]) -> list[dict]:
    """选要出题的能力项：**本轮重点** > 证据待核实/缺失 > 其余补齐。

    PRD §6.4 说取「待核实 + 缺失」作重点，但「本轮重点」是面试官在 P09 明确勾过的，
    优先级应当更高 —— 勾了却不出题，等于那个勾选框没用。
    """
    if not rows:
        return []
    keyed = [r for r in rows if r.get("is_key")]
    risky = [
        r for r in rows if str(r.get("status") or "") in (EVIDENCE_VERIFY, EVIDENCE_MISSING)
    ]
    picked: list[dict] = []
    seen: set[str] = set()
    for r in keyed + risky + list(rows):
        name = str(r.get("capability") or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        picked.append(r)
        if len(picked) >= MAX_NODES:
            break
    return picked


def retrieve(capability: str, focus: str, top_k: int = rag.DEFAULT_TOP_K) -> list[rag.AssetHit]:
    """检索组织技术资产库（可 mock：测试锁的是编排，不是向量库）。"""
    query = f"{capability}。考察重点：{focus}".strip()
    return rag.search(query, top_k=top_k)


def _asset_block(hits: list[rag.AssetHit]) -> str:
    if not hits:
        return "（本项未检索到内部资料）"
    return "\n".join(f"- 《{h.title}》：{h.text[:300]}" for h in hits)


def _prompt(
    *,
    position: Position,
    candidate: Candidate,
    rows: list[dict],
    focus: list[dict],
    hits: dict[str, list[rag.AssetHit]],
    duration: int,
    round_name: str = "",
    single: bool = False,
) -> str:
    matrix_lines = []
    for r in rows:
        matrix_lines.append(
            f"- {r.get('capability')}（状态：{r.get('status')}，权重：{r.get('weight')}）"
            f"证据：{r.get('evidence') or '无'}｜考察重点：{r.get('focus') or '无'}"
        )
    focus_lines = []
    for r in focus:
        focus_lines.append(
            f"【{r.get('capability')}】状态={r.get('status')}；"
            f"考察重点={r.get('focus') or '无'}\n检索到的内部资料：\n"
            f"{_asset_block(hits.get(str(r.get('capability')), []))}"
        )
    tail = (
        "请只输出 1 个节点（对应上面这 1 个能力项），换一种问法与切入角度。"
        if single
        else f"请为下面 {len(focus)} 个能力项各输出 1 个节点。"
    )
    return (
        f"岗位：{position.name}｜轮次：{round_name or '本轮'}｜本轮时长：{duration} 分钟\n\n"
        f"【能力-证据矩阵】\n" + "\n".join(matrix_lines) + "\n\n"
        f"【候选人简历摘要】\n{resume_digest(candidate)}\n\n"
        f"【本次要出题的能力项与检索到的内部资料】\n" + "\n\n".join(focus_lines) + "\n\n"
        f"{tail}"
    )




async def _call_llm(prompt: str) -> _ChainLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _ChainLLM,
        CHAIN_SYSTEM_PROMPT,
        prompt,
        timeout=120,
    )


def _unsupported_numbers(text: str, allowed: str) -> list[str]:
    """找出生成文本里「材料里找不到」的数字（只看两位及以上，避免单位性数字误报）。"""
    allowed_nums = set(_NUM_RE.findall(allowed))
    return sorted(
        {n for n in _NUM_RE.findall(text) if len(n) >= 2 and n not in allowed_nums}
    )


def _node_text(node: _NodeLLM) -> str:
    parts = [node.main_question, node.rag_refs and " ".join(node.rag_refs) or ""]
    for f in node.followups:
        parts.append(f.vague)
        parts.append(f.anti_fake)
    parts.extend(node.observations)
    return " ".join(p for p in parts if p)


def _title_key(title: str) -> str:
    """标题归一化：模型几乎一定会加上书名号/引号，直接等值比较必然匹配不上。"""
    return re.sub(r"[\s《》〈〉「」\"'`*]", "", str(title or ""))


def _match_refs(refs: list[str], allowed_titles: set[str]) -> list[str]:
    """只保留检索结果里真实存在的标题 —— 模型编的引用等于伪造出处。

    书名号、多余空格导致的形变允许通过（那是排版，不是造假）；
    但**完全对不上的一律丢弃**，宁可少显示一条引用，也不能让页面出现不存在的资料。
    """
    keys = {_title_key(t): t for t in allowed_titles}
    out: list[str] = []
    for ref in refs:
        key = _title_key(ref)
        if not key:
            continue
        if key in keys:
            out.append(keys[key])
            continue
        # 形变兜底：双向包含（模型可能只写了标题的前半段）
        hit = next(
            (real for k, real in keys.items() if len(key) >= 6 and (key in k or k in key)),
            None,
        )
        if hit and hit not in out:
            out.append(hit)
    return out


def _plain(text: str) -> str:
    """去掉模型偶发吐出的 Markdown 标记（`**优秀**：` 这种在页面上很难看）。"""
    s = clean_text(text)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[*#]+\s*", "", s)
    return s.strip()


def _normalize(
    data: _ChainLLM,
    focus: list[dict],
    hits: dict[str, list[rag.AssetHit]],
) -> list[dict]:
    """模型输出 → 存储结构：对齐能力项、清洗、裁量、校验 rag_refs 不是编的。"""
    by_name = {str(r.get("capability") or "").strip().lower(): r for r in focus}
    out: list[dict] = []
    for i, node in enumerate(data.nodes):
        name = str(node.capability or "").strip()
        row = by_name.get(name.lower())
        if row is None:
            # 模型改写了能力项名：按顺序回退对齐，找不到就跳过
            row = focus[i] if i < len(focus) else None
            if row is None:
                continue
            name = str(row.get("capability") or "")
        allowed_titles = {h.title for h in hits.get(name, [])}

        followups: list[dict] = []
        for j, f in enumerate(node.followups[:MAX_FOLLOWUP_LEVELS]):
            vague = _plain(f.vague)
            anti_fake = _plain(f.anti_fake)
            if not vague and not anti_fake:
                continue
            followups.append({"level": j + 1, "vague": vague, "anti_fake": anti_fake})
        if not followups:
            followups.append({"level": 1, "vague": "", "anti_fake": ""})

        observations = [_plain(o) for o in node.observations]
        observations = [o for o in observations if o][:MAX_OBSERVATIONS]

        out.append(
            {
                "id": f"n{i + 1}",
                "row_id": str(row.get("id") or ""),
                "capability": name,
                "status": str(row.get("status") or EVIDENCE_MISSING),
                "main_question": _plain(node.main_question)[:MAX_MAIN_QUESTION],
                "followups": followups,
                "observations": observations,
                "minutes": _clamp_minutes(node.minutes),
                # 只保留检索结果里真实存在的标题：模型编的引用等于伪造出处
                "rag_refs": _match_refs(node.rag_refs, allowed_titles),
                "flagged": False,
            }
        )
    return out


def _clamp_minutes(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return MIN_NODE_MINUTES
    return max(MIN_NODE_MINUTES, min(minutes, MAX_NODE_MINUTES))


def balance_minutes(nodes: list[dict], duration: int) -> int:
    """按预算重新分配每题耗时，返回实际预算。

    模型给的 `minutes` 只是初始估计，加起来几乎不可能正好等于本轮时长 ——
    直接采信的话 45 分钟的面试会被排进 70 分钟的题量，面试官根本问不完。

    分配办法：先给每题保底 `MIN_NODE_MINUTES`，剩下的按模型给的**比例**分，
    零头按小数部分从大到小补。这样「模型认为哪题更重」的判断被保留，
    又保证总和落在预算上（除非预算大到每道题都顶到 `MAX_NODE_MINUTES`，
    那种情况下问不完是面试官自己选的时长，不该由算法硬凑）。
    """
    budget = max(int(duration) - RESERVE_MINUTES, MIN_BUDGET_MINUTES)
    if not nodes:
        return budget
    n = len(nodes)
    raw = [_clamp_minutes(x.get("minutes")) for x in nodes]
    total = sum(raw) or n
    minutes = [MIN_NODE_MINUTES] * n
    remaining = budget - MIN_NODE_MINUTES * n

    if remaining > 0:
        desired = [remaining * r / total for r in raw]
        for i in range(n):
            minutes[i] = min(MAX_NODE_MINUTES, MIN_NODE_MINUTES + int(desired[i]))
        drift = budget - sum(minutes)
        order = sorted(range(n), key=lambda i: desired[i] - int(desired[i]), reverse=True)
        while drift > 0:
            moved = False
            for i in order:
                if drift <= 0:
                    break
                if minutes[i] < MAX_NODE_MINUTES:
                    minutes[i] += 1
                    drift -= 1
                    moved = True
            if not moved:  # 每题都顶到上限了，再补不进去
                break
    for node, value in zip(nodes, minutes):
        node["minutes"] = value
    return budget


def _allowed_text(candidate: Candidate, hits: dict[str, list[rag.AssetHit]]) -> str:
    parts = [resume_digest(candidate)]
    for items in hits.values():
        parts.extend(h.text for h in items)
    return "\n".join(parts)


async def _generate_nodes(
    position: Position,
    candidate: Candidate,
    rows: list[dict],
    focus: list[dict],
    hits: dict[str, list[rag.AssetHit]],
    duration: int,
    *,
    round_name: str = "",
    single: bool = False,
) -> list[dict]:
    prompt = _prompt(
        position=position,
        candidate=candidate,
        rows=rows,
        focus=focus,
        hits=hits,
        duration=duration,
        round_name=round_name,
        single=single,
    )
    data = await _call_llm(prompt)
    nodes = _normalize(data, focus, hits)
    if not nodes:
        raise bad_request(ErrorCode.LLM_FAILED, "AI 未产出有效的问题节点，请重试")

    allowed = _allowed_text(candidate, hits)
    offenders = {
        n["capability"]: _unsupported_numbers(_node_text_of(n), allowed) for n in nodes
    }
    offenders = {k: v for k, v in offenders.items() if v}
    if offenders:
        # 命中幻觉门禁：**重写一次**（不是无限重试）。重写后仍命中就打 flagged
        # 交给面试官确认 —— 静默通过等于没做门禁，无限重试则白烧 token
        logger.warning("问题链幻觉检测命中，触发一次重写：%s", offenders)
        detail = "；".join(f"{k}（可疑数字：{'、'.join(v)}）" for k, v in offenders.items())
        retry_prompt = (
            prompt
            + "\n\n【重写要求】上一版里出现了简历与内部资料中找不到的数字：\n"
            + detail
            + "\n请删掉这些无来源的数字，或改成让候选人自己给出量化结果，其余要求不变。"
        )
        data = await _call_llm(retry_prompt)
        nodes = _normalize(data, focus, hits) or nodes
        for n in nodes:
            if _unsupported_numbers(_node_text_of(n), allowed):
                n["flagged"] = True
    return nodes


def _node_text_of(node: dict) -> str:
    parts = [str(node.get("main_question") or "")]
    for f in node.get("followups") or []:
        parts.append(str(f.get("vague") or ""))
        parts.append(str(f.get("anti_fake") or ""))
    parts.extend(str(o) for o in (node.get("observations") or []))
    return " ".join(parts)


def _hits_store(hits: dict[str, list[rag.AssetHit]]) -> dict[str, list[dict]]:
    return {
        key: [
            {"title": h.title, "score": round(float(h.score), 4), "snippet": h.text[:160]}
            for h in items
        ]
        for key, items in hits.items()
    }


async def generate_chain(
    db, user: User, session: InterviewSession
) -> ChainOut:
    """C4 生成问题链（Celery 任务与测试共用；失败会向外抛，由调用方记录）。"""
    ensure_can_edit(user, session)
    candidate = await db.get(Candidate, session.candidate_id)
    position = await db.get(Position, session.position_id)
    reason = blocked_reason(candidate, position)
    if reason == "jd_not_confirmed":
        raise bad_request(
            ErrorCode.WORKBENCH_NO_JD, "该职位的 JD 还未确认，无法生成问题链"
        )
    if reason == "resume_missing":
        raise bad_request(
            ErrorCode.WORKBENCH_NO_RESUME, "候选人简历为空或已粉碎，无法生成问题链"
        )
    assert candidate is not None and position is not None

    rows = [r for r in ((session.matrix_json or {}).get("rows") or []) if isinstance(r, dict)]
    if not rows:
        raise bad_request(
            ErrorCode.WORKBENCH_CHAIN_NO_MATRIX, "请先生成能力-证据矩阵，再生成问题链"
        )
    focus = focus_rows(rows)

    hits: dict[str, list[rag.AssetHit]] = {}
    for r in focus:
        name = str(r.get("capability") or "")
        hits[name] = retrieve(name, str(r.get("focus") or ""))

    nodes = await _generate_nodes(
        position, candidate, rows, focus, hits, session.duration_minutes,
        round_name=session.round_name,
    )
    budget = balance_minutes(nodes, session.duration_minutes)
    return await _persist(
        db,
        session,
        nodes,
        budget=budget,
        rag_hits=_hits_store(hits),
        generated=True,
    )


async def _persist(
    db,
    session: InterviewSession,
    nodes: list[dict],
    *,
    budget: int,
    rag_hits: dict,
    generated: bool,
) -> ChainOut:
    now = _now()
    data: dict[str, Any] = dict(session.chain_json or {})
    data["nodes"] = nodes
    data["generated"] = generated
    data["budget_minutes"] = budget
    data["rag_hits"] = rag_hits
    if generated:
        data["generated_at"] = now.isoformat()
        data["model"] = settings.LLM_MODEL
    session.chain_json = data
    session.chain_revision = (session.chain_revision or 0) + 1
    session.chain_updated_at = now
    session.chain_status = CHAIN_READY
    session.chain_error = None
    session.updated_at = now
    # Step2 有产物即推进到 s3_draft；同样只前进不回退
    if session.status in (S1_DRAFT, S2_DRAFT):
        session.status = S3_DRAFT
    await db.commit()
    await db.refresh(session)
    return chain_out(session)


# ---------------------------------------------------------------- 保存与微调


def _clean_node(node: ChainNode, index: int) -> dict:
    followups = []
    for j, f in enumerate(node.followups[:MAX_FOLLOWUP_LEVELS]):
        vague = _plain(f.vague)
        anti_fake = _plain(f.anti_fake)
        if not vague and not anti_fake:
            continue
        followups.append({"level": j + 1, "vague": vague, "anti_fake": anti_fake})
    observations = [_plain(o) for o in node.observations]
    observations = [o for o in observations if o][:MAX_OBSERVATIONS]
    return {
        "id": clean_text(node.id) or f"n{index + 1}",
        "row_id": clean_text(node.row_id),
        "capability": clean_text(node.capability),
        "status": node.status or EVIDENCE_MISSING,
        "main_question": _plain(node.main_question)[:MAX_MAIN_QUESTION],
        "followups": followups,
        "observations": observations,
        "minutes": _clamp_minutes(node.minutes),
        "rag_refs": [str(r) for r in node.rag_refs][:5],
        # 人工改过之后就不再算「疑似幻觉」：面试官已经看过并认可了
        "flagged": False,
    }


async def save_chain(db, user: User, session: InterviewSession, payload: ChainIn) -> ChainSaveOut:
    """整块保存人工微调（换一换 / 改题 / 调顺序 / 调耗时）。"""
    ensure_can_edit(user, session)
    if not payload.nodes:
        raise bad_request(ErrorCode.WORKBENCH_CHAIN_INVALID, "问题链不能为空")

    nodes: list[dict] = []
    for i, node in enumerate(payload.nodes[:MAX_SAVE_NODES]):
        if not node.main_question.strip():
            raise bad_request(
                ErrorCode.WORKBENCH_CHAIN_INVALID, f"第 {i + 1} 个节点还缺主问题"
            )
        nodes.append(_clean_node(node, i))
    if not nodes:
        raise bad_request(ErrorCode.WORKBENCH_CHAIN_INVALID, "问题链至少要有 1 个节点")

    stale = payload.revision is not None and payload.revision != (session.chain_revision or 0)
    data = dict(session.chain_json or {})
    chain = await _persist(
        db,
        session,
        nodes,
        budget=int(data.get("budget_minutes") or 0),
        rag_hits=data.get("rag_hits") or {},
        generated=bool(data.get("generated")),
    )
    return ChainSaveOut(chain=chain, stale=stale)


async def regenerate_node(
    db, user: User, session: InterviewSession, node_id: str
) -> ChainSaveOut:
    """「换一换」：只重生成单个节点，其余节点原样保留。

    同步执行（单节点一次模型调用，云端 3~6s），前端走 AI 超时。
    整链重新生成会把面试官已经手改过的其他题目一起冲掉 —— 那不是「换一换」，是返工。
    """
    ensure_can_edit(user, session)
    data = dict(session.chain_json or {})
    raw_nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
    index = next(
        (i for i, n in enumerate(raw_nodes) if str(n.get("id") or "") == node_id), None
    )
    if index is None:
        raise bad_request(ErrorCode.WORKBENCH_NODE_NOT_FOUND, "要重新生成的节点不存在")

    candidate = await db.get(Candidate, session.candidate_id)
    position = await db.get(Position, session.position_id)
    if candidate is None or position is None:
        raise bad_request(ErrorCode.WORKBENCH_NO_RESUME, "候选人或职位数据缺失")

    current = raw_nodes[index]
    capability = str(current.get("capability") or "")
    rows = [r for r in ((session.matrix_json or {}).get("rows") or []) if isinstance(r, dict)]
    row = next(
        (r for r in rows if str(r.get("capability") or "") == capability),
        {"id": current.get("row_id"), "capability": capability, "status": current.get("status"), "focus": ""},
    )
    hits = {capability: retrieve(capability, str(row.get("focus") or ""))}
    nodes = await _generate_nodes(
        position, candidate, rows, [row], hits, session.duration_minutes,
        round_name=session.round_name, single=True,
    )
    if not nodes:
        raise bad_request(ErrorCode.LLM_FAILED, "AI 未产出有效的问题节点，请重试")

    fresh = nodes[0]
    fresh["id"] = node_id
    fresh["row_id"] = str(row.get("id") or "")
    raw_nodes[index] = fresh
    budget = balance_minutes(raw_nodes, session.duration_minutes)

    merged_hits = dict(data.get("rag_hits") or {})
    merged_hits[capability] = _hits_store(hits).get(capability, [])
    chain = await _persist(
        db,
        session,
        raw_nodes,
        budget=budget,
        rag_hits=merged_hits,
        generated=bool(data.get("generated")),
    )
    return ChainSaveOut(chain=chain, stale=False)


# ---------------------------------------------------------------- 异步状态


async def mark_running(db, session: InterviewSession, task_id: str) -> None:
    session.chain_status = CHAIN_RUNNING
    session.chain_task_id = task_id
    session.chain_error = None
    session.updated_at = _now()
    await db.commit()


async def mark_failed(db, session: InterviewSession, message: str) -> None:
    session.chain_status = CHAIN_FAILED
    session.chain_error = message[:500]
    session.updated_at = _now()
    await db.commit()
