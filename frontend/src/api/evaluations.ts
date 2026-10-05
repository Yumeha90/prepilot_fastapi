import apiClient from './client'

/** 列表一行：谁、哪一轮、什么结论（P17 入口） */
export interface EvaluationSummary {
  session_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  round_type: string
  round_name: string
  interviewer_id: number
  interviewer_name: string
  /** pass / pending / fail */
  conclusion: string
  submitted_at: string | null
  /** 已按 90 天保留策略清掉正文：行还在，内容没了 */
  content_purged: boolean
}

export interface EvaluationItem {
  row_id: string
  capability: string
  score: number | null
  evidences: { id: string; text: string; quote: boolean }[]
  note: string
  complete: boolean
  missing: string[]
}

export interface EvaluationFlag {
  category: string
  snippet: string
  reason: string
}

/** 提交那一刻的公平性结论 */
export interface FairnessSnapshot {
  result: string
  scanned_at: string
  findings: Record<string, unknown>[]
}

export interface EvaluationDetail {
  session_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  round_type: string
  round_name: string
  interviewer_id: number
  interviewer_name: string
  conclusion: string
  submitted_at: string | null
  duration_minutes: number
  items: EvaluationItem[]
  summary: string
  /** 采纳润色稿之前的原文（双栏对比，BR-08） */
  summary_original: string
  polished: string
  polished_adopted: boolean
  polished_flags: EvaluationFlag[]
  recommendation: string
  fairness: FairnessSnapshot
  content_purged: boolean
  read_only: boolean
}

export async function fetchEvaluations(params: {
  position_id?: number
  keyword?: string
  limit?: number
} = {}): Promise<EvaluationSummary[]> {
  const { data } = await apiClient.get<EvaluationSummary[]>('/api/evaluations', { params })
  return data
}

export async function fetchEvaluation(sessionId: number): Promise<EvaluationDetail> {
  const { data } = await apiClient.get<EvaluationDetail>(`/api/evaluations/${sessionId}`)
  return data
}
