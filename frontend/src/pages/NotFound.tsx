import { useTranslation } from 'react-i18next'
import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'

export default function NotFound() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <Result
      status="404"
      title="404"
      subTitle={t('common.error')}
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          {t('nav.home')}
        </Button>
      }
    />
  )
}
