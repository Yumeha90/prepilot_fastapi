import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Select, Typography } from 'antd'
import { useState } from 'react'

import { register } from '@/api/auth'
import { extractErrorCode, extractErrorMessage } from '@/api/client'

const { Title } = Typography

const ROLE_CODES = ['interviewer', 'hr', 'hr_lead', 'admin'] as const

export default function Register() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const onFinish = async (values: {
    email: string
    password: string
    confirm: string
    full_name: string
    role_code: string
  }) => {
    setError('')
    setSubmitting(true)
    try {
      await register({
        email: values.email,
        password: values.password,
        full_name: values.full_name ?? '',
        role_code: values.role_code as (typeof ROLE_CODES)[number],
      })
      navigate('/login', { replace: true })
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
        {t('auth.register')}
      </Title>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      <Form layout="vertical" onFinish={onFinish}>
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
        <Form.Item name="full_name" label={t('auth.name')}>
          <Input />
        </Form.Item>
        <Form.Item
          name="role_code"
          label={t('auth.role')}
          initialValue="interviewer"
          rules={[{ required: true }]}
        >
          <Select
            options={ROLE_CODES.map((code) => ({ value: code, label: t(`role.${code}`) }))}
          />
        </Form.Item>
        <Form.Item
          name="password"
          label={t('auth.password')}
          rules={[
            { required: true, message: t('auth.passwordRequired') },
            { min: 8, message: t('auth.passwordMin') },
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm"
          label={t('auth.confirmPassword')}
          dependencies={['password']}
          rules={[
            { required: true, message: t('auth.passwordRequired') },
            ({ getFieldValue }) => ({
              validator: (_rule, value) =>
                !value || value === getFieldValue('password')
                  ? Promise.resolve()
                  : Promise.reject(new Error(t('auth.passwordNotMatch'))),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          {t('auth.register')}
        </Button>
      </Form>

      <div style={{ marginTop: 16 }}>
        <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/login')}>
          {t('auth.goLogin')}
        </Button>
      </div>
    </Card>
  )
}
