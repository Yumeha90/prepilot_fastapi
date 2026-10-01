import apiClient, { AI_TIMEOUT } from './client'

import type { ResumeProfile } from './resume'

const BASE = '/api/candidates'

/** 简历侧状态：已上传 / 已解析 / 已确认 / 已粉碎 */
export type ProfileStatus = 'uploading' | 'parsed' | 'confirmed' | 'archived'

export interface ApplicationItem {
  id: number
  candidate_id: number
  position_id: number
  position_name: string
  stage: string
  current_round_name: string
  interviewer_name: string
  created_at: string
}

/** 面试会话（派单产物，详情里带出来） */
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

export interface CandidateDetail {
  id: number
  name: string
  contact_email: string
  contact_phone: string
  source: string
  resume_raw_text: string
  resume_file_name: string
  parsed_profile: ResumeProfile | null
  profile_status: ProfileStatus
  auth_tick: boolean
  auth_at: string | null
  confirmed_at: string | null
  purged_at: string | null
  created_by: number | null
  created_by_name: string
  created_at: string
  updated_at: string
  applications: ApplicationItem[]
  sessions: SessionItem[]
  /** 确认后派单失败的原因（正常派单为空字符串） */
  dispatch_notice: string
}

export async function fetchCandidate(id: number): Promise<CandidateDetail> {
  const { data } = await apiClient.get<CandidateDetail>(`${BASE}/${id}`)
  return data
}

/** 上传简历：D1 必须选职位，一步建候选人 + 应聘记录 */
export async function createCandidate(payload: {
  position_id: number
  name: string
  contact_email: string
  contact_phone: string
  source: 'upload' | 'paste'
  raw_text: string
  file_name: string
  auth_tick: boolean
}): Promise<CandidateDetail> {
  const { data } = await apiClient.post<CandidateDetail>(BASE, payload)
  return data
}

/** 确认解析结果：确认后才允许进入流程 */
export async function confirmCandidate(id: number, profile: ResumeProfile): Promise<CandidateDetail> {
  const { data } = await apiClient.post<CandidateDetail>(`${BASE}/${id}/confirm`, { profile })
  return data
}

/** 对已上传候选人重新解析（不落库，供解析页刷新右栏）。
 *
 * AI 接口，走 AI_TIMEOUT：云端一次结构化调用 17~23s，长简历分块更久。 */
export async function reparseCandidate(id: number): Promise<ParseResultLike> {
  const { data } = await apiClient.post<ParseResultLike>(`${BASE}/${id}/parse`, null, {
    timeout: AI_TIMEOUT,
  })
  return data
}

type ParseResultLike = { profile: ResumeProfile; chunks: number; notice: string }
