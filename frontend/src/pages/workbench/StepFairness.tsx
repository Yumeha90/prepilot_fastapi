/**
 * P11 · Step 3 公平性检查（PRD 3.4.3 / §6.5）—— 独立成页后的版本。
 *
 * 本页只负责把会话快照喂给 FairnessCheck 组件。
 *
 * **为什么进来不自动扫描（2026-10-04 拍板）**：自动扫会让「检查过了」变成
 * 一个没人触发过的动作 —— 进度条一进来就打勾，面试官根本不知道自己被放行了什么。
 * 合规检查要**由人点一次**才算数：进来是待检状态，点了「开始扫描」才出结论，
 * 结论通过才开下一步。扫描可以重复点（改了题、采纳了改写建议都要重扫一遍）。
 */
import { useEffect } from 'react'

import FairnessCheck from '@/components/FairnessCheck'
import { useWorkbench } from './context'

/** 通过 / 警告都算放行，阻断与「还没扫」都不过（与后端 FAIRNESS_DONE_RESULTS 同口径） */
const PASSED = new Set(['pass', 'warn'])

export default function StepFairness() {
  const { id, data, canEdit, setNextBlocked } = useWorkbench()

  const fairness = data?.fairness
  const passed = !!fairness?.scanned && PASSED.has(fairness.result)

  // 没检查过就别往下走：跳去写面评等于把这道闸拆了（§6.5）
  useEffect(() => {
    setNextBlocked(!passed)
    return () => setNextBlocked(false)
  }, [passed, setNextBlocked])

  return (
    <FairnessCheck
      sessionId={id}
      fairness={fairness}
      canEdit={canEdit}
      hasChain={(data?.chain?.nodes.length ?? 0) > 0}
    />
  )
}
