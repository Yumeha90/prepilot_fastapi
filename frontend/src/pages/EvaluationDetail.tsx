/**
 * P17 面评详情（PRD §5.15 / BR-17）—— 只读。
 *
 * 三条口径：
 * - **只读**：提交即成事实，这里没有任何编辑入口（要改请回工作台重新提交）
 * - **BR-17**：面试官只能看**本人**面评，他人面评后端 403 → 跳回看板并提示
 * - **综合评价双栏**（BR-08）：采纳过润色稿时，左原文右润色稿，看得出改了什么
 *
 * 已粉碎的面评（BR-10 90 天保留策略）：正文与证据清空、只留评分与能力项名，
 * 页面必须显式说明"内容已按保留策略粉碎"，不能显示成一片空白让人以为没写。
 */
import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Row,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import { fetchEvaluation, type EvaluationItem } from '@/api/evaluations'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Text, Paragraph, Title } = Typography

const CONCLUSION_COLOR: Record<string, string> = {
  pass: 'green',
  pending: 'gold',
  fail: 'red',
}

const FAIRNESS_COLOR: Record<string, string> = {
  pass: 'green',
  warn: 'orange',
  block: 'red',
  idle: 'default',
}

const RECOMMEND_TEXT: Record<string, string> = {
  proceed: 'evaluation.recommendProceed',
  hold: 'evaluation.recommendHold',
  reject: 'evaluation.recommendReject',
}

export default function EvaluationDetail() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { sessionId } = useParams<{ sessionId: string }>()
  const id = Number(sessionId)

  const { data, isLoading, error } = useQuery({
    queryKey: ['evaluation', id],
    queryFn: () => fetchEvaluation(id),
    enabled: Number.isFinite(id),
    retry: false,
  })

  const code = error ? extractErrorCode(error) : ''
  useEffect(() => {
    if (!error) return
    // BR-17：面试官打开他人面评 → 403，退回看板并提示（不是留在空白页）
    message.error(
      code ? t(`errors.${code}`, { defaultValue: extractErrorMessage(error) }) : extractErrorMessage(error),
    )
    void navigate('/candidates', { replace: true })
  }, [error, code, navigate, t])

  if (isLoading) {
    return (
      <Card>
        <Spin />
      </Card>
    )
  }
  if (!data) {
    return (
      <Card>
        <Empty />
      </Card>
    )
  }

  const columns: ColumnsType<EvaluationItem> = [
    { title: t('evaluation.capability'), dataIndex: 'capability' },
    {
      title: t('evaluation.score'),
      dataIndex: 'score',
      width: 90,
      render: (s: number | null) => (s === null ? <Text type="secondary">—</Text> : `${s}/5`),
    },
    {
      title: t('evaluation.evidence'),
      key: 'evidence',
      render: (_, row) => {
        if (data.content_purged) return <Text type="secondary">{t('evaluation.purged')}</Text>
        const items = (row.evidences ?? []).filter((e) => e.text.trim())
        if (!items.length && !row.note.trim()) return <Text type="secondary">—</Text>
        return (
          <>
            {items.map((e) => (
              <div key={e.id}>
                {e.quote ? <Tag color="blue">{t('evaluation.quote')}</Tag> : null}
                <Text>{e.text}</Text>
              </div>
            ))}
            {row.note.trim() && (
              <div>
                <Text type="secondary">{t('evaluation.note')}：</Text>
                <Text>{row.note}</Text>
              </div>
            )}
          </>
        )
      },
    },
  ]

  const hasDualSummary = Boolean(data.summary_original.trim()) && data.polished_adopted

  return (
    <Card
      title={t('evaluation.detailTitle')}
      extra={
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>
          {t('common.back')}
        </Button>
      }
    >
      {data.content_purged && (
        <Alert type="warning" showIcon message={t('evaluation.purgedHint')} style={{ marginBottom: 12 }} />
      )}

      <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
        <Descriptions.Item label={t('evaluation.candidate')}>
          {data.candidate_name}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.position')}>
          {data.position_name}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.round')}>{data.round_name}</Descriptions.Item>
        <Descriptions.Item label={t('evaluation.interviewer')}>
          {data.interviewer_name}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.conclusion')}>
          {data.conclusion ? (
            <Tag color={CONCLUSION_COLOR[data.conclusion] ?? 'default'}>
              {t(`workbench.submit.conclusionValue.${data.conclusion}`)}
            </Tag>
          ) : (
            '—'
          )}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.submittedAt')}>
          {data.submitted_at ? new Date(data.submitted_at).toLocaleString() : '—'}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.duration')}>
          {data.duration_minutes} {t('common.minutes')}
        </Descriptions.Item>
        <Descriptions.Item label={t('evaluation.fairness')}>
          {data.fairness?.result ? (
            <Tag color={FAIRNESS_COLOR[data.fairness.result] ?? 'default'}>
              {t(`evaluation.fairnessResult.${data.fairness.result}`)}
            </Tag>
          ) : (
            '—'
          )}
          {data.fairness?.scanned_at ? (
            <Text type="secondary" style={{ marginLeft: 8 }}>
              {new Date(data.fairness.scanned_at).toLocaleString()}
            </Text>
          ) : null}
        </Descriptions.Item>
      </Descriptions>

      <Card size="small" title={t('evaluation.scoreTable')} style={{ marginBottom: 16 }}>
        <Table
          rowKey={(row) => row.row_id || row.capability}
          size="small"
          pagination={false}
          columns={columns}
          dataSource={data.items}
        />
      </Card>

      <Card size="small" title={t('evaluation.summary')} style={{ marginBottom: 16 }}>
        {data.content_purged ? (
          <Text type="secondary">{t('evaluation.purged')}</Text>
        ) : hasDualSummary ? (
          <Row gutter={16}>
            <Col span={12}>
              <Title level={5}>{t('evaluation.original')}</Title>
              <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{data.summary_original}</Paragraph>
            </Col>
            <Col span={12}>
              <Title level={5}>{t('evaluation.adopted')}</Title>
              <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{data.summary}</Paragraph>
            </Col>
          </Row>
        ) : (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
            {data.summary || <Text type="secondary">—</Text>}
          </Paragraph>
        )}
        {data.recommendation && RECOMMEND_TEXT[data.recommendation] && (
          // 与 Step4 同口径：带上主语，否则一个孤零零的「待定」看不出是谁的建议
          <div style={{ marginTop: 8 }}>
            <Tag>
              {t('evaluation.recommendLabel', {
                v: t(RECOMMEND_TEXT[data.recommendation]),
              })}
            </Tag>
          </div>
        )}
      </Card>

      {data.polished_flags.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={t('evaluation.flagTitle')}
          description={
            <ul style={{ marginBottom: 0, paddingLeft: 18 }}>
              {data.polished_flags.map((f, i) => (
                <li key={i}>
                  <Text code>{f.snippet}</Text> — {f.reason}
                </li>
              ))}
            </ul>
          }
        />
      )}
    </Card>
  )
}
