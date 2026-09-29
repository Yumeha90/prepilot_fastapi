import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { Card, Empty } from 'antd'

import { MENU_ITEMS } from '@/config/menu'

/** 尚未实现的模块占位页：菜单可见但功能未落地时给出明确反馈，而不是 404 */
export default function Placeholder() {
  const { t } = useTranslation()
  const location = useLocation()

  const flat = MENU_ITEMS.flatMap((item) => [item, ...(item.children ?? [])])
  const matched = flat.find((item) => item.path === location.pathname)

  return (
    <Card>
      <Empty
        description={
          <>
            <div style={{ fontWeight: 500 }}>{matched ? t(matched.i18nKey) : location.pathname}</div>
            <div style={{ opacity: 0.6, marginTop: 4 }}>{t('placeholder.hint')}</div>
          </>
        }
      />
    </Card>
  )
}
