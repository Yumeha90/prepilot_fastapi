"""C1 JD 结构化拆解（L0：单次结构化调用，不上 LangGraph）。

为什么不上图：这就是一次「原文 → 结构化 JSON」的确定性转换，没有循环、
没有条件分支、不需要中断等待人工。重试交给 tenacity。
LangGraph 留给真正有状态的 C4（生成 → 校验 → 重生成）与 C5（BLOCK → 改写 → 再扫描）。

两条硬口径：
- **只拆不扩**：所有条目必须能在 JD 原文中找到依据，禁止凭岗位名脑补
- **结果不落库**：返回给前端填充表单，HR 编辑确认后随正常保存才生效（BR-01）

模型输出不能直接信：实测出现过条目带脏前缀（`L本科及以上`），
因此一律走 `normalize_items / normalize_competencies` 清洗。
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from app.ai.llm import structured_call
from app.core.errors import ErrorCode, bad_request
from app.schemas.position import CompetencyIn
from app.services.position import even_weights, normalize_competencies, normalize_items

logger = logging.getLogger(__name__)

MIN_JD_CHARS = 20
MAX_JD_CHARS = 8000


class JdStructure(BaseModel):
    """C1 输出结构。"""

    hard_gates: list[str] = Field(
        default_factory=list,
        description="硬性门槛：简历必须满足的硬性条件（学历、年限、必备技术或资质）",
    )
    competencies: list[str] = Field(
        default_factory=list,
        description="核心能力：岗位真正要考察的能力维度，3–5 项",
    )
    bonuses: list[str] = Field(
        default_factory=list, description="加分项：锦上添花但非必需的经历或技能"
    )


SYSTEM_PROMPT = """你是资深招聘顾问，负责把一份 JD 拆成结构化要素。

严格要求：
1. 只拆不扩：每一项都必须能在原文中找到依据，**禁止**凭岗位名称脑补或补充行业常识。
2. 硬性门槛：简历中必须满足的硬性条件（学历、工作年限、必备技术栈或资质），不满足即一票否决。
3. 核心能力：岗位真正要考察的能力维度，用于面试提问与人岗匹配加权，输出 3–5 项。
4. 加分项：锦上添花的经历或技能，非必需。
5. 每类最多 5 项；条目写成简洁中文短语，**不要**带编号、项目符号、引号或任何前缀字符。
6. 原文中确实没有对应内容时，该类返回空数组，宁缺毋滥。"""


async def parse_jd(raw_text: str) -> dict:
    """把 JD 原文拆成 {hard_gates, competencies, bonuses}。核心能力权重默认均分。"""
    text = (raw_text or "").strip()
    if len(text) < MIN_JD_CHARS:
        raise bad_request(
            ErrorCode.JD_TEXT_TOO_SHORT,
            f"JD 原文太短（{len(text)} 字），请先填写或上传完整的 JD 内容",
        )

    data = await asyncio.to_thread(
        structured_call,
        JdStructure,
        SYSTEM_PROMPT,
        f"请把下面这份 JD 拆成硬性门槛 / 核心能力 / 加分项：\n\n{text[:MAX_JD_CHARS]}",
        timeout=60,
    )

    gates = normalize_items(data.hard_gates, "hard_gate")
    comps_text = normalize_items(data.competencies, "competency")
    bonuses = normalize_items(data.bonuses, "bonus")

    # 权重默认均分（已保证落在 5 的倍数上，见 even_weights）
    weights = even_weights(len(comps_text))
    competencies = normalize_competencies(
        [CompetencyIn(text=t, weight=w) for t, w in zip(comps_text, weights)]
    )

    notice = ""
    if len(competencies) < 3:
        notice = f"AI 只拆出 {len(competencies)} 项核心能力，至少需要 3 项，请手动补充后再保存"
    return {
        "hard_gates": gates,
        "competencies": competencies,
        "bonuses": bonuses,
        "notice": notice,
    }
