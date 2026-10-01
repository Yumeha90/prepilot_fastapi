/**
 * P10 · Step 2 问题链（PRD 3.4.2 / §5.10）。
 *
 * 四条口径：
 * - **异步生成 + 轮询**：带 RAG 检索后云端 15~30s，同步等待既撞 nginx 超时
 *   又让页面干等（BR-12）。生成中给骨架屏，失败把后端写的原因直接显示出来。
 * - **「换一换」只换一个节点**：整链重新生成会把面试官已经手改过的其他题一起冲掉，
 *   那不是「换一换」，是返工。
 * - **引用来源要展示出来**：每个节点挂着 RAG 检索到的内部资料，能展开看原文片段 ——
 *   看不到引用，面试官无法判断这题是「本公司真正在意的」还是通用八股。
 * - **未实现的下一步置灰**：Step3 标 disabled + 说明，不会出现点了报错的半截状态。
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import {
  generateChain,
  regenerateNode,
  saveChain,
  type ChainNode,
  type ChainOut,
  type EvidenceStatus,
} from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Text, Paragraph } = Typography

const STATUS_COLOR: Record<EvidenceStatus, string> = {
  sufficient: 'green',
  verify: 'orange',
  missing: 'red',
}

function emptyNode(index: number): ChainNode {
  return {
    id: `new-${Date.now()}-${index}`,
    row_id: '',
    capability: '',
    status: 'verify',
    main_question: '',
    followups: [{ level: 1, vague: '', anti_fake: '' }],
    observations: [''],
    minutes: 8,
    rag_refs: [],
    flagged: false,
  }
}

export default function QuestionChain({
  sessionId,
  chain,
  canEdit,
  hasMatrix,
  blocked,
  onGenerated,
}: {
  sessionId: number
  chain: ChainOut
  canEdit: boolean
  hasMatrix: boolean
  blocked: string
  onGenerated: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [nodes, setNodes] = useState<ChainNode[]>(chain.nodes)
  const [dirty, setDirty] = useState(false)

  // 服务端数据到达（含异步生成完成后）时重置本地草稿
  useEffect(() => {
    setNodes(chain.nodes)
    setDirty(false)
  }, [chain])

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['workbench', sessionId] })
  }

  const generate = useMutation({
    mutationFn: () => generateChain(sessionId),
    onSuccess: () => {
      message.success(t('workbench.chain.msg.generating'))
      // 交给父组件的轮询去刷状态，这里只负责触发
      onGenerated()
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const save = useMutation({
    mutationFn: () => saveChain(sessionId, nodes, chain.revision),
    onSuccess: (res) => {
      setNodes(res.chain.nodes)
      setDirty(false)
      invalidate()
      message.success(t('workbench.chain.msg.saved'))
      if (res.stale) message.warning(t('workbench.chain.msg.stale'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const swap = useMutation({
    mutationFn: (nodeId: string) => regenerateNode(sessionId, nodeId),
    onSuccess: (res) => {
      setNodes(res.chain.nodes)
      setDirty(false)
      invalidate()
      message.success(t('workbench.chain.msg.swapped'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const patch = (index: number, next: Partial<ChainNode>) => {
    setNodes((prev) => prev.map((n, i) => (i === index ? { ...n, ...next } : n)))
    setDirty(true)
  }

  const move = (index: number, delta: number) => {
    setNodes((prev) => {
      const target = index + delta
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      next[index] = prev[target]
      next[target] = prev[index]
      return next
    })
    setDirty(true)
  }

  const remove = (index: number) => {
    setNodes((prev) => prev.filter((_, i) => i !== index))
    setDirty(true)
  }

  const handleSave = () => {
    if (nodes.some((n) => !n.main_question.trim())) {
      message.error(t('workbench.chain.mainRequired'))
      return
    }
    save.mutate()
  }

  const totalMinutes = useMemo(
    () => nodes.reduce((sum, n) => sum + (Number(n.minutes) || 0), 0),
    [nodes],
  )

  const running = chain.status === 'running'
  const failed = chain.status === 'failed'

  return (
    <Card
      title={t('workbench.chain.title')}
      extra={
        <Space>
          {chain.generated && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('workbench.revision')} {chain.revision}
            </Text>
          )}
          {canEdit && (
            <>
              <Button
                icon={<ThunderboltOutlined />}
                loading={generate.isPending || running}
                disabled={!!blocked || !hasMatrix}
                onClick={() => generate.mutate()}
              >
                {chain.nodes.length > 0
                  ? t('workbench.chain.regenerate')
                  : t('workbench.chain.generate')}
              </Button>
              <Button
                type="primary"
                disabled={!dirty}
                loading={save.isPending}
                onClick={handleSave}
              >
                {t('workbench.chain.save')}
              </Button>
            </>
          )}
        </Space>
      }
    >
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t('workbench.chain.hint')}
      </Paragraph>

      {running && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('workbench.chain.generating')}
          description={
            <Skeleton active paragraph={{ rows: 2 }} title={{ width: '40%' }} />
          }
        />
      )}

      {failed && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('workbench.chain.failed')}
          description={chain.error}
        />
      )}

      {!hasMatrix && !running && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('workbench.chain.needMatrix')}
        />
      )}

      {nodes.length === 0 && !running ? (
        <Empty
          description={canEdit ? t('workbench.chain.empty') : t('workbench.chain.emptyReadonly')}
        />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {nodes.map((node, index) => (
            <Card
              key={node.id || index}
              size="small"
              type="inner"
              title={
                <Space wrap>
                  <Text strong>{node.capability || '—'}</Text>
                  <Tag color={STATUS_COLOR[node.status] ?? 'default'}>
                    {t(`workbench.status.${node.status}`)}
                  </Tag>
                  {node.flagged && (
                    <Tag color="warning">{t('workbench.chain.flagged')}</Tag>
                  )}
                </Space>
              }
              extra={
                <Space size={4}>
                  {canEdit && (
                    <>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {t('workbench.chain.minutes')}
                      </Text>
                      <InputNumber
                        min={3}
                        max={90}
                        size="small"
                        style={{ width: 70 }}
                        value={node.minutes}
                        onChange={(v) => patch(index, { minutes: Number(v ?? 8) })}
                      />
                      <Tooltip title={t('workbench.chain.swap')}>
                        <Button
                          size="small"
                          icon={<ReloadOutlined />}
                          loading={swap.isPending && swap.variables === node.id}
                          onClick={() => swap.mutate(node.id)}
                        />
                      </Tooltip>
                      <Button
                        size="small"
                        icon={<ArrowUpOutlined />}
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                      />
                      <Button
                        size="small"
                        icon={<ArrowDownOutlined />}
                        disabled={index === nodes.length - 1}
                        onClick={() => move(index, 1)}
                      />
                      <Popconfirm
                        title={t('workbench.chain.confirmDelete')}
                        onConfirm={() => remove(index)}
                      >
                        <Button size="small" danger icon={<DeleteOutlined />} />
                      </Popconfirm>
                    </>
                  )}
                  {!canEdit && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {node.minutes} {t('workbench.minutes')}
                    </Text>
                  )}
                </Space>
              }
            >
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('workbench.chain.main')}
                  </Text>
                  {canEdit ? (
                    <Input
                      value={node.main_question}
                      placeholder={t('workbench.chain.mainPlaceholder')}
                      onChange={(e) => patch(index, { main_question: e.target.value })}
                    />
                  ) : (
                    <Paragraph style={{ marginBottom: 0 }}>
                      {node.main_question || '—'}
                    </Paragraph>
                  )}
                </div>

                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('workbench.chain.followups')}
                  </Text>
                  {(node.followups ?? []).map((f, fi) => (
                    <Space
                      key={fi}
                      direction="vertical"
                      size={4}
                      style={{ width: '100%', marginTop: 4 }}
                    >
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {t('workbench.chain.level', { n: f.level })}
                      </Text>
                      {canEdit ? (
                        <>
                          <Input
                            addonBefore={t('workbench.chain.vague')}
                            value={f.vague}
                            onChange={(e) =>
                              patch(index, {
                                followups: node.followups.map((x, xi) =>
                                  xi === fi ? { ...x, vague: e.target.value } : x,
                                ),
                              })
                            }
                          />
                          <Input
                            addonBefore={t('workbench.chain.antiFake')}
                            value={f.anti_fake}
                            onChange={(e) =>
                              patch(index, {
                                followups: node.followups.map((x, xi) =>
                                  xi === fi ? { ...x, anti_fake: e.target.value } : x,
                                ),
                              })
                            }
                          />
                        </>
                      ) : (
                        <>
                          <Text>
                            {t('workbench.chain.vague')}：{f.vague || '—'}
                          </Text>
                          <Text>
                            {t('workbench.chain.antiFake')}：{f.anti_fake || '—'}
                          </Text>
                        </>
                      )}
                    </Space>
                  ))}
                  {canEdit && (
                    <Button
                      type="dashed"
                      size="small"
                      icon={<PlusOutlined />}
                      style={{ marginTop: 6 }}
                      onClick={() =>
                        patch(index, {
                          followups: [
                            ...node.followups,
                            {
                              level: (node.followups?.length ?? 0) + 1,
                              vague: '',
                              anti_fake: '',
                            },
                          ],
                        })
                      }
                    >
                      {t('workbench.chain.addFollowup')}
                    </Button>
                  )}
                </div>

                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('workbench.chain.observations')}
                  </Text>
                  {(node.observations ?? []).map((o, oi) => (
                    <div key={oi} style={{ marginTop: 4 }}>
                      {canEdit ? (
                        <Space.Compact style={{ width: '100%' }}>
                          <Input
                            value={o}
                            placeholder={t('workbench.chain.obsPlaceholder')}
                            onChange={(e) =>
                              patch(index, {
                                observations: node.observations.map((x, xi) =>
                                  xi === oi ? e.target.value : x,
                                ),
                              })
                            }
                          />
                          <Button
                            icon={<DeleteOutlined />}
                            onClick={() =>
                              patch(index, {
                                observations: node.observations.filter(
                                  (_x, xi) => xi !== oi,
                                ),
                              })
                            }
                          />
                        </Space.Compact>
                      ) : (
                        <Text>- {o || '—'}</Text>
                      )}
                    </div>
                  ))}
                  {canEdit && (
                    <Button
                      type="dashed"
                      size="small"
                      icon={<PlusOutlined />}
                      style={{ marginTop: 6 }}
                      onClick={() =>
                        patch(index, { observations: [...node.observations, ''] })
                      }
                    >
                      {t('workbench.chain.addObservation')}
                    </Button>
                  )}
                </div>

                {(node.rag_refs?.length ?? 0) > 0 && (
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('workbench.chain.refs')}
                    </Text>
                    <div>
                      {node.rag_refs.map((r) => (
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
                          key: 'rag',
                          label: t('workbench.chain.ragTitle'),
                          children: (
                            <Space direction="vertical" size={6}>
                              {Object.entries(chain.rag_hits ?? {})
                                .filter(([cap]) => cap === node.capability)
                                .flatMap(([, hits]) => hits)
                                .map((h, hi) => (
                                  <div key={hi}>
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
              </Space>
            </Card>
          ))}
        </Space>
      )}

      {canEdit && (
        <Button
          type="dashed"
          block
          icon={<PlusOutlined />}
          style={{ marginTop: 12 }}
          onClick={() => {
            setNodes((prev) => [...prev, emptyNode(prev.length)])
            setDirty(true)
          }}
        >
          {t('workbench.chain.addNode')}
        </Button>
      )}

      {nodes.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('workbench.chain.total')} {totalMinutes} {t('workbench.minutes')}
            {chain.budget_minutes > 0 && ` / ${t('workbench.chain.budget')} ${chain.budget_minutes}`}
          </Text>
        </div>
      )}
    </Card>
  )
}
