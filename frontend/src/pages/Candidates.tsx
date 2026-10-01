/**
 * 候选人列表（按条件检索的补充视图）。
 *
 * 看板落地后主入口会变成看板，本页保留为「按条件检索」的补充视图。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Descriptions, Divider, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'

import {
  fetchCandidate,
  fetchCandidates,
  type CandidateItem,
  type ProfileStatus,
} from '@/api/candidates'
import { useAuthStore } from '@/store/auth'
import type { ResumeProfile } from '@/api/resume'

const { Text, Paragraph } = Typography

const STATUS_COLOR: Record<ProfileStatus, string> = {
  uploading: 'default',
  parsed: 'processing',
  confirmed: 'success',
  archived: 'default',
}

// 流程侧阶段：pending 待派单 / in_* 进行中 / accepted-rejected-pool-archived 终态
const STAGE_COLOR: Record<string, string> = {
  pending: 'default',
  in_r1: 'processing',
  in_r2: 'processing',
  in_hr: 'processing',
  in_offer: 'warning',
  accepted: 'success',
  rejected: 'error',
  in_pool: 'default',
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
  const [viewId, setViewId] = useState<number | null>(null)

  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v)
    setPage(1)
  }

  const { data, isLoading } = useQuery({
    queryKey: ['candidates', keyword, status, page, pageSize],
    queryFn: () =>
      fetchCandidates({ keyword: keyword || undefined, status, page, page_size: pageSize }),
  })

  // 列表是简版，点「查看简历」才拉档案原文
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['candidate', viewId],
    queryFn: () => fetchCandidate(viewId as number),
    enabled: viewId !== null,
  })
  const profile = (detail?.parsed_profile ?? null) as ResumeProfile | null

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
    { title: t('candidate.position'), dataIndex: 'position_name', width: 160 },
    {
      title: t('candidate.stage'),
      dataIndex: 'stage',
      width: 100,
      render: (v: string) =>
        v ? <Tag color={STAGE_COLOR[v] ?? 'default'}>{t(`candidate.stageName.${v}`)}</Tag> : '—',
    },
    {
      title: t('candidate.interviewer'),
      dataIndex: 'interviewer_name',
      width: 110,
      render: (v: string) => v || <Text type="secondary">—</Text>,
    },
    { title: t('candidate.createdBy'), dataIndex: 'created_by_name', width: 120 },
    {
      title: t('position.actions'),
      key: 'actions',
      width: 180,
      render: (_, row) => {
        // 已粉碎的只剩归档记录，简历内容已清空，无可操作
        if (row.profile_status === 'archived') return <Text type="secondary">—</Text>
        return (
          <Space size={0}>
            {row.profile_status !== 'confirmed' && (
              <Button
                type="link"
                size="small"
                onClick={() => navigate(`/candidates/${row.id}/parse`)}
              >
                {t('candidate.action.goParse')}
              </Button>
            )}
            <Button type="link" size="small" onClick={() => setViewId(row.id)}>
              {t('candidate.action.viewResume')}
            </Button>
          </Space>
        )
      },
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

      <Modal
        open={viewId !== null}
        title={t('candidate.action.viewResume')}
        width={720}
        loading={detailLoading}
        onCancel={() => setViewId(null)}
        footer={
          <Button onClick={() => setViewId(null)}>{t('common.ok')}</Button>
        }
      >
        {detail && (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label={t('candidate.name')}>{detail.name}</Descriptions.Item>
              <Descriptions.Item label={t('candidate.email')}>
                {detail.contact_email}
              </Descriptions.Item>
              <Descriptions.Item label={t('candidate.phone')}>
                {detail.contact_phone || '—'}
              </Descriptions.Item>
              <Descriptions.Item label={t('candidate.position')}>
                {detail.applications?.[0]?.position_name || '—'}
              </Descriptions.Item>
              <Descriptions.Item label={t('candidate.stage')}>
                {detail.applications?.[0]?.stage
                  ? t(`candidate.stageName.${detail.applications[0].stage}`)
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label={t('candidate.interviewer')}>
                {detail.applications?.[0]?.interviewer_name || '—'}
              </Descriptions.Item>
            </Descriptions>

            <Divider orientation="left" plain>
              {t('candidate.resumeOriginal')}
            </Divider>
            <Paragraph
              style={{
                maxHeight: 320,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                background: '#fafafa',
                padding: 12,
                borderRadius: 4,
              }}
            >
              {detail.resume_raw_text || '—'}
            </Paragraph>

            {profile && (
              <>
                <Divider orientation="left" plain>
                  {t('candidate.section.skills')}
                </Divider>
                {profile.skills?.length ? (
                  <Space wrap>
                    {profile.skills.map((s) => (
                      <Tag key={s}>{s}</Tag>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">—</Text>
                )}
              </>
            )}
            {!profile && (
              <>
                <Divider orientation="left" plain>
                  {t('candidate.section.skills')}
                </Divider>
                <Text type="secondary">{t('candidate.noProfile')}</Text>
              </>
            )}
          </>
        )}
      </Modal>
    </Card>
  )
}
