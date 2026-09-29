import apiClient, { setTokens } from './client'

export type RoleCode = 'admin' | 'hr_lead' | 'hr' | 'interviewer'

export interface UserRead {
  id: number
  email: string
  full_name: string
  role_code: RoleCode
  role_name_key: string
  avatar_url: string
  locale: string
  is_active: boolean
  last_login_at: string | null
}

export interface MeResponse {
  user: UserRead
  permissions: string[]
  scopes: Record<string, string>
  unread_count: number
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  full_name: string
  role_code: RoleCode
}

export async function login(payload: LoginPayload): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/auth/login', payload)
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function register(payload: RegisterPayload): Promise<UserRead> {
  const { data } = await apiClient.post<UserRead>('/api/auth/register', payload)
  return data
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await apiClient.get<MeResponse>('/api/auth/me')
  return data
}

export async function refreshToken(refresh: string): Promise<{ access_token: string }> {
  const { data } = await apiClient.post<{ access_token: string }>('/api/auth/refresh', {
    refresh_token: refresh,
  })
  return data
}

export async function logout(refresh: string): Promise<void> {
  try {
    await apiClient.post('/api/auth/logout', { refresh_token: refresh })
  } catch {
    // 退出登录以本地清态为准，接口失败不阻塞
  }
}

export async function updateMe(payload: {
  full_name?: string
  avatar_url?: string
  locale?: string
}): Promise<UserRead> {
  const { data } = await apiClient.patch<UserRead>('/api/auth/me', payload)
  return data
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await apiClient.post('/api/auth/password/change', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}

export interface ForgotPasswordResult {
  message: string
  dev_code?: string
}

export async function forgotPassword(email: string): Promise<ForgotPasswordResult> {
  const { data } = await apiClient.post<ForgotPasswordResult>('/api/auth/password/forgot', {
    email,
  })
  return data
}

export async function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  await apiClient.post('/api/auth/password/reset', {
    email,
    code,
    new_password: newPassword,
  })
}
