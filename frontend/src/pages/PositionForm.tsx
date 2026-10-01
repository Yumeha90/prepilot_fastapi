/**
 * 职位新建 / 编辑页（PRD 3.2.2、3.2.3）。
 *
 * 新建与编辑共用同一页面，仅标题不同（编辑回显数据）：
 * - /positions/new        新建
 * - /positions/:id/edit   编辑
 *
 * 分区：基本信息 / JD 与结构化拆解 / 面试流程。
 * - JD 区块：三区块均为「行内编辑 + 上下移 + 删除」，排序不影响权重（PRD §3.2.2）
 * - 「暂存草稿」不校验权重、不生成新版本；「保存」校验通过后才生效
 * - 保存前先调 save-preview：若判定 JD 或流程发生实质变更，弹二次确认
 * - 上传 JD 文件：后端同步抽取纯文本后回填输入框，文件不落库（不做 OCR）
 * - AI 拆解：根据原文生成三区块内容，只填表单不落库，确认后随保存生效
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Input,
  Modal,
  Row,
  Select,
  Slider,
  Space,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { extractJdText, parseJd } from '@/api/jd'

import {
  createPosition,
  fetchPosition,
  previewSave,
  saveJd,
  saveRounds,
  updatePosition,
  type Competency,
  type RoundPayload,
  type RoundType,
} from '@/api/positions'
import { fetchUserOptions } from '@/api/users'
import { extractErrorCode } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import Forbidden from '@/pages/Forbidden'

const { Text, Title } = Typography
const ROUND_TYPES: RoundType[] = ['r1', 'r2', 'hr', 'offer']

function evenWeights(count: number): number[] {
  // 按「5 的份数」分配再乘回 5：权重步进为 5，
  // 朴素的 100/count 在 3 项时会得到 34/33/33，保存会被校验拦下
  if (count <= 0) return []
  const units = 20
  const base = Math.floor(units / count)
  const rem = units % count
  return Array.from({ length: count }, (_, i) => (base + (i < rem ? 1 : 0)) * 5)
}

function move<T>(list: T[], index: number, delta: number): T[] {
  const target = index + delta
  if (target < 0 || target >= list.length) return list
  const next = [...list]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

export default function PositionForm() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { id } = useParams()
  const isEdit = Boolean(id)
  const positionId = id ? Number(id) : undefined

  // 该页不在侧边栏菜单里，RoutePermGuard 拦不到，权限自行校验
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canEdit = hasPerm('position:edit')

  const [name, setName] = useState('')
  const [ownerId, setOwnerId] = useState<number | null>(null)
  const [rawText, setRawText] = useState('')
  const [hardGates, setHardGates] = useState<string[]>([])
  const [competencies, setCompetencies] = useState<Competency[]>([])
  const [bonuses, setBonuses] = useState<string[]>([])
  const [rounds, setRounds] = useState<RoundPayload[]>([])
  const [changeInfo, setChangeInfo] = useState<{
    jd_changed: boolean
    rounds_changed: boolean
    affected_candidates: number
  } | null>(null)
  const [saving, setSaving] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [parseNotice, setParseNotice] = useState('')

  const { data: detail } = useQuery({
    queryKey: ['position', positionId],
    queryFn: () => fetchPosition(positionId as number),
    enabled: isEdit,
  })

  const { data: interviewers = [] } = useQuery({
    queryKey: ['user-options', 'interview'],
    queryFn: () => fetchUserOptions('interview'),
  })

  const { data: owners = [] } = useQuery({
    queryKey: ['user-options', 'owner'],
    queryFn: () => fetchUserOptions('owner'),
  })

  useEffect(() => {
    if (!detail) return
    setName(detail.name)
    setOwnerId(detail.owner_id ?? null)
    // 未确认的草稿优先回显，其次才是已生效字段
    const jd = detail.jd
    const src = jd.draft ?? {
      hard_gates: jd.hard_gates,
      competencies: jd.competencies,
      bonuses: jd.bonuses,
    }
    setRawText(jd.raw_text)
    setHardGates(src.hard_gates ?? [])
    setCompetencies(src.competencies ?? [])
    setBonuses(src.bonuses ?? [])
    setRounds(
      detail.rounds.map((r) => ({ type: r.type, interviewer_id: r.interviewer_id })),
    )
  }, [detail])

  const weightTotal = useMemo(
    () => competencies.reduce((sum, c) => sum + (Number(c.weight) || 0), 0),
    [competencies],
  )
  const weightOk = weightTotal === 100
  const jdEmpty =
    hardGates.length === 0 && competencies.length === 0 && bonuses.length === 0

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return t(`errors.${code}`, { defaultValue: t('common.error') })
  }

  const interviewOptions = interviewers.map((u) => ({
    value: u.id,
    label: u.role_code ? `${u.name}（${u.email}）` : u.name,
  }))
  const ownerOptions = owners.map((u) => ({ value: u.id, label: `${u.name}（${u.email}）` }))

  const jdPayload = () => ({
    raw_text: rawText,
    hard_gates: hardGates.filter((x) => x.trim()),
    competencies: competencies.filter((c) => c.text.trim()),
    bonuses: bonuses.filter((x) => x.trim()),
  })

  const persist = async (targetId: number, confirm: boolean) => {
    await saveRounds(targetId, rounds)
    if (!jdEmpty) {
      await saveJd(targetId, { ...jdPayload(), confirm })
    } else if (rawText.trim()) {
      // 只有原文时按草稿存，不做结构化校验
      await saveJd(targetId, { raw_text: rawText, hard_gates: [], competencies: [], bonuses: [], confirm: false })
    }
  }

  const createMut = useMutation({
    mutationFn: () => createPosition({ name: name.trim(), owner_id: ownerId }),
  })

  const runSave = async (confirm: boolean) => {
    if (!name.trim()) {
      message.error(t('position.namePlaceholder'))
      return
    }
    if (confirm && !jdEmpty && !weightOk) {
      message.error(t('position.form.weightInvalid', { n: weightTotal }))
      return
    }
    setSaving(true)
    try {
      if (isEdit && positionId) {
        if (confirm && !jdEmpty) {
          const pv = await previewSave(positionId, { jd: jdPayload(), rounds })
          if (pv.jd_error) {
            message.error(pv.jd_error)
            return
          }
          if (pv.rounds_error) {
            message.error(pv.rounds_error)
            return
          }
          if (pv.jd_changed || pv.rounds_changed) {
            setChangeInfo(pv)
            return
          }
        }
        await updatePosition(positionId, { name: name.trim(), owner_id: ownerId })
        await persist(positionId, confirm)
      } else {
        const created = await createMut.mutateAsync()
        await persist(created.id, confirm)
      }
      message.success(
        t(confirm ? (isEdit ? 'position.msg.updated' : 'position.msg.created') : 'position.msg.draftSaved'),
      )
      navigate('/positions')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const confirmChange = async () => {
    if (!positionId) return
    setSaving(true)
    try {
      await updatePosition(positionId, { name: name.trim(), owner_id: ownerId })
      await persist(positionId, true)
      message.success(t('position.msg.updated'))
      setChangeInfo(null)
      navigate('/positions')
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  // 上传 JD 文件：后端同步抽取纯文本后回填输入框，文件本身不留存
  const handleUpload = async (file: File) => {
    setExtracting(true)
    try {
      const res = await extractJdText(file)
      setRawText(res.text)
      message.success(t('position.msg.jdExtracted'))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setExtracting(false)
    }
  }

  // AI 拆解：结果只填充表单，不落库，HR 确认后随保存生效
  const handleParse = async () => {
    if (!rawText.trim()) {
      message.error(t('position.form.jdRawPlaceholder'))
      return
    }
    setParsing(true)
    try {
      const res = await parseJd(rawText)
      setHardGates(res.hard_gates)
      setCompetencies(res.competencies)
      setBonuses(res.bonuses)
      setParseNotice(res.notice)
      message.success(t('position.msg.jdParsed'))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setParsing(false)
    }
  }

  const addRound = (type: RoundType) =>
    setRounds((prev) => [...prev, { type, interviewer_id: null }])

  const usedTypes = new Set(rounds.map((r) => r.type))

  if (!canEdit) return <Forbidden />

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
          <Title level={4} style={{ margin: 0 }}>
            {t(isEdit ? 'position.editTitle' : 'position.newTitle')}
          </Title>
          <Space>
            <Button onClick={() => navigate('/positions')}>{t('position.backToList')}</Button>
          </Space>
        </Space>
      </Card>

      <Card title={t('position.form.basicSection')}>
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Text>{t('position.name')}</Text>
              <Input
                value={name}
                maxLength={200}
                placeholder={t('position.namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
              />
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Text>{t('position.owner')}</Text>
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                style={{ width: '100%' }}
                value={ownerId ?? undefined}
                placeholder={t('position.ownerPlaceholder')}
                options={ownerOptions}
                onChange={(v) => setOwnerId(v ?? null)}
              />
            </Space>
          </Col>
        </Row>
      </Card>

      <Card title={t('position.form.jdSection')}>
        <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
          <Text>{t('position.form.jdRaw')}</Text>
          <Input.TextArea
            rows={5}
            value={rawText}
            placeholder={t('position.form.jdRawPlaceholder')}
            onChange={(e) => setRawText(e.target.value)}
          />
          <Space wrap style={{ marginTop: 4 }}>
            <Upload
              accept=".pdf,.docx,.txt,.md"
              showUploadList={false}
              beforeUpload={(file) => {
                void handleUpload(file as unknown as File)
                return false // 阻止 antd 自动上传，改由上面自己发请求
              }}
            >
              <Button icon={<UploadOutlined />} loading={extracting}>
                {t('position.form.uploadJd')}
              </Button>
            </Upload>
            <Button
              icon={<ThunderboltOutlined />}
              loading={parsing}
              disabled={!rawText.trim()}
              onClick={handleParse}
            >
              {t('position.form.aiParse')}
            </Button>
            <Text type="secondary">{t('position.form.uploadHint')}</Text>
          </Space>
          {parseNotice && (
            <Alert type="warning" showIcon message={parseNotice} style={{ marginTop: 8 }} />
          )}
        </Space>

        <Divider orientation="left" style={{ margin: '8px 0' }}>
          {t('position.form.hardGates')}
        </Divider>
        <Alert
          type="error"
          showIcon
          message={t('position.form.hardGateHint')}
          style={{ marginBottom: 8 }}
        />
        {hardGates.map((item, i) => (
          <Space key={`hg-${i}`} style={{ display: 'flex', marginBottom: 8 }}>
            <Input
              style={{ width: 420 }}
              value={item}
              placeholder={t('position.form.itemPlaceholder')}
              onChange={(e) =>
                setHardGates(hardGates.map((x, j) => (j === i ? e.target.value : x)))
              }
            />
            <Button
              size="small"
              icon={<ArrowUpOutlined />}
              disabled={i === 0}
              onClick={() => setHardGates(move(hardGates, i, -1))}
            />
            <Button
              size="small"
              icon={<ArrowDownOutlined />}
              disabled={i === hardGates.length - 1}
              onClick={() => setHardGates(move(hardGates, i, 1))}
            />
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => setHardGates(hardGates.filter((_, j) => j !== i))}
            />
          </Space>
        ))}
        <Button
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setHardGates([...hardGates, ''])}
        >
          {t('position.form.add')}
        </Button>

        <Divider orientation="left" style={{ margin: '16px 0 8px' }}>
          {t('position.form.competencies')}
        </Divider>
        <Text type="secondary">{t('position.form.competencyHint')}</Text>
        {competencies.map((c, i) => (
          <Row key={`cp-${i}`} gutter={8} align="middle" style={{ marginTop: 8 }}>
            <Col flex="auto">
              <Input
                value={c.text}
                placeholder={t('position.form.itemPlaceholder')}
                onChange={(e) =>
                  setCompetencies(
                    competencies.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)),
                  )
                }
              />
            </Col>
            <Col style={{ width: 200 }}>
              <Slider
                min={0}
                max={100}
                step={5}
                value={Number(c.weight) || 0}
                onChange={(v) =>
                  setCompetencies(
                    competencies.map((x, j) => (j === i ? { ...x, weight: Number(v) } : x)),
                  )
                }
              />
            </Col>
            <Col style={{ width: 48 }}>
              <Text>{Number(c.weight) || 0}</Text>
            </Col>
            <Col>
              <Space size={4}>
                <Button
                  size="small"
                  icon={<ArrowUpOutlined />}
                  disabled={i === 0}
                  onClick={() => setCompetencies(move(competencies, i, -1))}
                />
                <Button
                  size="small"
                  icon={<ArrowDownOutlined />}
                  disabled={i === competencies.length - 1}
                  onClick={() => setCompetencies(move(competencies, i, 1))}
                />
                <Button
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => setCompetencies(competencies.filter((_, j) => j !== i))}
                />
              </Space>
            </Col>
          </Row>
        ))}
        <Space style={{ marginTop: 8 }} wrap>
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setCompetencies([...competencies, { text: '', weight: 0 }])}
          >
            {t('position.form.add')}
          </Button>
          <Button
            size="small"
            disabled={competencies.length === 0}
            onClick={() =>
              setCompetencies((prev) =>
                prev.map((c, i) => ({ ...c, weight: evenWeights(prev.length)[i] })),
              )
            }
          >
            {t('position.form.evenSplit')}
          </Button>
          <Text type={weightOk ? 'secondary' : 'danger'}>
            {weightOk
              ? t('position.form.weightTotal', { n: weightTotal })
              : t('position.form.weightInvalid', { n: weightTotal })}
          </Text>
        </Space>

        <Divider orientation="left" style={{ margin: '16px 0 8px' }}>
          {t('position.form.bonuses')}
        </Divider>
        {bonuses.map((item, i) => (
          <Space key={`bn-${i}`} style={{ display: 'flex', marginBottom: 8 }}>
            <Input
              style={{ width: 420 }}
              value={item}
              placeholder={t('position.form.itemPlaceholder')}
              onChange={(e) => setBonuses(bonuses.map((x, j) => (j === i ? e.target.value : x)))}
            />
            <Button
              size="small"
              icon={<ArrowUpOutlined />}
              disabled={i === 0}
              onClick={() => setBonuses(move(bonuses, i, -1))}
            />
            <Button
              size="small"
              icon={<ArrowDownOutlined />}
              disabled={i === bonuses.length - 1}
              onClick={() => setBonuses(move(bonuses, i, 1))}
            />
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => setBonuses(bonuses.filter((_, j) => j !== i))}
            />
          </Space>
        ))}
        <Button size="small" icon={<PlusOutlined />} onClick={() => setBonuses([...bonuses, ''])}>
          {t('position.form.add')}
        </Button>
      </Card>

      <Card title={t('position.form.roundSection')}>
        <Text type="secondary">{t('position.form.roundOrderHint')}</Text>
        <div style={{ marginTop: 12 }}>
          {rounds.map((r, i) => (
            <Space key={`rd-${i}`} style={{ display: 'flex', marginBottom: 8 }} wrap>
              <Text type="secondary" style={{ width: 24 }}>
                {i + 1}
              </Text>
              <Select
                style={{ width: 160 }}
                value={r.type}
                options={ROUND_TYPES.map((x) => ({
                  value: x,
                  label: t(`position.form.roundTypes.${x}`),
                  disabled: x !== r.type && usedTypes.has(x),
                }))}
                onChange={(v) =>
                  setRounds(rounds.map((x, j) => (j === i ? { ...x, type: v } : x)))
                }
              />
              <Select
                showSearch
                optionFilterProp="label"
                style={{ width: 260 }}
                value={r.interviewer_id ?? undefined}
                placeholder={t('position.form.interviewerPlaceholder')}
                options={interviewOptions}
                onChange={(v) =>
                  setRounds(rounds.map((x, j) => (j === i ? { ...x, interviewer_id: v ?? null } : x)))
                }
              />
              <Button
                size="small"
                icon={<ArrowUpOutlined />}
                disabled={i === 0}
                onClick={() => setRounds(move(rounds, i, -1))}
              />
              <Button
                size="small"
                icon={<ArrowDownOutlined />}
                disabled={i === rounds.length - 1}
                onClick={() => setRounds(move(rounds, i, 1))}
              />
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => setRounds(rounds.filter((_, j) => j !== i))}
              />
            </Space>
          ))}
          {rounds.length === 0 && (
            <Text type="secondary">{t('position.form.emptyHint')}</Text>
          )}
        </div>
        <Space style={{ marginTop: 8 }} wrap>
          {ROUND_TYPES.filter((x) => !usedTypes.has(x)).map((x) => (
            <Button
              key={x}
              size="small"
              icon={<PlusOutlined />}
              disabled={rounds.length >= 4}
              onClick={() => addRound(x)}
            >
              {t(`position.form.roundTypes.${x}`)}
            </Button>
          ))}
          {rounds.length >= 4 && (
            <Text type="secondary">{t('position.form.roundMax')}</Text>
          )}
        </Space>
      </Card>

      <Card>
        <Space>
          <Button type="primary" loading={saving} onClick={() => runSave(true)}>
            {t('position.form.save')}
          </Button>
          <Button loading={saving} onClick={() => runSave(false)}>
            {t('position.form.saveDraft')}
          </Button>
          <Text type="secondary">{t('position.form.draftHint')}</Text>
        </Space>
      </Card>

      <Modal
        open={changeInfo !== null}
        title={t('position.changeTitle')}
        okText={t('common.ok')}
        cancelText={t('common.cancel')}
        confirmLoading={saving}
        onCancel={() => setChangeInfo(null)}
        onOk={confirmChange}
      >
        <Space direction="vertical" size="small">
          {changeInfo?.jd_changed && (
            <Text>
              {changeInfo.affected_candidates > 0
                ? t('position.jdChangeWithCandidates', { n: changeInfo.affected_candidates })
                : t('position.jdChangeNoCandidates')}
            </Text>
          )}
          {changeInfo?.rounds_changed && <Text>{t('position.roundChangeHint')}</Text>}
        </Space>
      </Modal>
    </Space>
  )
}
