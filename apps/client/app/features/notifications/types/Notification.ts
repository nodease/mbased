export type NotificationType = 'organization.invitation';

export type NotificationItem = {
  id: string;
  type: NotificationType;
  organization_id: string;
  organization_name: string;
  organization_auth_state: 'member' | 'manager';
  created_at: string;
};

export type NotificationListResponse = {
  items: NotificationItem[];
};
