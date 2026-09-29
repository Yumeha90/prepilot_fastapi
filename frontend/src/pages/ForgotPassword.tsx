import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Steps, Typography, message } from 'antd'
import { useState } from 'react'

import { forgotPassword, resetPassword } from '@/api/auth'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Title, Text } = Typography

export default function ForgotPassword() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const showError = (err: unknown) => {
    const code = extractErrorCode(err)
    setError(code ? t(`errors.${code}`, { defaultValue: extractErrorMessage(err) }) : extractErrorMessage(err))
  }

  const sendCode = async (values: { email: string }) => {
    setError('')
    setNotice('')
    setSubmitting(true)
    try {
      const res = await forgotPassword(values.email)
      setEmail(values.email)
      setNotice(res.dev_code ? `${res.message}（${t('auth.devCode')}：${res.dev_code}）` : res.message)
      setStep(1)
    } catch (err) {
      showError(err)
    } finally {
      setSubmitting(false)
    }
  }

  const submitReset = async (values: { code: string; password: string }) => {
    setError('')
    setSubmitting(true)
    try {
      await resetPassword(email, values.code, values.password)
      message.success(t('auth.resetSuccess'))
      navigate('/login', { replace: true })
    } catch (err) {
      showError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <Title level={4} style={{ marginTop: 0 }}>
        {t('auth.forgotPassword')}
      </Title>
      <Steps
        size="small"
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: t('auth.stepEmail') }, { title: t('auth.stepReset') }]}
      />

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      {notice && <Alert type="success" showIcon message={notice} style={{ marginBottom: 16 }} />}

      {step === 0 ? (
        <Form layout="vertical" onFinish={sendCode}>
          <Form.Item
            name="email"
            label={t('auth.email')}
            rules={[
              { required: true, message: t('auth.emailRequired') },
              { type: 'email', message: t('auth.emailInvalid') },
            ]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {t('auth.sendCode')}
          </Button>
        </Form>
      ) : (
        <Form layout="vertical" onFinish={submitReset}>
          <Form.Item label={t('auth.email')}>
            <Text strong>{email}</Text>
          </Form.Item>
          <Form.Item name="code" label={t('auth.code')} rules={[{ required: true, len: 6 }]}>
            <Input maxLength={6} />
          </Form.Item>
          <Form.Item
            name="password"
            label={t('auth.newPassword')}
            rules={[
              { required: true, message: t('auth.passwordRequired') },
              { min: 8, message: t('auth.passwordMin') },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {t('auth.resetPassword')}
          </Button>
        </Form>
      )}

      <div style={{ marginTop: 16 }}>
        <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/login')}>
          {t('auth.goLogin')}
        </Button>
      </div>
    </Card>
  )
}
