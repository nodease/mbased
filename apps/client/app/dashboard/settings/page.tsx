'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle,
  ExternalLink,
  Key,
  Plus,
  RefreshCw,
  ShieldCheck,
  Settings,
  Trash2,
  Users,
} from 'lucide-react';
import {
  activeOrganizationHeaders,
  getStoredActiveOrganizationId,
  resolveActiveOrganizationId,
  setActiveOrganizationId,
} from '@/lib/activeOrganization';
import { csrfFetch } from '@/lib/csrfToken';
import { ActiveOrganizationMemberPicker } from '@/app/features/organization/components/ActiveOrganizationMemberPicker';
import { OrganizationAuthBadge } from '@/app/features/organization/components/OrganizationAuthBadge';
import type {
  OrganizationMember,
  OrganizationResponse,
} from '@/app/features/organization/types/Organization';
import { filterActiveOrganizationMembers } from '@/app/features/organization/utils/memberFilters';
import {
  knowledgeApi,
  type KnowledgeBaseResponse,
} from '@/app/features/knowledge/api/knowledgeApi';
import { DashboardTitle } from '@/app/features/dashboard/components/DashboardSurface';

type SettingsTab = 'access' | 'credentials';
type ResourceType = 'workflow' | 'knowledge_base' | 'llm_credential';
type GranteeType = 'team' | 'user';
type AuthState = 'viewer' | 'operator' | 'builder' | 'manager';

type TeamResponse = {
  id: string;
  organization_id: string;
  name: string;
  description?: string;
  is_active: boolean;
};

type TeamMemberResponse = {
  id: string;
  user_id: string;
  email: string;
  name: string;
  assigned_at: string;
};

type ResourcePermissionEntry = {
  id: string;
  grantee_type: GranteeType;
  grantee_id: string;
  grantee_name: string;
  auth_state: string;
  assigned_at: string;
};

type ResourcePermissionListResponse = {
  resource_type: ResourceType;
  resource_id: string;
  organization_id: string;
  team_permissions: ResourcePermissionEntry[];
  user_permissions: ResourcePermissionEntry[];
};

type LLMModelResponse = {
  id: string;
  name: string;
  model_id_for_api_call?: string;
};

type LLMProviderResponse = {
  id: string;
  name: string;
  description?: string;
  type: string;
  base_url: string;
  doc_url?: string;
  models: LLMModelResponse[];
};

type LLMCredentialResponse = {
  id: string;
  provider_id: string;
  organization_id?: string;
  credential_name: string;
  config_preview?: string;
  is_valid: boolean;
  created_at: string;
};

type AppResponse = {
  id: string;
  name: string;
  workflow_id?: string;
};

