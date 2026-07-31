'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  Building2,
  CheckCircle,
  Home,
  RefreshCw,
  ShieldCheck,
  Users,
  Workflow,
  Zap,
} from 'lucide-react';
import {
  activeOrganizationHeaders,
  ACTIVE_ORGANIZATION_CHANGED_EVENT,
  getStoredActiveOrganizationId,
  resolveActiveOrganizationId,
  setActiveOrganizationId,
} from '@/lib/activeOrganization';
import {
  DashboardPageHeader,
  DashboardPanel,
  DashboardSummaryCard,
} from '../features/dashboard/components/DashboardSurface';

type OrganizationResponse = {
  id: string;
  name: string;
  is_manager: boolean;
};

type AppResponse = {
  id: string;
  name: string;
  workflow_id?: string;
};

type TeamResponse = {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
};

type TeamMemberResponse = {
  id: string;
  user_id: string;
  email: string;
  name: string;
};

type WorkflowPermissionResponse = {
  workflow_id: string;
  auth_state: string;
  can_read: boolean;
  can_write: boolean;
  can_execute: boolean;
  can_manage: boolean;
};

type WorkflowAccessRow = {
  app: AppResponse;
  permission?: WorkflowPermissionResponse;
  permissionError?: string;
};

const API_BASE_URL = '/api/v1';

async function apiRequest<T>(
  path: string,
  organizationId = getStoredActiveOrganizationId(),
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...activeOrganizationHeaders(organizationId),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      body.error?.message || body.detail || body.message || 'Request failed',
    );
  }
  return body as T;
}

const roleLabel = (isManager?: boolean) => {
  if (isManager === undefined) return '확인 중';
  return isManager ? '관리자' : '멤버';
};

const permissionLabel = (permission?: WorkflowPermissionResponse) => {
  if (!permission) return '확인 필요';

  const labels: Record<string, string> = {
    manager: '관리자',
    builder: '편집 가능',
    operator: '실행 가능',
    viewer: '조회 가능',
    none: '권한 없음',
  };

  return labels[permission.auth_state] || permission.auth_state;
};

const permissionTone = (permission?: WorkflowPermissionResponse) => {
  if (!permission) return 'bg-gray-100 text-gray-700';
  if (permission.can_manage) return 'bg-blue-50 text-blue-700';
  if (permission.can_write) return 'bg-green-50 text-green-700';
  if (permission.can_execute) return 'bg-amber-50 text-amber-700';
  return 'bg-gray-100 text-gray-700';
};

