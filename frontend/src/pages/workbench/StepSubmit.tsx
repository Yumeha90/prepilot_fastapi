/**
 * P13 · Step 5 面评校准与提交（PRD §3.4.5 / §5.13）—— 独立成页。
 *
 * 四条口径：
 *
 * **① 面试官只交结论与面评，不指定下一轮（BR-06）**
 * 页面上没有「推荐下一轮」字段，结论右侧常驻说明卡写清「提交后由 HR 在看板处置」。
 * 面试官最容易误解的就是「我选了通过＝他会进下一轮」，必须当场说清楚。
 *
 * **② 提交前先扫一遍合规**
 * 题目里的红线在 Step3 拦了，但「年纪偏大」「看着不够机灵」只会写在面评里 ——
 * 面评是要给 HR 与其他面试官看的正式记录，比题目更需要这道闸。
 * 扫描单独给按钮而不是只藏在提交里：点了提交才知道被拦，等于让他白走一趟。
 *
 * **③ 完整性在提交时才拦（BR-07）**
 * Step4 保存一律放行，提交是终态动作，所以这里必须补齐每项评分与依据。
 *
 * **④ 提交不可逆**
 * 二次确认弹窗之外，后端还要 `confirm` 再挡一道；提交后整页转只读。
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Rate,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { CheckCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons'

import { scanSubmission, submitEvaluation } from '@/api/workbench'
import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useWorkbench } from './context'

const { Text, Paragraph } = Typography

type Conclusion = 'pass' | 'pending' | 'fail'

const CONCLUSIONS: Conclusion[] = ['pass', 'pending', 'fail']

export default function StepSubmit() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { id, data, canEdit } = useWorkbench()

  // ''(未选) 也是一个合法状态：结论必须面试官主动选，不给默认值 ——
  // 默认「通过」会让人一路回车就提交，默认「待定」又会让多数人忘记改
  const [conclusion, setConclusion] = useState<Conclusion | ''>('')

  const submission = data?.submission
  const evaluation = data?.evaluation
  const submitted = data?.status === 'submitted'

  useEffect(() => {
    if (!conclusion && submission?.conclusion) {
      setConclusion(submission.conclusion as Conclusion)
    }
  }, [conclusion, submission?.conclusion])

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code
      ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error)
      : extractErrorMessage(error)
  }

  const scan = useMutation({
    mutationFn: () => scanSubmission(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.submit.msg.scanned'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const submit = useMutation({
    mutationFn: () => submitEvaluation(id, conclusion),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
      message.success(t('workbench.submit.msg.submitted'))
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const hasContent = (evaluation?.items.length ?? 0) > 0
  if (!hasContent) {
    return (
      <Card title={t('workbench.submit.title')}>
        <Empty description={t('workbench.submit.needEvaluation')} />
      </Card>
    )
  }

  const incomplete = evaluation?.incomplete_count ?? 0
  const noSummary = !evaluation?.summary.trim()

  // BR-06：各项均分 < 2 时结论只能是「不通过」。这里直接把另外两个选项禁掉 ——
  // 让面试官点了提交再被告知「只能选不通过」，等于让他白填一次
  const scored = (evaluation?.items ?? []).filter((it) => it.score != null)
  const avg = scored.length
    ? scored.reduce((s, it) => s + (it.score ?? 0), 0) / scored.length
    : null
  const forcedFail = avg != null && avg < 2

  // BR-07：结论「不通过」需 ≥2 项评分 ≤ 2 且写明依据
  const weakCount = (evaluation?.items ?? []).filter(
    (it) =>
      it.score != null &&
      it.score <= 2 &&
      (it.evidences.some((e) => e.text.trim()) || it.note.trim()),
  ).length
  const failLacksBasis = conclusion === 'fail' && weakCount < 2

  const canSubmit =
    canEdit &&
    incomplete === 0 &&
    !noSummary &&
    conclusion !== '' &&
    !forcedFail &&
    !failLacksBasis

  const flags = submission?.flags ?? []
  const result = submission?.result ?? 'idle'
  // 按级别分开计数：命中 5 条里只有 1 条阻断时，「5 项问题」会吓到人又不说清后果
  const blockCount = flags.filter((f) => f.level === 'block').length
  const warnCount = flags.filter((f) => f.level === 'warn').length

  const askSubmit = () => {
    Modal.confirm({
      title: t('workbench.submit.confirmTitle'),
      content: t('workbench.submit.confirmDesc'),
      okText: t('workbench.submit.submit'),
      cancelText: t('common.cancel'),
      onOk: () => submit.mutate(),
    })
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* ① 结论 + 常驻说明卡 */}
      <Card title={t('workbench.submit.title')}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space size={12} wrap>
            <Text>{t('workbench.submit.conclusion')}</Text>
            {canEdit ? (
              <Select<Conclusion>
                style={{ width: 180 }}
                value={conclusion || undefined}
                placeholder={t('workbench.submit.conclusionPlaceholder')}
                onChange={setConclusion}
                options={CONCLUSIONS.map((c) => ({
                  value: c,
                  label: t(`workbench.submit.conclusionValue.${c}`),
                  // 均分 < 2 时只留「不通过」（BR-06）
                  disabled: forcedFail && c !== 'fail',
                }))}
              />
            ) : (
              <Tag
                color={
                  (submission?.conclusion as Conclusion) === 'pass'
                    ? 'green'
                    : (submission?.conclusion as Conclusion) === 'fail'
                      ? 'red'
                      : 'orange'
                }
              >
                {submission?.conclusion
                  ? t(`workbench.submit.conclusionValue.${submission.conclusion as Conclusion}`)
                  : '—'}
              </Tag>
            )}
            {submitted && submission?.submitted_at && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('workbench.submit.submittedAt', {
                  time: new Date(submission.submitted_at).toLocaleString(),
                })}
              </Text>
            )}
          </Space>

          <Alert
            type="info"
            showIcon
            message={t('workbench.submit.notice')}
          />

          {incomplete > 0 && (
            <Alert
              type="warning"
              showIcon
              message={t('workbench.submit.incomplete', { n: incomplete })}
            />
          )}
          {noSummary && (
            <Alert
              type="warning"
              showIcon
              message={t('workbench.submit.noSummary')}
            />
          )}
          {forcedFail && (
            <Alert
              type="warning"
              showIcon
              message={t('workbench.submit.avgForcedFail', { avg: avg?.toFixed(1) })}
            />
          )}
          {failLacksBasis && (
            <Alert
              type="warning"
              showIcon
              message={t('workbench.submit.failNeedsEvidence', { n: weakCount })}
            />
          )}
        </Space>
      </Card>

      {/* ② 提交前的合规检查 */}
      <Card
        title={t('workbench.submit.scanTitle')}
        extra={
          canEdit && (
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={scan.isPending}
              onClick={() => scan.mutate()}
            >
              {result === 'idle' || !submission?.scanned
                ? t('workbench.submit.scan')
                : t('workbench.submit.rescan')}
            </Button>
          )
        }
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t('workbench.submit.scanHint')}
        </Paragraph>

        {submission?.scanned ? (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {result === 'block' && (
              <Alert
                type="error"
                showIcon
                message={t('workbench.submit.block', { n: blockCount })}
                description={t('workbench.submit.blockDesc')}
              />
            )}
            {result === 'warn' && (
              <Alert
                type="warning"
                showIcon
                message={t('workbench.submit.warn', { n: warnCount })}
                description={t('workbench.submit.warnDesc')}
              />
            )}
            {result === 'pass' && (
              <Alert
                type="success"
                showIcon
                message={t('workbench.submit.pass')}
                description={t('workbench.submit.passDesc', {
                  n: submission.fields_scanned,
                })}
              />
            )}

            {flags.map((f, i) => (
              <Card key={i} size="small">
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    <Tag
                      color={f.level === 'block' ? 'red' : f.level === 'warn' ? 'orange' : 'blue'}
                    >
                      {t(`workbench.fairness.level.${f.level}`)}
                    </Tag>
                    <Text strong>
                      {t(`workbench.fairness.category.${f.category}`, {
                        defaultValue: f.category,
                      })}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t(`workbench.submit.field.${fieldKey(f.field)}`)}
                    </Text>
                    <Tag>{t(`workbench.fairness.source.${f.source}`)}</Tag>
                  </Space>
                  <Text style={{ fontSize: 12 }}>
                    {t('workbench.fairness.snippet')}：{f.snippet}
                  </Text>
                  <Text style={{ fontSize: 12 }}>
                    {t('workbench.fairness.reason')}：{f.reason}
                  </Text>
                  {f.suggestion && (
                    <Text style={{ fontSize: 12 }}>
                      {t('workbench.fairness.suggestion')}：{f.suggestion}
                    </Text>
                  )}
                  {f.refs.length > 0 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('workbench.fairness.refs')}：{f.refs.join('、')}
                    </Text>
                  )}
                </Space>
              </Card>
            ))}
          </Space>
        ) : (
          <Alert
            type="info"
            showIcon
            message={t('workbench.submit.idle')}
            description={t('workbench.submit.idleDesc')}
          />
        )}
      </Card>

      {/* ③ 面评回看 */}
      <Card title={t('workbench.submit.reviewTitle')}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions size="small" column={1} bordered>
            {(evaluation?.items ?? []).map((it) => (
              <Descriptions.Item key={it.row_id} label={it.capability || '—'}>
                <Space direction="vertical" size={2}>
                  <Space size={6}>
                    <Rate count={5} disabled value={it.score ?? 0} />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {it.score ?? t('workbench.evaluation.scoreUnset')}
                    </Text>
                  </Space>
                  {it.evidences
                    .filter((e) => e.text.trim())
                    .map((e) => (
                      <Text key={e.id} style={{ fontSize: 12 }}>
                        · {e.text}
                        {e.quote && (
                          <Tag color="blue" style={{ marginLeft: 6 }}>
                            {t('workbench.evaluation.quote')}
                          </Tag>
                        )}
                      </Text>
                    ))}
                  {it.note.trim() && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('workbench.evaluation.note')}：{it.note}
                    </Text>
                  )}
                </Space>
              </Descriptions.Item>
            ))}
          </Descriptions>

          <div>
            <Text strong>{t('workbench.evaluation.summaryTitle')}</Text>
            <Paragraph style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
              {evaluation?.summary || <Text type="secondary">—</Text>}
            </Paragraph>
          </div>

          {evaluation?.summary_original && (
            <Alert
              type="info"
              showIcon
              message={t('workbench.evaluation.originalTitle')}
              description={
                <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  {evaluation.summary_original}
                </Paragraph>
              }
            />
          )}
        </Space>
      </Card>

      {/* ④ 提交 */}
      <Card size="small">
        <Space wrap>
          {canEdit ? (
            <>
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                disabled={!canSubmit}
                loading={submit.isPending}
                onClick={askSubmit}
              >
                {t('workbench.submit.submit')}
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('workbench.submit.submitHint')}
              </Text>
            </>
          ) : (
            <Text type="secondary">{t('workbench.submit.readOnly')}</Text>
          )}
          {/* 「上一步」在底部导航条里已有，这里不再放一个功能相同的按钮 */}
        </Space>
      </Card>
    </Space>
  )
}

/** 命中字段 → i18n key：summary / note / evidence */
function fieldKey(field: string): string {
  if (field === 'summary') return 'summary'
  if (field.includes(':note')) return 'note'
  return 'evidence'
}
