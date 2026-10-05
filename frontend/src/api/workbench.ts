import apiClient, { AI_TIMEOUT } from './client'

/** 矩阵一行的证据状态：证据充足 / 待核实 / 缺失 */
export type EvidenceStatus = 'sufficient' | 'verify' | 'missing'
/** 行来源：岗位核心能力 / AI 从简历补充 / 面试官手动新增 */
export type RowSource = 'jd' | 'resume' | 'manual'

export interface MatrixRow {
  id: string
  source: RowSource
  capability: string
  /** 来自 JD 能力项的权重（展示用）；简历补充与手加行为 null */
  weight: number | null
  evidence: string
  status: EvidenceStatus
  focus: string
  /** 「本轮重点」—— 唯一入口在 P09（JD 编辑页没有这个勾选） */
  is_key: boolean
}

export interface MatrixOut {
  rows: MatrixRow[]
  generated: boolean
  revision: number
  updated_at: string | null
  generated_at: string
  model: string
}

export interface MatrixSaveOut {
  matrix: MatrixOut
  /** 本地 revision 落后于服务端：已按最新覆盖，提示刷新即可 */
  stale: boolean
}

export interface WorkbenchStep {
  key: string
  /** done / current / todo / disabled */
  state: string
}

export interface ChainFollowup {
  /** 第几层追问（从 1 开始） */
  level: number
  /** 候选人答得模糊时，怎么追问拿到行为证据 */
  vague: string
  /** 怀疑是背书 / 包装时，怎么验证真伪 */
  anti_fake: string
}

export interface ChainNode {
  id: string
  /** 关联的矩阵行 id：面试官改了矩阵后能看出哪些节点已经过时 */
  row_id: string
  capability: string
  status: EvidenceStatus
  /** 主问题（PRD 要求 ≤30 字） */
  main_question: string
  followups: ChainFollowup[]
  /** 评分观察点：答成什么样算好、什么样算差 */
  observations: string[]
  minutes: number
  /** 生成这题时引用到的内部资料标题（服务端校验过，不是模型编的） */
  rag_refs: string[]
  /** 幻觉门禁没过：追问里出现了材料里找不到的数字，需面试官自行确认 */
  flagged: boolean
}

export interface ChainRagHit {
  title: string
  score: number
  snippet: string
}

export interface ChainOut {
  nodes: ChainNode[]
  generated: boolean
  revision: number
  updated_at: string | null
  generated_at: string
  model: string
  /** idle / running / ready / failed —— 异步生成的轮询依据 */
  status: string
  error: string
  /** 时长预算 = 本轮时长 - 开场收尾预留 */
  budget_minutes: number
  /** 能力项 → 检索到的内部资料 */
  rag_hits: Record<string, ChainRagHit[]>
}

export interface ChainSaveOut {
  chain: ChainOut
  stale: boolean
}

export interface ChainTaskOut {
  session_id: number
  task_id: string
  status: string
}

const CHAIN_STATUS = {
  idle: 'idle',
  running: 'running',
  ready: 'ready',
  failed: 'failed',
} as const

export type ChainStatus = (typeof CHAIN_STATUS)[keyof typeof CHAIN_STATUS]

/** Step3 公平性：命中项的等级（阻断 / 警告 / 提示） */
export type FairnessLevel = 'block' | 'warn' | 'info'
/** 检查类别：婚育 / 年龄 / 性别 / 户籍地域民族宗教 / 健康残疾 / 诱导性提问 */
export type FairnessCategory =
  | 'marriage'
  | 'age'
  | 'gender'
  | 'region'
  | 'health'
  | 'leading'

export interface FairnessFinding {
  id: string
  node_id: string
  capability: string
  level: FairnessLevel
  category: FairnessCategory | string
  /** 定位到具体字段：main_question / followup:1:vague / observation:0 */
  field: string
  /** 命中所在的整句原文（面试官在这个基础上改写） */
  original_text: string
  snippet: string
  reason: string
  /** AI 给的中性改写建议（空 = 模型没给，只能手动改） */
  suggestion: string
  /** rule = 规则命中（依据来自法条与制度）；llm = 模型语义判断 */
  source: string
  /** 依据的合规资料标题（服务端校验过，不是模型编的） */
  refs: string[]
  /** pending / rewritten / accepted */
  disposition: string
  accepted_reason: string
  /** 已采纳改写并复检通过时，落到的那句新文本 */
  rewritten_to: string
}

export interface FairnessCheckItem {
  key: string
  level: FairnessLevel
  count: number
}

export interface FairnessOut {
  /** idle / pass / warn / block */
  result: string
  scanned: boolean
  scanned_at: string
  model: string
  nodes_scanned: number
  checks: FairnessCheckItem[]
  findings: FairnessFinding[]
  revision: number
  updated_at: string | null
  rag_hits: Record<string, ChainRagHit[]>
  /** 处置动作的结果说明（改写后仍命中 / 引入新风险），有值就要提示给用户 */
  notice: string
  /** 合规资料库不可用：判定照常，但命中项没有依据资料 */
  rag_degraded: boolean
}