const API_BASE_URL = '/api/v1';
const AUTH_STATES: AuthState[] = ['viewer', 'operator', 'builder', 'manager'];

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const organizationId = getStoredActiveOrganizationId();
  const response = await csrfFetch(`${API_BASE_URL}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...activeOrganizationHeaders(organizationId),
      ...(init?.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || body.message || 'Request failed');
  }
  return body as T;
}

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>('credentials');
  const [organization, setOrganization] = useState<OrganizationResponse | null>(
    null,
  );
  const [organizationMembers, setOrganizationMembers] = useState<
    OrganizationMember[]
  >([]);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [teamMembers, setTeamMembers] = useState<
    Record<string, TeamMemberResponse[]>
  >({});
  const [providers, setProviders] = useState<LLMProviderResponse[]>([]);
  const [credentials, setCredentials] = useState<LLMCredentialResponse[]>([]);
  const [apps, setApps] = useState<AppResponse[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseResponse[]>(
    [],
  );
  const [workflowPermissions, setWorkflowPermissions] =
    useState<ResourcePermissionListResponse | null>(null);
  const [knowledgePermissions, setKnowledgePermissions] =
    useState<ResourcePermissionListResponse | null>(null);
  const [credentialPermissions, setCredentialPermissions] =
    useState<ResourcePermissionListResponse | null>(null);

  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('');
  const [selectedCredentialId, setSelectedCredentialId] = useState('');
  const [newTeam, setNewTeam] = useState({ name: '', description: '' });
  const [memberForm, setMemberForm] = useState({ teamId: '', userId: '' });
  const [permissionForm, setPermissionForm] = useState<{
    resourceType: ResourceType;
    granteeType: GranteeType;
    granteeId: string;
    authState: AuthState;
  }>({
    resourceType: 'workflow',
    granteeType: 'team',
    granteeId: '',
    authState: 'viewer',
  });
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const workflowOptions = useMemo(
    () => apps.filter((app) => Boolean(app.workflow_id)),
    [apps],
  );
  const isManager = organization?.is_manager === true;
  const activeTeams = useMemo(
    () => teams.filter((team) => team.is_active),
    [teams],
  );
  const visibleTabs = useMemo<[SettingsTab, string][]>(
    () =>
      isManager
        ? [
            ['access', 'Access'],
            ['credentials', 'LLM Credentials'],
          ]
        : [['credentials', 'LLM Credentials']],
    [isManager],
  );
  const effectiveTab = visibleTabs.some(([key]) => key === activeTab)
    ? activeTab
    : 'credentials';

  const activePermissions =
    permissionForm.resourceType === 'workflow'
      ? workflowPermissions
      : permissionForm.resourceType === 'knowledge_base'
        ? knowledgePermissions
        : credentialPermissions;
  const selectedPermissionResourceId =
    permissionForm.resourceType === 'workflow'
      ? selectedWorkflowId
      : permissionForm.resourceType === 'knowledge_base'
        ? selectedKnowledgeBaseId
        : selectedCredentialId;
  const selectedTeamMemberUserIds = useMemo(
    () =>
      memberForm.teamId
        ? (teamMembers[memberForm.teamId] || []).map((member) => member.user_id)
        : [],
    [memberForm.teamId, teamMembers],
  );
  const availableTeamMemberCandidates = useMemo(
    () =>
      filterActiveOrganizationMembers(
        organizationMembers,
        selectedTeamMemberUserIds,
      ),
    [organizationMembers, selectedTeamMemberUserIds],
  );
  const availableDirectPermissionCandidates = useMemo(
    () => filterActiveOrganizationMembers(organizationMembers),
    [organizationMembers],
  );

  const loadPermissions = async (
    resourceType = permissionForm.resourceType,
    workflowId = selectedWorkflowId,
    knowledgeBaseId = selectedKnowledgeBaseId,
    credentialId = selectedCredentialId,
  ) => {
    if (organization && !organization.is_manager) return;
    try {
      if (resourceType === 'workflow' && workflowId) {
        const data = await apiRequest<ResourcePermissionListResponse>(
          `/permissions/workflows/${workflowId}`,
        );
        setWorkflowPermissions(data);
        return;
      }
      if (resourceType === 'knowledge_base' && knowledgeBaseId) {
        const data = await apiRequest<ResourcePermissionListResponse>(
          `/permissions/knowledge-bases/${knowledgeBaseId}`,
        );
        setKnowledgePermissions(data);
        return;
      }
      if (resourceType === 'llm_credential' && credentialId) {
        const data = await apiRequest<ResourcePermissionListResponse>(
          `/permissions/llm-credentials/${credentialId}`,
        );
        setCredentialPermissions(data);
        return;
      }
      if (resourceType === 'workflow') setWorkflowPermissions(null);
      if (resourceType === 'knowledge_base') setKnowledgePermissions(null);
      if (resourceType === 'llm_credential') setCredentialPermissions(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '권한 조회 실패');
      if (resourceType === 'workflow') setWorkflowPermissions(null);
      if (resourceType === 'knowledge_base') setKnowledgePermissions(null);
      if (resourceType === 'llm_credential') setCredentialPermissions(null);
    }
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const organizations =
        await apiRequest<OrganizationResponse[]>('/organizations');
      const organizationId = resolveActiveOrganizationId(organizations);
      if (!organizationId) {
        throw new Error('현재 선택된 조직이 없습니다.');
      }
      const org = await apiRequest<OrganizationResponse>(
        '/organizations/current',
        { headers: activeOrganizationHeaders(organizationId) },
      );
      setActiveOrganizationId(org.id);
      setOrganization(org);

      const [providerData, credentialData, appData, knowledgeData] =
        await Promise.all([
          apiRequest<LLMProviderResponse[]>('/llm/providers'),
          apiRequest<LLMCredentialResponse[]>('/llm/credentials'),
          apiRequest<AppResponse[]>('/apps'),
          org.is_manager
            ? knowledgeApi.getKnowledgeBases().catch(() => [])
            : [],
        ]);

      setProviders(providerData);
      setCredentials(credentialData);
      setApps(appData);
      setKnowledgeBases(knowledgeData);

      const firstWorkflowId =
        selectedWorkflowId ||
        appData.find((app) => app.workflow_id)?.workflow_id ||
        '';
      const firstKnowledgeBaseId =
        selectedKnowledgeBaseId || knowledgeData[0]?.id || '';
      const firstCredentialId =
        selectedCredentialId || credentialData[0]?.id || '';
      setSelectedWorkflowId(firstWorkflowId);
      setSelectedKnowledgeBaseId(firstKnowledgeBaseId);
      setSelectedCredentialId(firstCredentialId);

      if (!org.is_manager) {
        setOrganizationMembers([]);
        setTeams([]);
        setTeamMembers({});
        setWorkflowPermissions(null);
        setKnowledgePermissions(null);
        setCredentialPermissions(null);
        setMemberForm({ teamId: '', userId: '' });
        setPermissionForm((prev) => ({ ...prev, granteeId: '' }));
        return;
      }

      const [memberData, teamData] = await Promise.all([
        apiRequest<OrganizationMember[]>(`/organizations/${org.id}/members`),
        apiRequest<TeamResponse[]>(
          `/teams?organization_id=${org.id}&limit=100`,
        ),
      ]);
      setOrganizationMembers(memberData);
      setTeams(teamData);

      const teamMemberEntries = await Promise.all(
        teamData.map(async (team) => {
          try {
            const members = await apiRequest<TeamMemberResponse[]>(
              `/teams/${team.id}/members`,
            );
            return [team.id, members] as const;
          } catch {
            return [team.id, []] as const;
          }
        }),
      );
      setTeamMembers(Object.fromEntries(teamMemberEntries));
      setMemberForm((prev) => ({
        teamId:
          prev.teamId || teamData.find((team) => team.is_active)?.id || '',
        userId: prev.userId,
      }));
      setPermissionForm((prev) => ({
        ...prev,
        granteeId:
          prev.granteeId ||
          teamData.find((team) => team.is_active)?.id ||
          memberData.find((member) => member.membership_state === 'active')
            ?.user_id ||
          '',
      }));

      await Promise.all([
        loadPermissions(
          'workflow',
          firstWorkflowId,
          firstKnowledgeBaseId,
          firstCredentialId,
        ),
        loadPermissions(
          'knowledge_base',
          firstWorkflowId,
          firstKnowledgeBaseId,
          firstCredentialId,
        ),
        loadPermissions(
          'llm_credential',
          firstWorkflowId,
          firstKnowledgeBaseId,
          firstCredentialId,
        ),
      ]);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : '설정 데이터를 불러오지 못했습니다.',
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isManager) return;
    if (!selectedKnowledgeBaseId && knowledgeBases.length > 0) {
      setSelectedKnowledgeBaseId(knowledgeBases[0].id);
    }
  }, [isManager, knowledgeBases, selectedKnowledgeBaseId]);

  useEffect(() => {
    if (!isManager) return;
    if (
      memberForm.userId &&
      availableTeamMemberCandidates.some(
        (member) => member.user_id === memberForm.userId,
      )
    ) {
      return;
    }
    setMemberForm((prev) => ({
      ...prev,
      userId: availableTeamMemberCandidates[0]?.user_id || '',
    }));
  }, [availableTeamMemberCandidates, isManager, memberForm.userId]);

  useEffect(() => {
    if (!isManager) return;
    if (permissionForm.granteeType === 'team') {
      if (
        permissionForm.granteeId &&
        activeTeams.some((team) => team.id === permissionForm.granteeId)
      ) {
        return;
      }
      setPermissionForm((prev) => ({
        ...prev,
        granteeId: activeTeams[0]?.id || '',
      }));
      return;
    }

    if (
      permissionForm.granteeId &&
      availableDirectPermissionCandidates.some(
        (member) => member.user_id === permissionForm.granteeId,
      )
    ) {
      return;
    }
    setPermissionForm((prev) => ({
      ...prev,
      granteeId: availableDirectPermissionCandidates[0]?.user_id || '',
    }));
  }, [
    activeTeams,
    availableDirectPermissionCandidates,
    isManager,
    permissionForm.granteeId,
    permissionForm.granteeType,
  ]);

  const handleCreateTeam = async () => {
    if (!organization?.is_manager || !newTeam.name.trim()) return;
    setSubmitting(true);
    try {
      await apiRequest('/teams', {
        method: 'POST',
        body: JSON.stringify({
          organization_id: organization.id,
          name: newTeam.name.trim(),
          description: newTeam.description.trim() || null,
        }),
      });
      setNewTeam({ name: '', description: '' });
      await loadData();
    } catch (err) {
      alert(err instanceof Error ? err.message : '팀 생성 실패');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeactivateTeam = async (teamId: string) => {
    if (!organization?.is_manager) return;
    if (!confirm('이 팀을 비활성화할까요?')) return;
    await apiRequest(`/teams/${teamId}`, { method: 'DELETE' });
    await loadData();
  };

  const handleAddMember = async () => {
    if (!organization?.is_manager || !memberForm.teamId || !memberForm.userId) {
      return;
    }
    await apiRequest(`/teams/${memberForm.teamId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_id: memberForm.userId }),
    });
    const refreshed = await apiRequest<TeamMemberResponse[]>(
      `/teams/${memberForm.teamId}/members`,
    );
    setTeamMembers((prev) => ({ ...prev, [memberForm.teamId]: refreshed }));
  };

  const handleRemoveMember = async (teamId: string, userId: string) => {
    if (!organization?.is_manager) return;
    await apiRequest(`/teams/${teamId}/members/${userId}`, {
      method: 'DELETE',
    });
    const refreshed = await apiRequest<TeamMemberResponse[]>(
      `/teams/${teamId}/members`,
    );
    setTeamMembers((prev) => ({ ...prev, [teamId]: refreshed }));
  };

  const permissionPath = (
    resourceType: ResourceType,
    granteeType: GranteeType,
    granteeId: string,
  ) => {
    const resourceId =
      resourceType === 'workflow'
        ? selectedWorkflowId
        : resourceType === 'knowledge_base'
          ? selectedKnowledgeBaseId
          : selectedCredentialId;
    const resourcePath =
      resourceType === 'workflow'
        ? `/permissions/workflows/${resourceId}`
        : resourceType === 'knowledge_base'
          ? `/permissions/knowledge-bases/${resourceId}`
          : `/permissions/llm-credentials/${resourceId}`;
    return `${resourcePath}/${granteeType}s/${granteeId}`;
  };

  const handleGrantPermission = async () => {
    if (!organization?.is_manager || !permissionForm.granteeId) return;
    if (!selectedPermissionResourceId) return;
    await apiRequest(
      permissionPath(
        permissionForm.resourceType,
        permissionForm.granteeType,
        permissionForm.granteeId,
      ),
      {
        method: 'PUT',
        body: JSON.stringify({ auth_state: permissionForm.authState }),
      },
    );
    await loadPermissions();
  };

  const handleRevokePermission = async (
    resourceType: ResourceType,
    granteeType: GranteeType,
    granteeId: string,
  ) => {
    if (!organization?.is_manager) return;
    await apiRequest(permissionPath(resourceType, granteeType, granteeId), {
      method: 'DELETE',
    });
    await loadPermissions(resourceType);
  };

  const renderPermissionRows = (rows: ResourcePermissionEntry[]) =>
    rows.length === 0 ? (
      <div className="px-3 py-3 text-sm text-gray-500">부여된 권한 없음</div>
    ) : (
      rows.map((permission) => (
        <div
          key={permission.id}
          className="flex items-center justify-between gap-3 border-t border-gray-100 px-3 py-2"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-gray-900">
              {permission.grantee_name}
            </p>
            <p className="text-xs text-gray-500">{permission.auth_state}</p>
          </div>
          <button
            onClick={() =>
              handleRevokePermission(
                permissionForm.resourceType,
                permission.grantee_type,
                permission.grantee_id,
              )
            }
            className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
            title="권한 회수"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      ))
    );

  return (
    <div className="min-h-full bg-white p-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <DashboardTitle
              icon={Settings}
              title="설정"
              className="text-2xl font-bold text-gray-900"
            />
            {organization && (
              <OrganizationAuthBadge
                state={organization.is_manager ? 'manager' : 'member'}
              />
            )}
          </div>
          <p className="mt-1 text-sm text-gray-600">
            {organization ? organization.name : 'Organization 확인 중'}
          </p>
        </div>
        <button
          onClick={loadData}
          className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          <RefreshCw className="h-4 w-4" />
          새로고침
        </button>
      </div>

      <div className="mb-6 border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {visibleTabs.map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`border-b-2 px-1 pb-3 text-sm font-medium ${
                effectiveTab === key
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      {error && (
        <div className="mb-6 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-sm text-gray-500">
          로딩 중...
        </div>
      ) : effectiveTab === 'access' && isManager ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <section className="space-y-4">
            <div className="rounded-lg border border-gray-200">
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
                  <Users className="h-4 w-4 text-blue-600" />
                  Teams
                </h2>
                <div className="flex gap-2">
                  <input
                    value={newTeam.name}
                    onChange={(event) =>
                      setNewTeam((prev) => ({
                        ...prev,
                        name: event.target.value,
                      }))
                    }
                    className="h-9 w-40 rounded-md border border-gray-300 px-3 text-sm"
                    placeholder="팀 이름"
                  />
                  <button
                    onClick={handleCreateTeam}
                    disabled={submitting || !newTeam.name.trim()}
                    className="inline-flex h-9 items-center gap-1.5 rounded-md bg-gray-900 px-3 text-sm font-medium text-white disabled:opacity-40"
                  >
                    <Plus className="h-4 w-4" />
                    생성
                  </button>
                </div>
              </div>
              <div>
                {teams.length === 0 ? (
                  <div className="px-4 py-8 text-center text-sm text-gray-500">
                    표시할 team 없음
                  </div>
                ) : (
                  teams.map((team) => (
                    <div
                      key={team.id}
                      className="border-b border-gray-100 px-4 py-4 last:border-b-0"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium text-gray-900">
                              {team.name}
                            </p>
                            {!team.is_active && (
                              <span className="rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                                비활성
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500">
                            {team.description || '설명 없음'}
                          </p>
                        </div>
                        <button
                          onClick={() => handleDeactivateTeam(team.id)}
                          disabled={!team.is_active}
                          className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400"
                          title="팀 비활성화"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(teamMembers[team.id] || []).map((member) => (
                          <span
                            key={member.id}
                            className="inline-flex items-center gap-1 rounded-md bg-gray-100 px-2 py-1 text-xs text-gray-700"
                          >
                            {member.name}
                            <button
                              onClick={() =>
                                handleRemoveMember(team.id, member.user_id)
                              }
                              className="text-gray-400 hover:text-red-600"
                              title="멤버 제거"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="rounded-lg border border-gray-200 p-4">
              <h2 className="mb-3 text-sm font-semibold text-gray-900">
                Member 추가
              </h2>
              <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
                <select
                  value={memberForm.teamId}
                  onChange={(event) =>
                    setMemberForm((prev) => ({
                      ...prev,
                      teamId: event.target.value,
                    }))
                  }
                  className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                >
                  {activeTeams.map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
                </select>
                <ActiveOrganizationMemberPicker
                  members={organizationMembers}
                  value={memberForm.userId}
                  onChange={(userId) =>
                    setMemberForm((prev) => ({
                      ...prev,
                      userId,
                    }))
                  }
                  excludedUserIds={selectedTeamMemberUserIds}
                  placeholder="추가할 멤버 선택"
                  emptyLabel="추가 가능한 활성 멤버 없음"
                />
                <button
                  onClick={handleAddMember}
                  disabled={!memberForm.teamId || !memberForm.userId}
                  className="h-10 rounded-md bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  추가
                </button>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-gray-200">
            <div className="border-b border-gray-200 px-4 py-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
                <ShieldCheck className="h-4 w-4 text-blue-600" />
                Resource Permissions
              </h2>
            </div>
            <div className="space-y-4 p-4">
              <div className="grid grid-cols-2 gap-3">
                <select
                  value={permissionForm.resourceType}
                  onChange={(event) => {
                    const resourceType = event.target.value as ResourceType;
                    setPermissionForm((prev) => ({ ...prev, resourceType }));
                    loadPermissions(resourceType);
                  }}
                  className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                >
                  <option value="workflow">Workflow</option>
                  <option value="knowledge_base">Knowledge Base</option>
                  <option value="llm_credential">LLM Credential</option>
                </select>
                {permissionForm.resourceType === 'workflow' ? (
                  <select
                    value={selectedWorkflowId}
                    onChange={(event) => {
                      setSelectedWorkflowId(event.target.value);
                      loadPermissions('workflow', event.target.value);
                    }}
                    className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                  >
                    {workflowOptions.length === 0 ? (
                      <option value="">선택 가능한 workflow 없음</option>
                    ) : (
                      workflowOptions.map((app) => (
                        <option key={app.workflow_id} value={app.workflow_id}>
                          {app.name}
                        </option>
                      ))
                    )}
                  </select>
                ) : permissionForm.resourceType === 'knowledge_base' ? (
                  <select
                    value={selectedKnowledgeBaseId}
                    onChange={(event) => {
                      setSelectedKnowledgeBaseId(event.target.value);
                      loadPermissions(
                        'knowledge_base',
                        selectedWorkflowId,
                        event.target.value,
                      );
                    }}
                    className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                  >
                    {knowledgeBases.length === 0 ? (
                      <option value="">선택 가능한 지식 기반 없음</option>
                    ) : (
                      knowledgeBases.map((knowledgeBase) => (
                        <option key={knowledgeBase.id} value={knowledgeBase.id}>
                          {knowledgeBase.name}
                        </option>
                      ))
                    )}
                  </select>
                ) : (
                  <select
                    value={selectedCredentialId}
                    onChange={(event) => {
                      setSelectedCredentialId(event.target.value);
                      loadPermissions(
                        'llm_credential',
                        selectedWorkflowId,
                        selectedKnowledgeBaseId,
                        event.target.value,
                      );
                    }}
                    className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                  >
                    {credentials.length === 0 ? (
                      <option value="">선택 가능한 credential 없음</option>
                    ) : (
                      credentials.map((credential) => (
                        <option key={credential.id} value={credential.id}>
                          {credential.credential_name}
                        </option>
                      ))
                    )}
                  </select>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <select
                  value={permissionForm.granteeType}
                  onChange={(event) => {
                    const granteeType = event.target.value as GranteeType;
                    setPermissionForm((prev) => ({
                      ...prev,
                      granteeType,
                      granteeId:
                        granteeType === 'team'
                          ? activeTeams[0]?.id || ''
                          : availableDirectPermissionCandidates[0]?.user_id ||
                            '',
                    }));
                  }}
                  className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                >
                  <option value="team">Team</option>
                  <option value="user">User direct</option>
                </select>
                {permissionForm.granteeType === 'team' ? (
                  <select
                    value={permissionForm.granteeId}
                    onChange={(event) =>
                      setPermissionForm((prev) => ({
                        ...prev,
                        granteeId: event.target.value,
                      }))
                    }
                    className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                  >
                    {activeTeams.map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <ActiveOrganizationMemberPicker
                    members={organizationMembers}
                    value={permissionForm.granteeId}
                    onChange={(userId) =>
                      setPermissionForm((prev) => ({
                        ...prev,
                        granteeId: userId,
                      }))
                    }
                    placeholder="권한을 부여하거나 수정할 멤버 선택"
                    emptyLabel="권한을 부여할 활성 멤버 없음"
                  />
                )}
              </div>

              <div className="grid grid-cols-[1fr_auto] gap-3">
                <select
                  value={permissionForm.authState}
                  onChange={(event) =>
                    setPermissionForm((prev) => ({
                      ...prev,
                      authState: event.target.value as AuthState,
                    }))
                  }
                  className="h-10 rounded-md border border-gray-300 px-3 text-sm"
                >
                  {AUTH_STATES.map((state) => (
                    <option key={state} value={state}>
                      {state}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleGrantPermission}
                  disabled={
                    !selectedPermissionResourceId || !permissionForm.granteeId
                  }
                  className="h-10 rounded-md bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  부여
                </button>
              </div>

              <div className="overflow-hidden rounded-md border border-gray-200">
                <div className="bg-gray-50 px-3 py-2 text-xs font-semibold uppercase text-gray-500">
                  Team permissions
                </div>
                {renderPermissionRows(
                  activePermissions?.team_permissions || [],
                )}
                <div className="border-t border-gray-200 bg-gray-50 px-3 py-2 text-xs font-semibold uppercase text-gray-500">
                  User direct permissions
                </div>
                {renderPermissionRows(
                  activePermissions?.user_permissions || [],
                )}
              </div>
            </div>
          </section>
        </div>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <section className="grid gap-4">
            {providers.map((provider) => {
              const providerCredentials = credentials.filter(
                (credential) => credential.provider_id === provider.id,
              );
              return (
                <div
                  key={provider.id}
                  className="rounded-lg border border-gray-200 bg-white"
                >
                  <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <h2 className="font-semibold capitalize text-gray-900">
                          {provider.name}
                        </h2>
                        {providerCredentials.length > 0 && (
                          <span className="rounded-md bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
                            Connected
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-sm text-gray-500">
                        {provider.models.length} models · {provider.base_url}
                      </p>
                    </div>
                    {provider.doc_url && (
                      <a
                        href={provider.doc_url}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-md p-2 text-gray-500 hover:bg-gray-100"
                        title="Provider 문서"
                      >
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    )}
                  </div>
                  <div className="divide-y divide-gray-100">
                    {providerCredentials.length === 0 ? (
                      <div className="px-5 py-4 text-sm text-gray-500">
                        {isManager
                          ? '등록된 credential 없음'
                          : '접근 가능한 credential이 없습니다. 관리자에게 credential 등록 또는 권한 부여를 요청하세요.'}
                      </div>
                    ) : (
                      providerCredentials.map((credential) => (
                        <div
                          key={credential.id}
                          className="flex items-center justify-between gap-3 px-5 py-3"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <div
                              className={`rounded-full p-1.5 ${
                                credential.is_valid
                                  ? 'bg-green-50 text-green-600'
                                  : 'bg-red-50 text-red-600'
                              }`}
                            >
                              {credential.is_valid ? (
                                <CheckCircle className="h-4 w-4" />
                              ) : (
                                <AlertCircle className="h-4 w-4" />
                              )}
                            </div>
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-gray-900">
                                {credential.credential_name}
                              </p>
                              <p className="font-mono text-xs text-gray-500">
                                {credential.config_preview || 'preview 없음'}
                              </p>
                            </div>
                          </div>
                          <span className="rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600">
                            읽기 전용
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}
          </section>

          <section className="h-fit rounded-lg border border-gray-200 bg-gray-50 p-5">
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-gray-900">
              <Key className="h-4 w-4 text-blue-600" />
              Credential 접근 상태
            </h2>
            <p className="text-sm leading-6 text-gray-600">
              이 화면에서는 접근 가능한 credential만 확인할 수 있습니다. 등록,
              삭제, 모델 동기화는 관리 화면에서 다룹니다.
            </p>
          </section>
        </div>
      )}
    </div>
  );
}
