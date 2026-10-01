/**
 * AI 备面工作台 · 会话列表（PRD 3.4 入口）。
 *
 * 面试官看「我被派了哪些面试」，HR / HR 主管看全部但进去只能看不能改
 * （2026-10-01 拍板：只有被指派的面试官能写）。
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Empty, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { fetchSessions, type SessionItem } from '@/api/sessions'

const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  s1_draft: 'default',
  s2_draft: 'processing',
  s3_draft: 'processing',
  s4_draft: 'processing',
  s5_draft: 'processing',
  submitted: 'success',
}

export default function WorkbenchList() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => fetchSessions({ limit: 100 }),
  })

  const columns: ColumnsType<SessionItem> = [
    {
      title: t('workbench.list.colCandidate'),
      dataIndex: 'candidate_name',
      render: (v: string) => v || '—',
    },
    { title: t('workbench.list.colPosition'), dataIndex: 'position_name' },
    {
      title: t('workbench.list.colRound'),
      dataIndex: 'round_name',
      render: (v: string, row) => v || t(`workbench.round.${row.round_type}`),
    },
    { title: t('workbench.list.colInterviewer'), dataIndex: 'interviewer_name' },
    {
      title: t('workbench.list.colStatus'),
      dataIndex: 'status',
      width: 130,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{t(`candidate.sessionStatus.${v}`)}</Tag>
      ),
    },
    {
      title: t('workbench.list.colUpdated'),
      dataIndex: 'updated_at',
      width: 170,
      render: (v: string) => (v ? v.slice(0, 16).replace('T', ' ') : '—'),
    },
    {
      title: t('common.action'),
      key: 'op',
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => navigate(`/workbench/${row.id}`)}>
          {t('workbench.list.enter')}
        </Button>
      ),
    },
  ]

  return (
    <Card title={t('workbench.list.title')}>
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
        {t('workbench.list.hint')}
      </Text>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={data ?? []}
        columns={columns}
        locale={{
          emptyText: <Empty description={t('workbench.list.empty')} />,
        }}
      />
    </Card>
  )
}
