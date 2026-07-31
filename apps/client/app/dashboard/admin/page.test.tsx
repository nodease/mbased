import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigationMock = vi.hoisted(() => ({
  searchParams: 'tab=organization-structure&view=members',
  push: vi.fn(),
  replace: vi.fn(),
}));

const apiClientMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

const organizationApiMock = vi.hoisted(() => ({
  getCurrentOrganization: vi.fn(),
  listMembers: vi.fn(),
}));

const adminSummaryCardsMock = vi.hoisted(() => vi.fn());

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard/admin',
  useRouter: () => ({
    push: navigationMock.push,
    replace: navigationMock.replace,
  }),
  useSearchParams: () => new URLSearchParams(navigationMock.searchParams),
}));

vi.mock('@/lib/apiClient', () => ({ apiClient: apiClientMock }));

vi.mock('@/app/features/auth/api/authApi', () => ({
  authApi: {
    me: vi.fn().mockResolvedValue({ user: { id: 'user-1' } }),
  },
}));

vi.mock('@/app/features/organization/api/organizationApi', () => ({
  organizationApi: organizationApiMock,
}));

vi.mock('@/app/features/workflow/api/mailCredentialApi', () => ({
  mailCredentialApi: { listAvailable: vi.fn().mockResolvedValue([]) },
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: { getKnowledgeBases: vi.fn().mockResolvedValue([]) },
}));

vi.mock('@/app/features/admin/components/AdminSummaryCards', () => ({
  AdminSummaryCards: (props: unknown) => {
    adminSummaryCardsMock(props);
    return null;
  },
}));

import { PermissionsTab } from '@/app/features/admin/components/PermissionsTab';
import AdminConsolePage from './page';

describe('AdminConsolePage route contract', () => {
  it('page.tsx에서 테스트용 컴포넌트를 named export하지 않는다', () => {
    const pageSource = readFileSync(resolve(__dirname, 'page.tsx'), 'utf8');

    expect(pageSource).not.toMatch(/export\s+function\s+PermissionsTab\b/);
  });
});

const member = {
  id: 'membership-1',
  organization_id: 'org-1',
  user_id: 'user-1',
  user_email: 'member@example.com',
  user_name: '김멤버',
  membership_state: 'active',
  organization_auth_state: 'manager',
  invited_at: '2026-07-01T00:00:00Z',
  accepted_at: '2026-07-01T00:00:00Z',
  suspended_at: null,
  removed_at: null,
  current_month_usage: {
    total_cost: 0,
    workflow_execution_cost: 0,
    agent_builder_cost: 0,
    usage_data_complete: true,
    unresolved_provider_call_count: 0,
  },
};

const team = {
  id: 'team-1',
  organization_id: 'org-1',
  name: '개발팀',
  description: '제품 개발',
  is_active: true,
};

const members = Array.from({ length: 21 }, (_, index) => ({
  ...member,
  id: `membership-${index + 1}`,
  user_id: `user-${index + 1}`,
  user_email: `member-${index + 1}@example.com`,
  user_name: `김멤버${index + 1}`,
}));

const teams = Array.from({ length: 21 }, (_, index) => ({
  ...team,
  id: `team-${index + 1}`,
  name: `개발팀${index + 1}`,
}));

