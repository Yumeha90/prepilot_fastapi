import apiClient from './client'

/** 面试会话（工作台载体）。列表页用，进入后走 /api/workbench/sessions/{id} */
export interface SessionItem {
  id: number
  application_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  round_id: number
  round_type: string
  round_name: string
  interviewer_id: number
  interviewer_name: string
  status: string
  submitted_at: string | null
  created_at: string
  updated_at: string
}

export async function fetchSessions(params: {
  position_id?: number
  candidate_id?: number
  status?: string
  limit?: number
} = {}): Promise<SessionItem[]> {
  const { data } = await apiClient.get<SessionItem[]>('/api/sessions', { params })
  return data
}
