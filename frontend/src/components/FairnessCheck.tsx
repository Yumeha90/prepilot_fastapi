/**
 * P11 · Step 3 公平性检查（PRD 3.4.3 / §6.5）。
 *
 * 四条口径：
 * - **三态结论，阻断必须改写**：⛔ 阻断（BLOCK 级零容忍，不给「保留」的口子）
 *   / ⚠ 警告（可继续，允许保留但必须写原因）/ ✅ 通过。
 * - **每条命中都要能落到具体问题**：给出命中片段与所在字段（主问题 / 追问 / 观察点），
 *   只说「存在风险」而不定位，等于让面试官自己重读一遍问题链。
 * - **AI 给建议，人来拍板**：改写建议只在点「采纳改写」时写回**该条命中的那一句**，
 *   然后**只复检这一个节点**（BR-28）—— 采纳后整链重扫会把其他命中一起重新生成，
 *   面试官看到的「通过」其实是模型重抽了一次签。仍踩线就回到待处置并说明原因。
 * - **改的是哪一句要看得见**：卡片给出命中所在的**整句原文**（不是只给片段），
 *   并给出一个可编辑的改写框（默认填 AI 建议，可以自己改）—— 只给一句话建议
 *   而不给原句，面试官无从判断这条建议到底改了什么。
 * - **依据要能展开看**：命中的合规资料（制度 / 法条 / 公序良俗 / 判例）可展开原文，
 *   看不到依据，面试官完全可以不认这个结论。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { CheckCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import {
  acceptFinding,
  applyRewrite,
  scanFairness,
  type FairnessCheckItem,
  type FairnessFinding,
  type FairnessOut,
} from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Text, Paragraph } = Typography

const LEVEL_COLOR: Record<string, string> = {
  block: 'red',
  warn: 'orange',
  info: 'blue',
}

/** 字段 key → 人话（命中要能定位到那一句，而不是「某个节点有问题」） */
function fieldLabel(field: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!field || field === 'main_question') return t('workbench.fairness.field.main')
  if (field.startsWith('followup:')) {
    const [, level, key] = field.split(':')
    const which = key === 'anti_fake' ? t('workbench.chain.antiFake') : t('workbench.chain.vague')
    return t('workbench.fairness.field.followup', { n: level, which })
  }
  if (field.startsWith('observation:')) {
    return t('workbench.fairness.field.observation', { n: Number(field.split(':')[1] ?? 0) + 1 })
  }
  return field
}

