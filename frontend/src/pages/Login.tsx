import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, MailOutlined } from '@ant-design/icons'
import { useState } from 'react'

import { extractErrorCode, extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/store/auth'

const { Title } = Typography

export default function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const onFinish = async (values: { email: string; password: string }) => {
    setError('')
    setSubmitting(true)
    try {
      await login(values.email, values.password)
      navigate('/', { replace: true })
    } catch (err) {
      const code = extractErrorCode(err)
      setError(code ? t(`errors.${code}`, { defaultValue: extractErrorMessage(err) }) : extractErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <Title level={4} style={{ marginTop: 0 }}>
        {t('auth.login')}
      </Title>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="email"
          label={t('auth.email')}
          rules={[
            { required: true, message: t('auth.emailRequired') },
            { type: 'email', message: t('auth.emailInvalid') },
          ]}
        >
          <Input prefix={<MailOutlined />} autoComplete="username" placeholder="admin@prepilot.dev" />
        </Form.Item>
        <Form.Item
          name="password"
          label={t('auth.password')}
          rules={[{ required: true, message: t('auth.passwordRequired') }]}
        >
          <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          {t('auth.login')}
        </Button>
      </Form>

      <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/register')}>
          {t('auth.goRegister')}
        </Button>
        <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/forgot-password')}>
          {t('auth.forgotPassword')}
        </Button>
      </div>
    </Card>
  )
}
