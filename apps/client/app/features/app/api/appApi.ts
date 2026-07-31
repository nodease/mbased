import type {
  DeploymentBrowserAccessPolicy,
  DeploymentBrowserAccessRevisionCreate,
  DeploymentType,
} from '../../workflow/types/Deployment';
import { apiClient as api, publicApiClient } from '@/lib/apiClient';
import type { BudgetStatusPayload } from '../../budget/types';

export interface AppIcon {
  type: string;
  content: string;
  background_color: string;
}

export interface App {
  id: string;
  name: string;
  description?: string;
  icon: AppIcon;
  url_slug?: string;
  is_market: boolean;
  forked_from?: string;
  workflow_id?: string;
  active_deployment_id?: string;
  active_deployment_type?: DeploymentType;
  active_deployment_is_active?: boolean;
  budget_status?: BudgetStatusPayload | null;
  owner_name?: string;
  created_at: string;
  updated_at: string;
}

export interface AppAuthSecretStatus {
  configured: boolean;
  version: number;
  rotation_enabled: boolean;
  rotated_at?: string | null;
  previous_grace_active: boolean;
  previous_valid_until?: string | null;
}

export interface AppAuthSecretRotation {
  secret: string;
  version: number;
  rotated_at: string;
  previous_grace_active: boolean;
  previous_valid_until?: string | null;
}

export interface Deployment {
  id: string;
  app_id: string;
  version: number;
  type: DeploymentType;
  url_slug?: string;
  description?: string;
  is_active: boolean;
  created_at: string;
  created_by: string;
  graph_snapshot: unknown;
  input_schema?: unknown;
  output_schema?: unknown;
  config?: unknown;
  browser_access_policy?: DeploymentBrowserAccessPolicy | null;
}

export const appApi = {
  // 앱 목록 조회
  listApps: async (): Promise<App[]> => {
    const response = await api.get('/apps');
    return response.data;
  },

  // 탐색 페이지 (공개 앱) 조회
  getExploreApps: async (): Promise<App[]> => {
    const response = await publicApiClient.get('/apps/explore');
    return response.data;
  },

  // 앱 생성
  createApp: async (data: {
    name: string;
    description?: string;
    icon: AppIcon;
    is_market?: boolean;
  }): Promise<App> => {
    const response = await api.post('/apps', data);
    return response.data;
  },

  // 앱 상세 조회
  getApp: async (appId: string): Promise<App> => {
    const response = await api.get(`/apps/${appId}`);
    return response.data;
  },

  getAuthSecretStatus: async (
    appId: string,
  ): Promise<AppAuthSecretStatus> => {
    const response = await api.get(`/apps/${appId}/auth-secret/status`);
    return response.data;
  },

  rotateAuthSecret: async (
    appId: string,
    data: {
      expected_version: number;
      revoke_previous_immediately: boolean;
    },
  ): Promise<AppAuthSecretRotation> => {
    const response = await api.post(
      `/apps/${appId}/auth-secret/rotate`,
      data,
    );
    return response.data;
  },

  // 앱 복제
  cloneApp: async (appId: string): Promise<App> => {
    const response = await api.post(`/apps/${appId}/clone`);
    return response.data;
  },

  // 앱 수정
  updateApp: async (
    appId: string,
    data: {
      name?: string;
      description?: string;
      icon?: AppIcon;
      is_market?: boolean;
    },
  ): Promise<App> => {
    const response = await api.patch(`/apps/${appId}`, data);
    return response.data;
  },

  // 앱 삭제
  deleteApp: async (appId: string): Promise<void> => {
    await api.delete(`/apps/${appId}`);
  },

  // 배포 목록 조회
  getDeployments: async (appId: string): Promise<Deployment[]> => {
    const response = await api.get('/deployments', {
      params: { app_id: appId },
    });
    return response.data;
  },

  // 배포 토글 (활성화/비활성화)
  toggleDeployment: async (deploymentId: string): Promise<Deployment> => {
    const response = await api.patch(`/deployments/${deploymentId}/toggle`);
    return response.data;
  },

  createBrowserAccessRevision: async (
    sourceDeploymentId: string,
    data: DeploymentBrowserAccessRevisionCreate,
  ): Promise<Deployment> => {
    const response = await api.post(
      `/deployments/${sourceDeploymentId}/browser-access-revisions`,
      data,
    );
    return response.data;
  },
};
