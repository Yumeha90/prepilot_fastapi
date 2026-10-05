/**
 * AI 备面工作台：5 个步骤拆成 5 个页面后的公共约定。
 *
 * **为什么拆**：Step1~Step3 三个模块（矩阵 / 问题链 / 公平性）摞在一页上，
 * 面试官要在一屏里同时消化三套编辑控件，滚动条长得看不见尽头。
 * 拆页后每页只做一件事，顶部保留 Steps 进度条 —— 既知道自己在哪一步，
 * 也能一步点回上一步改内容。
 *
 * **为什么步骤路径写死在这里**：Steps 的 onChange 要给下标，
 * 底部「上一步 / 下一步」要给路径，两处必须同源，否则进度条和跳转会错位。
 */

/** 步骤在 URL 里的片段，顺序即步骤顺序 */
export const STEP_PATHS = ['matrix', 'chain', 'fairness', 'evaluation', 'submit'] as const

/** 步骤在后端 steps[] 里的 key（与 STEP_PATHS 一一对应） */
export const STEP_KEYS = ['step1', 'step2', 'step3', 'step4', 'step5'] as const

/** 五个步骤全部已实现（Step5 随 P13 落地） */
export const IMPLEMENTED_STEPS = 5

/** 当前 URL 落在第几步（从 0 起）；匹配不到就当第 1 步 */
export function stepIndexFromPath(pathname: string): number {
  const hit = STEP_PATHS.findIndex((p) => pathname.split('/').includes(p))
  return hit < 0 ? 0 : hit
}
