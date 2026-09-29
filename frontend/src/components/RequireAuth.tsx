import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Spin } from 'antd'

import { clearTokens } from '@/api/client'
import { useAuthStore } from '@/store/auth'

/**
 * 登录守卫：未登录跳 /login（保留来源路径），登录态恢复中显示 Spin。
 * 同时监听请求层抛出的 auth-expired 事件（refresh 失效）并清空登录态。
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const loaded = useAuthStore((s) => s.loaded)
  const location = useLocation()

  useEffect(() => {
    const handler = () => {
      clearTokens()
      useAuthStore.setState({ user: null, permissions: [], scopes: {}, unreadCount: 0 })
    }
    window.addEventListener('prepilot:auth-expired', handler)
    return () => window.removeEventListener('prepilot:auth-expired', handler)
  }, [])

  if (!loaded) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
