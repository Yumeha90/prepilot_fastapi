import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Card, Descriptions, Space, Table, Tag, Typography } from 'antd'
import { CheckCircleFilled, CloseCircleOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import { fetchRoleMatrix, type RoleRead } from '@/api/admin'
import { useAuthStore } from '@/store/auth'

const { Paragraph } = Typography

const SCOPE_LABEL: Record<string, string> = {
  all: 'scope.all',
  assigned: 'scope.assigned',
  own: 'scope.own',
  r1r2: 'scope.r1r2',
}

export default function Roles() {
  const { t } = useTranslation()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['role-matrix'],
    queryFn: fetchRoleMatrix,
    enabled: hasPerm('system:role_view'),
  })

  const roles: RoleRead[] = data?.roles ?? []
  const catalog = data?.permission_catalog ?? []

  const columns: ColumnsType<(typeof catalog)[number]> = [
    {
      title: t('roles.permission'),
      dataIndex: 'code',
      width: 260,
      render: (code: string) => (
        <Space direction="vertical" size={0}>
          <span>{t(`perm.${code}`, { defaultValue: code })}</span>
          <span style={{ fontSize: 12, opacity: 0.6 }}>{code}</span>
        </Space>
      ),
      fixed: 'left',
    },
    ...roles.map((role) => ({
      title: t(`role.${role.code}`),
      dataIndex: 'code',
      align: 'center' as const,
      width: 120,
      render: (code: string) =>
        role.permissions.includes(code) ? (
          <CheckCircleFilled style={{ color: '#52c41a' }} />
        ) : (
          <CloseCircleOutlined style={{ opacity: 0.25 }} />
        ),
    })),
  ]

  if (!hasPerm('system:role_view')) {
    return <Card>{t('common.noPermission')}</Card>
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t('roles.readonlyHint')}
        </Paragraph>
      </Card>

      <Card title={t('roles.matrixTitle')}>
        <Table
          rowKey="code"
          size="small"
          loading={isLoading}
          dataSource={catalog}
          columns={columns}
          pagination={false}
          scroll={{ x: 'max-content' }}
          locale={{ emptyText: isError ? t('common.error') : t('common.loading') }}
        />
      </Card>

      <Card title={t('roles.scopeTitle')}>
        <Space wrap size="large">
          {roles.map((role) => (
            <Descriptions
              key={role.id}
              size="small"
              bordered
              column={1}
              title={t(`role.${role.code}`)}
              style={{ minWidth: 220 }}
            >
              {Object.entries(role.scopes).map(([resource, scope]) => (
                <Descriptions.Item key={resource} label={resource}>
                  <Tag color={scope === 'all' ? 'blue' : 'orange'}>
                    {t(SCOPE_LABEL[scope] ?? scope)}
                  </Tag>
                </Descriptions.Item>
              ))}
            </Descriptions>
          ))}
        </Space>
      </Card>
    </Space>
  )
}
