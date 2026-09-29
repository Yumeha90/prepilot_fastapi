import { Outlet } from 'react-router-dom'
import { ConfigProvider, Layout, Typography } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import jaJP from 'antd/locale/ja_JP'
import { useTranslation } from 'react-i18next'

import LanguageSwitch from '@/components/LanguageSwitch'
import { useUIStore } from '@/store/ui'

const { Header, Content } = Layout
const { Title, Paragraph } = Typography

const antdLocales = {
  'zh-CN': zhCN,
  'en-US': enUS,
  'ja-JP': jaJP,
} as const

/** 登录 / 注册 / 忘记密码 的公用外框：顶部标题 + 语言切换，中间居中卡片 */
export default function AuthLayout() {
  const { t, i18n } = useTranslation()
  const language = useUIStore((s) => s.language)

  if (i18n.language !== language) {
    void i18n.changeLanguage(language)
  }

  return (
    <ConfigProvider locale={antdLocales[language] ?? zhCN} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Title level={5} style={{ color: '#fff', margin: 0 }}>
            {t('app.title')}
          </Title>
          <LanguageSwitch />
        </Header>
        <Content style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div style={{ width: '100%', maxWidth: 420 }}>
            <Paragraph type="secondary" style={{ textAlign: 'center', marginBottom: 16 }}>
              {t('app.subtitle')}
            </Paragraph>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </ConfigProvider>
  )
}
