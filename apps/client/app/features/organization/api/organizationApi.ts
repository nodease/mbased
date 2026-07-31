import { apiClient, publicApiClient } from '@/lib/apiClient';
import { activeOrganizationHeaders } from '@/lib/activeOrganization';

import type {
  MembershipState,
  OrganizationMember,
  OrganizationMemberListItem,
  OrganizationMemberInviteRequest,
  OrganizationMemberRemoveResponse,
  OrganizationMemberUpdateRequest,
  OrganizationResponse,
  OrganizationSummary,
  PermissionRequestCreateRequest,
  PermissionRequestResponse,
} from '../types/Organization';
import type {
  ActorResourceType,
  MemberAccessAction,
  MemberAccessActionResponse,
  MemberAccessProfile,
  MemberResourceAccessList,
  MemberTeamMembershipList,
} from '../../admin/types/ActorAccess';

export const organizationApi = {
  listOrganizations: async (): Promise<OrganizationResponse[]> => {
    const response = await apiClient.get('/organizations');
    return response.data;
  },

  listMemberships: async (): Promise<OrganizationSummary[]> => {
    const response = await apiClient.get('/organizations/memberships');
    return response.data;
  },

  getCurrentOrganization: async (): Promise<OrganizationResponse> => {
    const response = await apiClient.get('/organizations/current');
    return response.data;
  },

  listMembers: async (
    organizationId: string,
    state?: MembershipState,
  ): Promise<OrganizationMemberListItem[]> => {
    const response = await apiClient.get(
      `/organizations/${organizationId}/members`,
      {
        headers: activeOrganizationHeaders(organizationId),
        params: state ? { state } : undefined,
      },
    );
    return response.data;
  },

  inviteMember: async (
    organizationId: string,
    payload: OrganizationMemberInviteRequest,
  ): Promise<OrganizationMember> => {
    const response = await apiClient.post(
      `/organizations/${organizationId}/members/invitations`,
      payload,
      { headers: activeOrganizationHeaders(organizationId) },
    );
    return response.data;
  },

  acceptInvitation: async (
    organizationId: string,
  ): Promise<OrganizationMember> => {
    const response = await publicApiClient.post(
      `/organizations/${organizationId}/members/me/accept`,
    );
    return response.data;
  },

  declineInvitation: async (
    organizationId: string,
  ): Promise<OrganizationMember> => {
    const response = await publicApiClient.post(
      `/organizations/${organizationId}/members/me/decline`,
    );
    return response.data;
  },

  updateMember: async (
    organizationId: string,
    userId: string,
    payload: OrganizationMemberUpdateRequest,
  ): Promise<OrganizationMember> => {
    const response = await apiClient.patch(
      `/organizations/${organizationId}/members/${userId}`,
      payload,
      { headers: activeOrganizationHeaders(organizationId) },
    );
    return response.data;
  },

  removeMember: async (
    organizationId: string,
    userId: string,
  ): Promise<OrganizationMemberRemoveResponse> => {
    const response = await apiClient.delete(
      `/organizations/${organizationId}/members/${userId}`,
      { headers: activeOrganizationHeaders(organizationId) },
    );
    return response.data;
  },

  getMemberAccessProfile: async (
    organizationId: string,
    userId: string,
  ): Promise<MemberAccessProfile> => {
    const response = await apiClient.get(
      `/organizations/${organizationId}/members/${userId}/access-profile`,
      { headers: activeOrganizationHeaders(organizationId) },
    );
    return response.data;
  },

  listMemberTeamMemberships: async (
    organizationId: string,
    userId: string,
    params: { teamId?: string; page?: number; limit?: number } = {},
  ): Promise<MemberTeamMembershipList> => {
    const response = await apiClient.get(
      `/organizations/${organizationId}/members/${userId}/team-memberships`,
      { headers: activeOrganizationHeaders(organizationId), params },
    );
    return response.data;
  },

  listMemberResourceAccess: async (
    organizationId: string,
    userId: string,
    params: {
      resourceType: ActorResourceType;
      resourceId?: string;
      source?: 'all' | 'direct' | 'team';
      page?: number;
      limit?: number;
    },
  ): Promise<MemberResourceAccessList> => {
    const response = await apiClient.get(
      `/organizations/${organizationId}/members/${userId}/resource-access`,
      { headers: activeOrganizationHeaders(organizationId), params },
    );
    return response.data;
  },

  executeMemberAccessAction: async (
    organizationId: string,
    userId: string,
    payload: MemberAccessAction,
  ): Promise<MemberAccessActionResponse> => {
    const response = await apiClient.post(
      `/organizations/${organizationId}/members/${userId}/access-actions`,
      payload,
      { headers: activeOrganizationHeaders(organizationId) },
    );
    return response.data;
  },

  submitPermissionRequest: async (
    payload: PermissionRequestCreateRequest,
  ): Promise<PermissionRequestResponse> => {
    const response = await apiClient.post('/permission-requests', {
      requested_permission: 'app.create',
      ...payload,
    });
    return response.data;
  },
};
