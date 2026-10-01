import apiClient from './client'

export interface MatchBreakdown {
  competencyId: string
  name: string
  weight: number
  score: number
  contribution: number
  /** strong 证据充足 / verify 待核实 / weak 薄弱 / missing 缺失 */
  level: string
}

export interface MatchEvidence {
  competencyId: string
  segmentId: string
  quote: string
  confidence: number
}

export interface MatchBonus {
  id: string
  text: string
  delta: number
}

export interface MatchGate {
  gateId: string
  text: string
  reason: string
}

export interface MatchSummary {
  conclusion: string
  reasons: string[]
  gaps: string[]
  suggestions: string[]
}

export interface MatchFeedbackItem {
  id: number
  kind: string
  expected_low: number | null
  expected_high: number | null
  comment: string
  created_by_name: string
  created_at: string
}

export interface MatchOut {
  application_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  stage: string
  jd_version: number
  jd_status: string
  profile_status: string

  has_score: boolean
  score_id: number | null
  score: number | null
  tier: string
  /** CURRENT 有效 / STALE 岗位标准已变更，需重算 */
  status: string
  algorithm_version: string
  model_version: string
  /** pending 生成中 / ready 已生成 / failed 生成失败 */
  summary_status: string
  updated_at: string | null

  vetoed_gates: MatchGate[]
  unknown_gates: MatchGate[]
  breakdown: MatchBreakdown[]
  evidences: MatchEvidence[]
  bonuses: MatchBonus[]
  summary: MatchSummary | null

  feedbacks: MatchFeedbackItem[]
  /** profile_not_confirmed / jd_not_confirmed，非空时页面阻断 */
  blocked_reason: string
}

export interface MatchFeedbackIn {
  kind: string
  expected_low?: number | null
  expected_high?: number | null
  comment: string
}

const BASE = '/api/match/applications'

/** P18 一屏数据。没有分时 `has_score=false`，页面给「计算」按钮 */
export async function fetchMatch(applicationId: number): Promise<MatchOut> {
  const { data } = await apiClient.get<MatchOut>(`${BASE}/${applicationId}`)
  return data
}

/** 重算（JD 变更后置 STALE 的分数由这里补）。判定同步落定，AI 总结异步补写 */
export async function recomputeMatch(applicationId: number): Promise<MatchOut> {
  const { data } = await apiClient.post<MatchOut>(`${BASE}/${applicationId}/recompute`)
  return data
}

/** BR-22：提交评分异议。只落库 + 存快照，不改原分数 */
export async function submitMatchFeedback(
  applicationId: number,
  body: MatchFeedbackIn,
): Promise<MatchFeedbackItem> {
  const { data } = await apiClient.post<MatchFeedbackItem>(
    `${BASE}/${applicationId}/feedback`,
    body,
  )
  return data
}
