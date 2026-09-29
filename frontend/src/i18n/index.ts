import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import zhCN from './locales/zh-CN'
import enUS from './locales/en-US'
import jaJP from './locales/ja-JP'
import { DEFAULT_LANGUAGE, resolveInitialLanguage } from './config'

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'zh-CN': { translation: zhCN },
      'en-US': { translation: enUS },
      'ja-JP': { translation: jaJP },
    },
    lng: resolveInitialLanguage(),
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: ['zh-CN', 'en-US', 'ja-JP'],
    interpolation: { escapeValue: false },
    // 权限码形如 `position:view_all` 会作为 i18n key 使用（perm.position:view_all），
    // 必须关掉默认的命名空间分隔符（:），否则会被拆成 namespace + key
    nsSeparator: false,
    keySeparator: '.',
    detection: {
      // 顺序即优先级：显式选择(localStorage) > navigator(Accept-Language)
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'prepilot.lang',
      caches: ['localStorage'],
    },
    react: { useSuspense: false },
  })

export default i18n