describe('AdminConsolePage 조직 구성 상태 보존', () => {
  beforeEach(() => {
    navigationMock.searchParams = 'tab=organization-structure&view=members';
    navigationMock.push.mockReset();
    navigationMock.replace.mockReset();
    adminSummaryCardsMock.mockClear();
    apiClientMock.get.mockReset();
    apiClientMock.post.mockReset();
    organizationApiMock.getCurrentOrganization.mockResolvedValue({
      id: 'org-1',
      name: 'Nodease',
      is_manager: true,
    });
    organizationApiMock.listMembers.mockImplementation(
      (_organizationId: string, state?: string) =>
        Promise.resolve(state === 'removed' ? [] : members),
    );
    apiClientMock.get.mockImplementation((path: string) => {
      if (path === '/teams') return Promise.resolve({ data: teams });
      return Promise.resolve({ data: [] });
    });
  });

  it('보기를 전환해도 멤버와 팀의 검색·필터·페이지 상태를 각각 유지한다', async () => {
    const { rerender } = render(<AdminConsolePage />);

    const memberSearch = await screen.findByPlaceholderText('이름, email 검색');
    fireEvent.change(memberSearch, { target: { value: '멤버' } });
    fireEvent.change(screen.getByDisplayValue('전체 상태'), {
      target: { value: 'active' },
    });
    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    expect(screen.getByText('21개 중 page 2/2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '초대' })).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText('팀 이름, 설명 검색'),
    ).not.toBeInTheDocument();

    navigationMock.searchParams = 'tab=organization-structure&view=teams';
    rerender(<AdminConsolePage />);

    const teamSearch = await screen.findByPlaceholderText('팀 이름, 설명 검색');
    fireEvent.change(teamSearch, { target: { value: '팀' } });
    fireEvent.change(screen.getByDisplayValue('전체 상태'), {
      target: { value: 'active' },
    });
    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    expect(screen.getByText('21개 중 page 2/2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '팀 생성' })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('이름, email 검색')).not.toBeInTheDocument();

    navigationMock.searchParams = 'tab=organization-structure&view=members';
    rerender(<AdminConsolePage />);

    await waitFor(() =>
      expect(screen.getByPlaceholderText('이름, email 검색')).toHaveValue(
        '멤버',
      ),
    );
    expect(screen.getByDisplayValue('활성')).toBeInTheDocument();
    expect(screen.getByText('21개 중 page 2/2')).toBeInTheDocument();

    navigationMock.searchParams = 'tab=organization-structure&view=teams';
    rerender(<AdminConsolePage />);

    await waitFor(() =>
      expect(screen.getByPlaceholderText('팀 이름, 설명 검색')).toHaveValue(
        '팀',
      ),
    );
    expect(screen.getByDisplayValue('활성')).toBeInTheDocument();
    expect(screen.getByText('21개 중 page 2/2')).toBeInTheDocument();
  });

  it('일반 멤버에게 조직 구성과 관리 데이터를 노출하지 않는다', async () => {
    organizationApiMock.getCurrentOrganization.mockResolvedValue({
      id: 'org-1',
      name: 'Nodease',
      is_manager: false,
    });

    render(<AdminConsolePage />);

    expect(await screen.findByText('관리 권한 없음')).toBeInTheDocument();
    const title = screen.getByRole('heading', { level: 1, name: '관리' });
    expect(title.firstElementChild).toHaveClass('lucide-shield-check');
    expect(
      screen.queryByRole('group', { name: '조직 구성 보기' }),
    ).not.toBeInTheDocument();
    expect(apiClientMock.get).not.toHaveBeenCalled();
  });

  it('관리자 탭에 조직 설정 메뉴를 표시하지 않는다', async () => {
    render(<AdminConsolePage />);

    expect(
      await screen.findByRole('button', { name: '조직 구성' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '조직 설정' }),
    ).not.toBeInTheDocument();
  });

  it('선택한 상위 탭을 파란 글자와 파란 밑줄로 표시한다', async () => {
    render(<AdminConsolePage />);

    const organizationTab = await screen.findByRole('button', {
      name: '조직 구성',
    });
    const permissionsTab = screen.getByRole('button', { name: '권한' });
    const usageTab = screen.getByRole('button', { name: '비용' });

    expect(organizationTab).toHaveClass('border-blue-600', 'text-blue-600');
    expect(permissionsTab).toHaveClass(
      'border-transparent',
      'text-slate-500',
    );
    expect(usageTab).toHaveClass('border-transparent', 'text-slate-500');
  });

  it('한 줄 요약 카드에 현재 조직 지표를 전달한다', async () => {
    render(<AdminConsolePage />);

    await screen.findByRole('button', { name: '조직 구성' });

    expect(adminSummaryCardsMock).toHaveBeenCalledWith({
      members: { active: 21, invited: 0, suspended: 0, removed: 0 },
      teams: { active: 21, assignments: 0 },
      credentials: { active: 0, providers: 0 },
      knowledgeBases: 0,
    });
  });

  it('renders current month member usage in descending cost order', async () => {
    organizationApiMock.listMembers.mockImplementation(
      (_organizationId: string, state?: string) =>
        Promise.resolve(
          state === 'removed'
            ? []
            : [
                {
                  ...member,
                  id: 'membership-low',
                  user_id: 'user-low',
                  user_name: 'Low cost',
                  current_month_usage: {
                    total_cost: 1.25,
                    workflow_execution_cost: 1.25,
                    agent_builder_cost: 0,
                    usage_data_complete: true,
                    unresolved_provider_call_count: 0,
                  },
                },
                {
                  ...member,
                  id: 'membership-high',
                  user_id: 'user-high',
                  user_name: 'High cost',
                  current_month_usage: {
                    total_cost: 5,
                    workflow_execution_cost: 2,
                    agent_builder_cost: 3,
                    usage_data_complete: false,
                    unresolved_provider_call_count: 1,
                  },
                },
              ],
        ),
    );

    render(<AdminConsolePage />);

    expect(await screen.findByText('비용 (USD) ↓')).toBeInTheDocument();
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(within(rows[1]).getByText('High cost')).toBeInTheDocument();
    expect(within(rows[1]).getByText('$5.00')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Agent Builder $3.00')).toBeInTheDocument();
    expect(within(rows[1]).getByText('미확정 1건')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Low cost')).toBeInTheDocument();
    expect(within(rows[2]).getByText('$1.25')).toBeInTheDocument();
  });

  it('다중 선택 권한을 bulk grant API로 한 번 전송한다', async () => {
    navigationMock.searchParams = 'tab=permissions';
    apiClientMock.post.mockResolvedValue({ data: { grant_count: 4 } });
    apiClientMock.get.mockImplementation((path: string) => {
      if (path === '/teams') {
        return Promise.resolve({
          data: [team, { ...team, id: 'team-2', name: '운영팀' }],
        });
      }
      if (path === '/apps') {
        return Promise.resolve({
          data: [
            { id: 'app-1', name: '업무 자동화', workflow_id: 'workflow-1' },
            { id: 'app-2', name: '문서 질문', workflow_id: 'workflow-2' },
          ],
        });
      }
      if (path.startsWith('/permissions/workflows/')) {
        const resourceId = path.split('/').at(-1);
        return Promise.resolve({
          data: {
            resource_type: 'workflow',
            resource_id: resourceId,
            organization_id: 'org-1',
            team_permissions: [],
            user_permissions: [],
          },
        });
      }
      if (
        path === '/admin/permission-requests' ||
        path === '/admin/app-creation-permissions'
      ) {
        return Promise.resolve({ data: { total: 0, items: [] } });
      }
      return Promise.resolve({ data: [] });
    });

    render(<AdminConsolePage />);

    fireEvent.click(
      await screen.findByRole('button', { name: '권한 부여' }),
    );
    fireEvent.click(
      screen.getByRole('checkbox', { name: '문서 질문 선택' }),
    );
    fireEvent.click(
      screen.getByRole('checkbox', { name: '운영팀 선택' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 권한 부여' }),
    );

    await waitFor(() =>
      expect(apiClientMock.post).toHaveBeenCalledWith(
        '/permissions/bulk-grants',
        {
          resource_type: 'workflow',
          resource_ids: ['workflow-1', 'workflow-2'],
          grantee_type: 'team',
          grantee_ids: ['team-1', 'team-2'],
          auth_state: 'viewer',
        },
      ),
    );
    expect(apiClientMock.post).toHaveBeenCalledTimes(1);
  });
});

describe('PermissionsTab 표 기반 권한 부여', () => {
  const renderPermissionsTab = (overrides = {}) => {
    const onGrant = vi.fn();
    const onSelectResource = vi.fn();

    render(
      <PermissionsTab
        resourceType="workflow"
        workflowOptions={[
          {
            id: 'app-1',
            name: 'Enterprise 고객 티켓 처리',
            workflow_id: 'workflow-1',
          },
          {
            id: 'app-2',
            name: '사내 문서 질문 응답 봇',
            workflow_id: 'workflow-2',
          },
        ]}
        selectedWorkflowId="workflow-1"
        knowledgeBases={[]}
        selectedKnowledgeBaseId=""
        credentials={[]}
        selectedCredentialId=""
        mailCredentials={[]}
        selectedMailCredentialId=""
        activeTeams={[team]}
        activeMembers={[]}
        granteeType="team"
        granteeId="team-1"
        authState="viewer"
        permissionList={{
          resource_type: 'workflow',
          resource_id: 'workflow-1',
          organization_id: 'org-1',
          team_permissions: [],
          user_permissions: [],
        }}
        actionPending={false}
        onSelectResource={onSelectResource}
        onGrant={onGrant}
        onRevoke={vi.fn()}
        {...overrides}
      />,
    );

    return { onGrant, onSelectResource };
  };

  it('권한 부여 버튼으로 표 선택 모달을 열고 저장한다', async () => {
    const { onGrant } = renderPermissionsTab();

    expect(
      screen.queryByRole('dialog', { name: '리소스 권한 부여' }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '권한 부여' })).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: '리소스·권한 변경' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '권한 부여' }));

    const dialog = screen.getByRole('dialog', { name: '리소스 권한 부여' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '권한 대상 리소스' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '권한 부여 대상' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '리소스 목록' })).toHaveClass(
      'max-h-[500px]',
      'overflow-auto',
    );
    expect(screen.getByRole('region', { name: '부여 대상 목록' })).toHaveClass(
      'max-h-[500px]',
      'overflow-auto',
    );
    expect(
      within(screen.getByRole('table', { name: '권한 대상 리소스' }))
        .getAllByRole('rowgroup')[0],
    ).toHaveClass('sticky');
    expect(
      within(screen.getByRole('table', { name: '권한 부여 대상' }))
        .getAllByRole('rowgroup')[0],
    ).toHaveClass('sticky');

    fireEvent.click(screen.getByRole('button', { name: '선택한 권한 부여' }));
    await waitFor(() =>
      expect(onGrant).toHaveBeenCalledWith({
        resourceType: 'workflow',
        resourceIds: ['workflow-1'],
        granteeType: 'team',
        granteeIds: ['team-1'],
        authState: 'viewer',
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '리소스 권한 부여' }),
      ).not.toBeInTheDocument(),
    );
  });

  it('복수 리소스와 복수 대상을 checkbox로 선택해 한 번에 제출한다', async () => {
    const secondTeam = {
      ...team,
      id: 'team-2',
      name: '운영팀',
    };
    const { onGrant } = renderPermissionsTab({
      activeTeams: [team, secondTeam],
    });

    fireEvent.click(screen.getByRole('button', { name: '권한 부여' }));
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: '사내 문서 질문 응답 봇 선택',
      }),
    );
    fireEvent.click(
      screen.getByRole('checkbox', { name: '운영팀 선택' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 권한 부여' }),
    );

    await waitFor(() =>
      expect(onGrant).toHaveBeenCalledWith({
        resourceType: 'workflow',
        resourceIds: ['workflow-1', 'workflow-2'],
        granteeType: 'team',
        granteeIds: ['team-1', 'team-2'],
        authState: 'viewer',
      }),
    );
  });

  it('모달 선택과 취소는 페이지의 조회 리소스를 바꾸지 않는다', () => {
    const { onGrant } = renderPermissionsTab();
    fireEvent.click(screen.getByRole('button', { name: '권한 부여' }));

    fireEvent.change(screen.getByRole('searchbox', { name: '리소스 검색' }), {
      target: { value: '사내 문서' },
    });
    const resourceTable = screen.getByRole('table', {
      name: '권한 대상 리소스',
    });
    expect(
      within(resourceTable).queryByText('Enterprise 고객 티켓 처리'),
    ).not.toBeInTheDocument();
    expect(
      within(resourceTable).getByText('사내 문서 질문 응답 봇'),
    ).toBeVisible();

    fireEvent.click(
      screen.getByRole('checkbox', { name: '사내 문서 질문 응답 봇 선택' }),
    );
    expect(onGrant).not.toHaveBeenCalled();
    expect(screen.getByText('Enterprise 고객 티켓 처리')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '취소' }));
    expect(
      screen.queryByRole('dialog', { name: '리소스 권한 부여' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Enterprise 고객 티켓 처리')).toBeVisible();
  });

  it.each([
    ['리소스', '리소스 검색'],
    ['부여 대상', '대상 검색'],
  ])(
    '검색으로 선택한 %s가 숨겨지면 권한을 저장하지 않는다',
    (_selection, searchName) => {
      const { onGrant } = renderPermissionsTab();
      fireEvent.click(screen.getByRole('button', { name: '권한 부여' }));

      fireEvent.change(screen.getByRole('searchbox', { name: searchName }), {
        target: { value: '검색 결과 없음' },
      });

      const submitButton = screen.getByRole('button', {
        name: '선택한 권한 부여',
      });
      expect(submitButton).toBeDisabled();
      fireEvent.click(submitButton);
      expect(onGrant).not.toHaveBeenCalled();
    },
  );

  it('권한 저장 중에는 모든 닫기 경로와 선택 입력을 잠근다', () => {
    renderPermissionsTab({ actionPending: true });
    fireEvent.click(screen.getByRole('button', { name: '권한 부여' }));

    const dialog = screen.getByRole('dialog', { name: '리소스 권한 부여' });
    const backdrop = dialog.previousElementSibling;
    expect(dialog).toHaveAttribute('aria-busy', 'true');
    expect(backdrop).not.toBeNull();

    fireEvent.click(backdrop!);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: '권한 부여 닫기' }));
    fireEvent.click(screen.getByRole('button', { name: '취소' }));

    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '권한 부여 닫기' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '취소' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '저장 중...' })).toBeDisabled();
    within(dialog)
      .getAllByRole('searchbox')
      .forEach((input) => expect(input).toBeDisabled());
    within(dialog)
      .getAllByRole('combobox')
      .forEach((select) => expect(select).toBeDisabled());
    within(dialog)
      .getAllByRole('radio')
      .forEach((radio) => expect(radio).toBeDisabled());
    within(dialog)
      .getAllByRole('checkbox')
      .forEach((checkbox) => expect(checkbox).toBeDisabled());
  });

  it('본문 리소스 필터로 저장 없이 권한 조회 대상을 바꾼다', () => {
    const { onGrant, onSelectResource } = renderPermissionsTab();

    fireEvent.click(screen.getByRole('button', { name: '리소스 필터 변경' }));
    fireEvent.change(
      screen.getByRole('searchbox', { name: '조회 리소스 검색' }),
      { target: { value: '사내 문서' } },
    );
    fireEvent.click(
      screen.getByRole('option', { name: '사내 문서 질문 응답 봇' }),
    );

    expect(onSelectResource).toHaveBeenCalledWith('workflow', 'workflow-2');
    expect(onGrant).not.toHaveBeenCalled();
  });

  it('새 리소스 응답을 기다리는 동안 이전 리소스의 회수 버튼을 숨긴다', () => {
    renderPermissionsTab({
      selectedWorkflowId: 'workflow-2',
      permissionList: {
        resource_type: 'workflow',
        resource_id: 'workflow-1',
        organization_id: 'org-1',
        team_permissions: [
          {
            id: 'permission-1',
            grantee_type: 'team',
            grantee_id: 'team-1',
            grantee_name: '개발팀',
            auth_state: 'viewer',
            assigned_at: '2026-07-18T00:00:00Z',
          },
        ],
        user_permissions: [],
      },
    });

    expect(screen.queryByRole('button', { name: '회수' })).not.toBeInTheDocument();
    expect(screen.queryByText('개발팀')).not.toBeInTheDocument();
  });
});