// ---------------------------------------------------------------- Step4 评分与面评

export interface EvidenceItem {
  id: string
  text: string
  /** true = 直接引用候选人原话；false = 面试官复述 */
  quote: boolean
}

export interface EvaluationItem {
  /** 关联的矩阵行 id，评分表按它对齐 Step1 的能力项 */
  row_id: string
  capability: string
  /** null = 还没打分 */
  score: number | null
  evidences: EvidenceItem[]
  /** 快记笔记：来不及整理成证据时的速记，也算「有依据」 */
  note: string
  /** 服务端算的：有分 +（有证据或笔记） */
  complete: boolean
  /** no_score / no_evidence —— 前端据此说清该补哪个 */
  missing: string[]
}

export interface EvaluationFlag {
  category: string
  snippet: string
  reason: string
}

export interface EvaluationOut {
  items: EvaluationItem[]
  /** 综合评价正文（面试官写的原文） */
  summary: string
  /** 采纳润色稿前留下的原文（P17 双栏对比用） */
  summary_original: string
  /** AI 润色稿；非空即表示润色过 */
  polished: string
  polished_at: string
  polished_model: string
  polished_adopted: boolean
  /** 润色稿的二次合规扫描命中，常驻警示 */
  polished_flags: EvaluationFlag[]
  /** proceed 建议推进 / hold 待定 / reject 不推进 */
  recommendation: string
  revision: number
  updated_at: string | null
  /** 全部能力项都填完（有分 + 有证据或笔记） */
  complete: boolean
  incomplete_count: number
}

export interface EvaluationSaveOut {
  evaluation: EvaluationOut
  stale: boolean
}

/** 提交前合规扫描的一处命中 */
export interface SubmissionFlag {
  category: string
  /** block 阻断（提交会被拦）/ warn 警告 / info 提示 */
  level: FairnessLevel
  /** summary / item:{row_id}:note / item:{row_id}:evidence:{i} */
  field: string
  snippet: string
  reason: string
  /** 中性表述建议 */
  suggestion: string
  /** rule 规则命中 / llm 模型判断 */
  source: string
  refs: string[]
}

export interface SubmissionOut {
  /** idle / pass / warn / block */
  result: string
  scanned: boolean
  scanned_at: string
  model: string
  /** 扫了几个文本字段（综合评价 + 各项证据与快记） */
  fields_scanned: number
  flags: SubmissionFlag[]
  /** 检索到的合规资料（制度 / 法条 / 公序良俗 / 判例） */
  rag_hits: ChainRagHit[]
  /** pass / pending / fail（提交后才有值） */
  conclusion: string
  submitted_at: string | null
}

export interface WorkbenchOut {
  session_id: number
  application_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  round_type: string
  round_name: string
  interviewer_id: number
  interviewer_name: string
  status: string
  duration_minutes: number
  /** 只有被指派的面试官为 true；HR / HR 主管一律只读 */
  can_edit: boolean
  /** read_only / submitted */
  read_only_reason: string
  matrix: MatrixOut
  chain: ChainOut
  fairness: FairnessOut
  evaluation: EvaluationOut
  submission: SubmissionOut
  steps: WorkbenchStep[]
  /** jd_not_confirmed / resume_missing */
  blocked_reason: string
}

const BASE = '/api/workbench/sessions'

export async function fetchWorkbench(sessionId: number): Promise<WorkbenchOut> {
  const { data } = await apiClient.get<WorkbenchOut>(`${BASE}/${sessionId}`)
  return data
}

/** C3 纯 LLM 生成矩阵（覆盖旧矩阵，保留已勾选的本轮重点）。云端 15~30s，走 AI 超时 */
export async function generateMatrix(sessionId: number): Promise<MatrixSaveOut> {
  const { data } = await apiClient.post<MatrixSaveOut>(
    `${BASE}/${sessionId}/matrix/generate`,
    null,
    { timeout: AI_TIMEOUT },
  )
  return data
}

/** 整块保存人工编辑（增删行 / 改证据与考察重点 / 勾重点 / 调顺序） */
export async function saveMatrix(
  sessionId: number,
  rows: MatrixRow[],
  revision?: number,
): Promise<MatrixSaveOut> {
  const { data } = await apiClient.put<MatrixSaveOut>(`${BASE}/${sessionId}/matrix`, {
    rows,
    revision: revision ?? null,
  })
  return data
}

export async function updateDuration(
  sessionId: number,
  minutes: number,
): Promise<number> {
  const { data } = await apiClient.put<{ duration_minutes: number }>(
    `${BASE}/${sessionId}/duration`,
    { duration_minutes: minutes },
  )
  return data.duration_minutes
}

/**
 * 投递「生成问题链」任务（异步 + 轮询）。
 *
 * 为什么不同步等：一次生成要检索 3~6 个能力项再交给模型，云端 15~30s，
 * 同步等待会撞 nginx 120s，而且期间页面完全没有反馈（BR-12）。
 */
export async function generateChain(sessionId: number): Promise<ChainTaskOut> {
  const { data } = await apiClient.post<ChainTaskOut>(
    `${BASE}/${sessionId}/chain/generate`,
  )
  return data
}

