import apiClient from './client'

/** 职位状态：草稿 / 招聘中 / 已暂停 / 已关闭 */
export type PositionStatus = 'draft' | 'open' | 'paused' | 'closed'

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
  page?: number
  page_size?: number
}

const BASE = '/api/positions'

export async function fetchPositions(params: ListParams = {}): Promise<Paged<PositionItem>> {
  const { data } = await apiClient.get<Paged<PositionItem>>(BASE, { params })
  return data
}

export async function createPosition(name: string): Promise<{ id: number }> {
  const { data } = await apiClient.post(BASE, { name })
  return data
}

/** BR-24：复制只带 JD 与轮次骨架，面试官人选清空须重新指派 */
export async function duplicatePosition(id: number): Promise<{ id: number }> {
  const { data } = await apiClient.post(`${BASE}/${id}/duplicate`)
  return data
}

export async function deletePosition(id: number): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`)
}

export async function closePosition(id: number): Promise<void> {
  await apiClient.post(`${BASE}/${id}/close`)
}

export async function reopenPosition(id: number): Promise<void> {
  await apiClient.post(`${BASE}/${id}/reopen`)
}
