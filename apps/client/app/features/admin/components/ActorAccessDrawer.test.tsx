import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../../organization/api/organizationApi', () => ({
  organizationApi: {
    executeMemberAccessAction: vi.fn(),
    getMemberAccessProfile: vi.fn(),
    listMemberResourceAccess: vi.fn(),
    listMemberTeamMemberships: vi.fn(),
  },
}));

import { organizationApi } from '../../organization/api/organizationApi';
import type {
  MemberAccessProfile,
  MemberResourceAccess,
} from '../types/ActorAccess';
import { ActorAccessDrawer } from './ActorAccessDrawer';

const mockedProfile = vi.mocked(organizationApi.getMemberAccessProfile);
const mockedTeams = vi.mocked(organizationApi.listMemberTeamMemberships);
const mockedResources = vi.mocked(organizationApi.listMemberResourceAccess);
const mockedAction = vi.mocked(organizationApi.executeMemberAccessAction);

const allowed = { allowed: true, reason: null };
const profile: MemberAccessProfile = {
  member: {
    membership_id: 'membership-1',
    user_id: 'user-1',
    name: '김멤버',
    email: 'member@example.com',
    user_active: true,
    membership_state: 'active',
    organization_auth_state: 'member',
    updated_at: '2026-07-10T00:00:00Z',
  },
  control: {
    is_self: false,
    is_last_active_manager: false,
    manager_override: false,
    actions: {
      membership_suspend: allowed,
      membership_reactivate: {
        allowed: false,
        reason: 'member_state_not_applicable',
      },
      organization_role_set: {
        member: { allowed: false, reason: 'member_state_not_applicable' },
        manager: allowed,
      },
      team_membership_add: allowed,
      team_membership_remove: allowed,
      direct_permission_grant: allowed,
      direct_permission_revoke: allowed,
      app_creation_grant: allowed,
      app_creation_revoke: allowed,
    },
  },
  effective_access_enabled: true,
  team_membership_count: 0,
  app_creation: {
    effective: false,
    effective_source: 'none',
    direct_permission: null,
  },
  permission_counts: { direct: 0, team_inherited: 0 },
};

const directAccess: MemberResourceAccess = {
  resource_type: 'workflow',
  resource_id: 'workflow-1',
  resource_name: 'Workflow',
  effective_auth_state: 'operator',
  direct_permission: {
    permission_id: 'permission-1',
    auth_state: 'operator',
    assigned_at: '2026-07-10T00:00:00Z',
  },
  team_sources: [],
};

const renderDrawer = (overrideProfile: MemberAccessProfile = profile) => {
  mockedProfile.mockResolvedValue(overrideProfile);
  mockedTeams.mockResolvedValue({ total: 0, items: [] });
  mockedResources.mockResolvedValue({ total: 0, items: [] });
  return render(
    <ActorAccessDrawer
      organizationId="org-1"
      userId="user-1"
      teams={[{ id: 'team-1', name: 'Builders', is_active: true }]}
      resources={[
        { id: 'workflow-1', name: 'Workflow', resourceType: 'workflow' },
      ]}
      onClose={vi.fn()}
    />,
  );
};

afterEach(() => {
  vi.resetAllMocks();
});

