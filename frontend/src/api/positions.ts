import apiClient from './client'

/** 职位状态：草稿 / 招聘中 / 已暂停 / 已关闭 */
export type PositionStatus = 'draft' | 'open' | 'paused' | 'closed'

/** 轮次类型：r1 技术一面 / r2 技术二面 / hr HR 面试 / offer Offer 审批（终结节点） */
export type RoundType = 'r1' | 'r2' | 'hr' | 'offer'

export interface Competency {
  id?: string
  text: string
  weight: number
}

export interface JdData {
  raw_text: string
  hard_gates: string[]
  competencies: Competency[]
  bonuses: string[]
  status: 'empty' | 'draft' | 'confirmed'
  version: number
  draft: { hard_gates: string[]; competencies: Competency[]; bonuses: string[] } | null
}

export interface RoundItem {
  id: number
  seq: number
  name: string
  type: RoundType
  interviewer_id: number | null
  interviewer_name: string
}

export interface PositionItem {
  id: number
  name: string
  status: PositionStatus
  owner_id: number | null
  owner_name: string
  jd_version: number
  jd_status: 'empty' | 'draft' | 'confirmed'
  jd_completion: number
  candidate_count: number
  round_count: number
  created_at: string
  updated_at: string
}

export interface PositionDetail extends Omit<PositionItem, 'round_count'> {
  rounds: RoundItem[]
  jd: JdData
  copied_from_id: number | null
  closed_at: string | null
}


export interface Paged<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface ListParams {
  status?: PositionStatus
  keyword?: string
  include_closed?: boolean
  owner_id?: number
  page?: number
  page_size?: number
}

export interface JdPayload {
  raw_text?: string
  hard_gates: string[]
  competencies: Competency[]
  bonuses: string[]
}

export interface RoundPayload {
  type: RoundType
  interviewer_id: number | null
  name?: string
}

export interface SavePreviewResult {
  jd_changed: boolean
  rounds_changed: boolean
  affected_candidates: number
  jd_error: string
  rounds_error: string
}

const BASE = '/api/positions'

export async function fetchPositions(params: ListParams = {}): Promise<Paged<PositionItem>> {
  const { data } = await apiClient.get<Paged<PositionItem>>(BASE, { params })
  return data
}

export async function fetchPosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.get<PositionDetail>(`${BASE}/${id}`)
  return data
}

export async function createPosition(payload: {
  name: string
  owner_id?: number | null
}): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(BASE, payload)
  return data
}

export async function updatePosition(
  id: number,
  payload: { name?: string; owner_id?: number | null },
): Promise<PositionDetail> {
  const { data } = await apiClient.patch<PositionDetail>(`${BASE}/${id}`, payload)
  return data
}

export async function saveJd(id: number, payload: JdPayload & { confirm: boolean }) {
  const { data } = await apiClient.put<JdData>(`${BASE}/${id}/jd`, payload)
  return data
}

export async function saveRounds(id: number, rounds: RoundPayload[]): Promise<RoundItem[]> {
  const { data } = await apiClient.put<RoundItem[]>(`${BASE}/${id}/rounds`, { rounds })
  return data
}

/** 保存前预检：是否会 bump 版本 / 轮次是否变更 / 校验是否通过 */
export async function previewSave(
  id: number,
  payload: { jd?: JdPayload; rounds?: RoundPayload[] },
): Promise<SavePreviewResult> {
  const { data } = await apiClient.post<SavePreviewResult>(`${BASE}/${id}/save-preview`, payload)
  return data
}

/** 复制只带 JD 与轮次骨架，面试官人选清空须重新指派 */
export async function duplicatePosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/duplicate`)
  return data
}

export async function deletePosition(id: number): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`)
}

export async function closePosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/close`)
  return data
}

export async function reopenPosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/reopen`)
  return data
}

export async function publishPosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/publish`)
  return data
}

/** 招聘中 → 已暂停：停止接收新候选人 */
export async function pausePosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/pause`)
  return data
}

/** 已暂停 → 招聘中：恢复接收新候选人 */
export async function resumePosition(id: number): Promise<PositionDetail> {
  const { data } = await apiClient.post<PositionDetail>(`${BASE}/${id}/resume`)
  return data
}

/** JD 版本历史接口由人岗匹配详情页直接调用（后端保留，前端本期不展示历史列表）：
 *  GET /positions/{id}/jd/versions        列表（倒序）
 *  GET /positions/{id}/jd/versions/{v}    某版只读快照
 */
