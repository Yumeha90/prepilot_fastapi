/**
 * P09 · Step 1 策略与矩阵（PRD 3.4.1 / §5.9）—— 独立成页后的版本。
 *
 * 三条口径：
 * - **「本轮重点」的唯一入口就在这个页面**（JD 编辑页没有那个勾选框），
 *   勾中的能力项会在 Step2 的问题链里优先覆盖。
 * - **只有被指派的面试官能改**：HR / HR 主管进来只读，页面把编辑控件换成纯文本，
 *   而不是「能点但一点就报 403」—— 后者是纯粹的体验事故。
 * - **矩阵整块保存**：改完一起提交，中途失败不会留下半截状态。
 *   点「上一步 / 下一步 / 进度条 / 返回列表」时，未保存的编辑会自动存掉再跳转，
 *   不需要手动点保存；只有存不下（比如能力项名空着）才拦一道确认。
 * - **没有矩阵就把「下一步：问题链」置灰**（2026-10-04，与 Step2 同口径）：
 *   问题链是矩阵的展开，没有输入就没有输出 —— 与其让人跳过去看到一句
 *   「请先生成矩阵」，不如在这里就说明缺什么。
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
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
  generateMatrix,
  saveMatrix,
  updateDuration,
  type EvidenceStatus,
  type MatrixRow,
  type RowSource,
} from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useWorkbench } from './context'

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

export default function StepMatrix() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { id, data, isLoading, canEdit, blocked, leaveGuard, setNextBlocked } = useWorkbench()

  const [rows, setRows] = useState<MatrixRow[]>([])
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  // 面试时长属于 Step1 的决策（它决定问题链的时间预算），跟着矩阵页一起改
  const [duration, setDuration] = useState<number>(45)

  // 服务端数据到达（含「重新生成」之后）时重置本地草稿
  useEffect(() => {
    if (data) {
      setRows(data.matrix.rows)
      setDirty(false)
      dirtyRef.current = false
      setDuration(data.duration_minutes)
    }
  }, [data])

  // 注册离开钩子：点「上一步 / 下一步 / 进度条 / 返回列表」时先把未保存的编辑存掉，
  // 存不下（比如能力项名空着）才交回 Layout 弹确认。面试官不需要记得点保存。
  const saveNowRef = useRef<() => Promise<void>>(async () => {})
  useEffect(() => {
    leaveGuard.current = async () => {
      if (!canEdit || !dirtyRef.current) return true
      try {
        await saveNowRef.current()
        return !dirtyRef.current
      } catch {
        // 存不下（校验不过 / 请求出错）才交回 Layout 弹「离开会丢失」
        return false
      }
    }
    return () => {
      leaveGuard.current = null
    }
  }, [canEdit, leaveGuard])

  // 兜底：经侧边菜单或浏览器后退离开时不会走 goTo，卸载前尽力再存一次
  const flushRef = useRef<() => void>(() => {})
  useEffect(() => () => flushRef.current(), [])

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
      dirtyRef.current = false
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
      dirtyRef.current = false
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

  // 一行都没有就不许跳 Step2：跳过去也只能看到「请先生成矩阵」。
  // 看的是本地草稿而不是服务端快照 —— 手动加了一行还没保存，也该放行
  // （点下一步会自动先存，见 leaveGuard）。
  useEffect(() => {
    setNextBlocked(rows.length === 0, t('workbench.needMatrixNext'))
    return () => setNextBlocked(false)
  }, [rows.length, setNextBlocked, t])

  const matrix = data?.matrix

  const markDirty = () => {
    setDirty(true)
    dirtyRef.current = true
  }

  // 离开时自动保存用的动作。用 ref 持有：mutation 对象每次渲染都是新的，
  // 直接进 useEffect 依赖会让钩子反复重建
  saveNowRef.current = async () => {
    if (rows.some((r) => !r.capability.trim())) {
      message.error(t('workbench.capabilityRequired'))
      throw new Error('invalid')
    }
    await save.mutateAsync()
  }
  flushRef.current = () => {
    if (canEdit && dirtyRef.current) void saveNowRef.current().catch(() => undefined)
  }

  const patch = (index: number, next: Partial<MatrixRow>) => {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...next } : r)))
    markDirty()
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
    markDirty()
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
    markDirty()
  }

  const handleSave = () => {
    if (rows.some((r) => !r.capability.trim())) {
      message.error(t('workbench.capabilityRequired'))
      return
    }
    save.mutate()
  }

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
          <Checkbox checked={v} onChange={(e) => patch(index, { is_key: e.target.checked })} />
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
              markDirty()
            }}
          />
        </Space>
      ),
    })
  }

  return (
    <Card
      title={t('workbench.matrixTitle')}
      extra={
        <Space>
          {matrix?.generated && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.revision')} {matrix.revision}
            </Text>
          )}
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
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {/* HR 面的行是 HR 考察维度，不是岗位技术能力项 —— 沿用技术面的提示
            会让人以为「生成坏了，怎么没有岗位能力」 */}
        {t(data?.round_type === 'hr' ? 'workbench.hintHr' : 'workbench.hint')}
      </Paragraph>

      <Space size={8} style={{ marginBottom: 12 }} wrap>
        <Text>{t('workbench.info.duration')}</Text>
        {canEdit ? (
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
        ) : (
          <Text strong>{data?.duration_minutes ?? 45}</Text>
        )}
        <Text type="secondary">{t('workbench.minutes')}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t('workbench.durationHint')}
        </Text>
      </Space>

      {rows.length === 0 ? (
        <Empty description={canEdit ? t('workbench.empty') : t('workbench.emptyReadonly')} />
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
  )
}
