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
