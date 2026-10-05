/**
 * 候选人看板（PRD 3.3.3 / §5.8 P08）。
 *
 * 口径：
 * - 卡片 = 一条应聘记录（application），不是候选人（一人一职位本期成立）
 * - 列由**后端**下发：HR 8 列，面试官只有 r1 / r2（BR-15），前端不自己算
 * - **不做拖拽（D9）**：阶段变更一律走「推进 / 退回 / 终结处置」按钮
 * - 推进按职位的流程配置自动找下一轮并派单，HR 不手选下一轮（BR-06）
 */
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Drawer,
  Dropdown,
  Empty,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, UserOutlined } from '@ant-design/icons'

import { fetchBoard, transitionApplication, type BoardAction, type BoardCard } from '@/api/board'
import { fetchCandidate } from '@/api/candidates'
import { fetchPositions } from '@/api/positions'
import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import type { ResumeProfile } from '@/api/resume'

const { Text, Paragraph } = Typography

/** 当轮进展（会话状态）→ 徽章文案与颜色 */
const SESSION_PROGRESS: Record<string, { color: string }> = {
  '': { color: 'default' },
  s1_draft: { color: 'default' },
  s2_draft: { color: 'processing' },
  s3_draft: { color: 'processing' },
  s4_draft: { color: 'processing' },
  s5_draft: { color: 'processing' },
  submitted: { color: 'success' },
}

/** 还有下一步可走的阶段（终态列不给推进/退回） */
const FLOW_STAGES = new Set(['pending', 'in_r1', 'in_r2', 'in_hr'])
/** 终结处置后的阶段：卡片仍要看得见（v1.24 accepted 也成列），但要给撤销入口 */
const TERMINAL_STAGES = new Set(['accepted', 'rejected', 'in_pool', 'archived'])

/** 匹配分徽章颜色（与 P18 环形分同色系） */
const MATCH_COLOR: Record<string, string> = {
  excellent: 'green',
  good: 'blue',
  fair: 'orange',
  low: 'default',
  vetoed: 'red',
}

