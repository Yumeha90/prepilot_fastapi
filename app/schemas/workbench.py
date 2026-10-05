"""AI 备面工作台（PRD 3.4 / §5.9 P09）出入参。

**矩阵为什么整块进出而不是逐行接口**：P09 的表格是「整体替换」的编辑模型 ——
面试官改完一堆行再点一次保存，逐行接口会把一次编辑拆成 N 个请求，
中途失败就是半截状态。整块提交 + `revision` 校验就够了：
revision 不匹配说明本地落后于服务端（比如另一个标签页刚生成过），前端提示刷新。

`revision` **不做乐观锁强制拦截**（返回 409 让面试官白改一次是灾难），
只做提示：以服务端最新为准覆盖，前端 toast 说明。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.session import (
    CONCLUSION_PENDING,
    DISPOSITION_PENDING,
    EVIDENCE_MISSING,
    FAIRNESS_IDLE,
    LEVEL_INFO,
    ROW_SOURCE_JD,
)


class MatrixRow(BaseModel):
    """矩阵一行 = 一个能力要求项。

    - `source` 决定这一行是哪来的：JD 核心能力 / AI 从简历补的 / 面试官手加的。
      页面上三者的可操作性不同（JD 行不能删只能改考察重点，手加行可删），
      存下来才知道该怎么渲染。
    - `weight` 只来自 JD（展示用，让面试官知道这项占多少分）；
      简历补的行没有权重，前端留空而不是显示 0 —— 显示 0 会被误读成「这项不计分」。
    """

    id: str = ""
    source: str = ROW_SOURCE_JD
    capability: str = ""
    weight: float | None = None
    evidence: str = ""
    status: str = EVIDENCE_MISSING
    focus: str = ""
    # 「本轮重点」唯一入口在 P09（PRD §3.4.1：JD 编辑页不提供该勾选）
    is_key: bool = False


class MatrixOut(BaseModel):
    rows: list[MatrixRow] = Field(default_factory=list)
    generated: bool = False
    revision: int = 0
    updated_at: datetime | None = None
    generated_at: str = ""
    model: str = ""


class MatrixIn(BaseModel):
    rows: list[MatrixRow] = Field(default_factory=list)
    # 前端带上自己拿到的 revision；与服务端不一致时返回 `stale=True` 提示刷新
    revision: int | None = None


class MatrixSaveOut(BaseModel):
    matrix: MatrixOut
    # 本地 revision 落后于服务端（别处刚改过）：已按最新覆盖，前端提示一下
    stale: bool = False


class DurationIn(BaseModel):
    # 上下限在 service 里判（返回带 code 的 400），不放 pydantic 约束 ——
    # 校验失败会变成 422 + detail 数组，前端拿不到 code，只能弹一句含糊的"请求失败"
    duration_minutes: int = 45


class WorkbenchStep(BaseModel):
    """顶部步骤指示器。state: done / current / todo / disabled"""

    key: str
    state: str


# ---------------------------------------------------------------- Step2 问题链


class ChainFollowup(BaseModel):
    """一层追问（PRD §3.4.2：1-N 层，每层给两条候选）。

    两条的分工不同，不是同义反复：
    - `vague`：候选人答得**模糊**时怎么往下挖（拿行为证据）
    - `anti_fake`：怀疑**背书 / 包装**时怎么验证（防伪）
    """

    level: int = 1
    vague: str = ""
    anti_fake: str = ""


class ChainNode(BaseModel):
    """问题链一个节点 = 一个能力项展开成的一组题目。"""

    id: str = ""
    # 关联回矩阵行：面试官改了矩阵后能看出哪些节点已经过时
    row_id: str = ""
    capability: str = ""
    status: str = EVIDENCE_MISSING
    # 主问题 ≤30 字：超过就不是「问题」而是「一段背景」，候选人听完不知道从哪答
    main_question: str = ""
    followups: list[ChainFollowup] = Field(default_factory=list)
    # 评分观察点（PRD 要求 ≥2 条）：答成什么样算好、什么样算差
    observations: list[str] = Field(default_factory=list)
    minutes: int = 0
    # 生成这一题时引用到的内部资料标题（服务端校验过，不会是模型编的）
    rag_refs: list[str] = Field(default_factory=list)
    # 幻觉检测没过（追问里出现了材料里找不到的数字）：提示面试官自行确认
    flagged: bool = False


class ChainRagHit(BaseModel):
    """一次检索命中的内部资料 —— UI 上展示它，才能说明「这题不是凭空编的」。"""

    title: str = ""
    score: float = 0.0
    snippet: str = ""


class ChainOut(BaseModel):
    nodes: list[ChainNode] = Field(default_factory=list)
    generated: bool = False
    revision: int = 0
    updated_at: datetime | None = None
    generated_at: str = ""
    model: str = ""
    # 异步生成状态：idle / running / ready / failed（前端据此轮询或显示骨架屏）
    status: str = "idle"
    # 失败原因（落库，页面上要能看出是向量库不可用还是 AI 没产出）
    error: str = ""
    # 时长预算 = 本轮时长 - 开场收尾预留
    budget_minutes: int = 0
    # 能力项 → 检索到的内部资料
    rag_hits: dict[str, list[ChainRagHit]] = Field(default_factory=dict)


class ChainIn(BaseModel):
    nodes: list[ChainNode] = Field(default_factory=list)
    revision: int | None = None


class ChainSaveOut(BaseModel):
    chain: ChainOut
    stale: bool = False


class ChainTaskOut(BaseModel):
    """触发异步生成后的回执：前端拿 task_id 只是留痕，轮询看的是会话里的 status。"""

    session_id: int
    task_id: str = ""
    status: str = "running"


# ---------------------------------------------------------------- Step3 公平性


class FairnessFinding(BaseModel):
    """一条命中。

    - `field` 定位到具体字段（`main_question` / `followup:1:vague` / `observation:0`），
      前端才能高亮到那一句，而不是让面试官自己对着整条问题链找。
    - `source` 区分规则命中与模型命中：规则命中的依据来自法条与制度，模型命中的
      是语义判断 —— 两者的可信度不同，展示时要能看出来。
    - `disposition`：阻断项必须改写；警告级允许「保留并记录原因」，原因要留痕。
      `rewritten` = 已采纳改写并**复检通过**（这一条不再计入结论）。
    - `rewritten_to`：改写落到的文本，页面上要能看出「原来那句被改成什么了」。
    - `original_text`：命中所在的**整句原文**（不只是命中片段）。面试官要在这个基础上改，
      只给一个片段等于让他自己回去翻问题链找原句。
    """

    id: str = ""
    node_id: str = ""
    capability: str = ""
    level: str = LEVEL_INFO
    category: str = ""
    field: str = ""
    original_text: str = ""
    snippet: str = ""
    reason: str = ""
    suggestion: str = ""
    source: str = "rule"
    refs: list[str] = Field(default_factory=list)
    disposition: str = DISPOSITION_PENDING
    accepted_reason: str = ""
    rewritten_to: str = ""


class FairnessCheckItem(BaseModel):
    """六行检查表的一行（PRD §5.5）。level 是这类的最高等级，count 是命中条数。"""

    key: str = ""
    level: str = LEVEL_INFO
    count: int = 0


class FairnessOut(BaseModel):
    """Step3 结论。

    `result` 三态：**block 阻断（存在未处置的阻断项）/ warn 警告（可继续）/
    pass 通过**。idle 表示还没扫描过。
    """

    result: str = FAIRNESS_IDLE
    scanned: bool = False
    scanned_at: str = ""
    model: str = ""
    nodes_scanned: int = 0
    checks: list[FairnessCheckItem] = Field(default_factory=list)
    findings: list[FairnessFinding] = Field(default_factory=list)
    revision: int = 0
    updated_at: datetime | None = None
    # 节点 → 检索到的合规资料（制度 / 法条 / 公序良俗 / 判例）
    rag_hits: dict[str, list[ChainRagHit]] = Field(default_factory=dict)
    # 处置动作的结果说明（改写后仍命中 / 引入了新风险），页面要直接说清发生了什么
    notice: str = ""
    # 本次扫描合规资料库不可用 → 规则层与模型层照常判定，但命中项没有依据资料。
    # 必须让页面说出来，否则用户会以为「查过且没问题」
    rag_degraded: bool = False


class FairnessAcceptIn(BaseModel):
    """「保留并记录原因」：警告级允许放行，但必须写明为什么这么问是合理的。"""

    reason: str = ""


class FairnessRewriteIn(BaseModel):
    """采纳改写时可选地覆盖建议文本（面试官可以在 AI 建议基础上自己再改）。"""

    suggestion: str = ""


# ---------------------------------------------------------------- Step4 评分与面评


class EvidenceItem(BaseModel):
    """一条证据（PRD §3.4.4：自由文本 + 标记「是否直接引用候选人原话」）。

    `quote` 之所以要单独存：面试官复述过的证据与候选人原话的可信度不同，
    HR 复核时看得出哪些是「候选人自己说的」、哪些是面试官的推断。
    """

    id: str = ""
    text: str = ""
    # true = 直接引用候选人原话；false = 面试官复述
    quote: bool = False


class EvaluationItem(BaseModel):
    """一个能力项的评分与证据。能力项来自 Step1 矩阵（按 row_id 对齐）。"""

    row_id: str = ""
    capability: str = ""
    # None = 还没打分。序列化成 0 只是为了让前端少写一个可空分支
    score: int | None = None
    evidences: list[EvidenceItem] = Field(default_factory=list)
    # 快记笔记：面试当场来不及整理成证据时的速记，也算「有依据」（BR-07 的口径）
    note: str = ""
    # 服务端算的：有分 +（有证据或笔记）才算填完。前端用它决定要不要提示补充
    complete: bool = False
    missing: list[str] = Field(default_factory=list)


class EvaluationFlag(BaseModel):
    """润色稿二次合规扫描的一处命中（PRD §6.6）。"""

    category: str = ""
    snippet: str = ""
    reason: str = ""


class EvaluationOut(BaseModel):
    """Step4 一屏：评分表 + 综合评价 + 润色稿（与原文并存，BR-08）。"""

    items: list[EvaluationItem] = Field(default_factory=list)
    summary: str = ""
    # 采纳润色稿之前留下的原文（P17 双栏对比用）
    summary_original: str = ""
    # AI 润色稿；不为空即表示润色过
    polished: str = ""
    polished_at: str = ""
    polished_model: str = ""
    polished_adopted: bool = False
    polished_flags: list[EvaluationFlag] = Field(default_factory=list)
    # 综合建议：proceed 建议推进 / hold 待定 / reject 不推进（模型给，供 Step5 参考）
    recommendation: str = ""
    revision: int = 0
    updated_at: datetime | None = None
    # 全部能力项都填完（有分 + 有证据或笔记）才算完整 —— 不完整只提示，不拦截保存
    complete: bool = False
    incomplete_count: int = 0


class EvaluationIn(BaseModel):
    """一次保存（自动 / 手动）提交的整块草稿。"""

    items: list[EvaluationItem] = Field(default_factory=list)
    summary: str = ""
    revision: int | None = None


class EvaluationSaveOut(BaseModel):
    evaluation: EvaluationOut
    # 本地 revision 落后于服务端（另一标签页刚保存过）：已按最新覆盖
    stale: bool = False


# ---------------------------------------------------------------- Step5 校准与提交


class SubmissionFlag(BaseModel):
    """提交前合规扫描的一处命中（PRD §3.4.5：面评同样要过公平性检查）。

    与 Step3 的 `FairnessFinding` 同构，但没有 `node_id` / `disposition`：
    面评是**一整块文本**（不是可逐条改写的题目节点），命中只有「回去改」一个出口，
    没有「采纳改写」这条捷径 —— 面评改写的责任人是面试官自己，不是 AI。
    """

    category: str = ""
    level: str = LEVEL_INFO
    # 定位到具体字段：summary / item:{row_id}:note / item:{row_id}:evidence:{i}
    field: str = ""
    snippet: str = ""
    reason: str = ""
    # 中性表述建议（面评里也有「年纪偏大」→「近三年未接触新框架」这类改法）
    suggestion: str = ""
    source: str = "rule"
    refs: list[str] = Field(default_factory=list)


class SubmissionOut(BaseModel):
    """Step5 一屏：扫描结论 + 命中项 + 已提交信息。

    `result` 与 Step3 同三态：block（有阻断级命中，提交被拦）/ warn / pass。
    idle = 还没扫描过。
    """

    result: str = FAIRNESS_IDLE
    scanned: bool = False
    scanned_at: str = ""
    model: str = ""
    # 扫了几个文本字段（综合评价 + 各项证据与快记）：0 表示没有面评可扫
    fields_scanned: int = 0
    flags: list[SubmissionFlag] = Field(default_factory=list)
    # 检索到的合规资料（制度 / 法条 / 公序良俗 / 判例），可展开看原文
    rag_hits: list[ChainRagHit] = Field(default_factory=list)
    # 面试结论（提交后才有值）
    conclusion: str = ""
    submitted_at: datetime | None = None


class SubmissionIn(BaseModel):
    """提交面评。

    `confirm` 与粉碎同口径：提交是不可逆的终态动作，前端弹窗之外后端再挡一道。
    """

    conclusion: str = CONCLUSION_PENDING
    confirm: bool = False


class WorkbenchOut(BaseModel):
    session_id: int
    application_id: int
    candidate_id: int
    candidate_name: str = ""
    position_id: int
    position_name: str = ""
    round_type: str = ""
    round_name: str = ""
    interviewer_id: int
    interviewer_name: str = ""
    status: str = ""
    duration_minutes: int = 45

    # 只有被指派的面试官能编辑；HR / HR 主管一律只读（2026-10-01 拍板）。
    # 会话已提交时连面试官本人也只读。
    can_edit: bool = False
    # 只读的原因，前端据此给不同提示（read_only / submitted）
    read_only_reason: str = ""

    matrix: MatrixOut = Field(default_factory=MatrixOut)
    chain: ChainOut = Field(default_factory=ChainOut)
    fairness: FairnessOut = Field(default_factory=FairnessOut)
    evaluation: EvaluationOut = Field(default_factory=EvaluationOut)
    submission: SubmissionOut = Field(default_factory=SubmissionOut)
    steps: list[WorkbenchStep] = Field(default_factory=list)
    # 前置没满足时页面阻断：jd_not_confirmed / resume_missing
    blocked_reason: str = ""
