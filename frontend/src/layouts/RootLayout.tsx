import { useEffect, useMemo, useState } from 'react'
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

/** 扁平后的菜单项：path 用于匹配，parentKey 用于自动展开父级（如「系统管理」） */
type FlatItem = { path: string; parentKey?: string }

function flattenMenu(items: MenuItem[], parentKey?: string): FlatItem[] {
  const out: FlatItem[] = []
  for (const item of items) {
    if (item.path) out.push({ path: item.path, parentKey })
    if (item.children?.length) out.push(...flattenMenu(item.children, item.key))
  }
  return out
}

/**
 * 按**最长前缀**匹配当前路由落在哪个菜单下。
 *
 * 只按全等匹配的话，详情页是永远匹配不上的：`/candidates/12/match`、
 * `/evaluations/9`、`/positions/3/edit`、`/workbench/7/chain` 都不在菜单里，
 * 于是高亮掉回第一项「工作台首页」—— 点一下「查看面评」，侧边栏跑到首页去，
 * 看起来像被踢出了当前模块。这些详情页本来就该挂在各自的模块下。
 */
function matchMenu(pathname: string, flat: FlatItem[]): FlatItem | null {
  let best: FlatItem | null = null
  for (const item of flat) {
    const hit =
      item.path === '/'
        ? pathname === '/'
        : pathname === item.path || pathname.startsWith(`${item.path}/`)
    if (hit && (!best || item.path.length > best.path.length)) best = item
  }
  return best
}

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

  const visible = useMemo(() => filterMenu(MENU_ITEMS, hasPerm), [hasPerm])
  const menuItems = useMemo(() => toAntdItems(visible, t), [visible, t])
  const flat = useMemo(() => flattenMenu(visible), [visible])

  const hit = matchMenu(location.pathname, flat)
  const selectedKeys = useMemo(() => (hit ? [hit.path] : []), [hit])
  // 命中子项时展开它所属的父级（如直接访问 /system/roles），否则子菜单是收起的
  const [openKeys, setOpenKeys] = useState<string[]>(hit?.parentKey ? [hit.parentKey] : [])
  useEffect(() => {
    const key = hit?.parentKey
    if (!key) return
    setOpenKeys((prev) => (prev.includes(key) ? prev : [...prev, key]))
  }, [hit?.parentKey])

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
                openKeys={openKeys}
                onOpenChange={(keys) => setOpenKeys(keys as string[])}
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
