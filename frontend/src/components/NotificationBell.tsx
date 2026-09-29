import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Empty, List, Popover, Space, Tag, Typography } from 'antd'
import { BellOutlined, CheckOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/api/me'
import { useAuthStore } from '@/store/auth'

const { Text } = Typography

const TYPE_COLOR: Record<string, string> = {
  assignment: 'blue',
  stage_changed: 'orange',
  evaluation_submitted: 'green',
  system: 'default',
}

export default function NotificationBell() {
  const { t } = useTranslation()
  const unreadCount = useAuthStore((s) => s.unreadCount)
  const refreshMe = useAuthStore((s) => s.refreshMe)
  const queryClient = useQueryClient()

  const { data = [], isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => fetchNotifications(false, 10),
  })

  const readOne = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['notifications'] })
      await refreshMe()
    },
  })

  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['notifications'] })
      await refreshMe()
    },
  })

  const content = (
    <div style={{ width: 360 }}>
      <Space style={{ justifyContent: 'space-between', width: '100%', marginBottom: 8 }}>
        <Text strong>{t('notification.title')}</Text>
        <Button
          type="link"
          size="small"
          icon={<CheckOutlined />}
          loading={readAll.isPending}
          onClick={() => readAll.mutate()}
        >
          {t('notification.markAllRead')}
        </Button>
      </Space>
      {!isLoading && data.length === 0 ? (
        <Empty description={t('notification.empty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <List
          size="small"
          loading={isLoading}
          dataSource={data}
          renderItem={(item) => (
            <List.Item
              style={{ cursor: item.is_read ? 'default' : 'pointer' }}
              onClick={() => {
                if (!item.is_read) readOne.mutate(item.id)
              }}
            >
              <Space direction="vertical" size={2} style={{ width: '100%' }}>
                <Space>
                  <Tag color={TYPE_COLOR[item.type] ?? 'default'}>
                    {t(`notification.type.${item.type}`)}
                  </Tag>
                  <Text strong={!item.is_read}>{item.title}</Text>
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {item.body}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}
                </Text>
              </Space>
            </List.Item>
          )}
        />
      )}
    </div>
  )

  return (
    <Popover content={content} trigger="click" placement="bottomRight">
      <Badge count={unreadCount} size="small" offset={[-2, 2]}>
        <BellOutlined style={{ fontSize: 18, color: '#fff', cursor: 'pointer' }} />
      </Badge>
    </Popover>
  )
}