/** 整块保存人工微调（改题 / 调顺序 / 调耗时 / 增删节点） */
export async function saveChain(
  sessionId: number,
  nodes: ChainNode[],
  revision?: number,
): Promise<ChainSaveOut> {
  const { data } = await apiClient.put<ChainSaveOut>(`${BASE}/${sessionId}/chain`, {
    nodes,
    revision: revision ?? null,
  })
  return data
}

/** 「换一换」：只重生成单个节点，其余节点原样保留。单节点一次模型调用，同步返回 */
export async function regenerateNode(
  sessionId: number,
  nodeId: string,
): Promise<ChainSaveOut> {
  const { data } = await apiClient.post<ChainSaveOut>(
    `${BASE}/${sessionId}/chain/nodes/${nodeId}/regenerate`,
    null,
    { timeout: AI_TIMEOUT },
  )
  return data
}

/**
 * C5 公平性扫描（同步，P95 ≤ 10s）。
 *
 * 问题链生成时已自动预扫一次（PRD §6.4 质量门禁），这里用于**改完之后**的复扫。
 * 扫描失败会带 code 抛错（向量库不可用不降级），前端照旧提示。
 */
export async function scanFairness(sessionId: number): Promise<FairnessOut> {
  const { data } = await apiClient.post<FairnessOut>(
    `${BASE}/${sessionId}/fairness/scan`,
    null,
    { timeout: AI_TIMEOUT },
  )
  return data
}

/** 采纳改写：替换问题文本后**自动重新扫描**，返回新的结论 */
export async function applyRewrite(
  sessionId: number,
  findingId: string,
  suggestion?: string,
): Promise<FairnessOut> {
  const { data } = await apiClient.post<FairnessOut>(
    `${BASE}/${sessionId}/fairness/findings/${findingId}/apply`,
    { suggestion: suggestion ?? '' },
    { timeout: AI_TIMEOUT },
  )
  return data
}

/** 警告级「保留并记录原因」：原因必填，留痕是这个动作的唯一意义 */
export async function acceptFinding(
  sessionId: number,
  findingId: string,
  reason: string,
): Promise<FairnessOut> {
  const { data } = await apiClient.post<FairnessOut>(
    `${BASE}/${sessionId}/fairness/findings/${findingId}/accept`,
    { reason },
  )
  return data
}

/**
 * 保存评分草稿（手动 + 30 秒自动保存共用）。
 *
 * 服务端**不做完整性拦截**（写一半被拦＝逼面试官先编一条证据），
 * 缺哪些项由返回体的 incomplete_count / missing 提示，Step5 提交时才拦。
 */
export async function saveEvaluation(
  sessionId: number,
  items: EvaluationItem[],
  summary: string,
  revision?: number,
): Promise<EvaluationSaveOut> {
  const { data } = await apiClient.put<EvaluationSaveOut>(
    `${BASE}/${sessionId}/evaluation`,
    { items, summary, revision: revision ?? null },
  )
  return data
}

/**
 * C6 面评润色（同步，一次模型调用）。
 *
 * 润色稿**并存不覆盖**原文（BR-08），要显式「采纳」才生效；
 * 生成后服务端立刻跑二次合规扫描，命中进 polished_flags。
 */
export async function polishEvaluation(sessionId: number): Promise<EvaluationOut> {
  const { data } = await apiClient.post<EvaluationOut>(
    `${BASE}/${sessionId}/evaluation/polish`,
    null,
    { timeout: AI_TIMEOUT },
  )
  return data
}

/** 采纳润色稿：覆写综合评价正文，原文留进 summary_original */
export async function adoptPolished(sessionId: number): Promise<EvaluationOut> {
  const { data } = await apiClient.post<EvaluationOut>(
    `${BASE}/${sessionId}/evaluation/adopt`,
  )
  return data
}

/**
 * 提交前的合规扫描（只看不改）。
 *
 * 单独给一个口子而不是只在提交时扫：点了提交才知道被拦，面试官得回上一步
 * 改完再走一遍流程 —— 先扫一次，被拦的原因在提交之前就摆在他面前。
 */
export async function scanSubmission(sessionId: number): Promise<SubmissionOut> {
  const { data } = await apiClient.post<SubmissionOut>(
    `${BASE}/${sessionId}/submission/scan`,
    null,
    { timeout: AI_TIMEOUT },
  )
  return data
}

/**
 * 提交面评（终态）：完整性校验 → 合规扫描 → 落结论 → 会话置已提交。
 *
 * `confirm` 是后端那道闸（与粉碎同口径），提交后连面试官本人都只能回看。
 * 提交**不变更候选人阶段**：下一轮由 HR 在看板处置。
 */
export async function submitEvaluation(
  sessionId: number,
  conclusion: string,
): Promise<SubmissionOut> {
  const { data } = await apiClient.post<SubmissionOut>(
    `${BASE}/${sessionId}/submission`,
    { conclusion, confirm: true },
    { timeout: AI_TIMEOUT },
  )
  return data
}
