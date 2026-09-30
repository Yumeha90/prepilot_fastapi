/**
 * 候选人列表（PRD 3.3，S1 阶段的简版，便于自测）。
 *
 * S3 落地看板 P08 后，主入口会变成看板，本页保留为「按条件检索」的补充视图。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'

import { fetchCandidates, type CandidateItem, type ProfileStatus } from '@/api/candidates'
import { useAuthStore } from '@/store/auth'

const { Text } = Typography

const STATUS_COLOR: Record<ProfileStatus, string> = {
  uploading: 'default',
  parsed: 'processing',
  confirmed: 'success',
  archived: 'default',
}

export default function Candidates() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canUpload = hasPerm('candidate:upload_resume')

  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<ProfileStatus | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v)
    setPage(1)
  }

  const { data, isLoading } = useQuery({
    queryKey: ['candidates', keyword, status, page, pageSize],
    queryFn: () =>
      fetchCandidates({ keyword: keyword || undefined, status, page, page_size: pageSize }),
  })

  const columns: ColumnsType<CandidateItem> = [
    { title: t('candidate.name'), dataIndex: 'name', width: 120 },
    { title: t('candidate.email'), dataIndex: 'contact_email', width: 200 },
    {
      title: t('candidate.profileStatus'),
      dataIndex: 'profile_status',
      width: 110,
      render: (v: ProfileStatus) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{t(`candidate.status.${v}`)}</Tag>
      ),
    },
    { title: t('candidate.position'), dataIndex: 'position_name', width: 180 },
    { title: t('candidate.stage'), dataIndex: 'stage', width: 100 },
    { title: t('candidate.createdBy'), dataIndex: 'created_by_name', width: 120 },
    {
      title: t('position.actions'),
      key: 'actions',
      width: 160,
      render: (_, row) =>
        row.profile_status === 'confirmed' ? null : (
          <Button
            type="link"
            size="small"
            onClick={() => navigate(`/candidates/${row.id}/parse`)}
          >
            {t('candidate.action.goParse')}
          </Button>
        ),
    },
  ]

  return (
    <Card
      title={t('candidate.listTitle')}
      extra={
        canUpload && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/candidates/new')}>
            {t('candidate.uploadResume')}
          </Button>
        )
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          placeholder={t('candidate.keywordPlaceholder')}
          style={{ width: 220 }}
          onSearch={resetPage(setKeyword)}
        />
        <Space size="small">
          <Text type="secondary">{t('candidate.filterStatus')}</Text>
          <Select<ProfileStatus | 'all'>
            value={status ?? 'all'}
            style={{ width: 140 }}
            onChange={(v) => resetPage(setStatus)(v === 'all' ? undefined : v)}
            options={[
              { value: 'all', label: t('position.allStatus') },
              { value: 'uploading', label: t('candidate.status.uploading') },
              { value: 'parsed', label: t('candidate.status.parsed') },
              { value: 'confirmed', label: t('candidate.status.confirmed') },
              { value: 'archived', label: t('candidate.status.archived') },
            ]}
          />
        </Space>
      </Space>
      <Table<CandidateItem>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
        locale={{ emptyText: t('common.noData') }}
      />
    </Card>
  )
}
