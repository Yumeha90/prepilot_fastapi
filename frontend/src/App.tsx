import { useEffect } from 'react'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import jaJP from 'antd/locale/ja_JP'
import { useTranslation } from 'react-i18next'

import router from '@/routes'
import { useAuthStore } from '@/store/auth'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 10_000,
    },
  },
})

// antd 自带组件文案（分页「10 条/页」、空态、日期选择器等）跟随界面语言，
// 否则分页那一块会一直是英文，与全中文界面混着。
const ANTD_LOCALE = { 'zh-CN': zhCN, 'en-US': enUS, 'ja-JP': jaJP } as const

export default function App() {
  const { i18n } = useTranslation()

  // 启动时恢复登录态：localStorage 里有 access token 就拉一次 /auth/me
  useEffect(() => {
    void useAuthStore.getState().bootstrap()
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={ANTD_LOCALE[i18n.language as keyof typeof ANTD_LOCALE] ?? zhCN}
      >
        <RouterProvider router={router} />
      </ConfigProvider>
    </QueryClientProvider>
  )
}
