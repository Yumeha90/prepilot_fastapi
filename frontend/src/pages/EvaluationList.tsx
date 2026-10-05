/**
 * P17 面评列表（PRD §5.15 / BR-17）。
 *
 * 只列**已提交**的面评：草稿不是面评，HR 不该在 P17 里看到写着一半的评价。
 * 可见性由后端判定并直接过滤：HR / HR 主管 / 超管看全部，
 * 面试官只看**本人撰写**的（他人面评后端 403，不只是列表里不显示）。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Card, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { fetchEvaluations, type EvaluationSummary } from '@/api/evaluations'
import { fetchPositions } from '@/api/positions'

const { Text } = Typography

const CONCLUSION_COLOR: Record<string, string> = {
  pass: 'green',
  pending: 'gold',
  fail: 'red',
}

export default function EvaluationList() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [keyword, setKeyword] = useState('')
  const [positionId, setPositionId] = useState<number | undefined>(undefined)

  const { data, isLoading } = useQuery({
    queryKey: ['evaluations', positionId, keyword],
    queryFn: () => fetchEvaluations({ position_id: positionId, keyword, limit: 100 }),
  })
  const { data: positions } = useQuery({
    queryKey: ['positions', 'options'],
    queryFn: () => fetchPositions({ page: 1, page_size: 100 }),
  })

  const columns: ColumnsType<EvaluationSummary> = [
    {
      title: t('evaluation.candidate'),
      dataIndex: 'candidate_name',
      render: (name: string) => <Text strong>{name}</Text>,
    },
    { title: t('evaluation.position'), dataIndex: 'position_name' },
    { title: t('evaluation.round'), dataIndex: 'round_name' },
    { title: t('evaluation.interviewer'), dataIndex: 'interviewer_name' },
    {
      title: t('evaluation.conclusion'),
      dataIndex: 'conclusion',
      width: 110,
      render: (c: string) =>
        c ? (
          <Tag color={CONCLUSION_COLOR[c] ?? 'default'}>
            {t(`workbench.submit.conclusionValue.${c}`)}
          </Tag>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: t('evaluation.submittedAt'),
      dataIndex: 'submitted_at',
      width: 180,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString() : <Text type="secondary">—</Text>,
    },
    {
      title: t('common.action'),
      key: 'action',
      width: 160,
      render: (_, row) => (
        <Space size={4}>
          <ButtonLink onClick={() => navigate(`/evaluations/${row.session_id}`)}>
            {t('evaluation.viewDetail')}
          </ButtonLink>
          {row.content_purged && <Tag>{t('evaluation.purged')}</Tag>}
        </Space>
      ),
    },
  ]

  return (
    <Card
      title={t('evaluation.listTitle')}
      extra={
        <Space wrap>
          <Select
            allowClear
            style={{ width: 200 }}
            placeholder={t('candidate.filterPosition')}
            value={positionId}
            onChange={setPositionId}
            options={(positions?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
          />
          <Input.Search
            allowClear
            style={{ width: 180 }}
            placeholder={t('candidate.keywordPlaceholder')}
            onSearch={setKeyword}
          />
        </Space>
      }
    >
      <Text type="secondary">{t('evaluation.listHint')}</Text>
      <Table
        style={{ marginTop: 12 }}
        rowKey="session_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        locale={{ emptyText: <Empty description={t('evaluation.empty')} /> }}
      />
    </Card>
  )
}

function ButtonLink({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <Typography.Link onClick={onClick} style={{ whiteSpace: 'nowrap' }}>
      {children}
    </Typography.Link>
  )
}
