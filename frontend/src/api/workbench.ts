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
