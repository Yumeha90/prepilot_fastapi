import apiClient from './client'

export interface UserOption {
  id: number
  name: string
  email: string
  role_code: string
}

/**
 * 下拉用人员列表。
 * - interview：轮次面试官候选（有「提交面评」或「候选人处置」权限）
 * - owner：职位 HR 负责人候选（有「职位编辑」权限）
 */
export async function fetchUserOptions(
  purpose: 'interview' | 'owner' = 'interview',
): Promise<UserOption[]> {
  const { data } = await apiClient.get<UserOption[]>('/api/users/options', { params: { purpose } })
  return data
}
