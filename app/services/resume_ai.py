"""C2 简历结构化解析（L0 单次调用 + 超长降级 L1 分块）。

为什么默认 L0：原本给 C2 划的是 L1（段落分类 → 按段抽取 → 技术栈归一），
但拆开看只有「信息抽取」真需要 LLM —— 段落分类是规则（标题关键词 + 缩进），
技术栈归一是内置同义词表（§6.2 第 4 步）。拆三次约 9~15s，会撞 §9.2 的
「简历解析 ≤12s（P95）」，且任一环失败要整链重跑。所以默认退化成 L0。

阈值降级（D10）：L1 不是不用，而是留给**单次装不下**的长简历 ——
这才是 L1 该存在的场景，符合「分档用，不为用而用」。

两条硬口径：
- **只抽不编**：字段必须能在原文中找到，找不到就留空，禁止凭岗位名脑补
- **结果不落库**：返回给前端填充表单，人工纠错后点确认才写入（BR-04）
"""
from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from app.ai.llm import structured_call
from app.core.errors import ErrorCode, bad_request
from app.observability import metrics, tracing

logger = logging.getLogger(__name__)

MIN_RESUME_CHARS = 30
# 阈值（去空白字符数）。S1 实测后校准，常量集中在这里方便一处改动
L0_MAX_CHARS = 12_000
L1_MAX_CHARS = 24_000
CHUNK_CHARS = 8_000
MAX_CHUNKS = 3
# 单类最多保留条数（简历可以比 JD 长，给宽一点）
MAX_EXPERIENCE = 10
MAX_SKILLS = 30

_SECTION_KEYS = ("basic", "work", "projects", "skills", "education")


# ---------------------------------------------------------------- 输出结构


class ResumeBasic(BaseModel):
    name: str = Field(default="", description="候选人姓名")
    email: str = Field(default="", description="邮箱")
    phone: str = Field(default="", description="手机号")
    location: str = Field(default="", description="所在城市")
    years: str = Field(default="", description="工作年限，原文怎么写就怎么写，不要换算")


class ResumeWork(BaseModel):
    company: str = ""
    title: str = ""
    period: str = ""
    desc: str = ""


class ResumeProject(BaseModel):
    name: str = ""
    role: str = ""
    desc: str = ""


class ResumeEducation(BaseModel):
    school: str = ""
    major: str = ""
    degree: str = ""
    period: str = ""


class ResumeStructure(BaseModel):
    basic: ResumeBasic = Field(default_factory=ResumeBasic)
    work: list[ResumeWork] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[ResumeEducation] = Field(default_factory=list)
    confidence: dict[str, float] = Field(
        default_factory=dict,
        description="各区块置信度 0–1：basic/work/projects/skills/education，"
        "信息缺失或模糊时给低分，供前端标黄（§7.1）",
    )


SYSTEM_PROMPT = """你是资深招聘专员，负责把一份简历原文整理成结构化档案。

严格要求：
1. 只抽不编：每一项都必须能在原文中找到依据，**禁止**推测、补全或美化。
2. 原文没有的字段一律留空字符串，不要写"无""未知"之类的占位词。
3. 工作年限保持原文表述（如"5 年""三年半"），不要换算成数字。
4. 技能只列原文明确提到的技术 / 工具 / 语言，去掉熟练度修饰词（如"精通 Go" → "Go"）。
5. 置信度：该区块信息完整清晰给 0.8–1.0，部分缺失或表述模糊给 0.4–0.7，
   完全没有相关信息给 0–0.3。前端会据此标黄提示人工核对。
6. 工作经历与项目经验按原文顺序输出，最多各 10 条；技能最多 30 项。"""


# ---------------------------------------------------------------- 后处理


