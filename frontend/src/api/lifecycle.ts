import apiClient from './client'

export interface RetentionPolicyRead {
  id: number
  name: string
  resource: string
  days: number
  enabled: boolean
  status: string
  last_run_at: string | null
  last_purged: number
  created_at: string
}

export interface LifecycleOverview {
  policy: RetentionPolicyRead
  history: RetentionPolicyRead[]
  pending: number
  purged_total: number
}

export interface PolicyUpdatePayload {
  name?: string
  days?: number
  enabled?: boolean
}

export interface ScanItem {
  candidate_id: number
  name: string
  email: string
  position_names: string[]
  stage: string
  /** 保留期起算点：确认时间，未确认则退回上传时间 */
  since_at: string
  days_overdue: number
}

export interface ScanResult {
  days: number
  total: number
  items: ScanItem[]
}

export interface PurgePayload {
  candidate_ids: number[]
  confirm: boolean
}

export interface PurgeResult {
  requested: number
  purged: number
  skipped_purged: number
  skipped_accepted: number
  skipped_missing: number
  purged_ids: number[]
}

export interface AutoPurgeResult {
  enabled: boolean
  days: number
  scanned: number
  purged: number
  ran_at: string | null
}

const BASE = '/api/system/lifecycle'

export async function fetchLifecycle(): Promise<LifecycleOverview> {
  const { data } = await apiClient.get<LifecycleOverview>(BASE)
  return data
}

export async function updatePolicy(payload: PolicyUpdatePayload): Promise<RetentionPolicyRead> {
  const { data } = await apiClient.put<RetentionPolicyRead>(`${BASE}/policy`, payload)
  return data
}

/** 扫描待粉碎候选人（只预览，不清除） */
export async function scanExpired(days?: number, limit = 200): Promise<ScanResult> {
  const params: Record<string, number> = { limit }
  if (typeof days === 'number') params.days = days
  const { data } = await apiClient.get<ScanResult>(`${BASE}/scan`, { params })
  return data
}

/** 手动粉碎：confirm 必须为 true（后端还会再挡一道） */
export async function purgeCandidates(payload: PurgePayload): Promise<PurgeResult> {
  const { data } = await apiClient.post<PurgeResult>(`${BASE}/purge`, payload)
  return data
}

/** 立即执行一次定时扫描（不等下个整点） */
export async function runAutoPurge(): Promise<AutoPurgeResult> {
  const { data } = await apiClient.post<AutoPurgeResult>(`${BASE}/purge/auto`)
  return data
}
