import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Avatar, Button, Dropdown, Form, Input, Modal, Space, Typography, message } from 'antd'
import { LogoutOutlined, UserOutlined } from '@ant-design/icons'
import { useState } from 'react'

import { changePassword, updateMe } from '@/api/auth'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { useUIStore } from '@/store/ui'
import type { LanguageCode } from '@/i18n/config'
import { SUPPORTED_LANGUAGES } from '@/i18n/config'

const { Text } = Typography

export default function UserMenu() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const refreshMe = useAuthStore((s) => s.refreshMe)
  const setLanguage = useUIStore((s) => s.setLanguage)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const queryClient = useQueryClient()

  const saveMutation = useMutation({
    mutationFn: async (values: { full_name: string; locale: string; old_password?: string; new_password?: string }) => {
      await updateMe({ full_name: values.full_name, locale: values.locale })
      if (values.old_password && values.new_password) {
        await changePassword(values.old_password, values.new_password)
      }
    },
    onSuccess: async (_data, values) => {
      if (SUPPORTED_LANGUAGES.some((l) => l.code === values.locale)) {
        setLanguage(values.locale as LanguageCode)
      }
      await refreshMe()
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      message.success(t('profile.saved'))
      setOpen(false)
    },
    onError: (error) => {
      message.error(extractErrorMessage(error, t('common.error')))
    },
  })

  if (!user) return null

  const menuItems = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: t('user.profile'),
      onClick: () => {
        form.setFieldsValue({
          full_name: user.full_name,
          locale: user.locale,
          old_password: '',
          new_password: '',
        })
        setOpen(true)
      },
    },
    { type: 'divider' as const },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: t('user.logout'),
      onClick: () => void logout(),
    },
  ]

  return (
    <>
      <Dropdown menu={{ items: menuItems }} placement="bottomRight">
        <Space style={{ cursor: 'pointer', color: '#fff' }}>
          <Avatar size="small" src={user.avatar_url || undefined} icon={<UserOutlined />} />
          <span>{user.full_name || user.email}</span>
        </Space>
      </Dropdown>

      <Modal
        open={open}
        title={t('user.profile')}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
      >
        <Space direction="vertical" size="small" style={{ marginBottom: 12 }}>
          <Text type="secondary">{user.email}</Text>
          <Text>{t('role.current')}：{t(`role.${user.role_code}`)}</Text>
        </Space>
        <Form form={form} layout="vertical" onFinish={(v) => saveMutation.mutate(v)}>
          <Form.Item name="full_name" label={t('user.name')} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="locale" label={t('user.language')}>
            <Space>
              {SUPPORTED_LANGUAGES.map((l) => (
                <Button
                  key={l.code}
                  size="small"
                  type={user.locale === l.code ? 'primary' : 'default'}
                  onClick={() => {
                    form.setFieldValue('locale', l.code)
                    setLanguage(l.code)
                  }}
                >
                  {l.label}
                </Button>
              ))}
            </Space>
          </Form.Item>
          <Form.Item name="old_password" label={t('user.oldPassword')}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="new_password" label={t('user.newPassword')}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
