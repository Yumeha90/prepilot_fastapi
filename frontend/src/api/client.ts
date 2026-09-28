import axios, { AxiosError } from 'axios'

/**
 * 统一 API 客户端。
 * - 本地开发：baseURL 留空，由 vite dev server 代理 /api -> 127.0.0.1:8010
 * - 云端：静态资源与 API 同域（nginx 反代 /api -> 后端容器 8000）
 */
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
})

const TOKEN_KEY = 'prepilot.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

// 请求拦截：自动携带 JWT + 当前语言（供后端 Accept-Language 解析）
apiClient.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  config.headers['Accept-Language'] =
    (typeof localStorage !== 'undefined' && localStorage.getItem('prepilot.lang')) ||
    (typeof navigator !== 'undefined' ? navigator.language : 'zh-CN')
  return config
})

// 响应拦截：401 统一清 token（跳转由页面层处理）
apiClient.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      setToken(null)
    }
    return Promise.reject(error)
  },
)

export default apiClient
