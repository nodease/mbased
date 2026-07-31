'use client';

import { useState } from 'react';
import { Plus, RefreshCw, Search, SlidersHorizontal, X } from 'lucide-react';
import { DashboardPanel } from '@/app/features/dashboard/components/DashboardSurface';
import type { KnowledgeBaseResponse } from '@/app/features/knowledge/api/knowledgeApi';
import type { OrganizationMember } from '@/app/features/organization/types/Organization';
import type { MailCredentialOption } from '@/app/features/workflow/api/mailCredentialApi';

export type TeamResponse = {
  id: string;
  organization_id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  is_auto_add?: boolean;
  deactivated_at?: string | null;
};

export type AppResponse = {
  id: string;
  name: string;
  workflow_id?: string | null;
};

export type LLMCredentialResponse = {
  id: string;
  provider_id: string;
  credential_name: string;
  config_preview?: string;
  is_valid: boolean;
  created_at: string;
};

export type ResourceType =
  | 'workflow'
  | 'knowledge_base'
  | 'llm_credential'
  | 'mail_credential';
export type GranteeType = 'team' | 'user';
export type ResourceAuthState = 'viewer' | 'operator' | 'builder' | 'manager';

export type PermissionGrantSelection = {
  resourceType: ResourceType;
  resourceIds: string[];
  granteeType: GranteeType;
  granteeIds: string[];
  authState: ResourceAuthState;
};

type ResourcePermissionEntry = {
  id: string;
  grantee_type: GranteeType;
  grantee_id: string;
  grantee_name: string;
  auth_state: ResourceAuthState;
  assigned_at: string;
};

export type ResourcePermissionListResponse = {
  resource_type: ResourceType;
  resource_id: string;
  organization_id: string;
  team_permissions: ResourcePermissionEntry[];
  user_permissions: ResourcePermissionEntry[];
};

const RESOURCE_AUTH_STATES: ResourceAuthState[] = [
  'viewer',
  'operator',
  'builder',
  'manager',
];
const MAX_BULK_PERMISSION_GRANTS = 50;

const formatDateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : '-';

