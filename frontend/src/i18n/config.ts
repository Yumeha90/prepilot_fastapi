// 语言优先级（对齐 PRD）：显式选择 > 用户偏好 > Accept-Language > 默认 zh-CN
const PREFERRED_LANG_KEY = 'prepilot.lang'

export const SUPPORTED_LANGUAGES = [
  { code: 'zh-CN', label: '简体中文' },
  { code: 'en-US', label: 'English' },
  { code: 'ja-JP', label: '日本語' },
] as const

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code']

export const DEFAULT_LANGUAGE: LanguageCode = 'zh-CN'

/** 读取用户显式选择的语言（localStorage），无则返回 null */
export function getStoredLanguage(): LanguageCode | null {
  try {
    const v = localStorage.getItem(PREFERRED_LANG_KEY)
    if (v && SUPPORTED_LANGUAGES.some((l) => l.code === v)) {
      return v as LanguageCode
    }
  } catch {
    /* localStorage 不可用时忽略 */
  }
  return null
}

/** 持久化用户显式选择 */
export function setStoredLanguage(code: LanguageCode): void {
  try {
    localStorage.setItem(PREFERRED_LANG_KEY, code)
  } catch {
    /* ignore */
  }
}

/** 从 Accept-Language 推断受支持语言 */
export function detectFromAcceptLanguage(): LanguageCode | null {
  const raw = typeof navigator !== 'undefined' ? navigator.language : ''
  if (!raw) return null
  if (SUPPORTED_LANGUAGES.some((l) => l.code === raw)) return raw as LanguageCode
  const base = raw.split('-')[0]
  const matched = SUPPORTED_LANGUAGES.find((l) => l.code.split('-')[0] === base)
  return matched ? matched.code : null
}

/** 解析最终语言：显式 > 偏好 > Accept-Language > 默认 */
export function resolveInitialLanguage(): LanguageCode {
  return getStoredLanguage() ?? detectFromAcceptLanguage() ?? DEFAULT_LANGUAGE
}