export default function Board() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)

  const positionId = params.get('position_id') ? Number(params.get('position_id')) : undefined
  const [keyword, setKeyword] = useState('')
  const [viewId, setViewId] = useState<number | null>(null)

  const canUpload = hasPerm('candidate:upload_resume')
  // BR-18：面试官没有 match:view，卡片上连入口都不渲染
  const canMatch = hasPerm('match:view')
  // P17 入口：HR（view_all）看全部已提交面评，面试官（view_own）看本人那条
  const canViewEvaluation =
    hasPerm('evaluation:view_all') || hasPerm('evaluation:view_own')

  const { data, isLoading } = useQuery({
    queryKey: ['board', positionId, keyword],
    queryFn: () => fetchBoard({ position_id: positionId, keyword: keyword || undefined }),
  })

  const { data: positions } = useQuery({
    queryKey: ['positions', 'board-filter'],
    queryFn: () => fetchPositions({ page: 1, page_size: 100 }),
  })

  // 详情抽屉：看板卡片是简版，点开才拉简历原文与档案
  const { data: detail } = useQuery({
    queryKey: ['candidate', viewId],
    queryFn: () => fetchCandidate(viewId as number),
    enabled: viewId !== null,
  })
  const profile = (detail?.parsed_profile ?? null) as ResumeProfile | null

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return code ? t(`errors.${code}`, { defaultValue: '' }) || extractErrorMessage(error) : extractErrorMessage(error)
  }

  const transition = useMutation({
    mutationFn: (vars: { applicationId: number; action: BoardAction }) =>
      transitionApplication(vars.applicationId, vars.action),
    onSuccess: (res) => {
      if (res.notice) {
        message.warning(res.notice)
      } else {
        message.success(t('candidate.msg.stageChanged'))
      }
      void queryClient.invalidateQueries({ queryKey: ['board'] })
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const columns = data?.columns ?? []
  const actions = useMemo(() => new Set(data?.actions ?? []), [data?.actions])

  const renderActions = (card: BoardCard) => {
    if (!actions.size) return null
    // 已终结处置：不给推进/退回，但必须能撤销 —— 手滑点错一个「录用 / 淘汰」
    // 就永久钉死的话，HR 只能找人改库
    if (TERMINAL_STAGES.has(card.stage)) {
      if (!actions.has('reopen')) return null
      return (
        <Button
          type="link"
          size="small"
          loading={transition.isPending}
          onClick={() =>
            transition.mutate({ applicationId: card.application_id, action: 'reopen' })
          }
        >
          {t('candidate.action.reopen')}
        </Button>
      )
    }
    if (!FLOW_STAGES.has(card.stage)) return null
    const items = [
      { key: 'accept', label: t('candidate.action.accept') },
      { key: 'reject', label: t('candidate.action.reject') },
      { key: 'pool', label: t('candidate.action.pool') },
      { key: 'archive', label: t('candidate.action.archive') },
    ]
    return (
      <Space size={0} wrap>
        {actions.has('advance') && (
          <Button
            type="link"
            size="small"
            loading={transition.isPending}
            onClick={() =>
              transition.mutate({ applicationId: card.application_id, action: 'advance' })
            }
          >
            {t('candidate.action.advance')}
          </Button>
        )}
        {actions.has('rollback') && card.stage !== 'pending' && (
          <Button
            type="link"
            size="small"
            onClick={() =>
              transition.mutate({ applicationId: card.application_id, action: 'rollback' })
            }
          >
            {t('candidate.action.rollback')}
          </Button>
        )}
        <Dropdown
          menu={{
            items,
            onClick: ({ key }) =>
              transition.mutate({
                applicationId: card.application_id,
                action: key as BoardAction,
              }),
          }}
        >
          <Button type="link" size="small">
            {t('candidate.action.dispose')}
          </Button>
        </Dropdown>
      </Space>
    )
  }

  return (
    <Card
      title={t('candidate.boardTitle')}
      extra={
        <Space wrap>
          <Select
            allowClear
            style={{ width: 220 }}
            placeholder={t('candidate.filterPosition')}
            value={positionId}
            onChange={(v) => {
              const next = new URLSearchParams(params)
              if (v) next.set('position_id', String(v))
              else next.delete('position_id')
              setParams(next)
            }}
            options={(positions?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
          />
          <Input.Search
            allowClear
            style={{ width: 180 }}
            placeholder={t('candidate.keywordPlaceholder')}
            onSearch={setKeyword}
          />
          {canUpload && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/candidates/new')}
            >
              {t('candidate.uploadResume')}
            </Button>
          )}
        </Space>
      }
    >
      <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}>
        {columns.map((col) => (
          <div key={col.stage} style={{ flex: '0 0 260px' }}>
            <Card
              size="small"
              title={
                <Space>
                  <Text strong>{t(`candidate.stageName.${col.stage}`)}</Text>
                  <Tag>{col.cards.length}</Tag>
                </Space>
              }
              styles={{ body: { minHeight: 120, background: '#fafafa' } }}
            >
              {col.cards.length === 0 && (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={<Text type="secondary">—</Text>}
                />
              )}
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {col.cards.map((card) => (
                  <Card
                    key={card.application_id}
                    size="small"
                    hoverable
                    styles={{ body: { padding: 12 } }}
                  >
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      <Space>
                        <Text strong>{card.candidate_name || '—'}</Text>
                        {card.profile_status && card.profile_status !== 'confirmed' && (
                          <Tag color="warning">{t(`candidate.status.${card.profile_status}`)}</Tag>
                        )}
                      </Space>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {card.position_name}
                      </Text>
                      {card.interviewer_name && (
                        <Text style={{ fontSize: 12 }}>
                          <UserOutlined /> {card.interviewer_name}
                          {card.current_round_name ? ` · ${card.current_round_name}` : ''}
                        </Text>
                      )}
                      {card.session_status && (
                        <Tag color={SESSION_PROGRESS[card.session_status]?.color ?? 'default'}>
                          {t(`candidate.sessionStatus.${card.session_status}`)}
                        </Tag>
                      )}
                      {card.match_score !== null && (
                        <Tag
                          color={
                            card.match_status === 'STALE'
                              ? 'default'
                              : MATCH_COLOR[card.match_tier]
                          }
                        >
                          {card.match_status === 'STALE'
                            ? t('candidate.matchStale')
                            : `${Math.round(card.match_score)} · ${t(`match.tier.${card.match_tier}`)}`}
                        </Tag>
                      )}
                      <Space size={0} wrap>
                        {/* 待确认的简历：解析确认是 pending 卡片的**主入口**。
                            之前只能「查看 AI 匹配 → 再点去解析确认」，等于把主流程藏了两层 */}
                        {canUpload &&
                          card.profile_status &&
                          card.profile_status !== 'confirmed' && (
                            <Button
                              type="link"
                              size="small"
                              style={{ paddingLeft: 0 }}
                              onClick={() =>
                                void navigate(`/candidates/${card.candidate_id}/parse`)
                              }
                            >
                              {t('candidate.action.goParse')}
                            </Button>
                          )}
                        <Button
                          type="link"
                          size="small"
                          style={{
                            paddingLeft:
                              canUpload && card.profile_status !== 'confirmed' ? undefined : 0,
                          }}
                          onClick={() => setViewId(card.candidate_id)}
                        >
                          {t('candidate.action.viewResume')}
                        </Button>
                        {card.can_prepare && card.session_id && (
                          <Button
                            type="link"
                            size="small"
                            onClick={() => void navigate(`/workbench/${card.session_id}`)}
                          >
                            {t('candidate.action.prepare')}
                          </Button>
                        )}
                        {canMatch && (
                          <Button
                            type="link"
                            size="small"
                            onClick={() =>
                              void navigate(`/candidates/${card.application_id}/match`)
                            }
                          >
                            {t('candidate.action.viewMatch')}
                          </Button>
                        )}
                        {/* P17：面评已提交才可看（BR-17）。面试官看的是本人写的那条，
                            HR 看全部 —— 后端还会再校一次权限 */}
                        {canViewEvaluation &&
                          card.session_id &&
                          card.session_status === 'submitted' && (
                            <Button
                              type="link"
                              size="small"
                              onClick={() => void navigate(`/evaluations/${card.session_id}`)}
                            >
                              {t('candidate.action.viewEvaluation')}
                            </Button>
                          )}
                        {/* 历史轮次的面评：卡片只讲"当前卡在哪一轮"（BR-16），
                            推进到下一轮后上一轮的面评会从卡片上消失。
                            消失是对的，但面评不能找不到 —— 后端按 BR-17 判过可见性后才下发 id */}
                        {card.history_evaluation_session_id && (
                          <Button
                            type="link"
                            size="small"
                            onClick={() =>
                              void navigate(
                                `/evaluations/${card.history_evaluation_session_id}`,
                              )
                            }
                          >
                            {t('candidate.action.viewPastEvaluation')}
                          </Button>
                        )}
                      </Space>
                      {renderActions(card)}
                    </Space>
                  </Card>
                ))}
              </Space>
            </Card>
          </div>
        ))}
        {!isLoading && columns.length === 0 && (
          <Empty description={t('common.noData')} style={{ margin: '40px auto' }} />
        )}
      </div>

      {data?.summary && Object.keys(data.summary).length > 0 && (
        <Space wrap style={{ marginTop: 8 }}>
          {Object.entries(data.summary).map(([stage, n]) => (
            <Tag key={stage}>
              {t(`candidate.stageName.${stage}`)}：{n}
            </Tag>
          ))}
        </Space>
      )}

      <Drawer
        open={viewId !== null}
        title={t('candidate.action.viewResume')}
        width={720}
        onClose={() => setViewId(null)}
        extra={
          canUpload && detail && detail.profile_status !== 'confirmed' ? (
            <Button
              type="primary"
              onClick={() => {
                setViewId(null)
                void navigate(`/candidates/${detail.id}/parse`)
              }}
            >
              {t('candidate.action.goParse')}
            </Button>
          ) : null
        }
      >
        {detail && (
          <>
            <Paragraph>
              <Text strong>{detail.name}</Text>
              <Text type="secondary"> · {detail.contact_email}</Text>
              <br />
              <Tag>{t(`candidate.status.${detail.profile_status}`)}</Tag>
              <Text type="secondary">
                {detail.applications?.[0]?.position_name || '—'}
                {detail.applications?.[0]?.stage
                  ? ` · ${t(`candidate.stageName.${detail.applications[0].stage}`)}`
                  : ''}
              </Text>
            </Paragraph>
            {detail.resume_raw_text ? (
              <Paragraph
                style={{
                  maxHeight: 420,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  background: '#fafafa',
                  padding: 12,
                  borderRadius: 4,
                }}
              >
                {detail.resume_raw_text}
              </Paragraph>
            ) : (
              <Text type="secondary">{t('candidate.noProfile')}</Text>
            )}
            {profile?.skills?.length ? (
              <Space wrap style={{ marginTop: 12 }}>
                {profile.skills.map((s) => (
                  <Tag key={s}>{s}</Tag>
                ))}
              </Space>
            ) : null}
          </>
        )}
      </Drawer>
    </Card>
  )
}
