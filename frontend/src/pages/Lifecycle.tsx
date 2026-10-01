/**
 * 数据生命周期（PRD 3.5.1 / §5.14 P15）。
 *
 * 口径：
 * - **策略只可编辑不可删除**：保存后旧版本转历史（页面下方只读表），
 *   回答得了「这个人被粉碎时生效的是多少天」。
 * - **手动粉碎必须二次确认**：弹窗勾选 + 接口 `confirm=true`，后端还会再挡一道。
 * - **已录用不粉碎**（BR-10 原文是「未入职」）：扫描就排除了，执行时再兜一层。
 * - 定时任务每小时自动跑一次，页面提供「立即执行一次」用于验收与运维。
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Form,
  InputNumber,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'

import {
  fetchLifecycle,
  purgeCandidates,
  runAutoPurge,
  scanExpired,
  updatePolicy,
  type ScanItem,
} from '@/api/lifecycle'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/store/auth'

const { Paragraph } = Typography

export default function Lifecycle() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)

  const canView = hasPerm('system:lifecycle')
  const canPurge = hasPerm('system:purge')

  const [scanDays, setScanDays] = useState<number | null>(null)
  const [items, setItems] = useState<ScanItem[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [policyOpen, setPolicyOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['lifecycle'],
    queryFn: fetchLifecycle,
    enabled: canView,
  })

  const policy = data?.policy
  const effectiveDays = scanDays ?? policy?.days ?? 90

  const scanMutation = useMutation({
    mutationFn: () => scanExpired(effectiveDays),
    onSuccess: (res) => {
      setItems(res.items)
      setSelected([])
      message.success(t('lifecycle.msg.scanned', { n: res.total }))
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  })

  const purgeMutation = useMutation({
    mutationFn: () => purgeCandidates({ candidate_ids: selected, confirm: true }),
    onSuccess: (res) => {
      setConfirmOpen(false)
      setConfirmed(false)
      setSelected([])
      setItems([])
      const skipped = res.skipped_purged + res.skipped_accepted + res.skipped_missing
      if (skipped > 0) {
        message.warning(t('lifecycle.msg.purgedWithSkipped', { n: res.purged, m: skipped }))
      } else {
        message.success(t('lifecycle.msg.purged', { n: res.purged }))
      }
      void queryClient.invalidateQueries({ queryKey: ['lifecycle'] })
      void queryClient.invalidateQueries({ queryKey: ['board'] })
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  })

  const autoMutation = useMutation({
    mutationFn: runAutoPurge,
    onSuccess: (res) => {
      if (!res.enabled) {
        message.info(t('lifecycle.msg.autoDisabled'))
      } else {
        message.success(t('lifecycle.msg.autoRan', { n: res.scanned, m: res.purged }))
      }
      setItems([])
      setSelected([])
      void queryClient.invalidateQueries({ queryKey: ['lifecycle'] })
      void queryClient.invalidateQueries({ queryKey: ['board'] })
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  })

  const policyMutation = useMutation({
    mutationFn: (payload: { name?: string; days?: number; enabled?: boolean }) =>
      updatePolicy(payload),
    onSuccess: () => {
      setPolicyOpen(false)
      message.success(t('lifecycle.msg.policyUpdated'))
      void queryClient.invalidateQueries({ queryKey: ['lifecycle'] })
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  })

  const scanColumns: ColumnsType<ScanItem> = useMemo(
    () => [
      { title: t('lifecycle.colName'), dataIndex: 'name', width: 140 },
      { title: t('lifecycle.colEmail'), dataIndex: 'email', width: 200 },
      {
        title: t('lifecycle.colPosition'),
        dataIndex: 'position_names',
        render: (names: string[]) => names.filter(Boolean).join('、') || '—',
      },
      {
        title: t('lifecycle.colStage'),
        dataIndex: 'stage',
        width: 110,
        render: (stage: string) =>
          stage ? t(`candidate.stageName.${stage}`, { defaultValue: stage }) : '—',
      },
      {
        title: t('lifecycle.colSinceAt'),
        dataIndex: 'since_at',
        width: 130,
        render: (v: string) => dayjs(v).format('YYYY-MM-DD'),
      },
      {
        title: t('lifecycle.colOverdue'),
        dataIndex: 'days_overdue',
        width: 100,
        render: (n: number) => <Tag color="volcano">{t('lifecycle.overdueDays', { n })}</Tag>,
      },
    ],
    [t],
  )

  const historyColumns: ColumnsType<NonNullable<typeof data>['history'][number]> = [
    { title: t('lifecycle.colHistoryName'), dataIndex: 'name' },
    { title: t('lifecycle.colHistoryDays'), dataIndex: 'days', width: 110 },
    {
      title: t('lifecycle.colHistoryEnabled'),
      dataIndex: 'enabled',
      width: 110,
      render: (v: boolean) =>
        v ? (
          <Tag color="green">{t('lifecycle.statusEnabled')}</Tag>
        ) : (
          <Tag>{t('lifecycle.statusDisabled')}</Tag>
        ),
    },
    {
      title: t('lifecycle.colHistoryCreatedAt'),
      dataIndex: 'created_at',
      width: 180,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm'),
    },
  ]

  if (!canView) {
    return <Card>{t('common.noPermission')}</Card>
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card title={t('lifecycle.title')} loading={isLoading}>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t('lifecycle.hint')}
        </Paragraph>
      </Card>

      <Card
        title={t('lifecycle.policyTitle')}
        loading={isLoading}
        extra={
          <Space>
            <Button onClick={() => setPolicyOpen(true)}>{t('lifecycle.editPolicy')}</Button>
            {canPurge && (
              <Button
                type="primary"
                loading={autoMutation.isPending}
                onClick={() => autoMutation.mutate()}
              >
                {t('lifecycle.runNow')}
              </Button>
            )}
          </Space>
        }
      >
        {policy && (
          <Descriptions column={2} size="small">
            <Descriptions.Item label={t('lifecycle.policyName')}>{policy.name}</Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.days')}>
              {t('lifecycle.daysValue', { n: policy.days })}
            </Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.enabled')}>
              {policy.enabled ? (
                <Tag color="green">{t('lifecycle.statusEnabled')}</Tag>
              ) : (
                <Tag>{t('lifecycle.statusDisabled')}</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.pendingCount')}>{data?.pending ?? 0}</Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.lastRunAt')}>
              {policy.last_run_at ? dayjs(policy.last_run_at).format('YYYY-MM-DD HH:mm') : t('lifecycle.neverRun')}
            </Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.lastPurged')}>
              {policy.last_purged}
            </Descriptions.Item>
            <Descriptions.Item label={t('lifecycle.purgedTotal')}>
              {data?.purged_total ?? 0}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      {canPurge && (
        <Card
          title={t('lifecycle.scanTitle')}
          extra={
            <Space>
              <InputNumber
                min={0}
                max={3650}
                value={effectiveDays}
                onChange={(v) => setScanDays(typeof v === 'number' ? v : null)}
                addonAfter={t('lifecycle.daysUnit')}
                style={{ width: 160 }}
              />
              <Button
                loading={scanMutation.isPending}
                onClick={() => scanMutation.mutate()}
              >
                {t('lifecycle.scan')}
              </Button>
              <Button
                danger
                type="primary"
                disabled={selected.length === 0}
                onClick={() => {
                  setConfirmed(false)
                  setConfirmOpen(true)
                }}
              >
                {t('lifecycle.purgeSelected', { n: selected.length })}
              </Button>
            </Space>
          }
        >
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={t('lifecycle.scanHint')}
          />
          <Table
            rowKey="candidate_id"
            size="small"
            dataSource={items}
            columns={scanColumns}
            loading={scanMutation.isPending}
            pagination={false}
            scroll={{ x: 'max-content' }}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys as number[]),
            }}
            locale={{ emptyText: t('lifecycle.empty') }}
          />
        </Card>
      )}

      <Card title={t('lifecycle.historyTitle')}>
        <Table
          rowKey="id"
          size="small"
          dataSource={data?.history ?? []}
          columns={historyColumns}
          pagination={false}
          locale={{ emptyText: t('common.noData') }}
        />
      </Card>

      <Modal
        open={policyOpen}
        title={t('lifecycle.editPolicyTitle')}
        onCancel={() => setPolicyOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Paragraph type="secondary">{t('lifecycle.editHint')}</Paragraph>
        <Form
          layout="vertical"
          initialValues={{ days: policy?.days ?? 90, enabled: policy?.enabled ?? true }}
          onFinish={(values) => policyMutation.mutate(values)}
        >
          <Form.Item name="days" label={t('lifecycle.days')} rules={[{ required: true }]}>
            <InputNumber min={1} max={3650} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="enabled" label={t('lifecycle.enabled')} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={policyMutation.isPending}>
              {t('common.save')}
            </Button>
            <Button onClick={() => setPolicyOpen(false)}>{t('common.cancel')}</Button>
          </Space>
        </Form>
      </Modal>

      <Modal
        open={confirmOpen}
        title={t('lifecycle.purgeConfirmTitle')}
        okText={t('lifecycle.purgeConfirmOk')}
        cancelText={t('common.cancel')}
        okButtonProps={{ danger: true, disabled: !confirmed, loading: purgeMutation.isPending }}
        onOk={() => purgeMutation.mutate()}
        onCancel={() => {
          setConfirmOpen(false)
          setConfirmed(false)
        }}
        destroyOnClose
      >
        <Alert type="error" showIcon message={t('lifecycle.purgeConfirmHint')} style={{ marginBottom: 12 }} />
        <Paragraph>
          {selected.length} {t('lifecycle.purgeCountUnit')}
        </Paragraph>
        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          {items
            .filter((i) => selected.includes(i.candidate_id))
            .map((i) => i.name)
            .join('、')}
        </Paragraph>
        <Checkbox
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        >
          {t('lifecycle.purgeConfirmCheck')}
        </Checkbox>
      </Modal>
    </Space>
  )
}
