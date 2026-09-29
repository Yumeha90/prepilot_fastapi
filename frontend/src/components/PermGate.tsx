import type { ReactNode } from 'react'

import { useAuthStore } from '@/store/auth'

interface PermGateProps {
  /** 命中其中任意一个权限码即渲染 */
  anyPerm?: string[]
  /** 必须全部命中的权限码 */
  allPerm?: string[]
  children: ReactNode
  fallback?: ReactNode
}

/**
 * 按钮 / 区块级权限控制。
 * 例：<PermGate anyPerm={['evaluation:view_all']}><Button>查看面评</Button></PermGate>
 * 面试官没有 evaluation:view_all，因此看不到该按钮。
 */
export default function PermGate({ anyPerm, allPerm, children, fallback = null }: PermGateProps) {
  const hasPerm = useAuthStore((s) => s.hasPerm)

  const allowed = (() => {
    if (anyPerm?.length) return anyPerm.some(hasPerm)
    if (allPerm?.length) return allPerm.every(hasPerm)
    return true
  })()

  return <>{allowed ? children : fallback}</>
}