def _norm(text: str) -> str:
    """只去编号与项目符号 —— **不剥前导字母**。

    JD 拆解的 `normalize_items` 会剥孤立前导字母（模型偶尔吐出 `L本科及以上`），
    但简历字段不能照搬：公司名合法地以字母开头（"A公司""B站""TCL"），
    剥掉就是实打实的数据损坏。这里宁可容忍偶发脏前缀，也不能吃真内容。
    """
    s = re.sub(r"^(?:[0-9]+[.、)）:：]\s*|[-*•·▪◦]\s*)", "", str(text or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = _norm(raw)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _clamp_confidence(raw: dict | None) -> dict[str, float]:
    out = {k: 0.0 for k in _SECTION_KEYS}
    for key in _SECTION_KEYS:
        try:
            value = float((raw or {}).get(key, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        out[key] = round(min(1.0, max(0.0, value)), 2)
    return out


def _from_model(data: ResumeStructure) -> dict:
    basic = data.basic
    return {
        "basic": {
            "name": _norm(basic.name),
            "email": _norm(basic.email),
            "phone": _norm(basic.phone),
            "location": _norm(basic.location),
            "years": _norm(basic.years),
        },
        "work": [
            {
                "company": _norm(w.company),
                "title": _norm(w.title),
                "period": _norm(w.period),
                "desc": _norm(w.desc),
            }
            for w in data.work
            if _norm(w.company) or _norm(w.title) or _norm(w.desc)
        ][:MAX_EXPERIENCE],
        "projects": [
            {"name": _norm(p.name), "role": _norm(p.role), "desc": _norm(p.desc)}
            for p in data.projects
            if _norm(p.name) or _norm(p.desc)
        ][:MAX_EXPERIENCE],
        "skills": _dedup(data.skills)[:MAX_SKILLS],
        "education": [
            {
                "school": _norm(e.school),
                "major": _norm(e.major),
                "degree": _norm(e.degree),
                "period": _norm(e.period),
            }
            for e in data.education
            if _norm(e.school) or _norm(e.major)
        ][:MAX_EXPERIENCE],
        "confidence": _clamp_confidence(data.confidence),
    }


# ---------------------------------------------------------------- 分块与合并


def _split_chunks(text: str) -> list[str]:
    """按段落边界切块：只在换行处断开，避免把一段经历切成两半。"""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        buf.append(line)
        size += len(line) + 1
        if size >= CHUNK_CHARS:
            chunks.append("\n".join(buf))
            buf, size = [], 0
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if c.strip()]


def _merge(parts: list[dict]) -> dict:
    """合并分块结果：基本信息取首个非空，经历按序拼接，技能取并集，置信度取最低。"""
    merged: dict = {"work": [], "projects": [], "skills": [], "education": []}
    basic: dict[str, str] = {}
    for part in parts:
        for key, value in (part.get("basic") or {}).items():
            if value and not basic.get(key):
                basic[key] = value
        for key in ("work", "projects", "education"):
            merged[key].extend(part.get(key) or [])
        merged["skills"].extend(part.get("skills") or [])
    merged["basic"] = {k: basic.get(k, "") for k in ("name", "email", "phone", "location", "years")}
    merged["work"] = merged["work"][:MAX_EXPERIENCE]
    merged["projects"] = merged["projects"][:MAX_EXPERIENCE]
    merged["education"] = merged["education"][:MAX_EXPERIENCE]
    merged["skills"] = _dedup(merged["skills"])[:MAX_SKILLS]
    # 分块意味着至少有一块是"半份简历"，置信度取各块最低值更保守
    merged["confidence"] = {
        k: min((p.get("confidence", {}).get(k, 0.0) for p in parts), default=0.0)
        for k in _SECTION_KEYS
    }
    return merged


# ---------------------------------------------------------------- 入口


@tracing.ai_step("resume.parse")
async def parse_resume(raw_text: str) -> dict:
    """把简历原文解析成结构化档案 + 区块置信度。

    返回 `{profile, chunks, notice}`：`chunks` 供前端提示"简历较长，已分段解析"。
    """
    text = re.sub(r"[ \t\u3000]+", " ", (raw_text or "")).strip()
    if len(text) < MIN_RESUME_CHARS:
        metrics.record_op("resume.parse", "rejected")
        raise bad_request(
            ErrorCode.RESUME_TEXT_TOO_SHORT,
            f"简历内容太短（{len(text)} 字），请上传完整的简历或手动粘贴",
        )
    if len(text) > L1_MAX_CHARS:
        raise bad_request(
            ErrorCode.RESUME_TOO_LONG,
            f"简历过长（{len(text)} 字，上限 {L1_MAX_CHARS}），"
            "请精简后重新上传，或手动粘贴关键段落",
        )

    if len(text) <= L0_MAX_CHARS:
        data = await _call(text)
        tracing.add_metadata(chars=len(text), chunks=1, mode="L0")
        return {"profile": _from_model(data), "chunks": 1, "notice": ""}

    chunks = _split_chunks(text)
    if len(chunks) > MAX_CHUNKS:
        metrics.record_op("resume.parse", "rejected")
        raise bad_request(
            ErrorCode.RESUME_TOO_LONG,
            f"简历过长（需分 {len(chunks)} 段，上限 {MAX_CHUNKS} 段），"
            "请精简后重新上传，或手动粘贴关键段落",
        )
    logger.info("简历 %d 字，走 L1 分块解析（%d 段）", len(text), len(chunks))
    # 降级到分块解析必须能被看到：它意味着单次调用装不下，成本与耗时都会翻倍
    metrics.record_degraded("resume.parse", "chunked_l1")
    tracing.mark_degraded("简历过长，降级为分块解析", chunks=len(chunks))
    # 并发而非串行（2026-10-01）：云端单次调用实测 17~23s，3 段串行就是 50~70s，
    # 会撞 nginx proxy_read_timeout。各段互不依赖，gather 把墙钟压到单段的量级。
    results = await asyncio.gather(*(_call(c) for c in chunks))
    return {
        "profile": _merge([_from_model(r) for r in results]),
        "chunks": len(chunks),
        "notice": f"简历较长，已分 {len(chunks)} 段解析，请重点核对工作经历与项目经验",
    }


async def _call(text: str) -> ResumeStructure:
    return await asyncio.to_thread(
        structured_call,
        ResumeStructure,
        SYSTEM_PROMPT,
        f"请把下面这份简历整理成结构化档案：\n\n{text}",
        timeout=90,
    )
