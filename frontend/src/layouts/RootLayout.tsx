import { Outlet } from 'react-router-dom'
import { ConfigProvider, Layout, Typography } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import jaJP from 'antd/locale/ja_JP'
import { useTranslation } from 'react-i18next'

import { useUIStore } from '@/store/ui'
import LanguageSwitch from '@/components/LanguageSwitch'

const { Header, Content, Footer } = Layout

const antdLocales = {
  'zh-CN': zhCN,
  'en-US': enUS,
  'ja-JP': jaJP,
} as const

export default function RootLayout() {
  const { t, i18n } = useTranslation()
  const language = useUIStore((s) => s.language)

  // 语言切换：i18next 与 antd locale 同步
  if (i18n.language !== language) {
    void i18n.changeLanguage(language)
  }

  const locale = antdLocales[language] ?? zhCN

  return (
    <ConfigProvider locale={locale} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
            {t('app.title')}
          </Typography.Title>
          <LanguageSwitch />
        </Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
        <Footer style={{ textAlign: 'center', color: '#888' }}>
          PrepPilot · {t('scaffold.phase')}
        </Footer>
      </Layout>
    </ConfigProvider>
  )
}
