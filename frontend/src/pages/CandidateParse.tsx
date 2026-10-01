/**
 * 简历解析确认页。
 *
 * 口径：
 * - 左右分栏 50/50：左 = 简历原文只读，右 = 结构化结果可纠错
 * - 必须人工点「通过筛选，安排面试」才能推进（确认后才进流程）
 * - 低置信度区块标黄提示人工核对
 * - 解析失败仍可手动录入
 * - 本阶段确认后停在待处理，后续阶段接派单与首次算分
 */
import { useEffect, useState } from 'react'
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
  Row,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'

import { confirmCandidate, fetchCandidate, reparseCandidate } from '@/api/candidates'
import { emptyProfile, type ResumeProfile } from '@/api/resume'
import { extractErrorCode } from '@/api/client'

const { Text, Title } = Typography

/** 低于该置信度的区块标黄（§7.1） */
const LOW_CONFIDENCE = 0.6

type ProfileKey = 'basic' | 'work' | 'projects' | 'skills' | 'education'

export default function CandidateParse() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const candidateId = Number(id)

  const [profile, setProfile] = useState<ResumeProfile>(emptyProfile())
  const [parsing, setParsing] = useState(false)
  const [notice, setNotice] = useState('')
  const [parseFailed, setParseFailed] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['candidate', candidateId],
    queryFn: () => fetchCandidate(candidateId),
  })

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return t(`errors.${code}`, { defaultValue: t('common.error') })
  }

  // 未解析过的候选人进入页面即自动解析一次；已确认的直接用库里存的结果
  useEffect(() => {
    if (!data) return
    if (data.parsed_profile) {
      setProfile({ ...emptyProfile(), ...data.parsed_profile })
      return
    }
    if (data.profile_status !== 'confirmed') void runParse()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.id])

  const runParse = async () => {
    setParsing(true)
    setParseFailed(false)
    try {
      const out = await reparseCandidate(candidateId)
      setProfile({ ...emptyProfile(), ...out.profile })
      setNotice(out.notice)
      message.success(t('candidate.msg.parsed'))
    } catch (e) {
      setParseFailed(true)
      message.error(errMsg(e))
    } finally {
      setParsing(false)
    }
  }

  const confirmMut = useMutation({
    mutationFn: () => confirmCandidate(candidateId, profile),
    onSuccess: (d) => {
      // D5 确认即派单：派单成不成都要说清楚，否则 HR 以为流程已经走了
      const app = d.applications?.[0]
      if (d.dispatch_notice) {
        message.warning(t('candidate.msg.dispatchFailed', { reason: d.dispatch_notice }))
      } else if (app?.interviewer_name) {
        message.success(
          t('candidate.msg.dispatched', {
            name: app.interviewer_name,
            round: app.current_round_name || '',
          }),
        )
      } else {
        message.success(t('candidate.msg.confirmed'))
      }
      navigate('/candidates')
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const patchBasic = (key: keyof ResumeProfile['basic'], value: string) =>
    setProfile((p) => ({ ...p, basic: { ...p.basic, [key]: value } }))

  const patchItem = <K extends 'work' | 'projects' | 'education'>(
    key: K,
    index: number,
    field: keyof ResumeProfile[K][number],
    value: string,
  ) =>
    setProfile((p) => {
      const list = [...p[key]]
      list[index] = { ...list[index], [field]: value } as ResumeProfile[K][number]
      return { ...p, [key]: list }
    })

  const removeItem = (key: 'work' | 'projects' | 'education', index: number) =>
    setProfile((p) => ({ ...p, [key]: p[key].filter((_, i) => i !== index) }))

  const addItem = (key: 'work' | 'projects' | 'education') =>
    setProfile((p) => {
      const blank =
        key === 'work'
          ? { company: '', title: '', period: '', desc: '' }
          : key === 'projects'
            ? { name: '', role: '', desc: '' }
            : { school: '', major: '', degree: '', period: '' }
      return { ...p, [key]: [...p[key], blank] as never }
    })

  const conf = (key: ProfileKey) => profile.confidence?.[key] ?? 1
  const isLow = (key: ProfileKey) => conf(key) < LOW_CONFIDENCE

  const sectionTitle = (key: ProfileKey, label: string) => (
    <Space>
      <Text strong>{label}</Text>
      {isLow(key) && <Tag color="warning">{t('candidate.lowConfidence')}</Tag>}
    </Space>
  )

  if (isLoading) return <Card loading />

  const confirmed = data?.profile_status === 'confirmed'

  return (
    <Card
      title={t('candidate.parseTitle')}
      extra={
        <Space>
          {!confirmed && (
            <Button
              icon={<ThunderboltOutlined />}
              loading={parsing}
              onClick={runParse}
            >
              {t('candidate.reparse')}
            </Button>
          )}
          <Button onClick={() => navigate('/candidates')}>{t('position.backToList')}</Button>
        </Space>
      }
    >
      <Steps
        size="small"
        current={confirmed ? 2 : 1}
        style={{ marginBottom: 20 }}
        items={[
          { title: t('candidate.step.upload') },
          { title: t('candidate.step.parse') },
          { title: t('candidate.step.confirm') },
        ]}
      />

      {notice && <Alert type="info" showIcon message={notice} style={{ marginBottom: 12 }} />}
      {parseFailed && (
        <Alert
          type="warning"
          showIcon
          message={t('candidate.parseFailedHint')}
          style={{ marginBottom: 12 }}
        />
      )}
      {confirmed && (
        <Alert type="success" showIcon message={t('candidate.confirmedHint')} style={{ marginBottom: 12 }} />
      )}

      <Row gutter={16}>
        <Col span={12}>
          <Title level={5}>{t('candidate.resumeOriginal')}</Title>
          <Input.TextArea
            value={data?.resume_raw_text ?? ''}
            readOnly
            rows={28}
            style={{ fontFamily: 'monospace' }}
          />
        </Col>

        <Col span={12}>
          <Title level={5}>{sectionTitle('basic', t('candidate.section.basic'))}</Title>
          <Row gutter={8}>
            <Col span={12}>
              <Input
                addonBefore={t('candidate.field.name')}
                value={profile.basic.name}
                onChange={(e) => patchBasic('name', e.target.value)}
              />
            </Col>
            <Col span={12}>
              <Input
                addonBefore={t('candidate.field.email')}
                value={profile.basic.email}
                onChange={(e) => patchBasic('email', e.target.value)}
              />
            </Col>
          </Row>
          <Row gutter={8} style={{ marginTop: 8 }}>
            <Col span={12}>
              <Input
                addonBefore={t('candidate.field.phone')}
                value={profile.basic.phone}
                onChange={(e) => patchBasic('phone', e.target.value)}
              />
            </Col>
            <Col span={12}>
              <Input
                addonBefore={t('candidate.field.years')}
                value={profile.basic.years}
                onChange={(e) => patchBasic('years', e.target.value)}
              />
            </Col>
          </Row>
          <Input
            addonBefore={t('candidate.field.location')}
            value={profile.basic.location}
            onChange={(e) => patchBasic('location', e.target.value)}
            style={{ marginTop: 8 }}
          />

          <Divider />
          <Title level={5}>{sectionTitle('skills', t('candidate.section.skills'))}</Title>
          <Space wrap size={[4, 8]}>
            {profile.skills.map((s, i) => (
              <Tag
                key={`${s}-${i}`}
                closable
                onClose={() =>
                  setProfile((p) => ({ ...p, skills: p.skills.filter((_, x) => x !== i) }))
                }
              >
                {s}
              </Tag>
            ))}
          </Space>
          <Input
            size="small"
            style={{ width: 200, marginTop: 8 }}
            placeholder={t('candidate.addSkill')}
            onPressEnter={(e) => {
              const v = (e.target as HTMLInputElement).value.trim()
              if (v) {
                setProfile((p) => ({ ...p, skills: [...p.skills, v] }))
                ;(e.target as HTMLInputElement).value = ''
              }
            }}
          />

          <Divider />
          <Title level={5}>{sectionTitle('work', t('candidate.section.work'))}</Title>
          {profile.work.map((w, i) => (
            <Card
              key={i}
              size="small"
              style={{ marginBottom: 8 }}
              extra={
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={() => removeItem('work', i)}
                />
              }
            >
              <Row gutter={8}>
                <Col span={12}>
                  <Input
                    placeholder={t('candidate.field.company')}
                    value={w.company}
                    onChange={(e) => patchItem('work', i, 'company', e.target.value)}
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={t('candidate.field.title')}
                    value={w.title}
                    onChange={(e) => patchItem('work', i, 'title', e.target.value)}
                  />
                </Col>
              </Row>
              <Input
                placeholder={t('candidate.field.period')}
                value={w.period}
                onChange={(e) => patchItem('work', i, 'period', e.target.value)}
                style={{ marginTop: 8 }}
              />
              <Input.TextArea
                rows={2}
                placeholder={t('candidate.field.desc')}
                value={w.desc}
                onChange={(e) => patchItem('work', i, 'desc', e.target.value)}
                style={{ marginTop: 8 }}
              />
            </Card>
          ))}
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={() => addItem('work')}
            disabled={profile.work.length >= 10}
          >
            {t('candidate.addWork')}
          </Button>

          <Divider />
          <Title level={5}>{sectionTitle('projects', t('candidate.section.projects'))}</Title>
          {profile.projects.map((p, i) => (
            <Card
              key={i}
              size="small"
              style={{ marginBottom: 8 }}
              extra={
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={() => removeItem('projects', i)}
                />
              }
            >
              <Row gutter={8}>
                <Col span={12}>
                  <Input
                    placeholder={t('candidate.field.projectName')}
                    value={p.name}
                    onChange={(e) => patchItem('projects', i, 'name', e.target.value)}
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={t('candidate.field.role')}
                    value={p.role}
                    onChange={(e) => patchItem('projects', i, 'role', e.target.value)}
                  />
                </Col>
              </Row>
              <Input.TextArea
                rows={2}
                placeholder={t('candidate.field.desc')}
                value={p.desc}
                onChange={(e) => patchItem('projects', i, 'desc', e.target.value)}
                style={{ marginTop: 8 }}
              />
            </Card>
          ))}
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={() => addItem('projects')}
            disabled={profile.projects.length >= 10}
          >
            {t('candidate.addProject')}
          </Button>

          <Divider />
          <Title level={5}>{sectionTitle('education', t('candidate.section.education'))}</Title>
          {profile.education.map((e2, i) => (
            <Row gutter={8} key={i} style={{ marginBottom: 8 }}>
              <Col span={10}>
                <Input
                  placeholder={t('candidate.field.school')}
                  value={e2.school}
                  onChange={(e) => patchItem('education', i, 'school', e.target.value)}
                />
              </Col>
              <Col span={8}>
                <Input
                  placeholder={t('candidate.field.major')}
                  value={e2.major}
                  onChange={(e) => patchItem('education', i, 'major', e.target.value)}
                />
              </Col>
              <Col span={6}>
                <Space.Compact>
                  <Input
                    placeholder={t('candidate.field.degree')}
                    value={e2.degree}
                    onChange={(e) => patchItem('education', i, 'degree', e.target.value)}
                  />
                  <Button
                    icon={<DeleteOutlined />}
                    onClick={() => removeItem('education', i)}
                  />
                </Space.Compact>
              </Col>
            </Row>
          ))}
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={() => addItem('education')}
            disabled={profile.education.length >= 10}
          >
            {t('candidate.addEducation')}
          </Button>
        </Col>
      </Row>

      <Divider />

      {!confirmed && (
        <Space>
          <Button
            type="primary"
            disabled={!profile.basic.name.trim()}
            loading={confirmMut.isPending}
            onClick={() => confirmMut.mutate()}
          >
            {t('candidate.confirmAndArrange')}
          </Button>
          {!profile.basic.name.trim() && (
            <Text type="secondary">{t('candidate.confirmNeedName')}</Text>
          )}
        </Space>
      )}
    </Card>
  )
}
