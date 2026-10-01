/**
 * 人岗匹配详情 P18（PRD §5.16 / §6.7 / §6.8）。
 *
 * 三条硬口径：
 * - **BR-18 权限**：面试官没有 `match:view`，进来直接跳回看板并提示
 * - **BR-19 一票否决**：命中时不展示加权得分，只列未满足门槛与判定来源
 * - **BR-21 可解释**：分数区与构成表同源，构成表末行合计 = 环形分数
 *
 * AI 总结是异步补写的：summary_status=pending 时轮询 5s，
 * 不阻塞页面 —— 分数早就可用了，总结只是"为什么"。
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  InputNumber,
  Input,
  List,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'

import {
  fetchMatch,
  recomputeMatch,
  submitMatchFeedback,
  type MatchFeedbackIn,
  type MatchOut,
} from '@/api/match'
import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/store/auth'

const { Text, Paragraph, Title } = Typography

/** 等级 → 环形颜色（BR-19 的 vetoed 走单独分支，不显示数字） */
const TIER_COLOR: Record<string, string> = {
  excellent: '#52c41a',
  good: '#1677ff',
  fair: '#faad14',
  low: '#8c8c8c',
  vetoed: '#ff4d4f',
}

/** 证据强度 → 徽章颜色 */
const LEVEL_COLOR: Record<string, string> = {
  strong: 'green',
  verify: 'blue',
  weak: 'orange',
  missing: 'default',
}

