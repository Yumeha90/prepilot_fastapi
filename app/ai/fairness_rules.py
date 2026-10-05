"""公平性检查的**规则层**（PRD §6.5 C5：规则库优先，LLM 补充）。

为什么要规则层，不全交给模型：
五类受保护特征（婚育 / 年龄 / 性别 / 户籍地域民族宗教 / 健康残疾）是**硬红线**，
判断标准稳定、可穷举，交给模型意味着「同一个问题这次判合规、下次判违规」。
规则命中即阻断，模型只负责两件规则做不了的事：① 语义层面的诱导性 / 预设答案式提问
（属警告级）；② 规则没覆盖到的隐性歧视，并给出改写建议。

**三语词典（PRD §7.3）**：问题链与面评可能以中 / 英 / 日任一语言生成，
只写中文关键词等于另外两种语言完全不设防 —— 这是合规功能，不能只覆盖一种语言。

**误报的取舍**：宁可漏，不可滥 —— 但这里的五个类别只要词出现在本公司的提问里，
基本就是违规（一句「你什么时候要孩子」不存在合规的语境），所以按**词面命中即报**；
只有「年龄」这类容易与「年限」混淆的才做了上下文限定（要求出现"岁/年龄/年纪"）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.session import (
    CHECK_AGE,
    CHECK_GENDER,
    CHECK_HEALTH,
    CHECK_LEADING,
    CHECK_MARRIAGE,
    CHECK_REGION,
    LEVEL_BLOCK,
    LEVEL_WARN,
)


@dataclass(frozen=True)
class Rule:
    """一类红线。pattern 命中即算命中，reason 是给面试官看的依据。"""

    category: str
    level: str
    pattern: re.Pattern[str]
    reason: str


# 五类硬红线 + 一类警告（诱导性交给模型，这里只登记类别，pattern 为空表示不参与词面匹配）
RULES: tuple[Rule, ...] = (
    Rule(
        CHECK_MARRIAGE,
        LEVEL_BLOCK,
        re.compile(
            # 中文
            r"结婚(了吗|了么|没有)|已婚|未婚|离异|有对象|有男(朋)?友|有女(朋)?友|谈恋爱|"
            r"什么时(候|间)要孩子|打算.*?要(孩子|小孩|宝宝)|生育(计划|打算|状况)|生孩子|"
            r"二胎|三胎|孩子谁带|谁(来)?带(的)?(孩子|娃)|婚育|婚否|备孕|怀孕|哺乳期|"
            r"有没有孩子|几个孩子|计划生育|什么时候结婚|打算结婚"
            # 英文（不收 `single`：技术提问里 single point of failure / single thread 遍地都是，
            # 收了就是满屏误报，反而没人看提示）
            r"|\bmarital status\b|\b(married|divorced)\b"
            r"|\bplan(?:ning)? to have (?:a )?(?:child|baby|kids)\b|\bpregnan(?:t|cy)\b"
            r"|\bchildcare\b|\bfamily plan(?:ning)?\b|\bdo you have (?:kids|children)\b"
            # 日文
            r"|結婚|既婚|未婚|離婚|出産|妊娠|育児|子育て|配偶者|家庭計画",
            re.IGNORECASE,
        ),
        "直接询问婚姻或生育计划——《妇女权益保障法》第四十三条第（二）项禁止"
        "「进一步询问或者调查女性求职者的婚育情况」，询问本身即违规。",
    ),
    Rule(
        CHECK_AGE,
        LEVEL_BLOCK,
        re.compile(
            r"\d{2}\s*岁(以下|以上|以内|左右)?|年纪(太大|偏大|大了|不小)|年龄(太大|偏大|超限|门槛|限制|歧视)|"
            r"限.{0,6}岁以下|(85|90|95|00)后|出生年代|年龄段|多大年纪|几几年的|属什么"
            # 英文：不收裸的 `over 35`（"over 30 servers" 这类会误报），只收明确的问法
            r"|\bhow old\b|\byour age\b|\bage (?:limit|requirement|restriction)\b|\bbirth year\b"
            r"|年齢|何歳|\d{2}歳",
            re.IGNORECASE,
        ),
        "以年龄设门槛或作评价——年龄与「工作内在要求」无必然联系，属不合理差别对待；"
        "招聘信息写年龄门槛还可能违反《人力资源市场暂行条例》第二十四条。",
    ),
    Rule(
        CHECK_GENDER,
        LEVEL_BLOCK,
        re.compile(
            r"限男性|限女性|男性优先|女性优先|只招(男|女)|仅招(男|女)|(男|女)(性)?(工程师|程序员|开发|主管)?优先|"
            r"女生(做|干|搞|扛|学)|女生(不|没)|男(生|人)(更|比较)(适合|抗压|能)|"
            r"已婚未育|已婚已育|女同志|小伙子|姑娘家|娘们|性别(要求|限制|偏好)|你是(男|女)的|"
            r"\b(male|female)[- ](only|preferred)\b|\bgender\b|\b(female|male) (?:engineer|developer|candidate)\b"
            r"|女性|男性|性別|女子|男子",
            re.IGNORECASE,
        ),
        "以性别或性别刻板印象作判断——违反《劳动法》第十三条与《就业促进法》第二十七条，"
        "也是九部门「六不得」的重点治理对象。",
    ),
    Rule(
        CHECK_REGION,
        LEVEL_BLOCK,
        re.compile(
            # 不收裸的「省份」：多地域部署之类的技术提问里会出现，容易误报
            r"户口|户籍|本地人|外地人|农村户口|老家(是|在)?哪|哪个省|某省人|家乡在|地域(标签|限制)|"
            r"少数民族|民族|藏族|维吾尔|回族|信仰|宗教|信教|信什么教|党员|政治面貌|口音"
            r"|\bhukou\b|\bhousehold registration\b|\bethnic(?:ity)?\b|\breligio(?:n|us)\b"
            r"|\bwhere are you (?:from|originally)\b|\bhometown\b|\bprovince\b"
            r"|戸籍|本籍|地元|出身地|民族|宗教|信仰",
            re.IGNORECASE,
        ),
        "涉及户籍 / 地域 / 民族 / 宗教——均属「先赋因素」，《就业促进法》第二十八条、"
        "第三十一条明确禁止，指导案例 185 号亦认定以地域拒录构成歧视。",
    ),
    Rule(
        CHECK_HEALTH,
        LEVEL_BLOCK,
        re.compile(
            r"病史|既往病史|慢性病|住过院|住院史|体检(结果|报告)|乙肝|传染病|病原携带|残疾|残障|"
            r"精神(疾病|病史)|抑郁症|焦虑症|遗传病|身体(不太好|吃得消|扛得住)|健康状况|有没有病|"
            r"\bmedical history\b|\bdisabilit(?:y|ies)\b|\bhepatitis\b|\bchronic disease\b"
            r"|\bmental (?:health|illness)\b|\bhealth (?:condition|status)\b"
            r"|病歴|既往症|障害|肝炎|持病|健康状態",
            re.IGNORECASE,
        ),
        "涉及健康、既往病史或残疾——医疗健康属敏感个人信息（《个人信息保护法》第二十八条），"
        "且不得以残疾或乙肝病原携带为由拒录（《就业促进法》第三十条）。",
    ),
)

# 六行检查表（PRD §5.5）：前五类规则可判，诱导性只能靠语义
CHECK_ORDER: tuple[str, ...] = (
    CHECK_MARRIAGE,
    CHECK_AGE,
    CHECK_GENDER,
    CHECK_REGION,
    CHECK_HEALTH,
    CHECK_LEADING,
)
# 规则层判不了的类别，交给模型
LLM_ONLY_CATEGORIES = (CHECK_LEADING,)


@dataclass
class Hit:
    """一处命中。"""

    node_id: str
    capability: str
    category: str
    level: str
    field: str
    snippet: str
    reason: str


def _fields(node: dict) -> list[tuple[str, str]]:
    """要扫的文本：主问题、每层追问（模糊 / 防伪）、观察点。

    观察点也扫：它是写进面评的判断依据，同样可能带歧视表述。
    """
    out: list[tuple[str, str]] = []
    main = str(node.get("main_question") or "")
    if main:
        out.append(("main_question", main))
    for f in node.get("followups") or []:
        if not isinstance(f, dict):
            continue
        level = f.get("level") or 1
        for key in ("vague", "anti_fake"):
            text = str(f.get(key) or "")
            if text:
                out.append((f"followup:{level}:{key}", text))
    for i, o in enumerate(node.get("observations") or []):
        text = str(o or "")
        if text:
            out.append((f"observation:{i}", text))
    return out


def scan_text(text: str) -> list[Hit]:
    """对**一段自由文本**做词面扫描（PRD §6.6 的「二次合规扫描」）。

    面评润色稿同样可能带出「年纪偏大」「女生做不了」这类表述 ——
    面试题拦住了、面评却放过去，等于把风险换了个地方落地。
    面评是要给 HR 与其他面试官看的正式记录，比题目更需要这道闸。
    """
    return scan_nodes([{"id": "", "capability": "", "main_question": str(text or "")}])


def scan_nodes(nodes: list[dict]) -> list[Hit]:
    """对问题链做词面扫描。每个节点每个类别只报一次（同类多条没意义，反而刷屏）。"""
    hits: list[Hit] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        capability = str(node.get("capability") or "")
        seen: set[str] = set()
        for field, text in _fields(node):
            for rule in RULES:
                if rule.category in seen:
                    continue
                m = rule.pattern.search(text)
                if not m:
                    continue
                seen.add(rule.category)
                hits.append(
                    Hit(
                        node_id=node_id,
                        capability=capability,
                        category=rule.category,
                        level=rule.level,
                        field=field,
                        snippet=m.group(0),
                        reason=rule.reason,
                    )
                )
    return hits
