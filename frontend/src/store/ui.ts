import { create } from 'zustand'
import { DEFAULT_LANGUAGE, getStoredLanguage, setStoredLanguage, type LanguageCode } from '@/i18n/config'

/** UI 状态（zustand）：服务端状态一律交给 react-query，这里只放纯前端状态 */
interface UIState {
  language: LanguageCode
  sidebarCollapsed: boolean
  setLanguage: (lang: LanguageCode) => void
  toggleSidebar: () => void
}

export const useUIStore = create<UIState>((set) => ({
  language: getStoredLanguage() ?? DEFAULT_LANGUAGE,
  sidebarCollapsed: false,
  setLanguage: (lang) => {
    setStoredLanguage(lang)
    set({ language: lang })
  },
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}))
