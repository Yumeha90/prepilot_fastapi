/**
 * AI 备面工作台的壳（P09~P13 共用）。
 *
 * 职责边界：只做三件事 ——
 * ① 拉会话快照（含问题链异步生成时的轮询）；
 * ② 顶部候选人信息与 Steps 进度条（可点击跳转，未开放的步骤置灰）；
 * ③ 底部「上一步 / 下一步」。
 * 具体每个步骤的内容在各 Step*.tsx 里，通过 <Outlet /> 渲染。
 *
 * 数据放在这里而不是各步骤页，是因为三个步骤读的是同一份快照：
 * 拆页是为了少滚屏，不是为了把状态拆成三份。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Modal,
  Space,
  Steps,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons'

import { fetchWorkbench } from '@/api/workbench'
import { WorkbenchContext, type LeaveGuard, type WorkbenchContextValue } from './context'
import { IMPLEMENTED_STEPS, STEP_KEYS, STEP_PATHS, stepIndexFromPath } from './steps'

const { Text } = Typography

export default function WorkbenchLayout() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { sessionId } = useParams()
  const id = Number(sessionId)

  const [polling, setPolling] = useState(false)
  const [saving, setSaving] = useState(false)
  // 步骤页注册的「本步骤没做完不许往下走」；见 context.ts 的说明
  const [nextBlocked, setNextBlockedRaw] = useState(false)
  // 置灰原因也一并注册：只灰不说为什么，用户只会以为按钮坏了
  const [nextBlockedReason, setNextBlockedReason] = useState('')
  // 各步骤本地乐观标记的完成态：请求还没回来就先把勾打了
  const [localDone, setLocalDone] = useState<Record<string, boolean>>({})
  const leaveGuard = useRef<LeaveGuard | null>(null)

  const setNextBlocked = useCallback((blocked: boolean, reason?: string) => {
    setNextBlockedRaw(blocked)
    setNextBlockedReason(blocked ? (reason ?? '') : '')
  }, [])

  const markStepDone = useCallback((key: string, done: boolean) => {
    setLocalDone((prev) => (prev[key] === done ? prev : { ...prev, [key]: done }))
  }, [])

  const { data, isLoading } = useQuery({
    queryKey: ['workbench', id],
    queryFn: () => fetchWorkbench(id),
    enabled: Number.isFinite(id) && id > 0,
    refetchInterval: polling ? 3000 : false,
  })

  // 轮询只在「生成中」有意义；拿到终态就停，不再空转
  const chainStatus = data?.chain?.status
  useEffect(() => {
    if (chainStatus && chainStatus !== 'running') setPolling(false)
  }, [chainStatus])

  // 离开当前页时清掉别的步骤注册的守卫，避免串台
  useEffect(() => () => void (leaveGuard.current = null), [])

  /**
   * 带守卫的跳转。
   *
   * 顺序是「先存，再走」：点「上一步 / 下一步 / 进度条 / 返回列表」时，
   * 当前步骤若还有未保存的编辑，先自动存一次，落库了才跳转 ——
   * 这样面试官根本不需要记得点保存，来回翻页也不会丢内容。
   * 只有自动保存失败（校验不过或请求出错）才弹确认框，把丢不丢的决定权交回给他。
   */
  const goTo = useCallback(
    async (target: string | number) => {
      const path =
        typeof target === 'number'
          ? `/workbench/${id}/${STEP_PATHS[target] ?? STEP_PATHS[0]}`
          : target
      const guard = leaveGuard.current
      if (!guard) {
        navigate(path)
        return
      }
      setSaving(true)
      try {
        if (await guard()) {
          navigate(path)
          return
        }
        Modal.confirm({
          title: t('workbench.leaveTitle'),
          content: t('workbench.leaveDesc'),
          okText: t('common.ok'),
          cancelText: t('common.cancel'),
          okButtonProps: { danger: true },
          onOk: () => navigate(path),
        })
      } finally {
        setSaving(false)
      }
    },
    [id, navigate, t],
  )

  const startPolling = useCallback(() => {
    setPolling(true)
    void queryClient.invalidateQueries({ queryKey: ['workbench', id] })
  }, [id, queryClient])

  const index = stepIndexFromPath(location.pathname)
  const steps = data?.steps ?? []
  const canEdit = data?.can_edit ?? false
  const blocked = data?.blocked_reason ?? ''

  const stepItems = useMemo(
    () =>
      STEP_KEYS.map((key, i) => {
        // 首屏未到时按「已实现/未开放」给个默认态，避免进度条闪成全灰
        const server = steps[i]?.state ?? (i < IMPLEMENTED_STEPS ? 'todo' : 'disabled')
        // 本地乐观态只用来**提前打勾**，不用来取消服务端已确认的完成
        const state = localDone[key] ? 'done' : server
        return {
          title: t(`workbench.step.${key}`),
          status: state === 'done' ? ('finish' as const) : undefined,
          disabled: state === 'disabled',
          description: state === 'disabled' ? t('workbench.stepDisabled') : undefined,
        }
      }),
    [steps, localDone, t],
  )

  const ctx: WorkbenchContextValue = {
    id,
    data,
    isLoading,
    canEdit,
    blocked,
    steps,
    startPolling,
    goTo,
    leaveGuard,
    setNextBlocked,
    markStepDone,
  }

  return (
    <WorkbenchContext.Provider value={ctx}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Card
          title={t('workbench.title')}
          extra={
            <Button disabled={saving} onClick={() => void goTo('/workbench')}>
              {t('workbench.back')}
            </Button>
          }
        >
          <Descriptions size="small" column={3} style={{ marginBottom: 8 }}>
            <Descriptions.Item label={t('workbench.info.candidate')}>
              {data?.candidate_name || '—'}
            </Descriptions.Item>
            <Descriptions.Item label={t('workbench.info.position')}>
              {data?.position_name || '—'}
            </Descriptions.Item>
            <Descriptions.Item label={t('workbench.info.round')}>
              {data?.round_name || t(`workbench.round.${data?.round_type ?? 'r1'}`)}
            </Descriptions.Item>
          </Descriptions>

          <Steps
            size="small"
            items={stepItems}
            current={index}
            onChange={(next) => {
              // 往后跳同样受「本步骤没做完」的限制：按钮挡住了，进度条不能留后门
              if (next > index && nextBlocked) {
                message.info(nextBlockedReason || t('workbench.nextBlocked'))
                return
              }
              void goTo(next)
            }}
          />
        </Card>

        {data?.read_only_reason === 'submitted' && (
          <Alert type="info" showIcon message={t('workbench.submittedNotice')} />
        )}
        {data?.read_only_reason === 'read_only' && (
          <Alert type="info" showIcon message={t('workbench.readOnlyNotice')} />
        )}
        {data?.read_only_reason === 'not_current_round' && (
          <Alert type="warning" showIcon message={t('workbench.notCurrentRoundNotice')} />
        )}
        {blocked && (
          <Alert type="warning" showIcon message={t(`workbench.blocked.${blocked}`)} />
        )}

        <Outlet />

        <Card size="small">
          <Space wrap>
            <Button
              icon={<ArrowLeftOutlined />}
              disabled={index === 0 || saving}
              onClick={() => void goTo(index - 1)}
            >
              {t('workbench.nav.prev')}
            </Button>
            {index < STEP_PATHS.length - 1 && (
              <Tooltip
                title={nextBlocked ? nextBlockedReason || t('workbench.nextBlocked') : ''}
              >
                <Button
                  type="primary"
                  disabled={saving || nextBlocked}
                  onClick={() => void goTo(index + 1)}
                >
                  {t('workbench.nav.next', { name: t(`workbench.step.${STEP_KEYS[index + 1]}`) })}
                  <ArrowRightOutlined />
                </Button>
              </Tooltip>
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              {saving
                ? t('workbench.saving')
                : nextBlocked
                  ? nextBlockedReason || t('workbench.nextBlocked')
                  : t('workbench.autoSaveHint')}
            </Text>
          </Space>
        </Card>
      </Space>
    </WorkbenchContext.Provider>
  )
}
