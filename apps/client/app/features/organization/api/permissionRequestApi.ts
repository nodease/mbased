import { apiClient } from '@/lib/apiClient';

export const APP_CREATE_PERMISSION = 'app.create';

export type PermissionRequestStatus = 'pending' | 'approved' | 'rejected';

export interface PermissionRequestResponse {
  id: string;
  user: { id: string; name: string; email: string } | null;
  requested_permission: string;
  reason: string;
  status: PermissionRequestStatus;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
}

export interface PermissionRequestSubmitPayload {
  reason: string;
  requested_permission?: typeof APP_CREATE_PERMISSION;
}

/**
 * POST /permission-requests의 409 응답을 구분한다 (ORG-REQ-050).
 * 서버는 envelope 없이 `{"detail": <string>}` 형태로 반환한다.
 */
export type PermissionRequestConflict = 'already-granted' | 'already-pending';

export function resolvePermissionRequestConflict(
  error: unknown,
): PermissionRequestConflict | null {
  const response = (error as { response?: { status?: number; data?: unknown } })
    ?.response;
  if (response?.status !== 409) return null;
  const detail = (response.data as { detail?: unknown } | undefined)?.detail;
  if (detail === 'App creation permission already granted') {
    return 'already-granted';
  }
  if (detail === 'Pending permission request already exists') {
    return 'already-pending';
  }
  return null;
}

export const permissionRequestApi = {
  submitPermissionRequest: async (
    payload: PermissionRequestSubmitPayload,
  ): Promise<PermissionRequestResponse> => {
    const response = await apiClient.post('/permission-requests', {
      requested_permission:
        payload.requested_permission ?? APP_CREATE_PERMISSION,
      reason: payload.reason,
    });
    return response.data;
  },
};
