/**
 * 工作台各步骤页面共享的会话上下文。
 *
 * 数据查询放在 Layout 里而不是每个页面各自发一次 —— 三个步骤读的是同一份
 * 会话快照，拆页不能拆成三份状态（否则 A 页改完 B 页还是旧的）。
 *
/**
 * `leaveGuard` 是给「有未保存草稿」的步骤用的。
 *
 * 语义是**「离开前先自动保存」而不是「离开前问一句」**：
 * 返回 true 表示可以安全跳转（没改动，或已自动存好），返回 false 表示
 * 自动保存失败（校验不过 / 请求出错），此时 Layout 才弹「离开会丢失」的确认框。
 *
 * 之所以做成异步：保存是网络请求，跳转必须等它落地再走，
 * 否则点了「下一步」之后请求还在飞、页面已经卸载，反而更容易丢。
 */
import { createContext, useContext } from 'react'
import type { MutableRefObject } from 'react'

import type { WorkbenchOut, WorkbenchStep } from '@/api/workbench'

export interface WorkbenchContextValue {
  /** 会话 id */
  id: number
  data: WorkbenchOut | undefined
  isLoading: boolean
  /** 只有被指派的面试官为 true；其余角色只读 */
  canEdit: boolean
  /** 无法编辑的原因（jd_not_confirmed / resume_missing），空串表示正常 */
  blocked: string
  /** 后端下发的步骤状态；首屏未到时为空数组 */
  steps: WorkbenchStep[]
  /** 问题链异步生成：投递后开轮询 */
  startPolling: () => void
  /**
   * 带守卫的跳转：目标步骤写在这里，由 Layout 统一判断是否要先确认丢弃草稿。
   * 传绝对路径（如 `/workbench/3/chain`）或步骤下标都行。
   */
  /**
   * 步骤页注册的「离开前钩子」：有未保存内容就先存一次。
   * 返回 true = 已存好（或本来就没改动），可跳转；false = 存不下，由 Layout 拦一道。
   */
  goTo: (target: string | number) => void
  leaveGuard: MutableRefObject<LeaveGuard | null>
  /**
   * 「下一步」的开放开关，由当前步骤页注册。
   *
   * 只有**没做完本步骤就不该往下走**的步骤才需要它：Step3 公平性检查没通过，
   * 让面试官直接跳到评分与笔记，等于把合规检查这道闸拆了（§6.5）；
   * Step2 没有问题链同理 —— 公平性检查扫的是题目，没题可查。
   * 其余步骤允许带着半成品前后翻（BR-07 完整性只在提交前拦），不注册即为 false。
   * 页面卸载时 Layout 会自动复位，避免串到下一步上。
   *
   * `reason` 是置灰时显示在按钮旁边的原因（不传就用通用文案）。
   * 置灰不给原因，用户只会以为按钮坏了 —— 说清楚「缺什么」才有意义。
   */
  setNextBlocked: (blocked: boolean, reason?: string) => void
  /**
   * 本地乐观标记某一步已完成。
   *
   * 进度条的打勾原本要等一次「保存 → 服务端回包 → 重新拉快照」才亮，
   * 面试官填完最后一项要干等一拍，像是没反应。数据照样要落库（不能只在前端），
   * 但**打勾不必等请求** —— 填完即亮，请求在后台发。
   */
  markStepDone: (key: string, done: boolean) => void
}

/** 离开前钩子：异步，内部负责把未保存内容存掉 */
export type LeaveGuard = () => Promise<boolean>

export const WorkbenchContext = createContext<WorkbenchContextValue | null>(null)

export function useWorkbench(): WorkbenchContextValue {
  const ctx = useContext(WorkbenchContext)
  if (!ctx) throw new Error('useWorkbench 只能在 AI 备面工作台的步骤页里使用')
  return ctx
}
