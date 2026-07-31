export type PermissionRequestStatus = 'pending' | 'approved' | 'rejected';

export type PermissionRequestUser = {
  id: string;
  name: string;
  email: string;
};

export type PermissionRequestItem = {
  id: string;
  user: PermissionRequestUser | null;
  requested_permission: string;
  reason: string;
  status: PermissionRequestStatus;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
};

export type PermissionRequestListResponse = {
  total: number;
  items: PermissionRequestItem[];
};

export type AppCreationPermissionItem = {
  id: string;
  user: PermissionRequestUser | null;
  assigned_by: string;
  assigned_at: string;
};

export type AppCreationPermissionListResponse = {
  total: number;
  items: AppCreationPermissionItem[];
};

export type AppCreationPermissionRevokeResponse = {
  id: string;
  user_id: string;
};
