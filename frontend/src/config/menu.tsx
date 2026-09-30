import {
  AppstoreOutlined,
  DashboardOutlined,
  FileTextOutlined,
  NodeIndexOutlined,
  SettingOutlined,
  TeamOutlined,
  TrophyOutlined,
} from '@ant-design/icons'

/**
 * 侧边栏菜单配置。
 * - anyPerm：命中其中任意一个权限码才渲染（权限码由 /api/auth/me 下发）
 * - 有 children 的父节点：自身不配 anyPerm，由子节点决定是否可见（子项全不可见则父节点隐藏）
 * - 未实现的页面走 Placeholder 页，保证菜单点击有反馈而不是 404
 */
export interface MenuItem {
  key: string
  path?: string
  i18nKey: string
  icon?: React.ReactNode
  anyPerm?: string[]
  children?: MenuItem[]
}

export const MENU_ITEMS: MenuItem[] = [
  {
    key: 'dashboard',
    path: '/',
    i18nKey: 'nav.dashboard',
    icon: <DashboardOutlined />,
    anyPerm: ['dashboard:view'],
  },
  {
    key: 'position',
    path: '/positions',
    i18nKey: 'nav.positions',
    icon: <AppstoreOutlined />,
    anyPerm: ['position:view_all', 'position:view_assigned'],
  },
  {
    key: 'candidate',
    path: '/candidates',
    i18nKey: 'nav.candidates',
    icon: <TeamOutlined />,
    anyPerm: ['candidate:view_all', 'candidate:view_assigned'],
  },
  {
    key: 'workbench',
    path: '/workbench',
    i18nKey: 'nav.workbench',
    icon: <NodeIndexOutlined />,
    anyPerm: ['workbench:enter_all', 'workbench:enter_view', 'workbench:enter_assigned'],
  },
  {
    key: 'evaluation',
    path: '/evaluations',
    i18nKey: 'nav.evaluations',
    icon: <FileTextOutlined />,
    anyPerm: ['evaluation:view_all', 'evaluation:view_own'],
  },
  {
    key: 'match',
    path: '/matches',
    i18nKey: 'nav.matches',
    icon: <TrophyOutlined />,
    anyPerm: ['match:view'],
  },
  {
    key: 'system',
    i18nKey: 'nav.system',
    icon: <SettingOutlined />,
    children: [
      {
        key: 'system-roles',
        path: '/system/roles',
        i18nKey: 'nav.rolePermissions',
        anyPerm: ['system:role_view'],
      },
      {
        key: 'system-lifecycle',
        path: '/system/lifecycle',
        i18nKey: 'nav.lifecycle',
        anyPerm: ['system:lifecycle'],
      },
    ],
  },
]

/** 已实现的路由路径（其余走 Placeholder） */
export const IMPLEMENTED_PATHS = new Set<string>(['/', '/system/roles', '/positions'])
