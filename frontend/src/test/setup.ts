/**
 * 前端单测的公共准备（vitest，jsdom）。
 *
 * - antd 在 jsdom 下要 matchMedia 与 ResizeObserver 兜底，否则组件一渲染就抛
 * - i18n 用真实实例初始化，这样断言的是页面真正会显示的文案，不是写死的中文
 */
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

import i18n from '@/i18n'

// jsdom 的 navigator.language 是 en-US，LanguageDetector 会据此选英文。
// 项目的主语言是中文，断言统一按中文写，避免测试跟着环境语言漂移。
void i18n.changeLanguage('zh-CN')

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia
}

if (!globalThis.ResizeObserver) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

afterEach(() => {
  cleanup()
})
