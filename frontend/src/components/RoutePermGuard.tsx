import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { MENU_ITEMS } from '@/config/menu'
import Forbidden from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'

/**
 * 路由级权限拦截：按当前路径在菜单配置里找到所需权限码，
 * 一个都没命中则渲染 403（例如面试官直接访问 /system/roles）。
 */
export default function RoutePermGuard({ children }: { children: ReactNode }) {
  const location = useLocation()
  const hasPerm = useAuthStore((s) => s.hasPerm)

  const flat = MENU_ITEMS.flatMap((item) => [item, ...(item.children ?? [])])
  const matched = flat.find((item) => item.path === location.pathname)

  if (matched?.anyPerm?.length && !matched.anyPerm.some(hasPerm)) {
    return <Forbidden />
  }

  return <>{children}</>
}
