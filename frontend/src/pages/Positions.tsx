/**
 * 职位与 JD 列表页（PRD 3.2.1）。
 *
 * 交互口径（2026-09-30 改版）：
 * - 表格视图：职位名 / 状态 / HR 负责人 / JD 状态 / 轮次 / 候选人 / 最近更新 / 操作
 * - JD 状态直接显示「已确认 v1」这类文本，不再用进度条
 * - 新建 / 编辑跳转到独立页面（/positions/new、/positions/:id/edit）
 * - 复制 / 关闭 / 删除各自弹窗，标题与文案与操作一一对应；确定后执行并刷新列表
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Input, Modal, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'

import {
  closePosition,
  deletePosition,
  duplicatePosition,
  fetchPositions,
  pausePosition,
  publishPosition,
  reopenPosition,
  resumePosition,
  type PositionItem,
  type PositionStatus,
} from '@/api/positions'
import { extractErrorCode } from '@/api/client'
import { useAuthStore } from '@/store/auth'

const { Text } = Typography

const STATUS_COLOR: Record<PositionStatus, string> = {
  draft: 'default',
  open: 'processing',
  paused: 'warning',
  closed: 'default',
}

type ActionKind = 'duplicate' | 'close' | 'delete'

export default function Positions() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canEdit = hasPerm('position:edit')
  const queryClient = useQueryClient()

  const [status, setStatus] = useState<PositionStatus | undefined>(undefined)
  const [keyword, setKeyword] = useState('')
  const [includeClosed, setIncludeClosed] = useState(false)
  const [pending, setPending] = useState<{ action: ActionKind; item: PositionItem } | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['positions', status, keyword, includeClosed],
    queryFn: () =>
      fetchPositions({ status, keyword: keyword || undefined, include_closed: includeClosed }),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['positions'] })
  }

  const errMsg = (error: unknown, fallbackKey: string) =>
    t(`errors.${extractErrorCode(error)}`, { defaultValue: t(fallbackKey) })

  const afterAction = (msgKey: string) => {
    setPending(null)
    refresh()
    navigate('/positions')
    message.success(t(msgKey))
  }

  const dupMut = useMutation({
    mutationFn: (id: number) => duplicatePosition(id),
    onSuccess: () => afterAction('position.msg.duplicated'),
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const closeMut = useMutation({
    mutationFn: (id: number) => closePosition(id),
    onSuccess: () => afterAction('position.msg.closed'),
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const reopenMut = useMutation({
    mutationFn: (id: number) => reopenPosition(id),
    onSuccess: () => {
      refresh()
      message.success(t('position.msg.reopened'))
    },
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  // 状态流转三件套：发布（草稿→招聘中）/ 暂停（招聘中→已暂停）/ 恢复（已暂停→招聘中）
  // 均由后端校验（JD 未确认或轮次不合规 → publish_blocked / status_invalid），
  // 前端不额外弹窗，直接把后端文案提示出来。
  const statusMut = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'publish' | 'pause' | 'resume' }) =>
      action === 'publish' ? publishPosition(id) : action === 'pause' ? pausePosition(id) : resumePosition(id),
    onSuccess: (_data, vars) => {
      refresh()
      message.success(
        t(
          vars.action === 'publish'
            ? 'position.msg.published'
            : vars.action === 'pause'
              ? 'position.msg.paused'
              : 'position.msg.resumed',
        ),
      )
    },
    onError: (e) => message.error(errMsg(e, 'position.publishBlocked')),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deletePosition(id),
    onSuccess: () => afterAction('position.msg.deleted'),
    onError: (e) => message.error(errMsg(e, 'position.deleteBlocked')),
  })

  const runPending = () => {
    if (!pending) return
    const id = pending.item.id
    if (pending.action === 'duplicate') dupMut.mutate(id)
    else if (pending.action === 'close') closeMut.mutate(id)
    else deleteMut.mutate(id)
  }

  const dialogTitle = pending
    ? t(
        pending.action === 'duplicate'
          ? 'position.duplicateTitle'
          : pending.action === 'close'
            ? 'position.closeTitle'
            : 'position.deleteTitle',
      )
    : ''

  const dialogBody = pending
    ? t(
        pending.action === 'duplicate'
          ? 'position.duplicateHint'
          : pending.action === 'close'
            ? 'position.closeHint'
            : 'position.deleteHint',
      )
    : ''

  const columns: ColumnsType<PositionItem> = [
    { title: t('position.name'), dataIndex: 'name', key: 'name' },
    {
      title: t('position.filterStatus'),
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: PositionStatus) => (
        <Tag color={STATUS_COLOR[v]}>{t(`position.status.${v}`)}</Tag>
      ),
    },
    {
      title: t('position.owner'),
      dataIndex: 'owner_name',
      key: 'owner_name',
      width: 120,
      render: (v: string) => v || '-',
    },
    {
      title: t('position.jdColumn'),
      key: 'jd',
      width: 130,
      render: (_, row) => {
        if (row.jd_status === 'confirmed') {
          return <Tag color="success">{`${t('position.jdStatus.confirmed')} v${row.jd_version}`}</Tag>
        }
        return <Tag>{t(`position.jdStatus.${row.jd_status}`)}</Tag>
      },
    },
    { title: t('position.roundCount'), dataIndex: 'round_count', key: 'round_count', width: 80 },
    {
      title: t('position.candidateCount'),
      dataIndex: 'candidate_count',
      key: 'candidate_count',
      width: 90,
    },
    {
      title: t('position.updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
  ]

  if (canEdit) {
    columns.push({
      title: t('position.actions'),
      key: 'actions',
      width: 300,
      render: (_, row) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => navigate(`/positions/${row.id}/edit`)}>
            {t('position.action.edit')}
          </Button>
          <Button type="link" size="small" onClick={() => setPending({ action: 'duplicate', item: row })}>
            {t('position.action.duplicate')}
          </Button>
          {row.status === 'draft' && (
            <Button
              type="link"
              size="small"
              onClick={() => statusMut.mutate({ id: row.id, action: 'publish' })}
            >
              {t('position.action.publish')}
            </Button>
          )}
          {row.status === 'open' && (
            <Button
              type="link"
              size="small"
              onClick={() => statusMut.mutate({ id: row.id, action: 'pause' })}
            >
              {t('position.action.pause')}
            </Button>
          )}
          {row.status === 'paused' && (
            <Button
              type="link"
              size="small"
              onClick={() => statusMut.mutate({ id: row.id, action: 'resume' })}
            >
              {t('position.action.resume')}
            </Button>
          )}
          {row.status === 'closed' ? (
            <Button type="link" size="small" onClick={() => reopenMut.mutate(row.id)}>
              {t('position.action.reopen')}
            </Button>
          ) : (
            <Button type="link" size="small" onClick={() => setPending({ action: 'close', item: row })}>
              {t('position.action.close')}
            </Button>
          )}
          <Button
            type="link"
            size="small"
            danger
            disabled={row.status !== 'draft'}
            onClick={() => setPending({ action: 'delete', item: row })}
          >
            {t('position.action.delete')}
          </Button>
        </Space>
      ),
    })
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Space wrap size="middle">
          <Space size="small">
            <Text type="secondary">{t('position.keyword')}</Text>
            <Input.Search
              allowClear
              placeholder={t('position.keywordPlaceholder')}
              style={{ width: 220 }}
              onSearch={(v) => setKeyword(v.trim())}
            />
          </Space>
          <Space size="small">
            <Text type="secondary">{t('position.filterStatus')}</Text>
            <Select<PositionStatus | 'all'>
              value={status ?? 'all'}
              style={{ width: 140 }}
              onChange={(v) => setStatus(v === 'all' ? undefined : v)}
              options={[
                { value: 'all', label: t('position.allStatus') },
                { value: 'draft', label: t('position.status.draft') },
                { value: 'open', label: t('position.status.open') },
                { value: 'paused', label: t('position.status.paused') },
                { value: 'closed', label: t('position.status.closed') },
              ]}
            />
          </Space>
          <Space size="small">
            <Text type="secondary">{t('position.showClosed')}</Text>
            <Switch checked={includeClosed} onChange={setIncludeClosed} />
          </Space>
          {canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/positions/new')}>
              {t('position.new')}
            </Button>
          )}
        </Space>
      </Card>

      <Card>
        {isError ? (
          <Space direction="vertical">
            <Text type="danger">{t('common.error')}</Text>
            <Button onClick={() => refetch()}>{t('common.retry')}</Button>
          </Space>
        ) : (
          <Table<PositionItem>
            rowKey="id"
            loading={isLoading}
            columns={columns}
            dataSource={data?.items ?? []}
            pagination={false}
            locale={{ emptyText: t('common.noData') }}
          />
        )}
      </Card>

      <Modal
        open={pending !== null}
        title={dialogTitle}
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        confirmLoading={dupMut.isPending || closeMut.isPending || deleteMut.isPending}
        onCancel={() => setPending(null)}
        onOk={runPending}
      >
        <Space direction="vertical" size="small">
          <Text strong>{pending?.item.name}</Text>
          <Text>{dialogBody}</Text>
        </Space>
      </Modal>
    </Space>
  )
}
