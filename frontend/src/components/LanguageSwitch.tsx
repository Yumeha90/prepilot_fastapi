import { Select } from 'antd'
import { useTranslation } from 'react-i18next'

import { SUPPORTED_LANGUAGES, type LanguageCode } from '@/i18n/config'
import { useUIStore } from '@/store/ui'

export default function LanguageSwitch() {
  const { i18n } = useTranslation()
  const language = useUIStore((s) => s.language)
  const setLanguage = useUIStore((s) => s.setLanguage)

  return (
    <Select<LanguageCode>
      value={language}
      style={{ width: 140 }}
      onChange={(v) => {
        setLanguage(v)
        void i18n.changeLanguage(v)
      }}
      options={SUPPORTED_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
    />
  )
}
