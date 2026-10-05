/**
 * P12 · Step 4 评分与面评（PRD §3.4.4 / §6.6）—— 独立成页后的版本。
 *
 * 四条口径：
 *
 * **① 自动保存（BR-13）**
 * 面试结束后面试官还要赶下一场，让他记得点保存是不现实的。所以每 30 秒自动落库一次，
 * 同时把草稿写一份到本机 localStorage —— 自动保存还没触发就关页面的窗口里，
 * 下次进来能问一句「要不要恢复」。保存失败会明确报错，不假装成功。
 *
 * **② 完整性只提示不拦截（BR-07）**
 * 「每项至少 1 条证据或 1 条快记」是**提交前**的要求（Step5 才拦）。
 * 面试官是先记重点、后补证据的，写一半就被拦住等于逼他先编一条证据出来。
 *
 * **③ AI 润色不覆盖原文（BR-08）**
 * 润色稿与原文并排展示，点「采纳」才覆写，覆写前的原文留进 summary_original。
 *
 * **④ 润色稿也要过合规扫描**
 * 面试题拦住了、面评里却写出「年纪偏大」，等于把风险换个地方落地。
 * 命中项常驻警示，采纳由面试官确认（AI 也会犯错，但把改写权完全收回不合理）。
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Modal,
  Rate,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import {
  adoptPolished,
  polishEvaluation,
  saveEvaluation,
  type EvaluationItem,
} from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useWorkbench } from './context'

const { Text, Paragraph } = Typography

/** BR-13：30 秒自动保存。再短会频繁打断，再长就失去了「不用记得保存」的意义 */
const AUTO_SAVE_MS = 30_000

/**
 * 停手 2.5 秒就落一次库（30 秒定时器只作兜底）。
 *
 * 进度条与「已完成」徽章早就改成前端即时判定，但**数据要等下一次请求回来才真的存下** ——
 * 只靠 30 秒定时器，面试官改完就切走 / 关页面的窗口里有半分钟是"看着存了其实没存"。
 * 停手即存把这段窗口压到 2.5 秒，且保存过程有明确状态提示（保存中 / 未保存 / 已保存）。
 */
const IDLE_SAVE_MS = 2_500

/** 与后端 MAX_EVIDENCES_PER_ITEM 一致 */
const MAX_EVIDENCES = 6

type Recommendation = 'proceed' | 'hold' | 'reject'

/**
 * 「这项填完了没有」—— 前端本地判定，不等服务端回包。
 *
 * 与后端 `evaluation._item_out` 完全同口径（有分 + 有证据或快记）。
 * 之前直接用服务端返回的 complete/missing：打分、写快记都是纯本地 state，
 * 服务端要等下一次自动保存才知道 —— 于是面试官打完分，标签还挂着「未评分」，
 * 最长要等 30 秒。判定规则就那两行，没理由非得绕一圈网络。
 */
function missingOf(it: EvaluationItem): string[] {
  const missing: string[] = []
  if (it.score === null) missing.push('no_score')
  if (!it.evidences.some((e) => e.text.trim()) && !(it.note ?? '').trim()) {
    missing.push('no_evidence')
  }
  return missing
}

