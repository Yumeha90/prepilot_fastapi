import apiClient from './client'

/** 后端健康检查响应契约：GET /api/health -> { "status": "ok" } */
export interface HealthResponse {
  status: string
}

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/api/health')
  return data
}
