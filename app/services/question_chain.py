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
from app.observability import metrics, tracing
from app.services import round_profile
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
    invalidate_fairness,
    observation_text,
    plain_text,
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
                    observations=[_obs(o) for o in (n.get("observations") or [])],
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
    # 用 Any 而非 str：模型偶发把观察点写成对象（{"type": "bad", "desc": "..."}），
    # 声明成 str 会让整次生成直接校验失败 —— 结构可以事后还原，题目丢了才真没救。
    observations: list[Any] = Field(
        default_factory=list, description="评分观察点，至少 2 条，每条一句纯文本"
    )
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
   不要写「考察其沟通能力」这种无法判分的话。每条**只写一句纯文本**，
   **不要输出 JSON / 字典**（例如 `{"type": "bad", "desc": "..."}`）——
   要区分好坏就在句子里直接写「答得好：…」「答得差：…」。
6. `rag_refs` 只填你在给定内部资料里真正用到的标题，**照抄给定资料里的标题原文**
   （不要加《》，不要改写、不要缩写）。没用到就留空。
7. 每个能力项只输出 1 个节点。"""


# HR 面另起一套：技术资产库里没有「这个人稳不稳定」的资料，硬检索只会迫使模型
# 拿技术文档去套 HR 维度，出来的题不伦不类。HR 面考的是**可核实的过往事实**，
# 依据在简历里（任职时长、离职间隔、自述期望），不在组织资产库里。
CHAIN_SYSTEM_PROMPT_HR = """你是资深 HR 面试官，要为一场 **HR 面**设计「问题链」。

你会拿到：岗位与轮次、本轮时长、HR 面能力-证据矩阵、候选人简历摘要。
HR 面不考技术能力（那些由技术面判定），要问清的是：**能不能来、待得住、要多少、为什么走**。

出題方法：
1. **行为面试法（STAR）**：问过去真实发生的事，不问假设和态度。
   「你抗压能力怎么样」是无效问题；「过去一年里最紧的一次排期是什么，你怎么扛过去的」才有效。
2. **追问拿事实，不拿表态**：候选人答得模糊时，追问具体时间、时长、频次、当时谁在场；
   怀疑包装时，追问可核实的细节（离职交接人、通知期、offer 构成）而不是让他再表态一次。
3. **禁止编造事实**：追问里的具体数字、公司名、时间，必须能在简历里找到；
   找不到就不要写数字，改成让候选人自己给出。
4. `main_question` 一句话问完，不超过 30 字；不要写成一段背景介绍。
5. `followups` 每层给两条，分工不同：
   - `vague`：答得模糊、只讲态度时，怎么追问拿到**行为证据**
   - `anti_fake`：怀疑说法有水分时，怎么**用客观事实验证**（不是逼问，是交叉核对）
6. `observations` 至少 2 条，写**可判分的观察点**（答出什么算好、答成什么样算差），
   不要写「考察其稳定性」这种无法判分的话。每条**只写一句纯文本**，
   **不要输出 JSON / 字典**（例如 `{"type": "bad", "desc": "..."}`）——
   要区分好坏就在句子里直接写「答得好：…」「答得差：…」。
7. `rag_refs` HR 面不使用内部资料，固定留空。
8. 每个能力项只输出 1 个节点。

