import { useTranslation } from 'react-i18next'
import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'

/** 无权限访问某页面时的落地页（路由级拦截） */
export default function Forbidden() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <Result
      status="403"
      title="403"
      subTitle={t('common.noPermission')}
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          {t('nav.dashboard')}
        </Button>
      }
    />
  )
}