describe('ActorAccessDrawer', () => {
  it('호출 화면이 지정한 돌아가기 action을 표시한다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockResolvedValue({ total: 0, items: [] });
    mockedResources.mockResolvedValue({ total: 0, items: [] });
    const onClose = vi.fn();

    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[]}
        resources={[]}
        returnLabel="보안 알림 상세로 돌아가기"
        onClose={onClose}
      />,
    );

    await screen.findByText('김멤버 · member@example.com');
    expect(
      screen.getByRole('dialog', { name: '행위자 접근 관리' }),
    ).toHaveClass(
      'max-w-4xl',
      '[&_.text-xs]:text-sm',
      '[&_.text-sm]:text-base',
      '[&_.text-base]:text-lg',
      '[&_.text-lg]:text-xl',
    );
    fireEvent.click(
      screen.getByRole('button', { name: '보안 알림 상세로 돌아가기' }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('멤버십 요약 상태를 한글로 표시한다', async () => {
    renderDrawer({
      ...profile,
      member: {
        ...profile.member,
        membership_state: 'suspended',
      },
      effective_access_enabled: false,
    });

    await screen.findByText('김멤버 · member@example.com');
    expect(screen.getByText('상태').parentElement).toHaveTextContent('상태정지');
    expect(screen.getByText('조직 역할').parentElement).toHaveTextContent(
      '조직 역할멤버',
    );
    expect(screen.getByText('계정').parentElement).toHaveTextContent('계정활성');
    expect(screen.getByText('유효 접근').parentElement).toHaveTextContent(
      '유효 접근차단',
    );
  });

  it('profile 확인 후 team과 현재 resource tab을 bounded page로 조회한다', async () => {
    renderDrawer();

    expect(await screen.findByText('김멤버 · member@example.com')).toBeInTheDocument();
    expect(mockedProfile).toHaveBeenCalledWith('org-1', 'user-1');
    expect(mockedTeams).toHaveBeenCalledWith('org-1', 'user-1', {
      page: 1,
      limit: 20,
    });
    expect(mockedResources).toHaveBeenCalledTimes(1);
    expect(mockedResources).toHaveBeenCalledWith('org-1', 'user-1', {
      resourceType: 'workflow',
      source: 'all',
      page: 1,
      limit: 20,
    });
  });

  it('team/resource page와 source filter를 독립적으로 갱신한다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockImplementation(
      async (_organizationId, _userId, params = {}) => ({
        total: 21,
        items: params.page === 2 ? [] : [],
      }),
    );
    mockedResources.mockImplementation(
      async (_organizationId, _userId, params) => ({
        total: params.resourceId ? 0 : 21,
        items: [],
      }),
    );
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[]}
        resources={[]}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText('김멤버 · member@example.com');
    const nextButtons = screen.getAllByRole('button', { name: '다음' });
    fireEvent.click(nextButtons[0]);
    await waitFor(() =>
      expect(mockedTeams).toHaveBeenCalledWith('org-1', 'user-1', {
        page: 2,
        limit: 20,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'direct' }));
    await waitFor(() =>
      expect(mockedResources).toHaveBeenCalledWith('org-1', 'user-1', {
        resourceType: 'workflow',
        source: 'direct',
        page: 1,
        limit: 20,
      }),
    );
  });

  it('선택 resource를 exact filter로 확인하고 existing row precondition으로 변경한다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockResolvedValue({ total: 0, items: [] });
    mockedResources.mockImplementation(
      async (_organizationId, _userId, params) =>
        params.resourceId
          ? { total: 1, items: [directAccess] }
          : { total: 1, items: [directAccess] },
    );
    mockedAction.mockResolvedValue({
      status: 'applied',
      action: 'direct_permission.grant',
      target_type: 'user_workflow_permission',
      target_id: 'permission-1',
      effective_access_changed: true,
      affected_resource_source_count: null,
    });
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[]}
        resources={[
          { id: 'workflow-1', name: 'Workflow', resourceType: 'workflow' },
        ]}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText('김멤버 · member@example.com');
    fireEvent.change(screen.getByLabelText('직접 권한 resource'), {
      target: { value: 'workflow-1' },
    });
    await waitFor(() =>
      expect(mockedResources).toHaveBeenCalledWith('org-1', 'user-1', {
        resourceType: 'workflow',
        resourceId: 'workflow-1',
        source: 'all',
        page: 1,
        limit: 1,
      }),
    );
    fireEvent.change(screen.getByLabelText('직접 권한 상태'), {
      target: { value: 'builder' },
    });
    fireEvent.click(await screen.findByRole('button', { name: '변경' }));
    expect(screen.getByText('operator → builder')).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole('dialog', { name: '직접 권한 변경' })).getByRole(
        'button',
        { name: '확인' },
      ),
    );

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith(
        'org-1',
        'user-1',
        expect.objectContaining({
          action: 'direct_permission.grant',
          expected_permission_id: 'permission-1',
          expected_auth_state: 'operator',
          auth_state: 'builder',
        }),
      ),
    );
  });

  it('다른 page에 존재하는 team membership을 exact filter로 확인해 중복 추가를 막는다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockImplementation(
      async (_organizationId, _userId, params = {}) =>
        params.teamId
          ? {
              total: 1,
              items: [
                {
                  team_membership_id: 'team-membership-1',
                  team_id: 'team-1',
                  name: 'Builders',
                  is_active: true,
                  assigned_at: '2026-07-10T00:00:00Z',
                  inherited_resource_counts: {
                    workflow: 1,
                    knowledge_base: 0,
                    llm_credential: 0,
                    total: 1,
                  },
                },
              ],
            }
          : { total: 21, items: [] },
    );
    mockedResources.mockResolvedValue({ total: 0, items: [] });
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[{ id: 'team-1', name: 'Builders', is_active: true }]}
        resources={[]}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText('김멤버 · member@example.com');
    fireEvent.change(screen.getByLabelText('추가할 팀'), {
      target: { value: 'team-1' },
    });

    await waitFor(() =>
      expect(mockedTeams).toHaveBeenCalledWith('org-1', 'user-1', {
        teamId: 'team-1',
        page: 1,
        limit: 1,
      }),
    );
    expect(await screen.findByText(/이미 소속된 팀입니다/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^추가:/ })).toBeDisabled();
  });

  it('team/resource exact 조회 실패 시 absence precondition action을 차단한다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockImplementation(
      async (_organizationId, _userId, params = {}) => {
        if (params.teamId) throw new Error('team lookup failed');
        return { total: 0, items: [] };
      },
    );
    mockedResources.mockImplementation(
      async (_organizationId, _userId, params) => {
        if (params.resourceId) throw new Error('resource lookup failed');
        return { total: 0, items: [] };
      },
    );
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[{ id: 'team-1', name: 'Builders', is_active: true }]}
        resources={[
          { id: 'workflow-1', name: 'Workflow', resourceType: 'workflow' },
        ]}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText('김멤버 · member@example.com');
    fireEvent.change(screen.getByLabelText('추가할 팀'), {
      target: { value: 'team-1' },
    });
    expect(
      await screen.findByText('선택한 팀 소속의 최신 상태를 확인하지 못했습니다.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^추가:/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('직접 권한 resource'), {
      target: { value: 'workflow-1' },
    });
    expect(
      await screen.findByText(
        '선택한 Resource의 최신 접근 상태를 확인하지 못했습니다.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^부여:/ })).toBeDisabled();
    expect(mockedAction).not.toHaveBeenCalled();
  });

  it('confirm reason을 정규화하고 profile snapshot precondition을 전송한다', async () => {
    mockedAction.mockResolvedValue({
      status: 'applied',
      action: 'membership.suspend',
      target_type: 'organization_membership',
      target_id: 'membership-1',
      effective_access_changed: true,
      affected_resource_source_count: null,
    });
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.change(within(confirm).getByLabelText('접근 변경 사유'), {
      target: { value: '  운영\r\n검토  ' },
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith('org-1', 'user-1', {
        action: 'membership.suspend',
        expected_membership_id: 'membership-1',
        expected_user_active: true,
        expected_membership_state: 'active',
        expected_organization_auth_state: 'member',
        reason: '운영\n검토',
      }),
    );
    expect(await screen.findByText('접근 설정을 변경했습니다.')).toBeInTheDocument();
  });

  it('역할 승격은 desired role과 common snapshot을 전송한다', async () => {
    mockedAction.mockResolvedValue({
      status: 'applied',
      action: 'organization_role.set',
      target_type: 'organization_membership',
      target_id: 'membership-1',
      effective_access_changed: true,
      affected_resource_source_count: null,
    });
    renderDrawer();

    fireEvent.click(
      await screen.findByRole('button', { name: '관리자로 승격' }),
    );
    const confirm = screen.getByRole('dialog', {
      name: '조직 관리자로 승격',
    });
    expect(within(confirm).getByText('member → manager')).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith(
        'org-1',
        'user-1',
        expect.objectContaining({
          action: 'organization_role.set',
          role: 'manager',
          expected_organization_auth_state: 'member',
        }),
      ),
    );
  });

  it('team remove는 row id와 source impact를 confirm에 고정한다', async () => {
    mockedProfile.mockResolvedValue(profile);
    mockedTeams.mockResolvedValue({
      total: 1,
      items: [
        {
          team_membership_id: 'team-membership-1',
          team_id: 'team-1',
          name: 'Builders',
          is_active: true,
          assigned_at: '2026-07-10T00:00:00Z',
          inherited_resource_counts: {
            workflow: 2,
            knowledge_base: 1,
            llm_credential: 0,
            total: 3,
          },
        },
      ],
    });
    mockedResources.mockResolvedValue({ total: 0, items: [] });
    mockedAction.mockResolvedValue({
      status: 'applied',
      action: 'team_membership.remove',
      target_type: 'team_membership',
      target_id: 'team-membership-1',
      effective_access_changed: null,
      affected_resource_source_count: 3,
    });
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[]}
        resources={[]}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '제거' }));
    const confirm = screen.getByRole('dialog', { name: '팀 소속 제거' });
    expect(within(confirm).getByText(/총 3/)).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith(
        'org-1',
        'user-1',
        expect.objectContaining({
          action: 'team_membership.remove',
          team_id: 'team-1',
          expected_team_membership_id: 'team-membership-1',
        }),
      ),
    );
    expect(await screen.findByText(/관련 source 3개/)).toBeInTheDocument();
  });

  it('App creation revoke는 permission row id를 precondition으로 전송한다', async () => {
    const withAppPermission: MemberAccessProfile = {
      ...profile,
      app_creation: {
        effective: true,
        effective_source: 'direct',
        direct_permission: {
          permission_id: 'app-permission-1',
          assigned_at: '2026-07-10T00:00:00Z',
        },
      },
    };
    mockedAction.mockResolvedValue({
      status: 'applied',
      action: 'app_creation.revoke',
      target_type: 'user_app_creation_permission',
      target_id: 'app-permission-1',
      effective_access_changed: true,
      affected_resource_source_count: null,
    });
    renderDrawer(withAppPermission);

    fireEvent.click(
      await screen.findByRole('button', { name: '직접 권한 회수' }),
    );
    const confirm = screen.getByRole('dialog', {
      name: 'App 생성 직접 권한 회수',
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith(
        'org-1',
        'user-1',
        expect.objectContaining({
          action: 'app_creation.revoke',
          expected_permission_id: 'app-permission-1',
        }),
      ),
    );
  });

  it('500 Unicode code point를 넘는 reason은 API 호출 전에 거부한다', async () => {
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.change(within(confirm).getByLabelText('접근 변경 사유'), {
      target: { value: '😀'.repeat(501) },
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    expect(
      await within(confirm).findByText('사유는 500자 이하여야 합니다.'),
    ).toBeInTheDocument();
    expect(mockedAction).not.toHaveBeenCalled();
  });

  it('blank reason은 null이며 bidi control은 API 호출 전에 거부한다', async () => {
    mockedAction.mockResolvedValue({
      status: 'unchanged',
      action: 'membership.suspend',
      target_type: 'organization_membership',
      target_id: 'membership-1',
      effective_access_changed: false,
      affected_resource_source_count: null,
    });
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    let confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.change(within(confirm).getByLabelText('접근 변경 사유'), {
      target: { value: '   ' },
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));
    await waitFor(() =>
      expect(mockedAction).toHaveBeenLastCalledWith(
        'org-1',
        'user-1',
        expect.objectContaining({ reason: null }),
      ),
    );

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.change(within(confirm).getByLabelText('접근 변경 사유'), {
      target: { value: 'review\u202Ehidden' },
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));
    expect(
      await within(confirm).findByText(/허용되지 않는 제어 문자/),
    ).toBeInTheDocument();
    expect(mockedAction).toHaveBeenCalledTimes(1);
  });

  it('stale 409는 자동 재시도하지 않고 최신 profile을 다시 조회한다', async () => {
    const error = new AxiosError('Conflict');
    error.response = {
      status: 409,
      data: { error: { code: 'stale_state' } },
    } as AxiosResponse;
    mockedAction.mockRejectedValue(error);
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '멤버십 정지' }),
      ).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText(/접근 상태가 변경되었습니다/),
    ).toBeInTheDocument();
    await waitFor(() => expect(mockedProfile).toHaveBeenCalledTimes(2));
    expect(mockedAction).toHaveBeenCalledTimes(1);
  });

  it('audit persistence 500은 confirm을 유지하고 성공 상태로 반영하지 않는다', async () => {
    const error = new AxiosError('Audit failed');
    error.response = {
      status: 500,
      data: { error: { code: 'audit.persistence_failed' } },
    } as AxiosResponse;
    mockedAction.mockRejectedValue(error);
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    fireEvent.click(within(confirm).getByRole('button', { name: '확인' }));

    expect(
      await within(confirm).findByText(/감사 기록을 저장하지 못해/),
    ).toBeInTheDocument();
    expect(confirm).toBeInTheDocument();
    expect(mockedProfile).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('접근 설정을 변경했습니다.')).not.toBeInTheDocument();
  });

  it('confirm은 초기 focus, Shift+Tab trap, ESC 취소와 trigger focus 복귀를 제공한다', async () => {
    renderDrawer();

    const trigger = await screen.findByRole('button', { name: '정지' });
    trigger.focus();
    fireEvent.click(trigger);
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    const reasonInput = within(confirm).getByLabelText('접근 변경 사유');
    expect(reasonInput).toHaveFocus();

    fireEvent.keyDown(reasonInput, { key: 'Tab', shiftKey: true });
    expect(within(confirm).getByRole('button', { name: '확인' })).toHaveFocus();
    fireEvent.keyDown(confirm, { key: 'Escape' });

    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '멤버십 정지' }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('pending action은 중복 제출을 차단한다', async () => {
    let resolveAction!: (value: Awaited<ReturnType<typeof organizationApi.executeMemberAccessAction>>) => void;
    mockedAction.mockImplementation(
      () => new Promise((resolve) => {
        resolveAction = resolve;
      }),
    );
    renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: '정지' }));
    const confirm = screen.getByRole('dialog', { name: '멤버십 정지' });
    const submit = within(confirm).getByRole('button', { name: '확인' });
    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(mockedAction).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
    resolveAction({
      status: 'applied',
      action: 'membership.suspend',
      target_type: 'organization_membership',
      target_id: 'membership-1',
      effective_access_changed: true,
      affected_resource_source_count: null,
    });
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '멤버십 정지' }),
      ).not.toBeInTheDocument(),
    );
  });

  it('profile 403은 source를 조회하지 않고 권한 오류와 retry 상태를 표시한다', async () => {
    const error = new AxiosError('Forbidden');
    error.response = { status: 403 } as AxiosResponse;
    mockedProfile.mockRejectedValue(error);
    render(
      <ActorAccessDrawer
        organizationId="org-1"
        userId="user-1"
        teams={[]}
        resources={[]}
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByText('행위자 접근 관리 권한이 없습니다.'),
    ).toBeInTheDocument();
    expect(mockedTeams).not.toHaveBeenCalled();
    expect(mockedResources).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument();
  });

  it('server control이 막은 action은 disabled이며 confirm을 열지 않는다', async () => {
    renderDrawer({
      ...profile,
      control: {
        ...profile.control,
        actions: {
          ...profile.control.actions,
          membership_suspend: {
            allowed: false,
            reason: 'last_active_manager',
          },
        },
      },
    });

    const suspend = await screen.findByRole('button', { name: /^정지:/ });
    expect(suspend).toBeDisabled();
    fireEvent.click(suspend);
    expect(
      screen.queryByRole('dialog', { name: '멤버십 정지' }),
    ).not.toBeInTheDocument();
  });
});
