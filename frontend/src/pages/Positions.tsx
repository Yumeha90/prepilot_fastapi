/**
 * P02 职位列表（PRD 3.2.1）。
 *
 * 交互口径（2026-09-30 定）：
 * - 卡片视图：职位名 / 状态 / HR / JD 完成度 / 候选人数 / 最近更新
 * - 「复制」二次确认：明确告知面试官人选不会被复制（BR-24）
 * - 「关闭」二次确认：告知不会自动淘汰在流程候选人；非草稿**不可删除**
 * - 已关闭默认隐藏，可开关显示
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import { AppstoreOutlined, CopyOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'

import {
  closePosition,
  createPosition,
  deletePosition,
  duplicatePosition,
  fetchPositions,
  reopenPosition,
  type PositionItem,
  type PositionStatus,
} from '@/api/positions'
import { extractErrorCode } from '@/api/client'
import { useAuthStore } from '@/store/auth'

const { Text, Paragraph } = Typography

const STATUS_COLOR: Record<PositionStatus, string> = {
  draft: 'default',
  open: 'processing',
  paused: 'warning',
  closed: 'default',
}

export default function Positions() {
  const { t } = useTranslation()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canEdit = hasPerm('position:edit')
  const queryClient = useQueryClient()

  const [status, setStatus] = useState<PositionStatus | undefined>(undefined)
  const [keyword, setKeyword] = useState('')
  const [includeClosed, setIncludeClosed] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [target, setTarget] = useState<PositionItem | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['positions', status, keyword, includeClosed],
    queryFn: () =>
      fetchPositions({ status, keyword: keyword || undefined, include_closed: includeClosed }),
  })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['positions'] })

  const errMsg = (error: unknown, fallbackKey: string) =>
    t(`errors.${extractErrorCode(error)}`, { defaultValue: t(fallbackKey) })

  const createMut = useMutation({
    mutationFn: (name: string) => createPosition(name),
    onSuccess: () => {
      message.success(t('position.msg.created'))
      setCreateOpen(false)
      setNewName('')
      invalidate()
    },
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const dupMut = useMutation({
    mutationFn: (id: number) => duplicatePosition(id),
    onSuccess: () => {
      message.success(t('position.msg.duplicated'))
      setTarget(null)
      invalidate()
    },
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const closeMut = useMutation({
    mutationFn: (id: number) => closePosition(id),
    onSuccess: () => {
      message.success(t('position.msg.closed'))
      setTarget(null)
      invalidate()
    },
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const reopenMut = useMutation({
    mutationFn: (id: number) => reopenPosition(id),
    onSuccess: () => {
      message.success(t('position.msg.reopened'))
      invalidate()
    },
    onError: (e) => message.error(errMsg(e, 'common.error')),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deletePosition(id),
    onSuccess: () => {
      message.success(t('position.msg.deleted'))
      setTarget(null)
      invalidate()
    },
    onError: (e) => message.error(errMsg(e, 'position.deleteBlocked')),
  })

  const items = useMemo(() => data?.items ?? [], [data])

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
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              {t('position.new')}
            </Button>
          )}
        </Space>
      </Card>

      {isError ? (
        <Card>
          <Empty description={t('common.error')}>
            <Button onClick={() => refetch()}>{t('common.retry')}</Button>
          </Empty>
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {items.map((p) => (
            <Col key={p.id} xs={24} sm={12} lg={8} xl={6}>
              <Card
                size="small"
                loading={isLoading}
                title={
                  <Space size="small">
                    <AppstoreOutlined />
                    <span>{p.name}</span>
                  </Space>
                }
                extra={<Tag color={STATUS_COLOR[p.status]}>{t(`position.status.${p.status}`)}</Tag>}
                actions={
                  canEdit
                    ? [
                        <Button
                          key="dup"
                          type="link"
                          size="small"
                          icon={<CopyOutlined />}
                          onClick={() => setTarget(p)}
                        >
                          {t('position.action.duplicate')}
                        </Button>,
                        p.status === 'closed' ? (
                          <Button
                            key="reopen"
                            type="link"
                            size="small"
                            onClick={() => reopenMut.mutate(p.id)}
                          >
                            {t('position.action.reopen')}
                          </Button>
                        ) : (
                          <Button
                            key="close"
                            type="link"
                            size="small"
                            onClick={() => setTarget(p)}
                          >
                            {t('position.action.close')}
                          </Button>
                        ),
                        <Button
                          key="del"
                          type="link"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          disabled={p.status !== 'draft'}
                          onClick={() => setTarget(p)}
                        >
                          {t('position.action.delete')}
                        </Button>,
                      ]
                    : undefined
                }
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('position.owner')}：{p.owner_name || '-'}
                  </Text>
                  <Space size="small" wrap>
                    <Badge
                      status={p.jd_status === 'confirmed' ? 'success' : 'default'}
                      text={t(`position.jdStatus.${p.jd_status}`)}
                    />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      v{p.jd_version}
                    </Text>
                  </Space>
                  <Progress
                    percent={p.jd_completion}
                    size="small"
                    status={p.jd_completion === 100 ? 'success' : 'active'}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('position.roundCount')} {p.round_count} · {t('position.candidateCount')}{' '}
                    {p.candidate_count}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('position.updatedAt')}：{new Date(p.updated_at).toLocaleString()}
                  </Text>
                </Space>
              </Card>
            </Col>
          ))}
          {!isLoading && items.length === 0 && (
            <Col span={24}>
              <Card>
                <Empty description={t('common.loading')} />
              </Card>
            </Col>
          )}
        </Row>
      )}

      <Modal
        open={createOpen}
        title={t('position.new')}
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        confirmLoading={createMut.isPending}
        okButtonProps={{ disabled: !newName.trim() }}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createMut.mutate(newName.trim())}
      >
        <Input
          placeholder={t('position.namePlaceholder')}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
      </Modal>

      {/* 复制 / 关闭 / 删除 共用一个确认弹窗，按当前职位状态决定语义 */}
      <Modal
        open={target !== null}
        title={
          target?.status === 'draft' ? t('position.duplicateTitle') : t('position.duplicateTitle')
        }
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        onCancel={() => setTarget(null)}
        footer={[
          <Button key="cancel" onClick={() => setTarget(null)}>
            {t('common.cancel')}
          </Button>,
          <Button
            key="dup"
            type="primary"
            icon={<CopyOutlined />}
            loading={dupMut.isPending}
            onClick={() => target && dupMut.mutate(target.id)}
          >
            {t('position.action.duplicate')}
          </Button>,
          target && target.status !== 'closed' ? (
            <Button
              key="close"
              loading={closeMut.isPending}
              onClick={() => closeMut.mutate(target.id)}
            >
              {t('position.action.close')}
            </Button>
          ) : null,
          target?.status === 'draft' ? (
            <Button
              key="del"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMut.isPending}
              onClick={() => deleteMut.mutate(target.id)}
            >
              {t('position.action.delete')}
            </Button>
          ) : null,
        ]}
      >
        <Space direction="vertical" size="small">
          <Text strong>{target?.name}</Text>
          <Paragraph style={{ marginBottom: 0 }}>{t('position.duplicateHint')}</Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {t('position.closeHint')}
          </Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {t('position.roundFlowHint')}
          </Paragraph>
        </Space>
      </Modal>
    </Space>
  )
}
