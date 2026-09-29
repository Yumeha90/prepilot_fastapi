import { create } from 'zustand'

import { fetchMe, login as apiLogin, logout as apiLogout, type MeResponse, type UserRead } from '@/api/auth'
import { clearTokens, getAccessToken, getRefreshToken } from '@/api/client'

interface AuthState {
  user: UserRead | null
  permissions: string[]
  scopes: Record<string, string>
  unreadCount: number
  loaded: boolean
  /** 启动时恢复登录态：有 token 就拉 /auth/me */
  bootstrap: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshMe: () => Promise<void>
  applyMe: (me: MeResponse) => void
  hasPerm: (code: string) => boolean
  scopeOf: (resource: string) => string
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  permissions: [],
  scopes: {},
  unreadCount: 0,
  loaded: false,

  applyMe: (me) =>
    set({
      user: me.user,
      permissions: me.permissions ?? [],
      scopes: me.scopes ?? {},
      unreadCount: me.unread_count ?? 0,
      loaded: true,
    }),

  bootstrap: async () => {
    if (!getAccessToken()) {
      set({ loaded: true, user: null, permissions: [], scopes: {} })
      return
    }
    try {
      const me = await fetchMe()
      get().applyMe(me)
    } catch {
      clearTokens()
      set({ user: null, permissions: [], scopes: {}, unreadCount: 0, loaded: true })
    }
  },

  login: async (email, password) => {
    await apiLogin({ email, password })
    const me = await fetchMe()
    get().applyMe(me)
  },

  logout: async () => {
    const refresh = getRefreshToken()
    if (refresh) await apiLogout(refresh)
    clearTokens()
    set({ user: null, permissions: [], scopes: {}, unreadCount: 0 })
    window.location.replace('/login')
  },

  refreshMe: async () => {
    try {
      const me = await fetchMe()
      get().applyMe(me)
    } catch {
      /* ignore */
    }
  },

  hasPerm: (code) => get().permissions.includes(code),

  scopeOf: (resource) => get().scopes[resource] ?? 'none',
}))
