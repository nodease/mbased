import { apiClient } from '@/lib/apiClient';
import type {
  AuditLogDetailResponse,
  AuditLogListResponse,
  AuditLogSearchFilters,
} from '../types/AdminAudit';
import type {
  AppCreationPermissionListResponse,
  AppCreationPermissionRevokeResponse,
  PermissionRequestItem,
  PermissionRequestListResponse,
  PermissionRequestStatus,
} from '../types/AdminPermissionRequest';
import type {
  AdminOrganizationSummary,
  AdminWorkflowUsageResponse,
} from '../types/AdminUsage';
import type {
  ResolveSecurityAlertInput,
  SecurityAlertAuditLogListResponse,
  SecurityAlertDetail,
  SecurityAlertListParams,
  SecurityAlertListResponse,
  SecurityAlertSummaryResponse,
} from '../types/SecurityAlert';

export type AuditLogListParams = AuditLogSearchFilters & {
  cursor?: string;
  limit?: number;
};

const compactParams = (params: Record<string, unknown>) =>
  Object.fromEntries(
    Object.entries(params).filter(
      ([, value]) => value !== undefined && value !== null && value !== '',
    ),
  );

export const adminApi = {
  listAuditLogs: async (
    params: AuditLogListParams = {},
  ): Promise<AuditLogListResponse> => {
    const response = await apiClient.get('/admin/audit-logs', {
      params: compactParams(params),
    });
    return response.data;
  },

  getAuditLogDetail: async (
    auditLogId: string,
  ): Promise<AuditLogDetailResponse> => {
    const response = await apiClient.get(`/admin/audit-logs/${auditLogId}`);
    return response.data;
  },

  listPermissionRequests: async (params: {
    status?: PermissionRequestStatus;
    page?: number;
    limit?: number;
  } = {}): Promise<PermissionRequestListResponse> => {
    const response = await apiClient.get('/admin/permission-requests', {
      params: compactParams(params),
    });
    return response.data;
  },

  approvePermissionRequest: async (
    requestId: string,
  ): Promise<PermissionRequestItem> => {
    const response = await apiClient.post(
      `/admin/permission-requests/${requestId}/approve`,
    );
    return response.data;
  },

  rejectPermissionRequest: async (
    requestId: string,
  ): Promise<PermissionRequestItem> => {
    const response = await apiClient.post(
      `/admin/permission-requests/${requestId}/reject`,
    );
    return response.data;
  },

  listAppCreationPermissions: async (params: {
    page?: number;
    limit?: number;
  } = {}): Promise<AppCreationPermissionListResponse> => {
    const response = await apiClient.get('/admin/app-creation-permissions', {
      params: compactParams(params),
    });
    return response.data;
  },

  revokeAppCreationPermission: async (
    permissionId: string,
  ): Promise<AppCreationPermissionRevokeResponse> => {
    const response = await apiClient.delete(
      `/admin/app-creation-permissions/${permissionId}`,
    );
    return response.data;
  },

  listWorkflowUsage: async (params: {
    page?: number;
    limit?: number;
    startAt?: string;
    endAt?: string;
  } = {}): Promise<AdminWorkflowUsageResponse> => {
    const response = await apiClient.get('/admin/usage/workflows', {
      params: compactParams(params),
    });
    return response.data;
  },

  getOrganizationSummary: async (): Promise<AdminOrganizationSummary> => {
    const response = await apiClient.get('/admin/summary');
    return response.data;
  },

  listSecurityAlerts: async (
    params: SecurityAlertListParams = {},
  ): Promise<SecurityAlertListResponse> => {
    const response = await apiClient.get('/admin/security-alerts', {
      params: compactParams(params),
    });
    return response.data;
  },

  getSecurityAlertSummary:
    async (): Promise<SecurityAlertSummaryResponse> => {
      const response = await apiClient.get('/admin/security-alerts/summary');
      return response.data;
    },

  getSecurityAlertDetail: async (
    alertId: string,
  ): Promise<SecurityAlertDetail> => {
    const response = await apiClient.get(`/admin/security-alerts/${alertId}`);
    return response.data;
  },

  listSecurityAlertAuditLogs: async (
    alertId: string,
    params: { page?: number; limit?: number } = {},
  ): Promise<SecurityAlertAuditLogListResponse> => {
    const response = await apiClient.get(
      `/admin/security-alerts/${alertId}/audit-logs`,
      { params: compactParams(params) },
    );
    return response.data;
  },

  acknowledgeSecurityAlert: async (
    alertId: string,
    expectedVersion: number,
  ): Promise<SecurityAlertDetail> => {
    const response = await apiClient.post(
      `/admin/security-alerts/${alertId}/acknowledge`,
      { expected_version: expectedVersion },
    );
    return response.data;
  },

  reopenSecurityAlert: async (
    alertId: string,
    expectedVersion: number,
  ): Promise<SecurityAlertDetail> => {
    const response = await apiClient.post(
      `/admin/security-alerts/${alertId}/reopen`,
      { expected_version: expectedVersion },
    );
    return response.data;
  },

  resolveSecurityAlert: async (
    alertId: string,
    input: ResolveSecurityAlertInput,
  ): Promise<SecurityAlertDetail> => {
    const response = await apiClient.post(
      `/admin/security-alerts/${alertId}/resolve`,
      {
        expected_version: input.expectedVersion,
        resolution_type: input.resolutionType,
        reason: input.reason,
      },
    );
    return response.data;
  },
};