export function PermissionsTab({
  resourceType,
  workflowOptions,
  selectedWorkflowId,
  knowledgeBases,
  selectedKnowledgeBaseId,
  credentials,
  selectedCredentialId,
  mailCredentials,
  selectedMailCredentialId,
  activeTeams,
  activeMembers,
  granteeType,
  granteeId,
  authState,
  permissionList,
  actionPending,
  onSelectResource,
  onGrant,
  onRevoke,
}: {
  resourceType: ResourceType;
  workflowOptions: AppResponse[];
  selectedWorkflowId: string;
  knowledgeBases: KnowledgeBaseResponse[];
  selectedKnowledgeBaseId: string;
  credentials: LLMCredentialResponse[];
  selectedCredentialId: string;
  mailCredentials: MailCredentialOption[];
  selectedMailCredentialId: string;
  activeTeams: TeamResponse[];
  activeMembers: OrganizationMember[];
  granteeType: GranteeType;
  granteeId: string;
  authState: ResourceAuthState;
  permissionList: ResourcePermissionListResponse | null;
  actionPending: boolean;
  onSelectResource: (resourceType: ResourceType, resourceId: string) => void;
  onGrant: (
    selection: PermissionGrantSelection,
  ) => boolean | void | Promise<boolean | void>;
  onRevoke: (
    resourceType: ResourceType,
    granteeType: GranteeType,
    granteeId: string,
    label: string,
  ) => void;
}) {
  const [grantModalOpen, setGrantModalOpen] = useState(false);
  const [resourceFilterOpen, setResourceFilterOpen] = useState(false);
  const [resourceFilterType, setResourceFilterType] =
    useState<ResourceType>(resourceType);
  const [resourceFilterQuery, setResourceFilterQuery] = useState('');
  const selectedResourceId =
    resourceType === 'workflow'
      ? selectedWorkflowId
      : resourceType === 'knowledge_base'
        ? selectedKnowledgeBaseId
        : resourceType === 'llm_credential'
          ? selectedCredentialId
          : selectedMailCredentialId;
  const resourceMissing = !selectedResourceId;
  const selectedResourceName =
    resourceType === 'workflow'
      ? workflowOptions.find((item) => item.workflow_id === selectedWorkflowId)
          ?.name
      : resourceType === 'knowledge_base'
        ? knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId)?.name
        : resourceType === 'llm_credential'
          ? credentials.find((item) => item.id === selectedCredentialId)
              ?.credential_name
          : mailCredentials.find((item) => item.id === selectedMailCredentialId)
              ?.credential_name;
  const resourceFilterOptions =
    resourceFilterType === 'workflow'
      ? workflowOptions
          .filter((item) => item.workflow_id)
          .map((item) => ({ id: item.workflow_id!, name: item.name }))
      : resourceFilterType === 'knowledge_base'
        ? knowledgeBases.map((item) => ({ id: item.id, name: item.name }))
        : resourceFilterType === 'llm_credential'
          ? credentials.map((item) => ({
              id: item.id,
              name: item.credential_name,
            }))
          : mailCredentials.map((item) => ({
              id: item.id,
              name: item.credential_name,
            }));
  const visibleResourceFilterOptions = resourceFilterOptions.filter((item) =>
    item.name
      .toLowerCase()
      .includes(resourceFilterQuery.trim().toLowerCase()),
  );
  const visiblePermissionList =
    permissionList?.resource_type === resourceType &&
    permissionList.resource_id === selectedResourceId
      ? permissionList
      : null;

  return (
    <>
      <DashboardPanel
        title="권한"
        icon={SlidersHorizontal}
        aside={
          <button
            type="button"
            onClick={() => setGrantModalOpen(true)}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-3 text-sm font-semibold text-white hover:bg-slate-800"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            권한 부여
          </button>
        }
      >
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                리소스 필터
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-800">
                  유형: {resourceTypeLabel(resourceType)}
                </span>
                <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700">
                  리소스:{' '}
                  <span>{selectedResourceName || '선택 가능한 리소스 없음'}</span>
                </span>
              </div>
            </div>
            <button
              type="button"
              aria-expanded={resourceFilterOpen}
              aria-controls="permission-resource-filter"
              onClick={() => {
                setResourceFilterOpen((open) => !open);
                setResourceFilterType(resourceType);
                setResourceFilterQuery('');
              }}
              className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            >
              {resourceFilterOpen ? '필터 닫기' : '리소스 필터 변경'}
            </button>
          </div>

          {resourceFilterOpen && (
            <div
              id="permission-resource-filter"
              className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4"
            >
              <div className="grid gap-3 sm:grid-cols-[200px_minmax(0,1fr)]">
                <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                  리소스 유형
                  <select
                    value={resourceFilterType}
                    onChange={(event) => {
                      setResourceFilterType(event.target.value as ResourceType);
                      setResourceFilterQuery('');
                    }}
                    className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900"
                  >
                    <option value="workflow">Workflow</option>
                    <option value="knowledge_base">Knowledge Base</option>
                    <option value="llm_credential">LLM Credential</option>
                    <option value="mail_credential">Mail Credential</option>
                  </select>
                </label>
                <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                  리소스 이름
                  <span className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input
                      type="search"
                      aria-label="조회 리소스 검색"
                      value={resourceFilterQuery}
                      onChange={(event) => setResourceFilterQuery(event.target.value)}
                      placeholder="이름으로 검색"
                      className="h-10 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm"
                    />
                  </span>
                </label>
              </div>

              <div
                role="listbox"
                aria-label="조회할 리소스"
                className="mt-3 max-h-64 overflow-y-auto rounded-md border border-slate-200 bg-white"
              >
                {visibleResourceFilterOptions.length === 0 ? (
                  <p className="px-4 py-8 text-center text-sm text-slate-500">
                    조건에 맞는 리소스가 없습니다.
                  </p>
                ) : (
                  visibleResourceFilterOptions.map((item) => {
                    const selected =
                      resourceFilterType === resourceType &&
                      item.id === selectedResourceId;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        role="option"
                        aria-label={item.name}
                        aria-selected={selected}
                        onClick={() => {
                          onSelectResource(resourceFilterType, item.id);
                          setResourceFilterOpen(false);
                        }}
                        className={`flex w-full items-center justify-between border-b border-slate-100 px-4 py-3 text-left text-sm last:border-b-0 hover:bg-blue-50 ${selected ? 'bg-blue-50 font-semibold text-blue-800' : 'text-slate-700'}`}
                      >
                        <span>{item.name}</span>
                        <span className="text-xs font-normal text-slate-500">
                          {resourceTypeLabel(resourceFilterType)}
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
              <p className="mt-2 text-xs text-slate-500">
                리소스를 선택하면 저장 없이 아래 권한 목록이 바로 바뀝니다.
              </p>
            </div>
          )}
        </div>

        {resourceMissing ? (
          <PermissionPlaceholder
            title="선택 가능한 resource가 없습니다"
            description="Workflow, Knowledge Base, LLM Credential이 생성되면 권한을 부여할 수 있습니다."
          />
        ) : (
          <div className="grid gap-4 px-5 py-5 lg:grid-cols-2">
            <PermissionList
              title="Team permissions"
              rows={visiblePermissionList?.team_permissions || []}
              resourceType={resourceType}
              granteeType="team"
              onRevoke={onRevoke}
            />
            <PermissionList
              title="User direct permissions"
              rows={visiblePermissionList?.user_permissions || []}
              resourceType={resourceType}
              granteeType="user"
              onRevoke={onRevoke}
            />
          </div>
        )}
      </DashboardPanel>

      {grantModalOpen && (
        <PermissionGrantModal
          resourceType={resourceType}
          workflowOptions={workflowOptions}
          selectedWorkflowId={selectedWorkflowId}
          knowledgeBases={knowledgeBases}
          selectedKnowledgeBaseId={selectedKnowledgeBaseId}
          credentials={credentials}
          selectedCredentialId={selectedCredentialId}
          mailCredentials={mailCredentials}
          selectedMailCredentialId={selectedMailCredentialId}
          activeTeams={activeTeams}
          activeMembers={activeMembers}
          granteeType={granteeType}
          granteeId={granteeId}
          authState={authState}
          actionPending={actionPending}
          onGrant={onGrant}
          onClose={() => setGrantModalOpen(false)}
        />
      )}
    </>
  );
}

function PermissionGrantModal({
  resourceType,
  workflowOptions,
  selectedWorkflowId,
  knowledgeBases,
  selectedKnowledgeBaseId,
  credentials,
  selectedCredentialId,
  mailCredentials,
  selectedMailCredentialId,
  activeTeams,
  activeMembers,
  granteeType,
  granteeId,
  authState,
  actionPending,
  onGrant,
  onClose,
}: {
  resourceType: ResourceType;
  workflowOptions: AppResponse[];
  selectedWorkflowId: string;
  knowledgeBases: KnowledgeBaseResponse[];
  selectedKnowledgeBaseId: string;
  credentials: LLMCredentialResponse[];
  selectedCredentialId: string;
  mailCredentials: MailCredentialOption[];
  selectedMailCredentialId: string;
  activeTeams: TeamResponse[];
  activeMembers: OrganizationMember[];
  granteeType: GranteeType;
  granteeId: string;
  authState: ResourceAuthState;
  actionPending: boolean;
  onGrant: (
    selection: PermissionGrantSelection,
  ) => boolean | void | Promise<boolean | void>;
  onClose: () => void;
}) {
  const [resourceQuery, setResourceQuery] = useState('');
  const [granteeQuery, setGranteeQuery] = useState('');
  const [draftResourceType, setDraftResourceType] =
    useState<ResourceType>(resourceType);
  const initialResourceId =
    resourceType === 'workflow'
      ? selectedWorkflowId
      : resourceType === 'knowledge_base'
        ? selectedKnowledgeBaseId
        : resourceType === 'llm_credential'
          ? selectedCredentialId
          : selectedMailCredentialId;
  const [draftResourceIds, setDraftResourceIds] = useState<string[]>(
    initialResourceId ? [initialResourceId] : [],
  );
  const [draftGranteeType, setDraftGranteeType] =
    useState<GranteeType>(granteeType);
  const [draftGranteeIds, setDraftGranteeIds] = useState<string[]>(
    granteeId ? [granteeId] : [],
  );
  const [draftAuthState, setDraftAuthState] =
    useState<ResourceAuthState>(authState);

  const getResourceOptions = (type: ResourceType) =>
    type === 'workflow'
      ? workflowOptions
          .filter((item) => item.workflow_id)
          .map((item) => ({ id: item.workflow_id!, name: item.name }))
      : type === 'knowledge_base'
        ? knowledgeBases.map((item) => ({ id: item.id, name: item.name }))
        : type === 'llm_credential'
          ? credentials.map((item) => ({
              id: item.id,
              name: item.credential_name,
            }))
          : mailCredentials.map((item) => ({
              id: item.id,
              name: item.credential_name,
            }));
  const resourceOptions = getResourceOptions(draftResourceType);
  const visibleResources = resourceOptions.filter((item) =>
    item.name.toLowerCase().includes(resourceQuery.trim().toLowerCase()),
  );
  const granteeOptions =
    draftGranteeType === 'team'
      ? activeTeams.map((item) => ({
          id: item.id,
          name: item.name,
          detail: item.description || '활성 팀',
        }))
      : activeMembers.map((item) => ({
          id: item.user_id,
          name: item.user_name,
          detail: item.user_email,
        }));
  const visibleGrantees = granteeOptions.filter((item) =>
    `${item.name} ${item.detail}`
      .toLowerCase()
      .includes(granteeQuery.trim().toLowerCase()),
  );
  const visibleResourceIds = new Set(visibleResources.map((item) => item.id));
  const visibleGranteeIds = new Set(visibleGrantees.map((item) => item.id));
  const hasVisibleResourceSelection =
    draftResourceIds.length > 0 &&
    draftResourceIds.every((id) => visibleResourceIds.has(id));
  const hasVisibleGranteeSelection =
    draftGranteeIds.length > 0 &&
    draftGranteeIds.every((id) => visibleGranteeIds.has(id));
  const grantPairCount = draftResourceIds.length * draftGranteeIds.length;
  const withinBulkLimit = grantPairCount <= MAX_BULK_PERMISSION_GRANTS;
  const toggleSelection = (
    id: string,
    selectedIds: string[],
    setSelectedIds: (ids: string[]) => void,
  ) => {
    setSelectedIds(
      selectedIds.includes(id)
        ? selectedIds.filter((selectedId) => selectedId !== id)
        : [...selectedIds, id],
    );
  };
  const canSubmit =
    !actionPending &&
    hasVisibleResourceSelection &&
    hasVisibleGranteeSelection &&
    withinBulkLimit;

  const submitGrant = async () => {
    if (!canSubmit) return;
    const success = await onGrant({
      resourceType: draftResourceType,
      resourceIds: draftResourceIds,
      granteeType: draftGranteeType,
      granteeIds: draftGranteeIds,
      authState: draftAuthState,
    });
    if (success !== false) onClose();
  };

  const requestClose = () => {
    if (!actionPending) onClose();
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 px-4 py-6">
      <div
        className="absolute inset-0"
        onClick={requestClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-busy={actionPending}
        aria-label="리소스 권한 부여"
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          if (actionPending) {
            event.preventDefault();
            return;
          }
          onClose();
        }}
        className="relative flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <h2 className="text-lg font-semibold text-slate-950">권한 부여</h2>
            <p className="mt-1 text-sm text-slate-500">
              리소스와 부여 대상을 표에서 선택한 뒤 권한을 적용합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={requestClose}
            disabled={actionPending}
            aria-label="권한 부여 닫기"
            className="rounded-md p-2 text-slate-500 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5">
          <section className="rounded-lg border border-slate-200 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-950">
                  1. 리소스 선택 ({draftResourceIds.length})
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  권한을 적용할 리소스를 하나 이상 선택하세요.
                </p>
              </div>
              <RefreshCw className="h-4 w-4 text-blue-600" aria-hidden="true" />
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
              <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                리소스 검색
                <span className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                  <input
                    type="search"
                    disabled={actionPending}
                    value={resourceQuery}
                    onChange={(event) => setResourceQuery(event.target.value)}
                    className="h-10 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm"
                    placeholder="리소스 이름 검색"
                  />
                </span>
              </label>
              <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                리소스 유형
                <select
                  disabled={actionPending}
                  value={draftResourceType}
                  onChange={(event) => {
                    const nextType = event.target.value as ResourceType;
                    setDraftResourceType(nextType);
                    const nextResourceId = getResourceOptions(nextType)[0]?.id;
                    setDraftResourceIds(nextResourceId ? [nextResourceId] : []);
                    setResourceQuery('');
                  }}
                  className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900"
                >
                  <option value="workflow">Workflow</option>
                  <option value="knowledge_base">Knowledge Base</option>
                  <option value="llm_credential">LLM Credential</option>
                  <option value="mail_credential">Mail Credential</option>
                </select>
              </label>
            </div>
            <div
              role="region"
              aria-label="리소스 목록"
              tabIndex={0}
              className="mt-3 max-h-[500px] overflow-auto rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <table className="w-full min-w-[620px] text-left text-sm" aria-label="권한 대상 리소스">
                <thead className="sticky top-0 z-10 border-y border-slate-200 bg-white text-xs text-slate-500">
                  <tr><th className="w-12 px-3 py-2">선택</th><th className="px-3 py-2">리소스 이름</th><th className="px-3 py-2">유형</th><th className="px-3 py-2">설명</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleResources.length === 0 ? (
                    <tr><td colSpan={4} className="px-3 py-8 text-center text-slate-500">선택 가능한 리소스가 없습니다.</td></tr>
                  ) : (
                    visibleResources.map((item) => (
                      <tr key={item.id}>
                        <td className="px-3 py-3"><input type="checkbox" disabled={actionPending} checked={draftResourceIds.includes(item.id)} onChange={() => toggleSelection(item.id, draftResourceIds, setDraftResourceIds)} aria-label={`${item.name} 선택`} /></td>
                        <td className="px-3 py-3 font-medium text-blue-700">{item.name}</td>
                        <td className="px-3 py-3 text-slate-600">{resourceTypeLabel(draftResourceType)}</td>
                        <td className="px-3 py-3 text-slate-500">현재 조직에서 사용할 수 있는 리소스</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-4 rounded-lg border border-slate-200 p-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-950">
                2. 부여 대상 선택 ({draftGranteeIds.length})
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                팀 또는 사용자를 하나 이상 선택해 권한을 부여합니다.
              </p>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
              <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                대상 검색
                <span className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                  <input type="search" disabled={actionPending} value={granteeQuery} onChange={(event) => setGranteeQuery(event.target.value)} className="h-10 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm" placeholder="팀 또는 사용자 검색" />
                </span>
              </label>
              <label className="flex flex-col gap-1.5 text-xs font-medium text-slate-600">
                대상 유형
                <select disabled={actionPending} value={draftGranteeType} onChange={(event) => {
                  const nextType = event.target.value as GranteeType;
                  setDraftGranteeType(nextType);
                  const nextGranteeId = nextType === 'team'
                    ? activeTeams[0]?.id
                    : activeMembers[0]?.user_id;
                  setDraftGranteeIds(nextGranteeId ? [nextGranteeId] : []);
                  setGranteeQuery('');
                }} className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900">
                  <option value="team">Team</option>
                  <option value="user">User direct</option>
                </select>
              </label>
            </div>
            <div
              role="region"
              aria-label="부여 대상 목록"
              tabIndex={0}
              className="mt-3 max-h-[500px] overflow-auto rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <table className="w-full min-w-[620px] text-left text-sm" aria-label="권한 부여 대상">
                <thead className="sticky top-0 z-10 border-y border-slate-200 bg-white text-xs text-slate-500"><tr><th className="w-12 px-3 py-2">선택</th><th className="px-3 py-2">이름</th><th className="px-3 py-2">유형</th><th className="px-3 py-2">설명</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleGrantees.length === 0 ? (
                    <tr><td colSpan={4} className="px-3 py-8 text-center text-slate-500">선택 가능한 대상이 없습니다.</td></tr>
                  ) : (
                    visibleGrantees.map((item) => (
                      <tr key={item.id}>
                        <td className="px-3 py-3"><input type="checkbox" disabled={actionPending} checked={draftGranteeIds.includes(item.id)} onChange={() => toggleSelection(item.id, draftGranteeIds, setDraftGranteeIds)} aria-label={`${item.name} 선택`} /></td>
                        <td className="px-3 py-3 font-medium text-blue-700">{item.name}</td>
                        <td className="px-3 py-3 text-slate-600">{draftGranteeType === 'team' ? 'Team' : 'User direct'}</td>
                        <td className="px-3 py-3 text-slate-500">{item.detail}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-4 rounded-lg border border-slate-200 p-4">
            <h3 className="text-sm font-semibold text-slate-950">3. 권한 선택</h3>
            <p className="mt-1 text-xs text-slate-500">선택한 리소스와 대상에 적용할 권한입니다.</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {RESOURCE_AUTH_STATES.map((state) => (
                <label key={state} className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-3 text-sm ${draftAuthState === state ? 'border-blue-600 bg-blue-50 text-blue-800' : 'border-slate-300 text-slate-700'}`}>
                  <input type="radio" name="permission-auth-state" value={state} disabled={actionPending} checked={draftAuthState === state} onChange={() => setDraftAuthState(state)} />
                  {resourcePermissionLabel(draftResourceType, state)}
                </label>
              ))}
            </div>
          </section>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white px-6 py-4">
          <p className="text-sm text-slate-600">
            리소스 {draftResourceIds.length}개 · 대상 {draftGranteeIds.length}개 · 총 {grantPairCount}건 · {resourcePermissionLabel(draftResourceType, draftAuthState)}
            {!withinBulkLimit && ` · 최대 ${MAX_BULK_PERMISSION_GRANTS}건`}
          </p>
          <div className="flex items-center gap-2">
            <button type="button" onClick={requestClose} disabled={actionPending} className="h-10 rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">취소</button>
            <button type="button" onClick={submitGrant} disabled={!canSubmit} className="h-10 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40">{actionPending ? '저장 중...' : '선택한 권한 부여'}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function resourcePermissionLabel(
  resourceType: ResourceType,
  state: ResourceAuthState,
) {
  if (resourceType === 'llm_credential' || resourceType === 'mail_credential') {
    const labels: Record<ResourceAuthState, string> = {
      viewer: 'Credential 조회 가능',
      operator: 'Credential 사용 가능',
      builder: 'Credential 수정 가능',
      manager: 'Credential 관리 가능',
    };
    return labels[state];
  }
  if (resourceType === 'knowledge_base') {
    const labels: Record<ResourceAuthState, string> = {
      viewer: 'KB 조회 가능',
      operator: 'KB 사용 가능',
      builder: 'KB 수정 가능',
      manager: 'KB 관리 가능',
    };
    return labels[state];
  }
  const labels: Record<ResourceAuthState, string> = {
    viewer: 'Workflow 조회 가능',
    operator: 'Workflow 실행 가능',
    builder: 'Workflow 수정 가능',
    manager: 'Workflow 관리 가능',
  };
  return labels[state];
}

function resourceTypeLabel(resourceType: ResourceType) {
  if (resourceType === 'workflow') return 'Workflow';
  if (resourceType === 'knowledge_base') return 'Knowledge Base';
  if (resourceType === 'llm_credential') return 'LLM Credential';
  return 'Mail Credential';
}

function PermissionList({
  title,
  rows,
  resourceType,
  granteeType,
  onRevoke,
}: {
  title: string;
  rows: ResourcePermissionEntry[];
  resourceType: ResourceType;
  granteeType: GranteeType;
  onRevoke: (
    resourceType: ResourceType,
    granteeType: GranteeType,
    granteeId: string,
    label: string,
  ) => void;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-slate-200">
      <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold uppercase text-slate-500">
        {title}
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-slate-500">
          부여된 권한이 없습니다.
        </div>
      ) : (
        <div className="divide-y divide-slate-100">
          {rows.map((row) => (
            <div
              key={row.id}
              className="flex items-center justify-between gap-3 px-4 py-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-950">
                  {row.grantee_name}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {resourcePermissionLabel(resourceType, row.auth_state)} ·{' '}
                  {formatDateTime(row.assigned_at)}
                </p>
              </div>
              <button
                onClick={() =>
                  onRevoke(
                    resourceType,
                    granteeType,
                    row.grantee_id,
                    `${row.grantee_name} · ${resourcePermissionLabel(
                      resourceType,
                      row.auth_state,
                    )}`,
                  )
                }
                className="rounded-md border border-red-200 px-2 py-1 text-xs font-semibold text-red-700 hover:bg-red-50"
              >
                회수
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PermissionPlaceholder({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="font-semibold text-slate-900">{title}</p>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-500">
        {description}
      </p>
    </div>
  );
}
