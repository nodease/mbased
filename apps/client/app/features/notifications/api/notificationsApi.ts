import { apiBaseUrl, apiClient } from '@/lib/apiClient';
import type { NotificationListResponse } from '../types/Notification';

export const NOTIFICATIONS_REFRESH_EVENT = 'nodease-notifications-refresh';

export const notificationsApi = {
  listNotifications: async (): Promise<NotificationListResponse> => {
    const response = await apiClient.get('/notifications');
    return response.data;
  },

  createEventSource: () =>
    new EventSource(`${apiBaseUrl}/notifications/stream`, {
      withCredentials: true,
    }),
};