export default function StepEvaluation() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { id, data, canEdit, blocked, leaveGuard, markStepDone } = useWorkbench()

  const [items, setItems] = useState<EvaluationItem[]>([])
  const [summary, setSummary] = useState('')
  const [dirty, setDirty] = useState(false)
  const [savedAt, setSavedAt] = useState<string>('')

  // 编辑中 / 保存中：服务端数据回来时不覆盖本地草稿，避免打字被打断
  const dirtyRef = useRef(false)
  const savingRef = useRef(false)
  const draftKey = `prepilot.eval.draft.${id}`
  const restoredRef = useRef(false)

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const save = useMutation({
    mutationFn: () => saveEvaluation(id, items, summary, data?.evaluation.revision),
    onMutate: () => {
      savingRef.current = true
    },
    onSuccess: (res) => {
      setItems(res.evaluation.items)
      setSummary(res.evaluation.summary)
      setDirty(false)
      dirtyRef.current = false
      setSavedAt(new Date().toLocaleTimeString())
      // 已落库，本机缓冲就没用了 —— 留着反而会在下次进来误报「有未保存草稿」
      try {
        localStorage.removeItem(draftKey)
      } catch {
        /* 隐私模式下 localStorage 不可用，忽略即可 */
      }
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      if (res.stale) message.warning(t('workbench.evaluation.msg.stale'))
    },
    onError: (e) => message.error(errMsg(e)),
    onSettled: () => {
      savingRef.current = false
    },
  })

  const polish = useMutation({
    mutationFn: async () => {
      // 润色读的是**服务端已存**的面评。本地还有没存下的草稿就先存一次，
      // 否则润出来的是上一版的内容，面试官看到会对不上
      if (canEdit && dirtyRef.current) await save.mutateAsync()
      return polishEvaluation(id)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.evaluation.msg.polished'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const adopt = useMutation({
    mutationFn: () => adoptPolished(id),
    onSuccess: (res) => {
      // 采纳 =「用润色稿覆写正文」，服务端返回值就是新的权威值，**必须直接写回本地**。
      // 只靠 data 变更来回填会被上面那条「本地有草稿就不覆盖」的规则挡掉，
      // 而且残留的 dirty 会让 30 秒后的自动保存把旧草稿又写回去、把采纳结果冲掉。
      setItems(res.items)
      setSummary(res.summary)
      setDirty(false)
      dirtyRef.current = false
      try {
        localStorage.removeItem(draftKey)
      } catch {
        /* 隐私模式下不可用，忽略 */
      }
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.evaluation.msg.adopted'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  // ---- 服务端快照同步 ----

  useEffect(() => {
    if (!data) return
    // 正在编辑时不覆盖：本地草稿优先，否则打字会被服务端回包打断
    if (dirtyRef.current) return
    setItems(data.evaluation.items)
    setSummary(data.evaluation.summary)
    setDirty(false)
  }, [data])

  // 本机缓冲：自动保存还没触发就关了页面时，下次进来问一句要不要恢复
  useEffect(() => {
    if (!data || restoredRef.current) return
    restoredRef.current = true
    let raw: string | null = null
    try {
      raw = localStorage.getItem(draftKey)
    } catch {
      return
    }
    if (!raw) return
    try {
      const draft = JSON.parse(raw) as { items?: EvaluationItem[]; summary?: string; ts?: number }
      if (!draft?.items?.length && !draft?.summary) return
      const same =
        JSON.stringify(draft.items) === JSON.stringify(data.evaluation.items) &&
        (draft.summary ?? '') === data.evaluation.summary
      if (same) {
        localStorage.removeItem(draftKey)
        return
      }
      const when = draft.ts ? new Date(draft.ts).toLocaleString() : ''
      Modal.confirm({
        title: t('workbench.evaluation.restoreTitle'),
        content: t('workbench.evaluation.restoreDesc', { time: when }),
        okText: t('workbench.evaluation.restoreOk'),
        cancelText: t('workbench.evaluation.restoreNo'),
        onOk: () => {
          setItems(draft.items ?? [])
          setSummary(draft.summary ?? '')
          setDirty(true)
          dirtyRef.current = true
        },
        onCancel: () => {
          try {
            localStorage.removeItem(draftKey)
          } catch {
            /* ignore */
          }
        },
      })
    } catch {
      localStorage.removeItem(draftKey)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // 有改动就写一份到本机（自动保存的兜底）
  useEffect(() => {
    if (!dirty) return
    try {
      localStorage.setItem(
        draftKey,
        JSON.stringify({ items, summary, ts: Date.now() }),
      )
    } catch {
      /* ignore */
    }
  }, [items, summary, dirty, draftKey])

  // ---- 自动保存（BR-13）----

  // 用 ref 持有保存动作：mutation 对象每次渲染都是新的，直接进依赖会让定时器
  // 被打字时的重渲染不断重建 —— 结果是「一直在打字就永远不自动保存」
  const doSaveRef = useRef<() => void>(() => {})
  doSaveRef.current = () => {
    if (!canEdit || !dirtyRef.current || savingRef.current) return
    save.mutate()
  }

  useEffect(() => {
    if (!canEdit) return
    const timer = window.setInterval(() => doSaveRef.current(), AUTO_SAVE_MS)
    return () => window.clearInterval(timer)
  }, [canEdit])

  // 停手即存：等 30 秒的定时器太慢，面试官改完就切走时数据还没落库。
  // 依赖 items/summary —— 每敲一下就重置计时器，真正停下来才会发请求。
  // save.isPending 进依赖是为了"上一次存失败/被占用"时能重新排一次，而不是就此搁置。
  useEffect(() => {
    if (!canEdit || !dirty) return
    const timer = window.setTimeout(() => doSaveRef.current(), IDLE_SAVE_MS)
    return () => window.clearTimeout(timer)
  }, [items, summary, dirty, canEdit, save.isPending])

  // 离开时自动保存：30 秒的定时器覆盖不了「刚改完就点下一步」的那几秒，
  // 所以点导航时先存一次再跳转（Layout 的 goTo 调它）。存不下才弹「会丢失」确认。
  const saveNowRef = useRef<() => Promise<void>>(async () => {})
  saveNowRef.current = async () => {
    if (!canEdit || !dirtyRef.current || savingRef.current) return
    await save.mutateAsync()
  }

  // 兜底：经侧边菜单或浏览器后退离开时不会走 goTo，卸载前尽力再存一次
  const flushRef = useRef<() => void>(() => {})
  flushRef.current = () => {
    if (canEdit && dirtyRef.current) void saveNowRef.current().catch(() => undefined)
  }
  useEffect(() => () => flushRef.current(), [])

  useEffect(() => {
    leaveGuard.current = async () => {
      if (!canEdit || !dirtyRef.current) return true
      try {
        await saveNowRef.current()
        // 自动保存是静默的（30 秒那次不弹提示），但「离开时替你存了」必须让他知道
        if (!dirtyRef.current) message.success(t('workbench.autoSaved'))
        return !dirtyRef.current
      } catch {
        return false
      }
    }
    return () => {
      leaveGuard.current = null
    }
  }, [canEdit, leaveGuard, t])

  // ---- 编辑 ----

  const markDirty = () => {
    setDirty(true)
    dirtyRef.current = true
  }

  const patch = (index: number, next: Partial<EvaluationItem>) => {
    setItems((prev) => prev.map((it, i) => (i === index ? { ...it, ...next } : it)))
    markDirty()
  }

  // 进度条**立刻**打勾，不等保存回包（2026-10-04）。
  // 请求照发（面评必须落库，不能只在前端），但「这一项填完了」是本地就能判断的事：
  // 等一次网络往返才亮，面试官填完最后一项会以为没生效。
  const allComplete =
    items.length > 0 && items.every((it) => missingOf(it).length === 0)
  useEffect(() => {
    markStepDone('step4', allComplete)
    return () => markStepDone('step4', false)
  }, [allComplete, markStepDone])

  const evaluation = data?.evaluation
  const hasMatrix = (data?.matrix.rows.length ?? 0) > 0

  if (!hasMatrix) {
    return (
      <Card title={t('workbench.evaluation.title')}>
        <Empty description={t('workbench.evaluation.needMatrix')} />
      </Card>
    )
  }

  const incompleteNames = items.filter((it) => missingOf(it).length > 0).map((it) => it.capability)
  const hasContent = items.some(
    (it) =>
      it.score !== null ||
      it.note.trim() !== '' ||
      it.evidences.some((e) => e.text.trim() !== ''),
  )

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        title={t('workbench.evaluation.scoreTitle')}
        extra={
          <Space size={8}>
            {/* 保存态必须看得见：徽章是即时判定的，落库还在路上。
                只说「已自动保存」不说「正在保存」，面试官会以为已经存好了 */}
            {save.isPending ? (
              <Space size={4}>
                <Spin size="small" />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('workbench.saving')}
                </Text>
              </Space>
            ) : dirty ? (
              <Text type="warning" style={{ fontSize: 12 }}>
                {t('workbench.evaluation.unsaved')}
              </Text>
            ) : savedAt ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('workbench.evaluation.autosaved', { time: savedAt })}
              </Text>
            ) : null}
            {canEdit && (
              <Button
                type="primary"
                disabled={!dirty}
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                {t('common.save')}
              </Button>
            )}
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('workbench.evaluation.hint')}
        </Paragraph>

        {incompleteNames.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={t('workbench.evaluation.incomplete', {
              n: incompleteNames.length,
              list: incompleteNames.join('、'),
            })}
          />
        )}

        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {items.map((it, index) => (
            <Card
              key={it.row_id || index}
              size="small"
              title={
                <Space size={6} wrap>
                  <Text strong>{it.capability || '—'}</Text>
                  {missingOf(it).length === 0 ? (
                    <Tag color="green">{t('workbench.evaluation.done')}</Tag>
                  ) : (
                    missingOf(it).map((m) => (
                      <Tag key={m} color="orange">
                        {t(`workbench.evaluation.missing.${m}`)}
                      </Tag>
                    ))
                  )}
                </Space>
              }
            >
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Space size={8} wrap>
                  <Text>{t('workbench.evaluation.score')}</Text>
                  {canEdit ? (
                    <Tooltip title={t('workbench.evaluation.scoreClear')}>
                      <Rate
                        count={5}
                        allowClear
                        value={it.score ?? 0}
                        onChange={(v) => patch(index, { score: v > 0 ? v : null })}
                      />
                    </Tooltip>
                  ) : (
                    <Rate count={5} disabled value={it.score ?? 0} />
                  )}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {it.score ?? t('workbench.evaluation.scoreUnset')}
                  </Text>
                </Space>

                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('workbench.evaluation.evidence')}
                </Text>
                {it.evidences.map((ev, ei) => (
                  <Space key={ev.id || ei} size={6} style={{ width: '100%' }} align="start">
                    {canEdit ? (
                      <>
                        <Input.TextArea
                          value={ev.text}
                          autoSize={{ minRows: 1, maxRows: 4 }}
                          style={{ flex: 1 }}
                          placeholder={t('workbench.evaluation.evidencePlaceholder')}
                          onChange={(e) =>
                            patch(index, {
                              evidences: it.evidences.map((x, xi) =>
                                xi === ei ? { ...x, text: e.target.value } : x,
                              ),
                            })
                          }
                        />
                        <Checkbox
                          checked={ev.quote}
                          onChange={(e) =>
                            patch(index, {
                              evidences: it.evidences.map((x, xi) =>
                                xi === ei ? { ...x, quote: e.target.checked } : x,
                              ),
                            })
                          }
                        >
                          {t('workbench.evaluation.quote')}
                        </Checkbox>
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() =>
                            patch(index, {
                              evidences: it.evidences.filter((_, xi) => xi !== ei),
                            })
                          }
                        />
                      </>
                    ) : (
                      <Space size={4} wrap>
                        <Text>{ev.text || '—'}</Text>
                        {ev.quote && <Tag color="blue">{t('workbench.evaluation.quote')}</Tag>}
                      </Space>
                    )}
                  </Space>
                ))}
                {canEdit && it.evidences.length < MAX_EVIDENCES && (
                  <Button
                    type="dashed"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() =>
                      patch(index, {
                        evidences: [
                          ...it.evidences,
                          { id: `e${Date.now()}`, text: '', quote: false },
                        ],
                      })
                    }
                  >
                    {t('workbench.evaluation.addEvidence')}
                  </Button>
                )}

                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('workbench.evaluation.note')}
                </Text>
                {canEdit ? (
                  <Input.TextArea
                    value={it.note}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    maxLength={1000}
                    placeholder={t('workbench.evaluation.notePlaceholder')}
                    onChange={(e) => patch(index, { note: e.target.value })}
                  />
                ) : (
                  <Text>{it.note || '—'}</Text>
                )}
              </Space>
            </Card>
          ))}
        </Space>
      </Card>

      <Card
        title={t('workbench.evaluation.summaryTitle')}
        extra={
          canEdit && (
            <Button
              icon={<ThunderboltOutlined />}
              loading={polish.isPending}
              disabled={!hasContent || !!blocked}
              onClick={() => polish.mutate()}
            >
              {evaluation?.polished
                ? t('workbench.evaluation.repolish')
                : t('workbench.evaluation.polish')}
            </Button>
          )
        }
      >
        {canEdit ? (
          <Input.TextArea
            value={summary}
            autoSize={{ minRows: 4, maxRows: 12 }}
            maxLength={4000}
            showCount
            placeholder={t('workbench.evaluation.summaryPlaceholder')}
            onChange={(e) => {
              setSummary(e.target.value)
              markDirty()
            }}
          />
        ) : (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
            {summary || <Text type="secondary">—</Text>}
          </Paragraph>
        )}

        {evaluation?.summary_original && (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message={t('workbench.evaluation.originalTitle')}
            description={
              <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                {evaluation.summary_original}
              </Paragraph>
            }
          />
        )}

        {evaluation?.polished && (
          <Card
            size="small"
            style={{ marginTop: 12 }}
            title={t('workbench.evaluation.polishedTitle')}
            extra={
              <Space>
                {evaluation.polished_at && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('workbench.evaluation.polishedAt', {
                      time: new Date(evaluation.polished_at).toLocaleString(),
                    })}
                  </Text>
                )}
                {evaluation.recommendation && (
                  // AI 润色时顺带给出的推进倾向。必须带主语：光秃秃一个「待定」
                  // 挂在「生成于 xxx」旁边，看不出是谁的待定、和面试官自己选的结论无关
                  <Tooltip title={t('workbench.evaluation.recommendTip')}>
                    <Tag
                      color={
                        evaluation.recommendation === 'proceed'
                          ? 'green'
                          : evaluation.recommendation === 'reject'
                            ? 'red'
                            : 'orange'
                      }
                    >
                      {t('workbench.evaluation.recommendLabel', {
                        v: t(
                          `workbench.evaluation.recommend.${evaluation.recommendation as Recommendation}`,
                        ),
                      })}
                    </Tag>
                  </Tooltip>
                )}
              </Space>
            }
          >
            {(evaluation.polished_flags?.length ?? 0) > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message={t('workbench.evaluation.flagsTitle', {
                  n: evaluation.polished_flags.length,
                })}
                description={
                  <Space direction="vertical" size={4}>
                    {evaluation.polished_flags.map((f, i) => (
                      <Text key={i} style={{ fontSize: 12 }}>
                        · {t(`workbench.fairness.category.${f.category}`, {
                          defaultValue: f.category,
                        })}
                        ：{f.snippet}（{f.reason}）
                      </Text>
                    ))}
                  </Space>
                }
              />
            )}

            <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
              {evaluation.polished}
            </Paragraph>

            {canEdit &&
              (evaluation.polished_adopted ? (
                <Tag color="green">{t('workbench.evaluation.adopted')}</Tag>
              ) : (
                <Button
                  type="primary"
                  loading={adopt.isPending}
                  onClick={() =>
                    Modal.confirm({
                      title: t('workbench.evaluation.adoptConfirmTitle'),
                      content: t('workbench.evaluation.adoptConfirmDesc'),
                      okText: t('workbench.evaluation.adopt'),
                      cancelText: t('common.cancel'),
                      onOk: () => adopt.mutate(),
                    })
                  }
                >
                  {t('workbench.evaluation.adopt')}
                </Button>
              ))}
          </Card>
        )}

        {canEdit && (
          <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12 }}>
            {t('workbench.evaluation.autoSaveHint')}
          </Paragraph>
        )}
      </Card>
    </Space>
  )
}
