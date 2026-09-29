import { useMemo } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ConfigProvider, Layout, Menu, Spin, Typography, type MenuProps } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import jaJP from 'antd/locale/ja_JP'
import { useTranslation } from 'react-i18next'

import { MENU_ITEMS, type MenuItem } from '@/config/menu'
import LanguageSwitch from '@/components/LanguageSwitch'
import NotificationBell from '@/components/NotificationBell'
import RoutePermGuard from '@/components/RoutePermGuard'
import UserMenu from '@/components/UserMenu'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'

const { Header, Content, Sider } = Layout
const { Title } = Typography

const antdLocales = {
  'zh-CN': zhCN,
  'en-US': enUS,
  'ja-JP': jaJP,
} as const

/** 按权限码过滤菜单：父节点在其子项全部不可见时一并隐藏 */
function filterMenu(items: MenuItem[], hasPerm: (code: string) => boolean): MenuItem[] {
  const result: MenuItem[] = []
  for (const item of items) {
    if (item.children?.length) {
      const children = filterMenu(item.children, hasPerm)
      if (children.length) result.push({ ...item, children })
      continue
    }
    if (item.anyPerm?.length && !item.anyPerm.some(hasPerm)) continue
    result.push(item)
  }
  return result
}

type AntdMenuItems = NonNullable<MenuProps['items']>

function toAntdItems(items: MenuItem[], t: (k: string) => string): AntdMenuItems {
  return items.map((item) => {
    const base = { key: item.path ?? item.key, icon: item.icon, label: t(item.i18nKey) }
    return item.children?.length
      ? { ...base, children: toAntdItems(item.children, t) }
      : base
  })
}

export default function RootLayout() {
  const { t, i18n } = useTranslation()
  const language = useUIStore((s) => s.language)
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const loaded = useAuthStore((s) => s.loaded)
  const navigate = useNavigate()
  const location = useLocation()

  if (i18n.language !== language) {
    void i18n.changeLanguage(language)
  }

  const menuItems = useMemo(() => toAntdItems(filterMenu(MENU_ITEMS, hasPerm), t), [hasPerm, t])
  const selectedKeys = useMemo(() => {
    const path = location.pathname
    const exists = menuItems.some((i) => i && 'key' in i && i.key === path)
    return [exists ? path : String(menuItems[0] && 'key' in menuItems[0] ? menuItems[0].key : '/')]
  }, [location.pathname, menuItems])

  const locale = antdLocales[language] ?? zhCN

  return (
    <ConfigProvider locale={locale} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingInline: 16,
          }}
        >
          <Title level={5} style={{ color: '#fff', margin: 0 }}>
            {t('app.title')}
          </Title>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <LanguageSwitch />
            <NotificationBell />
            <UserMenu />
          </div>
        </Header>
        <Layout>
          <Sider breakpoint="lg" collapsedWidth="64" theme="light" width={200}>
            {loaded ? (
              <Menu
                mode="inline"
                style={{ height: '100%', borderInlineEnd: 0, paddingTop: 8 }}
                items={menuItems}
                selectedKeys={selectedKeys}
                onClick={({ key }) => navigate(key)}
              />
            ) : (
              <div style={{ padding: 24, textAlign: 'center' }}>
                <Spin />
              </div>
            )}
          </Sider>
          <Content style={{ padding: 24 }}>
            <RoutePermGuard>
              <Outlet />
            </RoutePermGuard>
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}