**合规红线（违反即不合格，本轮必守）**：
- 不得追问或暗示婚育计划、是否有孩子、何时生育
- 不得评价或追问年龄、户籍、民族、宗教、健康状况与残疾
- 薪资只问**期望区间与构成**，不得追问当前薪资明细、家庭收入或负债
- 稳定性只能从**过往任职事实**（时长、离职原因、项目周期）去问，不从生活状况推断
- 到岗约束只问客观事实（竞业、通知期、签证），不得以此施压透露隐私"""


CHAIN_SYSTEM_PROMPT_TECH = CHAIN_SYSTEM_PROMPT  # 技术面是默认分支


def chain_system_prompt(round_type: str | None) -> str:
    return (
        CHAIN_SYSTEM_PROMPT_HR
        if round_profile.is_hr_round(round_type)
        else CHAIN_SYSTEM_PROMPT_TECH
    )


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
    return rag.search_assets(query, top_k=top_k)


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
    round_type: str = "",
    single: bool = False,
) -> str:
    hr = round_profile.is_hr_round(round_type)
    matrix_lines = []
    for r in rows:
        matrix_lines.append(
            f"- {r.get('capability')}（状态：{r.get('status')}，权重：{r.get('weight')}）"
            f"证据：{r.get('evidence') or '无'}｜考察重点：{r.get('focus') or '无'}"
        )
    focus_lines = []
    if hr:
        # HR 面没有内部资料可检索：给「考察重点 + 合规红线」，不出「检索到的内部资料」段。
        # 留着那段会让模型为了凑 rag_refs 去编造引用，反而把题目带偏。
        for r in focus:
            focus_lines.append(
                f"【{r.get('capability')}】状态={r.get('status')}；"
                f"考察重点={r.get('focus') or '无'}"
            )
        focus_block = (
            "【本次要出题的考察维度】\n" + "\n".join(focus_lines) + "\n\n"
            f"【合规红线（违反即不合格）】\n{round_profile.hr_redline_lines()}\n\n"
        )
        tail = (
            "请只输出 1 个节点（对应上面这 1 个维度），换一种问法与切入角度。"
            if single
            else f"请为下面 {len(focus)} 个考察维度各输出 1 个节点。"
        )
        return (
            f"岗位：{position.name}｜轮次：{round_name or 'HR 面'}"
            f"（HR 面，不考技术能力）｜本轮时长：{duration} 分钟\n\n"
            f"【HR 面能力-证据矩阵】\n" + "\n".join(matrix_lines) + "\n\n"
            f"【候选人简历摘要】\n{resume_digest(candidate)}\n\n"
            f"{focus_block}"
            f"{tail}"
        )

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




async def _call_llm(prompt: str, system: str = CHAIN_SYSTEM_PROMPT) -> _ChainLLM:
    import asyncio

    return await asyncio.to_thread(
        structured_call,
        _ChainLLM,
        system,
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


def _plain(text: Any) -> str:
    """去掉模型偶发吐出的 Markdown 标记（`**优秀**：` 这种在页面上很难看）。

    先过 `plain_text`：模型有时把一句话写成 JSON，页面上会露出
    `{"type": "bad", "desc": "..."}`，那不是给用户看的东西。
    """
    s = clean_text(plain_text(text))
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"^\s*[*#]+\s*", "", s)
    return s.strip()


def _obs(value: Any) -> str:
    """评分观察点 → 人能读的一句话（JSON 只取正文与「好 / 差」）。"""
    return _plain(observation_text(value))


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

        observations = [_obs(o) for o in node.observations]
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
    round_type: str = "",
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
        round_type=round_type,
        single=single,
    )
    data = await _call_llm(prompt, system=chain_system_prompt(round_type))
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
        # 幻觉重写是质量门禁真正起作用的证据，必须能被数出来：
        # 哪天这个数突然涨了，说明模型或提示词退化，而不是等面试官自己发现题目有假数字。
        metrics.record_degraded("chain.generate", "hallucination_rewrite")
        tracing.mark_degraded("幻觉检测命中，已重写一次", offenders=sorted(offenders))
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


@tracing.ai_step("chain.generate")
async def generate_chain(
    db, user: User, session: InterviewSession
) -> ChainOut:
    """C4 生成问题链（Celery 任务与测试共用；失败会向外抛，由调用方记录）。"""
    tracing.add_metadata(
        session_id=session.id,
        position_id=session.position_id,
        candidate_id=session.candidate_id,
        round_type=session.round_type,
    )
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
    # HR 面不检索组织技术资产库：库里是技术文档与故障复盘，拿它去问「稳定性」只会
    # 逼模型编引用。HR 面的依据在简历里，不在资产库里（round_profile 的注释同此理）。
    if not round_profile.is_hr_round(session.round_type):
        for r in focus:
            name = str(r.get("capability") or "")
            hits[name] = retrieve(name, str(r.get("focus") or ""))

    nodes = await _generate_nodes(
        position, candidate, rows, focus, hits, session.duration_minutes,
        round_name=session.round_name, round_type=session.round_type,
    )
    budget = balance_minutes(nodes, session.duration_minutes)
    tracing.add_metadata(
        nodes=len(nodes),
        flagged=sum(1 for n in nodes if n.get("flagged")),
        budget_minutes=budget,
        retrieved_libraries=0 if round_profile.is_hr_round(session.round_type) else len(hits),
    )
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
    # 题目变了，上一轮的公平性结论就作废 —— 它是对旧文本给的，留着会让
    # Step3 一进来就打勾（面试官以为检查过了，其实是上一次的结果）。
    # **只清结论不动会话状态**：回到 Step3 重新扫一次即可，不用从头备面。
    invalidate_fairness(session)
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
    observations = [_obs(o) for o in node.observations]
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
    hits = (
        {}
        if round_profile.is_hr_round(session.round_type)
        else {capability: retrieve(capability, str(row.get("focus") or ""))}
    )
    nodes = await _generate_nodes(
        position, candidate, rows, [row], hits, session.duration_minutes,
        round_name=session.round_name, round_type=session.round_type, single=True,
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


def _set_node_text(node: dict, field: str, text: str) -> None:
    """把文本写回节点的某个字段（公平性改写用）。

    `field` 形如 `main_question` / `followup:1:vague` / `observation:0`。
    定位不到就退回主问题 —— 改写总得落在某个地方，静默丢弃等于什么都没改。
    """
    if not field or field == "main_question":
        node["main_question"] = text[:MAX_MAIN_QUESTION]
        return
    if field.startswith("followup:"):
        parts = field.split(":", 2)
        if len(parts) == 3:
            _, level, key = parts
            for f in node.get("followups") or []:
                if isinstance(f, dict) and str(f.get("level")) == str(level):
                    f[key] = text
                    return
    if field.startswith("observation:"):
        try:
            index = int(field.split(":", 1)[1])
        except (TypeError, ValueError):
            index = -1
        observations = list(node.get("observations") or [])
        if 0 <= index < len(observations):
            observations[index] = text
            node["observations"] = observations
            return
    node["main_question"] = text[:MAX_MAIN_QUESTION]


async def patch_node_text(
    db, session: InterviewSession, node_id: str, field: str, text: str
) -> ChainOut:
    """替换单个节点某个字段的文本，其余节点原样保留。

    供 Step3 公平性「采纳改写」调用：改写只动被点名的那一句，
    面试官手改过的其他题目不受影响（与「换一换」同一原则）。
    """
    data = dict(session.chain_json or {})
    raw_nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
    index = next(
        (i for i, n in enumerate(raw_nodes) if str(n.get("id") or "") == node_id), None
    )
    if index is None:
        raise bad_request(ErrorCode.WORKBENCH_NODE_NOT_FOUND, "要改写的问题节点不存在")

    node = dict(raw_nodes[index])
    _set_node_text(node, field, text)
    raw_nodes[index] = node
    return await _persist(
        db,
        session,
        raw_nodes,
        budget=int(data.get("budget_minutes") or 0),
        rag_hits=data.get("rag_hits") or {},
        generated=bool(data.get("generated")),
    )


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