export default function MatchDetail() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { aid } = useParams<{ aid: string }>()
  const queryClient = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const applicationId = Number(aid)

  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [form] = Form.useForm<MatchFeedbackIn>()

  // BR-18：面试官进来直接退回看板
  const allowed = hasPerm('match:view')
  useEffect(() => {
    if (!allowed) {
      message.error(t('match.noPermission'))
      void navigate('/candidates', { replace: true })
    }
  }, [allowed, navigate, t])

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const { data, isLoading } = useQuery({
    queryKey: ['match', applicationId],
    queryFn: () => fetchMatch(applicationId),
    enabled: allowed && Number.isFinite(applicationId),
    // 总结异步补写：还差这一块时轮询，直到 ready / failed
    refetchInterval: (q) =>
      q.state.data?.summary_status === 'pending' ? 5000 : false,
  })

  const recompute = useMutation({
    mutationFn: () => recomputeMatch(applicationId),
    onSuccess: () => {
      message.success(t('match.msg.recomputed'))
      void queryClient.invalidateQueries({ queryKey: ['match', applicationId] })
      void queryClient.invalidateQueries({ queryKey: ['board'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const feedback = useMutation({
    mutationFn: (body: MatchFeedbackIn) => submitMatchFeedback(applicationId, body),
    onSuccess: () => {
      message.success(t('match.msg.feedbackOk'))
      setFeedbackOpen(false)
      form.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['match', applicationId] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  // 能力项 × 证据：后端两个数组同序生成，用 competencyId 兜底配对
  const rows = useMemo(() => {
    if (!data) return []
    const ev = data.evidences ?? []
    return (data.breakdown ?? []).map((b, i) => {
      const e = ev.find((x) => x.competencyId && x.competencyId === b.competencyId) ?? ev[i]
      return {
        key: b.competencyId || String(i),
        name: b.name,
        weight: b.weight,
        score: b.score,
        contribution: b.contribution,
        level: b.level,
        quote: e?.quote ?? '',
        segmentId: e?.segmentId ?? '',
        confidence: e?.confidence ?? 0,
      }
    })
  }, [data])

  if (!allowed) return null

  const renderBlocked = (d: MatchOut) => (
    <Card>
      <Empty
        description={
          d.blocked_reason === 'jd_not_confirmed'
            ? t('match.blocked.jdNotConfirmed')
            : t('match.blocked.profileNotConfirmed')
        }
      />
      {d.blocked_reason === 'profile_not_confirmed' && (
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <Button
            type="primary"
            onClick={() => void navigate(`/candidates/${d.candidate_id}/parse`)}
          >
            {t('candidate.action.goParse')}
          </Button>
        </div>
      )}
    </Card>
  )

  const renderScoreCard = (d: MatchOut) => {
    if (!d.has_score || d.score === null) {
      return (
        <Card
          title={t('match.scoreTitle')}
          extra={
            <Button
              icon={<ReloadOutlined />}
              loading={recompute.isPending}
              onClick={() => recompute.mutate()}
            >
              {t('match.compute')}
            </Button>
          }
        >
          <Empty description={t('match.noScore')} />
        </Card>
      )
    }
    const vetoed = d.tier === 'vetoed'
    return (
      <Card
        title={t('match.scoreTitle')}
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              loading={recompute.isPending}
              onClick={() => recompute.mutate()}
            >
              {d.status === 'STALE' ? t('match.recompute') : t('match.recomputeAgain')}
            </Button>
            <Button onClick={() => setFeedbackOpen(true)}>
              {t('match.feedback')}
              {d.feedbacks.length > 0 ? `（${d.feedbacks.length}）` : ''}
            </Button>
          </Space>
        }
      >
        {d.status === 'STALE' && (
          <Alert type="warning" showIcon message={t('match.staleHint')} style={{ marginBottom: 12 }} />
        )}
        {vetoed ? (
          <>
            <Alert
              type="error"
              showIcon
              message={t('match.veto.title')}
              description={
                <List
                  size="small"
                  dataSource={d.vetoed_gates}
                  renderItem={(g) => (
                    <List.Item>
                      <Text delete>{g.text}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {g.reason}
                      </Text>
                    </List.Item>
                  )}
                />
              }
            />
            {d.unknown_gates.length > 0 && (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message={t('match.unknownGates')}
                description={
                  <List
                    size="small"
                    dataSource={d.unknown_gates}
                    renderItem={(g) => (
                      <List.Item>
                        <Text>{g.text}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {g.reason}
                        </Text>
                      </List.Item>
                    )}
                  />
                }
              />
            )}
          </>
        ) : (
          <Space align="center" size={24} wrap>
            <Progress
              type="dashboard"
              percent={Math.round(d.score)}
              strokeColor={TIER_COLOR[d.tier]}
              format={(p) => `${p}`}
            />
            <Space direction="vertical">
              <Tag color={TIER_COLOR[d.tier]} style={{ fontSize: 14 }}>
                {t(`match.tier.${d.tier}`)}
              </Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('match.algorithm')}：{d.algorithm_version} · {t('match.jdVersion')} v
                {d.jd_version}
              </Text>
              {(d.bonuses ?? []).length > 0 && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('match.bonusTotal')}：+
                  {d.bonuses.reduce((sum, b) => sum + b.delta, 0)}
                </Text>
              )}
            </Space>
          </Space>
        )}
      </Card>
    )
  }

  const renderSummary = (d: MatchOut) => {
    const s = d.summary
    if (d.summary_status === 'pending') {
      return (
        <Card title={t('match.summary.title')}>
          <Space>
            <Spin size="small" />
            <Text type="secondary">{t('match.summary.pending')}</Text>
          </Space>
        </Card>
      )
    }
    if (d.summary_status === 'failed' || !s) {
      return (
        <Card title={t('match.summary.title')}>
          <Alert type="warning" showIcon message={t('match.summary.failed')} />
        </Card>
      )
    }
    return (
      <Card title={t('match.summary.title')}>
        <Paragraph strong>{s.conclusion}</Paragraph>
        {s.reasons?.length > 0 && (
          <>
            <Text strong>{t('match.summary.reasons')}</Text>
            <List
              size="small"
              dataSource={s.reasons}
              renderItem={(x) => <List.Item>{x}</List.Item>}
            />
          </>
        )}
        {s.gaps?.length > 0 && (
          <>
            <Text strong>{t('match.summary.gaps')}</Text>
            <List
              size="small"
              dataSource={s.gaps}
              renderItem={(x) => <List.Item>{x}</List.Item>}
            />
          </>
        )}
        {s.suggestions?.length > 0 && (
          <>
            <Text strong>{t('match.summary.suggestions')}</Text>
            <List
              size="small"
              dataSource={s.suggestions}
              renderItem={(x) => <List.Item>{x}</List.Item>}
            />
          </>
        )}
      </Card>
    )
  }

  const renderMatrix = (d: MatchOut) => (
    <Card title={t('match.matrix.title')}>
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        columns={[
          { title: t('match.matrix.competency'), dataIndex: 'name', key: 'name' },
          { title: t('match.matrix.weight'), dataIndex: 'weight', key: 'weight', width: 80 },
          {
            title: t('match.matrix.level'),
            dataIndex: 'level',
            key: 'level',
            width: 100,
            render: (lv: string) => (
              <Tag color={LEVEL_COLOR[lv] ?? 'default'}>{t(`match.level.${lv}`)}</Tag>
            ),
          },
          { title: t('match.matrix.score'), dataIndex: 'score', key: 'score', width: 70 },
          {
            title: t('match.matrix.contribution'),
            dataIndex: 'contribution',
            key: 'contribution',
            width: 90,
          },
          {
            title: t('match.matrix.evidence'),
            dataIndex: 'quote',
            key: 'quote',
            render: (q: string, r) =>
              q ? (
                <Text style={{ fontSize: 12 }}>
                  {r.segmentId ? `[${r.segmentId}] ` : ''}
                  {q}
                </Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  —
                </Text>
              ),
          },
          {
            title: t('match.matrix.confidence'),
            dataIndex: 'confidence',
            key: 'confidence',
            width: 90,
            render: (c: number) => (c ? `${Math.round(c * 100)}%` : '—'),
          },
        ]}
        summary={() => (
          <Table.Summary.Row>
            <Table.Summary.Cell index={0}>
              <Text strong>{t('match.matrix.total')}</Text>
            </Table.Summary.Cell>
            <Table.Summary.Cell index={1}>
              {rows.reduce((s, r) => s + r.weight, 0)}
            </Table.Summary.Cell>
            <Table.Summary.Cell index={2} />
            <Table.Summary.Cell index={3} />
            <Table.Summary.Cell index={4}>
              <Text strong>{d.score ?? 0}</Text>
            </Table.Summary.Cell>
            <Table.Summary.Cell index={5} />
            <Table.Summary.Cell index={6} />
          </Table.Summary.Row>
        )}
      />
    </Card>
  )

  const renderTriple = (d: MatchOut) => {
    const strong = rows.filter((r) => r.score >= 3)
    const weak = rows.filter((r) => r.score <= 2)
    return (
      <Row gutter={12}>
        <Col span={8}>
          <Card size="small" title={t('match.strengths')}>
            {strong.length ? (
              <Space wrap>
                {strong.map((r) => (
                  <Tag color="green" key={r.key}>
                    {r.name}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title={t('match.risks')}>
            {weak.length ? (
              <Space wrap>
                {weak.map((r) => (
                  <Tag color="orange" key={r.key}>
                    {r.name}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title={t('match.toVerify')}>
            {d.summary?.suggestions?.length ? (
              <List
                size="small"
                dataSource={d.summary.suggestions}
                renderItem={(x) => <List.Item>{x}</List.Item>}
              />
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Card>
        </Col>
      </Row>
    )
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        size="small"
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => void navigate('/candidates')}
            />
            <Title level={5} style={{ margin: 0 }}>
              {t('match.title')}
            </Title>
          </Space>
        }
      >
        {isLoading && <Spin />}
        {data && (
          <Descriptions size="small" column={4}>
            <Descriptions.Item label={t('match.info.candidate')}>
              {data.candidate_name || '—'}
            </Descriptions.Item>
            <Descriptions.Item label={t('match.info.position')}>
              {data.position_name || '—'}
            </Descriptions.Item>
            <Descriptions.Item label={t('match.info.stage')}>
              {t(`candidate.stageName.${data.stage}`)}
            </Descriptions.Item>
            <Descriptions.Item label={t('match.info.updatedAt')}>
              {data.updated_at ? new Date(data.updated_at).toLocaleString() : '—'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      {data && (data.blocked_reason ? renderBlocked(data) : (
        <>
          {renderScoreCard(data)}
          {renderSummary(data)}
          {rows.length > 0 && renderMatrix(data)}
          {renderTriple(data)}
          {data.feedbacks.length > 0 && (
            <Card size="small" title={t('match.feedbackHistory')}>
              <List
                size="small"
                dataSource={data.feedbacks}
                renderItem={(f) => (
                  <List.Item>
                    <Space direction="vertical" size={2}>
                      <Space>
                        <Tag>{t(`match.feedbackKind.${f.kind}`)}</Tag>
                        {f.expected_low !== null && f.expected_high !== null && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {t('match.expected')}：{f.expected_low}–{f.expected_high}
                          </Text>
                        )}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {f.created_by_name} · {new Date(f.created_at).toLocaleString()}
                        </Text>
                      </Space>
                      <Text>{f.comment}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </Card>
          )}
        </>
      ))}

      <Modal
        open={feedbackOpen}
        title={t('match.feedbackTitle')}
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        confirmLoading={feedback.isPending}
        onCancel={() => setFeedbackOpen(false)}
        onOk={() => void form.submit()}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t('match.feedbackHint')}
        />
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => feedback.mutate(values)}
        >
          <Form.Item name="kind" label={t('match.feedbackKindLabel')} rules={[{ required: true }]}>
            <Select
              options={[
                'higher',
                'lower',
                'wrong_quote',
                'missing_evidence',
                'bad_weight',
                'other',
              ].map((k) => ({ value: k, label: t(`match.feedbackKind.${k}`) }))}
            />
          </Form.Item>
          <Space>
            <Form.Item name="expected_low" label={t('match.expectedLow')}>
              <InputNumber min={0} max={100} />
            </Form.Item>
            <Form.Item name="expected_high" label={t('match.expectedHigh')}>
              <InputNumber min={0} max={100} />
            </Form.Item>
          </Space>
          <Form.Item
            name="comment"
            label={t('match.comment')}
            rules={[{ required: true, min: 10, message: t('match.commentHint') }]}
          >
            <Input.TextArea rows={4} maxLength={2000} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
