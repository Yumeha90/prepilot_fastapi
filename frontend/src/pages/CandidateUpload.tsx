/**
 * P04 简历上传与授权（PRD 3.3.1 / §5.6）。
 *
 * 口径：
 * - D1 上传必须选职位，一步建候选人 + 应聘记录
 * - BR-09 未勾选《数据处理授权》拦截上传
 * - 职位下拉只列 JD 已确认的（否则 S4 没有权重可算）；
 *   面试官可选范围天然收窄为被指派职位（后端数据范围已过滤）
 * - 文件抽取完即丢弃，不落库、不进 COS（D13）
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Steps,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'

import { createCandidate } from '@/api/candidates'
import { extractResume } from '@/api/resume'
import { fetchPositions } from '@/api/positions'
import { extractErrorCode } from '@/api/client'
import Forbidden from '@/pages/Forbidden'
import { useAuthStore } from '@/store/auth'

const { Text, Title, Paragraph } = Typography

export default function CandidateUpload() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  // 该页不在侧边栏菜单里，RoutePermGuard 拦不到，权限自行校验
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canUpload = hasPerm('candidate:upload_resume')

  const [positionId, setPositionId] = useState<number | undefined>(undefined)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [rawText, setRawText] = useState('')
  const [fileName, setFileName] = useState('')
  const [authTick, setAuthTick] = useState(false)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [extracting, setExtracting] = useState(false)

  const errMsg = (error: unknown) => {
    const code = extractErrorCode(error)
    return t(`errors.${code}`, { defaultValue: t('common.error') })
  }

  const { data: posData, isLoading: posLoading } = useQuery({
    queryKey: ['positions', 'for-upload'],
    queryFn: () => fetchPositions({ page_size: 100 }),
  })

  // JD 未确认的职位不能选：没有权重就没法算匹配分（S4）
  const options = (posData?.items ?? [])
    .filter((p) => p.jd_status === 'confirmed')
    .map((p) => ({ value: p.id, label: p.name }))

  const createMut = useMutation({
    mutationFn: () =>
      createCandidate({
        position_id: positionId as number,
        name: name.trim(),
        contact_email: email.trim(),
        contact_phone: phone.trim(),
        source: fileName ? 'upload' : 'paste',
        raw_text: rawText,
        file_name: fileName,
        auth_tick: authTick,
      }),
    onSuccess: (d) => {
      message.success(t('candidate.msg.uploaded'))
      navigate(`/candidates/${d.id}/parse`)
    },
    onError: (e) => message.error(errMsg(e)),
  })

  const onUpload = async (file: File) => {
    setExtracting(true)
    try {
      const out = await extractResume(file)
      setRawText(out.text)
      setFileName(out.filename)
      message.success(t('candidate.msg.extracted', { n: out.text.length }))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setExtracting(false)
    }
    return false // 阻止 antd 默认上传行为
  }

  const canSubmit = Boolean(positionId) && authTick && rawText.trim().length >= 30 && email.trim()

  if (!canUpload) return <Forbidden />

  return (
    <Card title={t('candidate.uploadTitle')}>
      <Steps
        size="small"
        current={0}
        style={{ marginBottom: 24 }}
        items={[
          { title: t('candidate.step.upload') },
          { title: t('candidate.step.parse') },
          { title: t('candidate.step.confirm') },
        ]}
      />

      <Alert type="warning" showIcon message={t('candidate.authRequiredHint')} style={{ marginBottom: 16 }} />

      <Row gutter={16}>
        <Col span={12}>
          <Text strong>{t('candidate.positionRequired')}</Text>
          <Select
            style={{ width: '100%', marginTop: 8 }}
            loading={posLoading}
            placeholder={t('candidate.positionPlaceholder')}
            value={positionId}
            onChange={setPositionId}
            options={options}
            notFoundContent={t('candidate.noSelectablePosition')}
          />
        </Col>
      </Row>

      <Divider />

      <Row gutter={16}>
        <Col span={8}>
          <Text type="secondary">{t('candidate.name')}</Text>
          <Input
            style={{ marginTop: 8 }}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('candidate.namePlaceholder')}
          />
        </Col>
        <Col span={8}>
          <Text type="secondary">
            {t('candidate.email')} <Text type="danger">*</Text>
          </Text>
          <Input
            style={{ marginTop: 8 }}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
          />
        </Col>
        <Col span={8}>
          <Text type="secondary">{t('candidate.phone')}</Text>
          <Input
            style={{ marginTop: 8 }}
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </Col>
      </Row>

      <Divider />

      <Upload.Dragger
        accept=".pdf,.docx,.txt,.md"
        maxCount={1}
        showUploadList={false}
        beforeUpload={onUpload}
        disabled={extracting}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">{t('candidate.uploadHint')}</p>
        <p className="ant-upload-hint">{t('candidate.uploadTypes')}</p>
      </Upload.Dragger>
      {fileName && (
        <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
          {t('candidate.extractedFile', { name: fileName, n: rawText.length })}
        </Text>
      )}

      <Divider>{t('candidate.orPaste')}</Divider>
      <Input.TextArea
        rows={8}
        value={rawText}
        onChange={(e) => {
          setRawText(e.target.value)
          setFileName('')
        }}
        placeholder={t('candidate.pastePlaceholder')}
      />

      <Divider />

      <Space align="start">
        <Checkbox
          checked={authTick}
          onChange={(e) => {
            if (e.target.checked) setAuthModalOpen(true)
            else setAuthTick(false)
          }}
        >
          {t('candidate.authCheckbox')}
        </Checkbox>
        <Button type="link" size="small" onClick={() => setAuthModalOpen(true)}>
          {t('candidate.authReadFull')}
        </Button>
      </Space>

      <div style={{ marginTop: 24 }}>
        <Space>
          <Button
            type="primary"
            disabled={!canSubmit}
            loading={createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {t('candidate.nextStep')}
          </Button>
          <Button onClick={() => navigate('/candidates')}>{t('position.backToList')}</Button>
        </Space>
        {!canSubmit && (
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
            {t('candidate.submitHint')}
          </Text>
        )}
      </div>

      <Modal
        open={authModalOpen}
        title={t('candidate.authTitle')}
        width={640}
        onCancel={() => setAuthModalOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setAuthModalOpen(false)}>{t('common.cancel')}</Button>
            <Button
              type="primary"
              onClick={() => {
                setAuthTick(true)
                setAuthModalOpen(false)
                message.success(t('candidate.msg.authAccepted'))
              }}
            >
              {t('candidate.authAgree')}
            </Button>
          </Space>
        }
      >
        <Title level={5}>{t('candidate.authTitle')}</Title>
        {[1, 2, 3, 4].map((i) => (
          <Paragraph key={i}>{t(`candidate.authBody${i}`)}</Paragraph>
        ))}
      </Modal>
    </Card>
  )
}