export default function DashboardHomePage() {
  const router = useRouter();
  const [organization, setOrganization] = useState<OrganizationResponse | null>(
    null,
  );
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [teamMembers, setTeamMembers] = useState<
    Record<string, TeamMemberResponse[]>
  >({});
  const [workflowRows, setWorkflowRows] = useState<WorkflowAccessRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeTeams = useMemo(
    () => teams.filter((team) => team.is_active),
    [teams],
  );
  const inactiveTeams = useMemo(
    () => teams.filter((team) => !team.is_active),
    [teams],
  );
  const totalMembers = useMemo(
    () =>
      Object.values(teamMembers).reduce(
        (total, members) => total + members.length,
        0,
      ),
    [teamMembers],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const organizations =
        await apiRequest<OrganizationResponse[]>('/organizations', null);
      const organizationId = resolveActiveOrganizationId(organizations);
      if (!organizationId) {
        throw new Error('현재 선택된 조직이 없습니다.');
      }

      const org = await apiRequest<OrganizationResponse>(
        '/organizations/current',
        organizationId,
      );
      if (getStoredActiveOrganizationId() !== org.id) {
        setActiveOrganizationId(org.id);
      }
      setOrganization(org);

      const apps = await apiRequest<AppResponse[]>('/apps', org.id);
      const rows = await Promise.all(
        apps
          .filter((app) => Boolean(app.workflow_id))
          .map(async (app) => {
            try {
              const permission = await apiRequest<WorkflowPermissionResponse>(
                `/workflows/${app.workflow_id}/permissions/me`,
                org.id,
              );
              return { app, permission };
            } catch (err) {
              return {
                app,
                permissionError:
                  err instanceof Error ? err.message : '권한 조회 실패',
              };
            }
          }),
      );
      setWorkflowRows(rows);

      if (!org.is_manager) {
        setTeams([]);
        setTeamMembers({});
        return;
      }

      const teamData = await apiRequest<TeamResponse[]>(
        `/teams?organization_id=${org.id}`,
        org.id,
      );
      setTeams(teamData);

      const memberEntries = await Promise.all(
        teamData.map(async (team) => {
          const members = await apiRequest<TeamMemberResponse[]>(
            `/teams/${team.id}/members`,
            org.id,
          ).catch(() => []);
          return [team.id, members] as const;
        }),
      );
      setTeamMembers(Object.fromEntries(memberEntries));
    } catch (err) {
      setError(err instanceof Error ? err.message : '홈 데이터를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const handleActiveOrganizationChanged = () => {
      void loadData();
    };
    window.addEventListener(
      ACTIVE_ORGANIZATION_CHANGED_EVENT,
      handleActiveOrganizationChanged,
    );
    return () =>
      window.removeEventListener(
        ACTIVE_ORGANIZATION_CHANGED_EVENT,
        handleActiveOrganizationChanged,
      );
  }, [loadData]);

  return (
    <div className="min-h-full bg-white px-6 py-8">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <DashboardPageHeader
          icon={Home}
          title="Nodease"
          description="AI 워크플로우를 시각화하고 안전하게 제어할 수 있는 서비스"
          badge={
            organization && (
              <span className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs font-medium text-slate-600">
                {roleLabel(organization.is_manager)}
              </span>
            )
          }
          meta={
            <div className="flex min-w-0 items-center gap-2 text-xs text-slate-500">
              <Building2 className="h-3.5 w-3.5 shrink-0 text-blue-600" />
              <span className="truncate">
                {organization?.name || '조직 확인 중'}
              </span>
            </div>
          }
          action={
            <button
              onClick={loadData}
              className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100"
            >
              <RefreshCw className="h-4 w-4" />
              새로고침
            </button>
          }
        />

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className="rounded-lg border border-slate-200 bg-white px-6 py-16 text-center text-sm text-slate-500">
            로딩 중...
          </div>
        ) : (
          <>
            <section className="grid gap-4 md:grid-cols-3">
              <DashboardSummaryCard
                label="현재 조직"
                value={organization?.name || '-'}
                icon={ShieldCheck}
                description="요청과 권한이 적용되는 작업 공간입니다."
              />
              <DashboardSummaryCard
                label="내 조직 역할"
                value={roleLabel(organization?.is_manager)}
                icon={CheckCircle}
                iconClassName="text-green-600"
                description={
                  organization?.is_manager
                    ? '팀과 권한을 관리할 수 있습니다.'
                    : '부여된 워크플로우 권한 안에서 작업합니다.'
                }
              />
              <DashboardSummaryCard
                label="접근 가능한 워크플로우"
                value={`${workflowRows.length}개`}
                icon={Workflow}
                iconClassName="text-violet-600"
                description="내가 조회할 수 있는 워크플로우 기준입니다."
              />
            </section>

            {organization?.is_manager && (
              <section className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
                <div className="rounded-lg border border-slate-200 bg-white p-5">
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                    <Users className="h-4 w-4 text-blue-600" />
                    팀 현황
                  </h2>
                  <div className="mt-4 grid grid-cols-3 gap-3">
                    <div>
                      <p className="text-xs text-slate-500">활성 팀</p>
                      <p className="mt-1 text-xl font-semibold text-slate-950">
                        {activeTeams.length}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500">비활성 팀</p>
                      <p className="mt-1 text-xl font-semibold text-slate-950">
                        {inactiveTeams.length}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500">팀 멤버 수</p>
                      <p className="mt-1 text-xl font-semibold text-slate-950">
                        {totalMembers}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => router.push('/dashboard/admin')}
                    className="mt-5 inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-3 text-sm font-medium text-white hover:bg-slate-800"
                  >
                    조직 접근 관리
                    <ArrowRight className="h-4 w-4" />
                  </button>
                </div>

                <DashboardPanel title="팀 목록">
                  <div className="divide-y divide-slate-100">
                    {teams.length === 0 ? (
                      <div className="px-5 py-8 text-center text-sm text-slate-500">
                        표시할 팀이 없습니다.
                      </div>
                    ) : (
                      teams.map((team) => (
                        <div
                          key={team.id}
                          className="grid gap-3 px-5 py-3 sm:grid-cols-[1fr_auto]"
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="truncate text-sm font-semibold text-slate-950">
                                {team.name}
                              </p>
                              {!team.is_active && (
                                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                                  비활성
                                </span>
                              )}
                            </div>
                            <p className="mt-1 text-xs text-slate-500">
                              {team.description || '설명 없음'}
                            </p>
                          </div>
                          <p className="text-sm text-slate-600">
                            {(teamMembers[team.id] || []).length}명
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </DashboardPanel>
              </section>
            )}

            <DashboardPanel
              title="워크플로우 접근 권한"
              icon={Zap}
              aside={
                !organization?.is_manager && (
                  <span className="text-xs text-slate-500">
                    접근 가능한 워크플로우만 표시됩니다.
                  </span>
                )
              }
            >
              <div className="divide-y divide-slate-100">
                {workflowRows.length === 0 ? (
                  <div className="px-5 py-8 text-center text-sm text-slate-500">
                    접근 가능한 워크플로우가 없습니다.
                  </div>
                ) : (
                  workflowRows.map(({ app, permission, permissionError }) => (
                    <div
                      key={app.id}
                      className="grid gap-3 px-5 py-3 sm:grid-cols-[1fr_auto]"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-slate-950">
                          {app.name}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          {permissionError || '권한 상태 확인됨'}
                        </p>
                      </div>
                      <span
                        className={`h-fit rounded-md px-2 py-1 text-xs font-medium ${permissionTone(
                          permission,
                        )}`}
                      >
                        {permissionLabel(permission)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </DashboardPanel>
          </>
        )}
      </div>
    </div>
  );
}