export default function FairnessCheck({
  sessionId,
  fairness,
  canEdit,
  hasChain,
}: {
  sessionId: number
  fairness: FairnessOut | undefined
  canEdit: boolean
  hasChain: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [acceptId, setAcceptId] = useState('')
  const [reason, setReason] = useState('')
  /** 每条命中的改写草稿：默认就是 AI 建议，面试官可以自己再改（空 = 用建议原文） */
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const data: FairnessOut = fairness ?? {
    result: 'idle',
    scanned: false,
    scanned_at: '',
    model: '',
    nodes_scanned: 0,
    checks: [],
    findings: [],
    revision: 0,
    updated_at: null,
    rag_hits: {},
    notice: '',
    rag_degraded: false,
  }

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['workbench', sessionId] })
  }

  const scan = useMutation({
    mutationFn: () => scanFairness(sessionId),
    onSuccess: () => {
      invalidate()
      message.success(t('workbench.fairness.msg.scanned'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const apply = useMutation({
    mutationFn: ({ findingId, text }: { findingId: string; text: string }) =>
      applyRewrite(sessionId, findingId, text),
    onSuccess: (out, { findingId }) => {
      // 采纳完就把这条的草稿清掉：输入框回到「原句 / 新建议」的状态，
      // 否则上一次改的内容会一直挂在框里，看起来像还没提交
      setDrafts((d) => ({ ...d, [findingId]: '' }))
      invalidate()
      // 采纳改写只处置这一条：改写后仍踩线 / 引入了新风险要说出来，
      // 否则「点了一下，结论就绿了」没人知道到底发生了什么
      if (out.notice) message.warning(out.notice)
      else message.success(t('workbench.fairness.msg.applied'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const accept = useMutation({
    mutationFn: ({ findingId, reason }: { findingId: string; reason: string }) =>
      acceptFinding(sessionId, findingId, reason),
    onSuccess: () => {
      setAcceptId('')
      setReason('')
      invalidate()
      message.success(t('workbench.fairness.msg.accepted'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const pending = data.findings.filter((f) => f.disposition === 'pending')
  const blockCount = pending.filter((f) => f.level === 'block').length
  const warnCount = pending.filter((f) => f.level === 'warn').length

  const banner = () => {
    if (!data.scanned) {
      return (
        <Alert
          type="info"
          showIcon
          message={t('workbench.fairness.idle')}
          description={hasChain ? t('workbench.fairness.idleDesc') : undefined}
        />
      )
    }
    if (data.result === 'block') {
      return (
        <Alert
          type="error"
          showIcon
          message={t('workbench.fairness.block', { n: blockCount })}
          description={t('workbench.fairness.blockDesc')}
        />
      )
    }
    if (data.result === 'warn') {
      return (
        <Alert
          type="warning"
          showIcon
          message={t('workbench.fairness.warn', { n: warnCount })}
          description={t('workbench.fairness.warnDesc')}
        />
      )
    }
    return (
      <Alert
        type="success"
        showIcon
        message={t('workbench.fairness.pass')}
        description={t('workbench.fairness.passDesc', { n: data.nodes_scanned })}
      />
    )
  }

  const checkColumns: ColumnsType<FairnessCheckItem> = [
    {
      title: t('workbench.fairness.colCheck'),
      dataIndex: 'key',
      render: (v: string) => t(`workbench.fairness.category.${v}`),
    },
    {
      title: t('workbench.fairness.colResult'),
      key: 'result',
      width: 160,
      render: (_v, row) =>
        row.count === 0 ? (
          <Tag color="green">{t('workbench.fairness.notFound')}</Tag>
        ) : (
          <Tag color={LEVEL_COLOR[row.level] ?? 'default'}>
            {t(`workbench.fairness.level.${row.level}`)} {row.count}
          </Tag>
        ),
    },
  ]

  const renderFinding = (f: FairnessFinding) => {
    // 已处置（改写通过 / 保留并记录原因）的条目不再挂红 / 橙等级标签：
    // 等级标签是「待办信号灯」，处置完还留着红色，页面上看不出到底还剩几条没处理。
    // 等级文字保留成灰色，是为了仍能看出这条**原来**是阻断还是警告（留痕要能复盘）。
    const resolved = f.disposition !== 'pending'
    return (
      <Card
        key={f.id}
        size="small"
        type="inner"
        style={{ marginBottom: 8, opacity: resolved ? 0.85 : 1 }}
        title={
          <Space wrap>
            <Tag color={resolved ? 'default' : LEVEL_COLOR[f.level] ?? 'default'}>
              {t(`workbench.fairness.level.${f.level}`)}
            </Tag>
            <Text strong>{t(`workbench.fairness.category.${f.category}`)}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {f.capability} · {fieldLabel(f.field, t)}
            </Text>
            <Tag>{t(`workbench.fairness.source.${f.source}`)}</Tag>
          </Space>
        }
        extra={
          f.disposition === 'accepted' ? (
            <Tag color="blue">{t('workbench.fairness.accepted')}</Tag>
          ) : f.disposition === 'rewritten' ? (
            <Tag color="green" icon={<CheckCircleOutlined />}>
              {t('workbench.fairness.rewritten')}
            </Tag>
          ) : null
        }
      >
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('workbench.fairness.originalText')}
          </Text>
          <Paragraph style={{ marginBottom: 0 }}>
            <Text code>{f.original_text || f.snippet || '—'}</Text>
          </Paragraph>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('workbench.fairness.snippet')}
          </Text>
          <Paragraph style={{ marginBottom: 0 }}>
            <Text code>{f.snippet || '—'}</Text>
          </Paragraph>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('workbench.fairness.reason')}
          </Text>
          <Paragraph style={{ marginBottom: 0 }}>{f.reason || '—'}</Paragraph>
        </div>
        {f.suggestion && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.fairness.suggestion')}
            </Text>
            <Paragraph style={{ marginBottom: 0 }}>{f.suggestion}</Paragraph>
          </div>
        )}
        {f.rewritten_to && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.fairness.rewrittenTo')}
            </Text>
            <Paragraph style={{ marginBottom: 0 }}>{f.rewritten_to}</Paragraph>
          </div>
        )}
        {f.accepted_reason && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.fairness.acceptedReason')}
            </Text>
            <Paragraph style={{ marginBottom: 0 }}>{f.accepted_reason}</Paragraph>
          </div>
        )}
        {(f.refs?.length ?? 0) > 0 && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.fairness.refs')}
            </Text>
            <div>
              {f.refs.map((r) => (
                <Tag key={r} color="geekblue" style={{ marginTop: 4 }}>
                  {r}
                </Tag>
              ))}
            </div>
            <Collapse
              size="small"
              ghost
              items={[
                {
                  key: 'ref',
                  label: t('workbench.fairness.ragTitle'),
                  children: (
                    <Space direction="vertical" size={6}>
                      {(data.rag_hits?.[f.node_id] ?? []).map((h, i) => (
                        <div key={i}>
                          <Text strong style={{ fontSize: 12 }}>
                            《{h.title}》
                          </Text>
                          <div>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {h.snippet}
                            </Text>
                          </div>
                        </div>
                      ))}
                    </Space>
                  ),
                },
              ]}
            />
          </div>
        )}
        {canEdit && f.disposition === 'pending' && (
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {/* 改写框默认就是 AI 建议，可以直接改完再采纳 —— 建议常常只改了半句 */}
            <Input.TextArea
              rows={2}
              value={drafts[f.id] ?? f.suggestion}
              placeholder={t('workbench.fairness.rewritePlaceholder')}
              onChange={(e) => setDrafts((d) => ({ ...d, [f.id]: e.target.value }))}
            />
            <Space wrap>
              <Button
                size="small"
                type="primary"
                disabled={!(drafts[f.id] ?? f.suggestion).trim()}
                loading={apply.isPending && apply.variables?.findingId === f.id}
                onClick={() =>
                  apply.mutate({
                    findingId: f.id,
                    text: (drafts[f.id] ?? f.suggestion).trim(),
                  })
                }
              >
                {t('workbench.fairness.apply')}
              </Button>
              {f.level !== 'block' && (
                <Button size="small" onClick={() => setAcceptId(f.id)}>
                  {t('workbench.fairness.accept')}
                </Button>
              )}
              {!f.suggestion && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('workbench.fairness.noSuggestion')}
                </Text>
              )}
            </Space>
          </Space>
        )}
      </Space>
    </Card>
    )
  }

  return (
    <Card
      title={t('workbench.fairness.title')}
      extra={
        <Space>
          {data.scanned && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.revision')} {data.revision}
            </Text>
          )}
          {canEdit && (
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={scan.isPending || apply.isPending}
              disabled={!hasChain}
              onClick={() => scan.mutate()}
            >
              {data.scanned ? t('workbench.fairness.rescan') : t('workbench.fairness.scan')}
            </Button>
          )}
        </Space>
      }
    >
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('workbench.fairness.hint')}
      </Paragraph>

      {!hasChain && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('workbench.fairness.needChain')}
        />
      )}

      {data.rag_degraded && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('workbench.fairness.ragDegraded')}
        />
      )}

      <div style={{ marginBottom: 12 }}>{banner()}</div>

      {data.scanned && (
        <>
          <Table
            size="small"
            pagination={false}
            rowKey={(r) => r.key}
            dataSource={data.checks}
            columns={checkColumns}
            style={{ marginBottom: 12 }}
          />
          {data.findings.length === 0 ? (
            <Empty description={t('workbench.fairness.noFindings')} />
          ) : (
            data.findings.map(renderFinding)
          )}
        </>
      )}

      <Modal
        open={!!acceptId}
        title={t('workbench.fairness.acceptTitle')}
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        confirmLoading={accept.isPending}
        onCancel={() => {
          setAcceptId('')
          setReason('')
        }}
        onOk={() => {
          if (!reason.trim()) {
            message.error(t('workbench.fairness.reasonRequired'))
            return
          }
          accept.mutate({ findingId: acceptId, reason: reason.trim() })
        }}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('workbench.fairness.acceptHint')}
        </Paragraph>
        <Input.TextArea
          rows={3}
          value={reason}
          placeholder={t('workbench.fairness.reasonPlaceholder')}
          onChange={(e) => setReason(e.target.value)}
        />
      </Modal>
    </Card>
  )
}
