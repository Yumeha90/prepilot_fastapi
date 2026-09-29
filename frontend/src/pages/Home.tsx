import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from 'antd'

import PermGate from '@/components/PermGate'
import { useAuthStore } from '@/store/auth'

const { Title, Paragraph, Text } = Typography

const SCOPE_LABEL: Record<string, string> = {
  all: 'scope.all',
  assigned: 'scope.assigned',
  own: 'scope.own',
  r1r2: 'scope.r1r2',
}

export default function Home() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const scopes = useAuthStore((s) => s.scopes)

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Title level={3} style={{ marginTop: 0 }}>
          {t('app.welcome')}
          {user ? `，${user.full_name || user.email}` : ''}
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t('app.subtitle')}
        </Paragraph>

        {user && (
          <Descriptions column={2} size="small" style={{ marginTop: 16 }} bordered>
            <Descriptions.Item label={t('auth.email')}>{user.email}</Descriptions.Item>
            <Descriptions.Item label={t('auth.role')}>
              {t(`role.${user.role_code}`)}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card title={t('home.permissionTitle')}>
        <Paragraph type="secondary">{t('home.permissionHint')}</Paragraph>
        <Space wrap>
          {permissions.map((code) => (
            <Tag key={code}>{code}</Tag>
          ))}
        </Space>

        <Descriptions column={1} size="small" style={{ marginTop: 16 }} title={t('home.scopeTitle')}>
          {Object.entries(scopes).map(([resource, scope]) => (
            <Descriptions.Item key={resource} label={resource}>
              <Tag color={scope === 'all' ? 'blue' : 'orange'}>
                {t(SCOPE_LABEL[scope] ?? scope)}
              </Tag>
            </Descriptions.Item>
          ))}
        </Descriptions>
      </Card>

      <Card title={t('home.buttonDemoTitle')}>
        <Paragraph type="secondary">{t('home.buttonDemoHint')}</Paragraph>
        <Space wrap>
          {/* 面试官没有 evaluation:view_all，看不到「查看面评」；但有 view_own，能看到「查看我的面评」 */}
          <PermGate anyPerm={['evaluation:view_all']}>
            <Button type="primary" onClick={() => navigate('/evaluations')}>
              {t('home.viewEvaluation')}
            </Button>
          </PermGate>
          <PermGate anyPerm={['evaluation:view_own']}>
            <Button onClick={() => navigate('/evaluations')}>{t('home.viewMyEvaluation')}</Button>
          </PermGate>
          <PermGate anyPerm={['candidate:dispose']}>
            <Button onClick={() => navigate('/candidates')}>{t('home.disposeCandidate')}</Button>
          </PermGate>
          <PermGate anyPerm={['system:purge']}>
            <Button danger onClick={() => navigate('/system/lifecycle')}>
              {t('home.purgeData')}
            </Button>
          </PermGate>
        </Space>
        {!permissions.includes('evaluation:view_all') && (
          <Alert
            style={{ marginTop: 16 }}
            type="info"
            showIcon
            message={
              <Text>{t('home.noViewAllHint')}</Text>
            }
          />
        )}
      </Card>
    </Space>
  )
}
