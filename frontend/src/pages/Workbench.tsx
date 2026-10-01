/**
 * P09 · Step 1 策略与矩阵（PRD 3.4.1 / §5.9）。
 *
 * 三条口径：
 * - **「本轮重点」的唯一入口就在这个页面**（JD 编辑页没有那个勾选框），
 *   勾中的能力项会在 Step2 的问题链里优先覆盖。
 * - **只有被指派的面试官能改**：HR / HR 主管进来只读，页面把编辑控件换成纯文本，
 *   而不是「能点但一点就报 403」—— 后者是纯粹的体验事故。
 * - **矩阵整块保存**：改完一起提交，中途失败不会留下半截状态。
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import {
  fetchWorkbench,
  generateMatrix,
  saveMatrix,
  updateDuration,
  type EvidenceStatus,
  type MatrixRow,
  type RowSource,
} from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Text, Paragraph } = Typography

const STATUS_OPTIONS: EvidenceStatus[] = ['sufficient', 'verify', 'missing']
const STATUS_COLOR: Record<EvidenceStatus, string> = {
  sufficient: 'green',
  verify: 'orange',
  missing: 'red',
}
const SOURCE_COLOR: Record<RowSource, string> = {
  jd: 'blue',
  resume: 'purple',
  manual: 'default',
}

export default function Workbench() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { sessionId } = useParams()
  const id = Number(sessionId)

  const [rows, setRows] = useState<MatrixRow[]>([])
  const [dirty, setDirty] = useState(false)
  const [duration, setDuration] = useState<number>(45)

  const { data, isLoading } = useQuery({
    queryKey: ['workbench', id],
    queryFn: () => fetchWorkbench(id),
    enabled: Number.isFinite(id) && id > 0,
  })

  // 服务端数据到达（含「重新生成」之后）时重置本地草稿
  useEffect(() => {
    if (data) {
      setRows(data.matrix.rows)
      setDirty(false)
      setDuration(data.duration_minutes)
    }
  }, [data])

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const generate = useMutation({
    mutationFn: () => generateMatrix(id),
    onSuccess: (res) => {
      setRows(res.matrix.rows)
      setDirty(false)
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.msg.generated'))
      if (res.stale) message.warning(t('workbench.msg.stale'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const save = useMutation({
    mutationFn: () => saveMatrix(id, rows, data?.matrix.revision),
    onSuccess: (res) => {
      setRows(res.matrix.rows)
      setDirty(false)
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.msg.saved'))
      if (res.stale) message.warning(t('workbench.msg.stale'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const saveDuration = useMutation({
    mutationFn: (minutes: number) => updateDuration(id, minutes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.msg.durationSaved'))
    },
    onError: (e: unknown) => message.error(errMsg(e)),
  })

  const canEdit = data?.can_edit ?? false
  const matrix = data?.matrix

  const patch = (index: number, next: Partial<MatrixRow>) => {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...next } : r)))
    setDirty(true)
  }

  const move = (index: number, delta: number) => {
    setRows((prev) => {
      const target = index + delta
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      next[index] = prev[target]
      next[target] = prev[index]
      return next
    })
    setDirty(true)
  }

  const addRow = () => {
    setRows((prev) => [
      ...prev,
      {
        id: `new-${Date.now()}`,
        source: 'manual',
        capability: '',
        weight: null,
        evidence: '',
        status: 'verify',
        focus: '',
        is_key: false,
      },
    ])
    setDirty(true)
  }

  const handleSave = () => {
    if (rows.some((r) => !r.capability.trim())) {
      message.error(t('workbench.capabilityRequired'))
      return
    }
    save.mutate()
  }

  const stepItems = useMemo(
    () =>
      (data?.steps ?? []).map((s) => ({
        title: t(`workbench.step.${s.key}`),
        // 未实现的步骤统一 wait + 说明，避免看起来"能点"
        description: s.state === 'disabled' ? t('workbench.nextStepDisabled') : undefined,
        status:
          s.state === 'done'
            ? ('finish' as const)
            : s.state === 'current'
              ? ('process' as const)
              : ('wait' as const),
      })),
    [data?.steps, t],
  )
  const currentStep = (data?.steps ?? []).findIndex((s) => s.state === 'current')

  const columns: ColumnsType<MatrixRow> = [
    {
      title: t('workbench.col.capability'),
      dataIndex: 'capability',
      width: 220,
      render: (v: string, row, index) => (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {canEdit ? (
            <Input
              value={v}
              placeholder={t('workbench.capabilityPlaceholder')}
              onChange={(e) => patch(index, { capability: e.target.value })}
            />
          ) : (
            <Text strong>{v || '—'}</Text>
          )}
          <Space size={4} wrap>
            <Tag color={SOURCE_COLOR[row.source]}>{t(`workbench.source.${row.source}`)}</Tag>
            {row.weight !== null && (
              <Tag>
                {t('workbench.weight')} {row.weight}
              </Tag>
            )}
          </Space>
        </Space>
      ),
    },
    {
      title: t('workbench.col.evidence'),
      dataIndex: 'evidence',
      width: 260,
      render: (v: string, _row, index) =>
        canEdit ? (
          <Input.TextArea
            value={v}
            autoSize={{ minRows: 2 }}
            placeholder={t('workbench.evidencePlaceholder')}
            onChange={(e) => patch(index, { evidence: e.target.value })}
          />
        ) : (
          <Text type="secondary">{v || '—'}</Text>
        ),
    },
    {
      title: t('workbench.col.status'),
      dataIndex: 'status',
      width: 130,
      render: (v: EvidenceStatus, _row, index) =>
        canEdit ? (
          <Select
            value={v}
            style={{ width: '100%' }}
            options={STATUS_OPTIONS.map((s) => ({
              value: s,
              label: t(`workbench.status.${s}`),
            }))}
            onChange={(next) => patch(index, { status: next })}
          />
        ) : (
          <Tag color={STATUS_COLOR[v]}>{t(`workbench.status.${v}`)}</Tag>
        ),
    },
    {
      title: t('workbench.col.focus'),
      dataIndex: 'focus',
      width: 280,
      render: (v: string, _row, index) =>
        canEdit ? (
          <Input.TextArea
            value={v}
            autoSize={{ minRows: 2 }}
            placeholder={t('workbench.focusPlaceholder')}
            onChange={(e) => patch(index, { focus: e.target.value })}
          />
        ) : (
          <Text>{v || '—'}</Text>
        ),
    },
    {
      title: t('workbench.col.key'),
      dataIndex: 'is_key',
      width: 90,
      render: (v: boolean, _row, index) =>
        canEdit ? (
          <Checkbox
            checked={v}
            onChange={(e) => patch(index, { is_key: e.target.checked })}
          />
        ) : (
          <Tag color={v ? 'blue' : 'default'}>{v ? t('common.yes') : t('common.no')}</Tag>
        ),
    },
  ]

  if (canEdit) {
    columns.push({
      title: t('workbench.col.ops'),
      key: 'ops',
      width: 110,
      render: (_v, _row, index) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            icon={<ArrowUpOutlined />}
            disabled={index === 0}
            onClick={() => move(index, -1)}
          />
          <Button
            type="link"
            size="small"
            icon={<ArrowDownOutlined />}
            disabled={index === rows.length - 1}
            onClick={() => move(index, 1)}
          />
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => {
              setRows((prev) => prev.filter((_, i) => i !== index))
              setDirty(true)
            }}
          />
        </Space>
      ),
    })
  }

  const blocked = data?.blocked_reason ?? ''

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        title={t('workbench.title')}
        extra={
          <Space>
            <Button onClick={() => navigate('/workbench')}>{t('workbench.back')}</Button>
            {canEdit && (
              <>
                <Button
                  icon={<ThunderboltOutlined />}
                  loading={generate.isPending}
                  disabled={!!blocked}
                  onClick={() => generate.mutate()}
                >
                  {matrix?.generated ? t('workbench.regenerate') : t('workbench.generate')}
                </Button>
                <Button type="primary" disabled={!dirty} loading={save.isPending} onClick={handleSave}>
                  {t('workbench.save')}
                </Button>
              </>
            )}
          </Space>
        }
      >
        <Descriptions size="small" column={4} style={{ marginBottom: 8 }}>
          <Descriptions.Item label={t('workbench.info.candidate')}>
            {data?.candidate_name || '—'}
          </Descriptions.Item>
          <Descriptions.Item label={t('workbench.info.position')}>
            {data?.position_name || '—'}
          </Descriptions.Item>
          <Descriptions.Item label={t('workbench.info.round')}>
            {data?.round_name || t(`workbench.round.${data?.round_type ?? 'r1'}`)}
          </Descriptions.Item>
          <Descriptions.Item label={t('workbench.info.duration')}>
            {canEdit ? (
              <Space size={4}>
                <InputNumber
                  min={10}
                  max={240}
                  value={duration}
                  style={{ width: 90 }}
                  onChange={(v) => setDuration(Number(v ?? 45))}
                  onBlur={() => {
                    if (duration !== data?.duration_minutes) saveDuration.mutate(duration)
                  }}
                />
                <Text type="secondary">{t('workbench.minutes')}</Text>
              </Space>
            ) : (
              `${data?.duration_minutes ?? 45} ${t('workbench.minutes')}`
            )}
          </Descriptions.Item>
        </Descriptions>

        <Steps size="small" items={stepItems} current={currentStep < 0 ? 0 : currentStep} />
      </Card>

      {data?.read_only_reason === 'submitted' && (
        <Alert type="info" showIcon message={t('workbench.submittedNotice')} />
      )}
      {data?.read_only_reason === 'read_only' && (
        <Alert type="info" showIcon message={t('workbench.readOnlyNotice')} />
      )}
      {blocked && (
        <Alert type="warning" showIcon message={t(`workbench.blocked.${blocked}`)} />
      )}

      <Card
        title={t('workbench.matrixTitle')}
        extra={
          matrix?.generated ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.revision')} {matrix.revision}
            </Text>
          ) : null
        }
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('workbench.hint')}
        </Paragraph>
        {rows.length === 0 ? (
          <Empty
            description={canEdit ? t('workbench.empty') : t('workbench.emptyReadonly')}
          />
        ) : (
          <Table
            rowKey={(_r, i) => `${_r.id || i}-${i}`}
            size="small"
            loading={isLoading}
            pagination={false}
            dataSource={rows}
            columns={columns}
            scroll={{ x: 1100 }}
          />
        )}
        {canEdit && (
          <Button
            type="dashed"
            block
            icon={<PlusOutlined />}
            style={{ marginTop: 12 }}
            onClick={addRow}
          >
            {t('workbench.addRow')}
          </Button>
        )}
      </Card>

      <Card>
        <Space>
          <Tooltip title={t('workbench.nextStepDisabled')}>
            <Button type="primary" disabled>
              {t('workbench.nextStep')}
            </Button>
          </Tooltip>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('workbench.nextStepHint')}
          </Text>
        </Space>
      </Card>
    </Space>
  )
}
