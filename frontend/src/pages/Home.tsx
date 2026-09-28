import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Space, Tag, Typography } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons'

import { fetchHealth } from '@/api/health'

const { Title, Paragraph } = Typography

export default function Home() {
  const { t } = useTranslation()

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    retry: 1,
    refetchInterval: 15_000,
  })

  const online = !isError && data?.status === 'ok'

  const statusTag = isLoading ? (
    <Tag icon={<LoadingOutlined />} color="processing">
      {t('health.checking')}
    </Tag>
  ) : online ? (
    <Tag icon={<CheckCircleOutlined />} color="success">
      {t('health.online')}
    </Tag>
  ) : (
    <Tag icon={<CloseCircleOutlined />} color="error">
      {t('health.offline')}
    </Tag>
  )

  return (
    <Card>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Title level={2} style={{ marginBottom: 0 }}>
          {t('app.welcome')}
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t('app.subtitle')}
        </Paragraph>

        <Space>
          <span>{t('health.backend')}：</span>
          {statusTag}
        </Space>

        {isError && (
          <Alert
            type="warning"
            showIcon
            message={t('health.offline')}
            description={
              <span>
                {(error as Error)?.message ?? ''}
                <br />
                {t('scaffold.note')}
              </span>
            }
            action={
              <a onClick={() => void refetch()} style={{ cursor: 'pointer' }}>
                {isFetching ? t('common.loading') : t('common.retry')}
              </a>
            }
          />
        )}

        {online && <Alert type="success" showIcon message={t('scaffold.note')} />}
      </Space>
    </Card>
  )
}
