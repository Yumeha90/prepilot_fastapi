import apiClient from './client'

/** 看板卡片 = 一条应聘记录（application）。一人一职位，所以一张卡就是一个候选人 */
export interface BoardCard {
  application_id: number
  candidate_id: number
  candidate_name: string
  position_id: number
  position_name: string
  stage: string
  current_round_name: string
  interviewer_name: string
  /** 当轮进展：会话状态；无会话（待派单 / offer 列）为空 */
  session_status: string
  /** 当轮会话 id：「开始备面」直接进工作台要它；无会话为 null */
  session_id: number | null
  /** 能否进工作台备面：只有被指派给本人的面试官 + 会话未提交（后端判定） */
  can_prepare: boolean
  profile_status: string
  /** 最新人岗匹配分；未计算 / 未参与计算时为 null */
  match_score: number | null
  match_tier: string
  /** CURRENT 有效 / STALE 岗位标准已变更（徽章置灰提示需重算） */
  match_status: string
  updated_at: string
}

export interface BoardColumn {
  stage: string
  cards: BoardCard[]
}

export interface BoardData {
  /** 列与顺序由后端下发（BR-15：面试官只拿 r1 / r2） */
  columns: BoardColumn[]
  /** 不占看板列的计数，如已录用 */
  summary: Record<string, number>
  /** 当前用户可用的处置动作，前端据此决定要不要渲染按钮 */
  actions: string[]
}

export type BoardAction = 'advance' | 'rollback' | 'accept' | 'reject' | 'pool' | 'archive'

export async function fetchBoard(params: {
  position_id?: number
  keyword?: string
} = {}): Promise<BoardData> {
  const { data } = await apiClient.get<BoardData>('/api/board', { params })
  return data
}

export async function transitionApplication(
  applicationId: number,
  action: BoardAction,
): Promise<{ application_id: number; stage: string; notice: string }> {
  const { data } = await apiClient.post<{
    application_id: number
    stage: string
    notice: string
  }>(`/api/board/applications/${applicationId}/transition`, { action })
  return data
}

/** 在流程（未终结处置）的候选人数 —— 关闭职位前的二次确认显示 */
export async function fetchActiveCandidates(positionId: number): Promise<number> {
  const { data } = await apiClient.get<{ count: number }>(
    `/api/positions/${positionId}/active-candidates`,
  )
  return data.count
}
