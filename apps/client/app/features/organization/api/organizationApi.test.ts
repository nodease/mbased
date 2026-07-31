import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/activeOrganization', () => ({
  activeOrganizationHeaders: vi.fn((organizationId: string | null) =>
    organizationId ? { 'X-Organization-Id': organizationId } : {},
  ),
}));

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
  publicApiClient: {
    post: vi.fn(),
  },
}));

import { apiClient } from '@/lib/apiClient';
import { publicApiClient } from '@/lib/apiClient';
import { organizationApi } from './organizationApi';

const mockedPost = vi.mocked(apiClient.post);
const mockedGet = vi.mocked(apiClient.get);
const mockedPublicPost = vi.mocked(publicApiClient.post);

afterEach(() => {
  vi.resetAllMocks();
});

describe('organizationApi.submitPermissionRequest', () => {
  it('App 생성 권한 신청 payload를 전송한다', async () => {
    const response = {
      id: 'req-1',
      requested_permission: 'app.create',
      reason: 'workflow 생성이 필요합니다.',
      status: 'pending',
    };
    mockedPost.mockResolvedValueOnce({ data: response });

    const result = await organizationApi.submitPermissionRequest({
      reason: 'workflow 생성이 필요합니다.',
    });

    expect(mockedPost).toHaveBeenCalledWith('/permission-requests', {
      requested_permission: 'app.create',
      reason: 'workflow 생성이 필요합니다.',
    });
    expect(result).toEqual(response);
  });
});

describe('organizationApi.declineInvitation', () => {
  it('현재 사용자의 초대 거절 endpoint를 호출한다', async () => {
    const response = {
      id: 'membership-1',
      organization_id: 'org-1',
      user_id: 'user-1',
      membership_state: 'removed',
    };
    mockedPublicPost.mockResolvedValueOnce({ data: response });

    const result = await organizationApi.declineInvitation('org-1');

    expect(mockedPublicPost).toHaveBeenCalledWith(
      '/organizations/org-1/members/me/decline',
    );
    expect(result).toEqual(response);
  });
});

describe('organizationApi actor access management', () => {
  it('matching organization header로 profile과 resource source를 조회한다', async () => {
    mockedGet
      .mockResolvedValueOnce({ data: { member: { user_id: 'user-1' } } })
      .mockResolvedValueOnce({ data: { total: 0, items: [] } })
      .mockResolvedValueOnce({ data: { total: 0, items: [] } });

    await organizationApi.getMemberAccessProfile('org-1', 'user-1');
    await organizationApi.listMemberTeamMemberships('org-1', 'user-1', {
      teamId: 'team-1',
      page: 1,
      limit: 1,
    });
    await organizationApi.listMemberResourceAccess('org-1', 'user-1', {
      resourceType: 'knowledge_base',
      resourceId: 'kb-1',
      source: 'team',
      page: 2,
      limit: 10,
    });

    expect(mockedGet).toHaveBeenNthCalledWith(
      1,
      '/organizations/org-1/members/user-1/access-profile',
      { headers: { 'X-Organization-Id': 'org-1' } },
    );
    expect(mockedGet).toHaveBeenNthCalledWith(
      2,
      '/organizations/org-1/members/user-1/team-memberships',
      {
        headers: { 'X-Organization-Id': 'org-1' },
        params: { teamId: 'team-1', page: 1, limit: 1 },
      },
    );
    expect(mockedGet).toHaveBeenNthCalledWith(
      3,
      '/organizations/org-1/members/user-1/resource-access',
      {
        headers: { 'X-Organization-Id': 'org-1' },
        params: {
          resourceType: 'knowledge_base',
          resourceId: 'kb-1',
          source: 'team',
          page: 2,
          limit: 10,
        },
      },
    );
  });

  it('단일 access action을 JSON body로 전송한다', async () => {
    mockedPost.mockResolvedValueOnce({
      data: { status: 'applied', action: 'membership.suspend' },
    });
    const payload = {
      action: 'membership.suspend' as const,
      expected_membership_id: 'membership-1',
      expected_user_active: true,
      expected_membership_state: 'active' as const,
      expected_organization_auth_state: 'member' as const,
      reason: 'review',
    };

    await organizationApi.executeMemberAccessAction('org-1', 'user-1', payload);

    expect(mockedPost).toHaveBeenCalledWith(
      '/organizations/org-1/members/user-1/access-actions',
      payload,
      { headers: { 'X-Organization-Id': 'org-1' } },
    );
  });
});
