/**
 * P10 · Step 2 问题链（PRD 3.4.2 / §5.10）—— 独立成页后的版本。
 *
 * 本页只负责把会话快照喂给 QuestionChain 组件，并处理「异步生成 + 轮询」：
 * 投递后由 Layout 开轮询，状态不再是 running 就自动停。
 *
 * **没有问题链就把「下一步：公平性检查」置灰**（2026-10-04）：
 * 公平性检查扫的是题目，没题可查 —— 放过去只能在 Step3 看到一句
 * 「请先生成问题链」，那是把可预知的死路留给用户去撞。
 */
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import QuestionChain from '@/components/QuestionChain'
import type { ChainOut } from '@/api/workbench'
import type { LeaveGuard } from './context'
import { useWorkbench } from './context'

// 首屏数据还没到时给问题链一个空壳，避免子组件里到处判 undefined
const EMPTY_CHAIN: ChainOut = {
  nodes: [],
  generated: false,
  revision: 0,
  updated_at: null,
  generated_at: '',
  model: '',
  status: 'idle',
  error: '',
  budget_minutes: 0,
  rag_hits: {},
}

export default function StepChain() {
  const { id, data, canEdit, blocked, startPolling, leaveGuard, setNextBlocked } = useWorkbench()
  const { t } = useTranslation()

  // 同 Step1：问题链也是「改完要保存」。离开时先自动存一次，存不下才拦（见 Layout）
  const guard = useRef<LeaveGuard | null>(null)
  useEffect(() => {
    leaveGuard.current = () => (guard.current ? guard.current() : Promise.resolve(true))
    return () => {
      leaveGuard.current = null
    }
  }, [leaveGuard])

  // 生成中或一条题都没有 → 不许跳 Step3（生成完轮询回来自动解禁）
  const hasChain = (data?.chain?.nodes.length ?? 0) > 0
  const generating = data?.chain?.status === 'running'
  useEffect(() => {
    setNextBlocked(!hasChain || generating, t('workbench.chain.needChainNext'))
    return () => setNextBlocked(false)
  }, [hasChain, generating, setNextBlocked, t])

  return (
    <QuestionChain
      sessionId={id}
      chain={data?.chain ?? EMPTY_CHAIN}
      canEdit={canEdit}
      hasMatrix={(data?.matrix.rows.length ?? 0) > 0}
      blocked={blocked}
      roundType={data?.round_type ?? ''}
      onDirtyChange={(fn) => {
        guard.current = fn
      }}
      onGenerated={startPolling}
    />
  )
}
