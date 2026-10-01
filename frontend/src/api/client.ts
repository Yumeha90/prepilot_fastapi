import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

/**
 * 统一 API 客户端。
 * - 本地开发：baseURL 留空，由 vite dev server 代理 /api -> 127.0.0.1:8010
 * - 云端：静态资源与 API 同域（nginx 反代 /api -> 后端容器 8000）
 *
 * 登录态：短期 access token + 长效 refresh token（均存 localStorage）。
 * 遇 401 时用 refresh 换一次新 access 并重放原请求；refresh 也失败则清空登录态。
 */
/**
 * AI 类接口专用超时（简历解析 / JD 拆解）。
 *
 * 2026-10-01 线上问题：默认 15s 在云端必挂 —— 百炼一次结构化调用实测 17~23s
 * （简历比 JD 长，输出 token 多），L1 分块还会翻几倍。JD 侧当初已经单独放宽过，
 * 简历侧漏了，表现为「一进解析页就报错」。
 * 后端 LLM_TIMEOUT=120s、nginx proxy_read_timeout=120s，这里取 120s 与之对齐。
 */
export const AI_TIMEOUT = 120_000

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  // 普通接口 30s 足够；AI 类接口走 AI_TIMEOUT
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

const ACCESS_KEY = 'prepilot.access_token'
const REFRESH_KEY = 'prepilot.refresh_token'

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(ACCESS_KEY)
  } catch {
    return null
  }
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY)
  } catch {
    return null
  }
}

export function setTokens(access: string, refresh: string): void {
  try {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  } catch {
    /* ignore */
  }
}

export function setAccessToken(access: string): void {
  try {
    localStorage.setItem(ACCESS_KEY, access)
  } catch {
    /* ignore */
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  } catch {
    /* ignore */
  }
}

// 请求拦截：自动携带 JWT + 当前语言（供后端 Accept-Language 解析）
apiClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  config.headers['Accept-Language'] =
    (typeof localStorage !== 'undefined' && localStorage.getItem('prepilot.lang')) ||
    (typeof navigator !== 'undefined' ? navigator.language : 'zh-CN')
  return config
})

// 刷新锁：并发 401 只触发一次 refresh，其余请求排队复用同一个 Promise
let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    const refresh = getRefreshToken()
    if (!refresh) return null
    try {
      // 用裸 axios 发，避免再次经过本实例的响应拦截造成递归
      const { data } = await axios.post<{ access_token: string }>(
        `${import.meta.env.VITE_API_BASE_URL ?? ''}/api/auth/refresh`,
        { refresh_token: refresh },
        { headers: { 'Content-Type': 'application/json' } },
      )
      setAccessToken(data.access_token)
      return data.access_token
    } catch {
      clearTokens()
      return null
    }
  })()

  try {
    return await refreshPromise
  } finally {
    refreshPromise = null
  }
}

// 无需重试的接口（登录/刷新本身失败就是凭证问题，重试无意义）
const NO_REFRESH_PATHS = ['/api/auth/login', '/api/auth/refresh', '/api/auth/logout']

apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const status = error.response?.status
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    const url = original?.url ?? ''

    if (status !== 401 || !original || original._retry) {
      return Promise.reject(error)
    }
    if (NO_REFRESH_PATHS.some((p) => url.includes(p))) {
      return Promise.reject(error)
    }

    original._retry = true
    const newToken = await refreshAccessToken()
    if (!newToken) {
      // refresh 也失效：清空登录态，交由路由守卫跳登录页
      window.dispatchEvent(new CustomEvent('prepilot:auth-expired'))
      return Promise.reject(error)
    }
    original.headers.Authorization = `Bearer ${newToken}`
    return apiClient.request(original)
  },
)

/** 后端错误体：{ detail: { code, message } } */
export interface ApiErrorBody {
  code?: string
  message?: string
}

export function extractErrorMessage(error: unknown, fallback = ''): string {
  const ax = error as AxiosError<{ detail?: ApiErrorBody | string }>
  const detail = ax?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') return detail.message ?? fallback
  return (error as Error)?.message || fallback
}

export function extractErrorCode(error: unknown): string | undefined {
  const ax = error as AxiosError<{ detail?: ApiErrorBody | string }>
  // axios 超时没有响应体，只能从 code/message 识别；统一成 `timeout` 供 i18n 出文案
  if (ax?.code === 'ECONNABORTED' || /timeout of \d+ms exceeded/i.test(ax?.message ?? '')) {
    return 'timeout'
  }
  const detail = ax?.response?.data?.detail
  if (detail && typeof detail === 'object') return detail.code
  return undefined
}

export default apiClient
