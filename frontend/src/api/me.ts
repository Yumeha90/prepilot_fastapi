import apiClient from './client'

export interface NotificationItem {
  id: number
  type: string
  title: string
  body: string
  payload_json: string
  is_read: boolean
  created_at: string
}

export interface SubscriptionItem {
  event_type: string
  enabled: boolean
}

export async function fetchNotifications(unread = false, limit = 20): Promise<NotificationItem[]> {
  const { data } = await apiClient.get<NotificationItem[]>('/api/me/notifications', {
    params: { unread, limit },
  })
  return data
}

export async function markNotificationRead(id: number): Promise<void> {
  await apiClient.post(`/api/me/notifications/${id}/read`)
}

export async function markAllNotificationsRead(): Promise<void> {
  await apiClient.post('/api/me/notifications/read-all')
}

export async function fetchSubscriptions(): Promise<SubscriptionItem[]> {
  const { data } = await apiClient.get<SubscriptionItem[]>('/api/me/notification-subscriptions')
  return data
}

export async function updateSubscriptions(items: SubscriptionItem[]): Promise<SubscriptionItem[]> {
  const { data } = await apiClient.put<SubscriptionItem[]>('/api/me/notification-subscriptions', {
    items,
  })
  return data
}
