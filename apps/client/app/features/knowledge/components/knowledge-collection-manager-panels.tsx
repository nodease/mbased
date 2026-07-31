'use client';

import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import {
  Archive,
  ArrowDown,
  ArrowUp,
  Check,
  Eye,
  EyeOff,
  FolderPlus,
  Link2,
  Loader2,
  Plus,
  RotateCcw,
  Search,
  ShieldAlert,
  Trash2,
  Users,
} from 'lucide-react';
import type {
  KnowledgeCollectionItemsResponse,
  KnowledgeCollectionLinkCandidate,
  KnowledgeCollectionPermissionResponse,
  KnowledgeCollectionRoleBundle,
  KnowledgeCollectionResponse,
  KnowledgeCollectionVisibility,
  KnowledgeDelegationSubjectsResponse,
  KnowledgeDomainAction,
  KnowledgeDomainPermissionListResponse,
} from '@/app/features/knowledge/api/knowledgeApi';

export type CollectionCapabilities = {
  can_create_collection: boolean;
  can_change_public_visibility: boolean;
  can_manage_catalog: boolean;
  can_delegate_permissions: boolean;
  can_manage_lifecycle: boolean;
  can_manage_sync: boolean;
  can_manage_domain_permissions: boolean;
};

export type CollectionFormState = {
  name: string;
  description: string;
  safeLabel: string;
};

export type GrantFormState = {
  subject_type: 'team' | 'user';
  subject_id: string;
  role_bundle: KnowledgeCollectionRoleBundle;
};

export type DomainGrantFormState = {
  subject_type: 'team' | 'user';
  subject_id: string;
  permission_action: KnowledgeDomainAction;
};

const domainActions: Array<{ value: KnowledgeDomainAction; label: string }> = [
  { value: 'catalog_manage', label: 'Catalog manager' },
  { value: 'permission_delegate', label: 'Permission delegator' },
  { value: 'lifecycle_manage', label: 'Lifecycle manager' },
  { value: 'sync_manage', label: 'Sync manager' },
];

const collectionRoleBundles: Array<{
  value: KnowledgeCollectionRoleBundle;
  label: string;
}> = [
  { value: 'viewer', label: 'Viewer (read)' },
  { value: 'workflow_router', label: 'Workflow Router (read + route)' },
  { value: 'maintainer', label: 'Maintainer (read + manage)' },
  { value: 'sync_operator', label: 'Sync Operator (read + sync)' },
];

const isCollectionOrderConflict = (error: unknown): boolean => {
  if (typeof error !== 'object' || error === null || !('response' in error)) {
    return false;
  }
  return (
    (
      error as {
        response?: { data?: { error?: { details?: { reason?: string } } } };
      }
    ).response?.data?.error?.details?.reason === 'collection_order_stale'
  );
};

type DomainDelegationPanelProps = {
  form: DomainGrantFormState;
  hasSubjectLoadError: boolean;
  isSubjectLoading: boolean;
  isSaving: boolean;
  permissions: KnowledgeDomainPermissionListResponse['permissions'];
  subjectQuery: string;
  subjects: KnowledgeDelegationSubjectsResponse;
  onGrant: () => void;
  onLoadMoreSubjects: () => void;
  onRetrySubjects: () => void;
  onRevoke: (
    subjectType: 'team' | 'user',
    subjectId: string,
    action: KnowledgeDomainAction,
  ) => void;
  onSubjectQueryChange: (query: string) => void;
  setForm: Dispatch<SetStateAction<DomainGrantFormState>>;
};

export function DomainDelegationPanel({
  form,
  hasSubjectLoadError,
  isSubjectLoading,
  isSaving,
  permissions,
  subjectQuery,
  subjects,
  onGrant,
  onLoadMoreSubjects,
  onRetrySubjects,
  onRevoke,
  onSubjectQueryChange,
  setForm,
}: DomainDelegationPanelProps) {
  const subjectOptions = subjects.subjects ?? [];
  return (
    <section className="rounded-lg border border-blue-200 bg-blue-50/40 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Users className="h-4 w-4 text-blue-700" />
        <h2 className="text-sm font-bold text-slate-900">
          Knowledge 관리 위임
        </h2>
      </div>
      <p className="mb-4 text-xs text-slate-600">
        Team에 관리 역할을 먼저 위임합니다. 이 권한은 KB use나 문서 원문 접근을
        자동으로 부여하지 않습니다.
      </p>
      <div className="grid gap-2 md:grid-cols-[120px_1fr_180px_auto]">
        <select
          value={form.subject_type}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              subject_type: event.target.value as 'team' | 'user',
              subject_id: '',
            }))
          }
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm"
        >
          <option value="team">team</option>
          <option value="user">user</option>
        </select>
        <div className="space-y-2">
          <label className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2">
            <Search className="h-4 w-4 text-slate-400" />
            <input
              value={subjectQuery}
              onChange={(event) => onSubjectQueryChange(event.target.value)}
              maxLength={100}
              placeholder="이름으로 검색"
              className="min-w-0 flex-1 bg-transparent text-sm outline-none"
            />
          </label>
          <select
            value={form.subject_id}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                subject_id: event.target.value,
              }))
            }
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm"
          >
            <option value="">
              {isSubjectLoading ? '검색 중…' : '대상 선택'}
            </option>
            {subjectOptions.map((subject) => (
              <option key={subject.subject_id} value={subject.subject_id}>
                {subject.subject_safe_label}
              </option>
            ))}
          </select>
          {subjects.next_cursor && (
            <button
              type="button"
              onClick={onLoadMoreSubjects}
              disabled={isSubjectLoading}
              className="text-xs font-semibold text-blue-700 disabled:text-slate-400"
            >
              대상 더 보기
            </button>
          )}
          {hasSubjectLoadError && (
            <button
              type="button"
              onClick={onRetrySubjects}
              disabled={isSubjectLoading}
              className="text-xs font-semibold text-red-700 disabled:text-slate-400"
            >
              대상 조회 다시 시도
            </button>
          )}
        </div>
        <select
          value={form.permission_action}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              permission_action: event.target.value as KnowledgeDomainAction,
            }))
          }
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm"
        >
          {domainActions.map((action) => (
            <option key={action.value} value={action.value}>
              {action.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onGrant}
          disabled={isSaving || !form.subject_id}
          className="rounded-md bg-blue-700 px-3 py-2 text-sm font-semibold text-white disabled:bg-slate-300"
        >
          위임
        </button>
      </div>
      {permissions.length > 0 && (
        <div className="mt-4 grid gap-2 md:grid-cols-2">
          {permissions.map((permission) => (
            <div
              key={permission.permission_id}
              className="flex items-center justify-between rounded-md border border-blue-100 bg-white px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate">
                <strong>{permission.subject_safe_label}</strong>{' '}
                <span className="text-slate-500">
                  {permission.permission_action}
                  {permission.is_expired ? ' · expired' : ''}
                </span>
              </span>
              <button
                type="button"
                onClick={() =>
                  onRevoke(
                    permission.subject_type,
                    permission.subject_id,
                    permission.permission_action,
                  )
                }
                disabled={isSaving}
                className="rounded-md p-2 text-slate-500 hover:bg-red-50 hover:text-red-600"
                aria-label="Knowledge 관리 위임 회수"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

type CollectionSidebarProps = {
  capabilities: CollectionCapabilities;
  collections: KnowledgeCollectionResponse[];
  form: CollectionFormState;
  isLoading: boolean;
  isSaving: boolean;
  lifecycleState: 'active' | 'archived';
  selectedBulkIds: string[];
  selectedId: string | null;
  onCreate: () => void;
  onSelect: (collectionId: string) => void;
  onLifecycleStateChange: (state: 'active' | 'archived') => void;
  onToggleBulkCollection: (collectionId: string) => void;
  setForm: Dispatch<SetStateAction<CollectionFormState>>;
};

export function CollectionSidebar({
  capabilities,
  collections,
  form,
  isLoading,
  isSaving,
  lifecycleState,
  selectedBulkIds,
  selectedId,
  onCreate,
  onSelect,
  onLifecycleStateChange,
  onToggleBulkCollection,
  setForm,
}: CollectionSidebarProps) {
  return (
    <div className="space-y-4">
      {capabilities.can_create_collection && lifecycleState === 'active' && (
        <CollectionCreatePanel
          form={form}
          isSaving={isSaving}
          onCreate={onCreate}
          setForm={setForm}
        />
      )}

      <CollectionListPanel
        canDelegatePermissions={capabilities.can_delegate_permissions}
        collections={collections}
        isLoading={isLoading}
        lifecycleState={lifecycleState}
        selectedBulkIds={selectedBulkIds}
        selectedId={selectedId}
        onLifecycleStateChange={onLifecycleStateChange}
        onSelect={onSelect}
        onToggleBulkCollection={onToggleBulkCollection}
      />
    </div>
  );
}

type CollectionCreatePanelProps = {
  form: CollectionFormState;
  isSaving: boolean;
  onCreate: () => void;
  setForm: Dispatch<SetStateAction<CollectionFormState>>;
};

function CollectionCreatePanel({
  form,
  isSaving,
  onCreate,
  setForm,
}: CollectionCreatePanelProps) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center gap-2">
        <FolderPlus className="h-4 w-4 text-blue-600" />
        <h2 className="text-sm font-bold text-slate-900">Collection 생성</h2>
      </div>
      <div className="space-y-3">
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">
            관리용 이름
          </span>
          <input
            value={form.name}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
            placeholder="Collection 이름"
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">
            안전 표시 이름
          </span>
          <input
            value={form.safeLabel}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                safeLabel: event.target.value,
              }))
            }
            maxLength={255}
            placeholder="Workflow에서 표시할 이름"
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </label>
        <p className="text-xs leading-5 text-slate-500">
          Workflow picker에는 관리용 이름 대신 이 안전 표시 이름만 노출됩니다.
        </p>
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">설명</span>
          <textarea
            value={form.description}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                description: event.target.value,
              }))
            }
            placeholder="설명"
            rows={3}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </label>
        <button
          type="button"
          onClick={onCreate}
          disabled={isSaving || !form.name.trim() || !form.safeLabel.trim()}
          className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isSaving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          생성
        </button>
      </div>
    </div>
  );
}

type CollectionListPanelProps = {
  canDelegatePermissions: boolean;
  collections: KnowledgeCollectionResponse[];
  isLoading: boolean;
  lifecycleState: 'active' | 'archived';
  selectedBulkIds: string[];
  selectedId: string | null;
  onLifecycleStateChange: (state: 'active' | 'archived') => void;
  onSelect: (collectionId: string) => void;
  onToggleBulkCollection: (collectionId: string) => void;
};

function CollectionListPanel({
  canDelegatePermissions,
  collections,
  isLoading,
  lifecycleState,
  selectedBulkIds,
  selectedId,
  onLifecycleStateChange,
  onSelect,
  onToggleBulkCollection,
}: CollectionListPanelProps) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-bold text-slate-900">
          Knowledge Collections
        </h2>
        <div className="mt-3 grid grid-cols-2 gap-2" role="tablist">
          {(['active', 'archived'] as const).map((state) => (
            <button
              key={state}
              type="button"
              role="tab"
              aria-selected={lifecycleState === state}
              onClick={() => onLifecycleStateChange(state)}
              className={`rounded-md px-2 py-1.5 text-xs font-semibold ${
                lifecycleState === state
                  ? 'bg-slate-900 text-white'
                  : 'bg-slate-100 text-slate-600'
              }`}
            >
              {state}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
        </div>
      ) : collections.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-slate-500">
          등록된 Collection이 없습니다.
        </div>
      ) : (
        <div className="divide-y divide-slate-100">
          {collections.map((collection) => (
            <div
              key={collection.id}
              className={`flex items-start gap-2 px-3 py-3 transition-colors ${
                selectedId === collection.id
                  ? 'bg-blue-50'
                  : 'hover:bg-slate-50'
              }`}
            >
              <input
                type="checkbox"
                checked={selectedBulkIds.includes(collection.id)}
                onChange={() => onToggleBulkCollection(collection.id)}
                disabled={!collection.can_manage && !canDelegatePermissions}
                aria-label={`${collection.name} bulk 권한 대상 선택`}
                className="mt-1 disabled:cursor-not-allowed"
              />
              <button
                type="button"
                onClick={() => onSelect(collection.id)}
                className="min-w-0 flex-1 text-left"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-sm font-semibold text-slate-900">
                    {collection.name}
                  </span>
                  <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">
                    {collection.visibility === 'public' ? 'public' : 'private'}
                  </span>
                </div>
                <p className="mt-1 truncate text-xs text-slate-500">
                  {collection.description || '설명 없음'}
                </p>
                <div className="mt-2 flex gap-2 text-xs text-slate-500">
                  <span>KB {collection.linked_kb_count_bucket}</span>
                  <span>
                    {collection.is_system_managed ? 'system' : 'manual'}
                  </span>
                </div>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type CollectionDetailPanelProps = {
  acknowledgePublic: boolean;
  candidates: KnowledgeCollectionLinkCandidate[];
  capabilities: CollectionCapabilities;
  collection: KnowledgeCollectionResponse | null;
  editForm: CollectionFormState;
  grantForm: GrantFormState;
  hasSubjectLoadError: boolean;
  isDetailLoading: boolean;
  isSubjectLoading: boolean;
  isSaving: boolean;
  itemData: KnowledgeCollectionItemsResponse | null;
  permissions: KnowledgeCollectionPermissionResponse[];
  selectedBulkCount: number;
  subjectQuery: string;
  subjects: KnowledgeDelegationSubjectsResponse;
  onArchive: () => void;
  onBulkPermission: (operation: 'grant' | 'revoke') => void;
  onGrantPermission: () => void;
  onLinkCandidate: (candidateId: string) => void;
  onLoadMoreSubjects: () => void;
  onRetrySubjects: () => void;
  onReorderItems: (
    items: { item_id: string; rank: number }[],
    expectedOrderRevision: string,
  ) => Promise<void>;
  onRevokePermission: (permissionId: string) => void;
  onRevokePermissionBundle: () => void;
  onRestore: () => void;
  onSubjectQueryChange: (query: string) => void;
  onUnlinkItem: (itemId: string) => void;
  onUpdateCollection: () => void;
  onUpdateVisibility: (visibility: KnowledgeCollectionVisibility) => void;
  setAcknowledgePublic: Dispatch<SetStateAction<boolean>>;
  setEditForm: Dispatch<SetStateAction<CollectionFormState>>;
  setGrantForm: Dispatch<SetStateAction<GrantFormState>>;
};

export function CollectionDetailPanel({
  acknowledgePublic,
  candidates,
  capabilities,
  collection,
  editForm,
  grantForm,
  hasSubjectLoadError,
  isDetailLoading,
  isSubjectLoading,
  isSaving,
  itemData,
  permissions,
  selectedBulkCount,
  subjectQuery,
  subjects,
  onArchive,
  onBulkPermission,
  onGrantPermission,
  onLinkCandidate,
  onLoadMoreSubjects,
  onRetrySubjects,
  onReorderItems,
  onRevokePermission,
  onRevokePermissionBundle,
  onRestore,
  onSubjectQueryChange,
  onUnlinkItem,
  onUpdateCollection,
  onUpdateVisibility,
  setAcknowledgePublic,
  setEditForm,
  setGrantForm,
}: CollectionDetailPanelProps) {
  if (!collection) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-sm text-slate-500">
        Collection을 선택하세요.
      </div>
    );
  }

  return (
    <div className="space-y-6 p-5">
      <CollectionHeader
        canArchive={collection.can_manage || capabilities.can_manage_lifecycle}
        canRestore={collection.can_manage || capabilities.can_manage_lifecycle}
        collection={collection}
        isSaving={isSaving}
        onArchive={onArchive}
        onRestore={onRestore}
      />

      <section className="grid gap-4 md:grid-cols-2">
        <CollectionInfoPanel
          canManageCatalog={capabilities.can_manage_catalog}
          collection={collection}
          editForm={editForm}
          isSaving={isSaving}
          onUpdateCollection={onUpdateCollection}
          setEditForm={setEditForm}
        />
        <CollectionVisibilityPanel
          acknowledgePublic={acknowledgePublic}
          canChangeVisibility={capabilities.can_change_public_visibility}
          collection={collection}
          isSaving={isSaving}
          onUpdateVisibility={onUpdateVisibility}
          setAcknowledgePublic={setAcknowledgePublic}
        />
      </section>

      <CollectionItemsPanel
        acknowledgePublic={acknowledgePublic}
        candidates={candidates}
        collection={collection}
        canManageCatalog={capabilities.can_manage_catalog}
        canManagePublicMembership={capabilities.can_change_public_visibility}
        isDetailLoading={isDetailLoading}
        isSaving={isSaving}
        itemData={itemData}
        onLinkCandidate={onLinkCandidate}
        onReorderItems={onReorderItems}
        onUnlinkItem={onUnlinkItem}
      />

      <CollectionPermissionsPanel
        canDelegatePermissions={capabilities.can_delegate_permissions}
        collection={collection}
        grantForm={grantForm}
        hasSubjectLoadError={hasSubjectLoadError}
        isSubjectLoading={isSubjectLoading}
        isSaving={isSaving}
        permissions={permissions}
        selectedBulkCount={selectedBulkCount}
        subjectQuery={subjectQuery}
        subjects={subjects}
        onBulkPermission={onBulkPermission}
        onGrantPermission={onGrantPermission}
        onLoadMoreSubjects={onLoadMoreSubjects}
        onRetrySubjects={onRetrySubjects}
        onRevokePermission={onRevokePermission}
        onRevokePermissionBundle={onRevokePermissionBundle}
        onSubjectQueryChange={onSubjectQueryChange}
        setGrantForm={setGrantForm}
      />

      {collection.visibility === 'public' && <PublicCollectionWarning />}
    </div>
  );
}

type CollectionHeaderProps = {
  canArchive: boolean;
  canRestore: boolean;
  collection: KnowledgeCollectionResponse;
  isSaving: boolean;
  onArchive: () => void;
  onRestore: () => void;
};

function CollectionHeader({
  canArchive,
  canRestore,
  collection,
  isSaving,
  onArchive,
  onRestore,
}: CollectionHeaderProps) {
  return (
    <header className="flex flex-col gap-3 border-b border-slate-100 pb-5 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0">
        <h2 className="truncate text-lg font-bold text-slate-950">
          {collection.name}
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {collection.description || '설명 없음'}
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold">
          <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-600">
            {collection.lifecycle_state}
          </span>
          <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-600">
            {collection.sync_state}
          </span>
          <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-600">
            KB {collection.linked_kb_count_bucket}
          </span>
        </div>
      </div>
      {collection.lifecycle_state === 'archived' ? (
        <button
          type="button"
          onClick={onRestore}
          disabled={
            isSaving ||
            !canRestore ||
            collection.is_system_managed ||
            collection.sync_state === 'source_deleted'
          }
          className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          <RotateCcw className="h-4 w-4" />
          Restore
        </button>
      ) : (
        <button
          type="button"
          onClick={onArchive}
          disabled={isSaving || !canArchive || collection.is_system_managed}
          className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          <Archive className="h-4 w-4" />
          Archive
        </button>
      )}
    </header>
  );
}

type CollectionInfoPanelProps = {
  canManageCatalog: boolean;
  collection: KnowledgeCollectionResponse;
  editForm: CollectionFormState;
  isSaving: boolean;
  onUpdateCollection: () => void;
  setEditForm: Dispatch<SetStateAction<CollectionFormState>>;
};

function CollectionInfoPanel({
  canManageCatalog,
  collection,
  editForm,
  isSaving,
  onUpdateCollection,
  setEditForm,
}: CollectionInfoPanelProps) {
  const isEditable =
    collection.lifecycle_state === 'active' &&
    (collection.can_manage || canManageCatalog) &&
    !collection.is_system_managed;

  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <h3 className="mb-3 text-sm font-bold text-slate-900">정보</h3>
      <div className="space-y-3">
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">
            관리용 이름
          </span>
          <input
            value={editForm.name}
            onChange={(event) =>
              setEditForm((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
            disabled={!isEditable}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-50"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">
            안전 표시 이름
          </span>
          <input
            value={editForm.safeLabel}
            onChange={(event) =>
              setEditForm((current) => ({
                ...current,
                safeLabel: event.target.value,
              }))
            }
            disabled={!isEditable}
            maxLength={255}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-50"
          />
        </label>
        {isEditable && !editForm.safeLabel.trim() && (
          <p
            role="status"
            className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            Workflow에서 이 Collection을 구분할 수 있도록 안전 표시 이름을
            입력하세요.
          </p>
        )}
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">설명</span>
          <textarea
            value={editForm.description}
            onChange={(event) =>
              setEditForm((current) => ({
                ...current,
                description: event.target.value,
              }))
            }
            disabled={!isEditable}
            rows={3}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-50"
          />
        </label>
        <button
          type="button"
          onClick={onUpdateCollection}
          disabled={
            isSaving ||
            !isEditable ||
            !editForm.name.trim() ||
            !editForm.safeLabel.trim()
          }
          className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <Check className="h-4 w-4" />
          저장
        </button>
      </div>
    </div>
  );
}

type CollectionVisibilityPanelProps = {
  acknowledgePublic: boolean;
  canChangeVisibility: boolean;
  collection: KnowledgeCollectionResponse;
  isSaving: boolean;
  onUpdateVisibility: (visibility: KnowledgeCollectionVisibility) => void;
  setAcknowledgePublic: Dispatch<SetStateAction<boolean>>;
};

function CollectionVisibilityPanel({
  acknowledgePublic,
  canChangeVisibility,
  collection,
  isSaving,
  onUpdateVisibility,
  setAcknowledgePublic,
}: CollectionVisibilityPanelProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <div className="mb-3 flex items-center gap-2">
        {collection.visibility === 'public' ? (
          <Eye className="h-4 w-4 text-emerald-600" />
        ) : (
          <EyeOff className="h-4 w-4 text-slate-500" />
        )}
        <h3 className="text-sm font-bold text-slate-900">공개 상태</h3>
      </div>
      <p className="text-sm font-semibold text-slate-700">
        {collection.visibility === 'public' ? 'public' : 'private'}
      </p>
      {canChangeVisibility && collection.lifecycle_state === 'active' ? (
        <>
          <label className="mt-4 flex items-start gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={acknowledgePublic}
              onChange={(event) => setAcknowledgePublic(event.target.checked)}
              className="mt-1"
            />
            <span>
              public 전환과 public Collection의 KB 연결 변경이 execution subject
              없는 RAG 후보 범위에 영향을 줄 수 있음을 확인했습니다.
            </span>
          </label>
          <div className="mt-4 flex gap-2">
            <button
              type="button"
              onClick={() => onUpdateVisibility('public')}
              disabled={
                isSaving ||
                !acknowledgePublic ||
                collection.visibility === 'public'
              }
              className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              public
            </button>
            <button
              type="button"
              onClick={() => onUpdateVisibility('private')}
              disabled={isSaving || collection.visibility === 'private'}
              className="rounded-md border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:text-slate-300"
            >
              private
            </button>
          </div>
        </>
      ) : (
        <p className="mt-4 text-sm text-slate-500">
          공개 상태 전환은 organization manager만 수행할 수 있습니다.
        </p>
      )}
    </div>
  );
}

type CollectionItemsPanelProps = {
  acknowledgePublic: boolean;
  canManageCatalog: boolean;
  canManagePublicMembership: boolean;
  candidates: KnowledgeCollectionLinkCandidate[];
  collection: KnowledgeCollectionResponse;
  isDetailLoading: boolean;
  isSaving: boolean;
  itemData: KnowledgeCollectionItemsResponse | null;
  onLinkCandidate: (candidateId: string) => void;
  onReorderItems: (
    items: { item_id: string; rank: number }[],
    expectedOrderRevision: string,
  ) => Promise<void>;
  onUnlinkItem: (itemId: string) => void;
};

function CollectionItemsPanel({
  acknowledgePublic,
  canManageCatalog,
  canManagePublicMembership,
  candidates,
  collection,
  isDetailLoading,
  isSaving,
  itemData,
  onLinkCandidate,
  onReorderItems,
  onUnlinkItem,
}: CollectionItemsPanelProps) {
  const serverItems = itemData?.items ?? [];
  const [draftItems, setDraftItems] = useState(serverItems);
  const [orderConflict, setOrderConflict] = useState(false);

  useEffect(() => {
    setDraftItems(itemData?.items ?? []);
    setOrderConflict(false);
  }, [itemData]);

  const canMutateMembership =
    collection.lifecycle_state !== 'active'
      ? false
      : collection.visibility === 'public'
        ? canManagePublicMembership && acknowledgePublic
        : collection.can_manage || canManageCatalog;
  const isOrderDirty = draftItems.some(
    (item, index) => serverItems[index]?.item_id !== item.item_id,
  );
  const canReorder =
    canMutateMembership &&
    Boolean(itemData?.reorder_supported) &&
    draftItems.length > 1;

  const moveItem = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (!canReorder || nextIndex < 0 || nextIndex >= draftItems.length) return;
    setDraftItems((current) => {
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
    setOrderConflict(false);
  };

  const saveOrder = async () => {
    if (!itemData || !isOrderDirty) return;
    try {
      await onReorderItems(
        draftItems.map((item, rank) => ({ item_id: item.item_id, rank })),
        itemData.order_revision,
      );
    } catch (error) {
      setOrderConflict(isCollectionOrderConflict(error));
    }
  };
  return (
    <section className="rounded-lg border border-slate-200 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Link2 className="h-4 w-4 text-blue-600" />
        <h3 className="text-sm font-bold text-slate-900">연결된 KB</h3>
        {isDetailLoading && (
          <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
        )}
      </div>
      {!itemData?.reorder_supported && itemData?.safe_reason_code && (
        <p className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          항목이 많아 이 화면에서는 순서를 변경할 수 없습니다.
        </p>
      )}
      {orderConflict && (
        <p
          role="alert"
          className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800"
        >
          다른 변경이 먼저 저장되었습니다. Collection을 다시 선택해 최신 순서를
          불러온 뒤 다시 시도하세요.
        </p>
      )}
      {isOrderDirty && (
        <div className="mb-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setDraftItems(serverItems)}
            disabled={isSaving}
            className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:text-slate-300"
          >
            순서 취소
          </button>
          <button
            type="button"
            onClick={saveOrder}
            disabled={isSaving || !canReorder}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:bg-slate-300"
          >
            순서 저장
          </button>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-2">
          {draftItems.length === 0 ? (
            <p className="text-sm text-slate-500">연결된 KB가 없습니다.</p>
          ) : (
            draftItems.map((item, index) => (
              <div
                key={item.item_id}
                className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-800">
                    {item.safe_label || 'Knowledge Base'}
                  </p>
                  <p className="text-xs text-slate-500">
                    use {item.can_use_kb ? '가능' : '불가'} · manage{' '}
                    {item.can_manage_kb ? '가능' : '불가'}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => moveItem(index, -1)}
                    disabled={isSaving || !canReorder || index === 0}
                    className="rounded-md p-1.5 text-slate-500 disabled:text-slate-300"
                    aria-label={`${item.safe_label || 'Knowledge Base'} 위로 이동`}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveItem(index, 1)}
                    disabled={
                      isSaving || !canReorder || index === draftItems.length - 1
                    }
                    className="rounded-md p-1.5 text-slate-500 disabled:text-slate-300"
                    aria-label={`${item.safe_label || 'Knowledge Base'} 아래로 이동`}
                  >
                    <ArrowDown className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onUnlinkItem(item.item_id)}
                    disabled={isSaving || !canMutateMembership || isOrderDirty}
                    className="rounded-md p-2 text-slate-500 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:text-slate-300"
                    aria-label="KB 연결 해제"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
        <div className="space-y-2">
          {candidates.length === 0 ? (
            <p className="text-sm text-slate-500">연결 후보가 없습니다.</p>
          ) : (
            candidates.map((candidate) => (
              <div
                key={candidate.knowledge_base_id}
                className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2"
              >
                <span className="truncate text-sm font-semibold text-slate-800">
                  {candidate.safe_label || 'Knowledge Base'}
                </span>
                <button
                  type="button"
                  onClick={() => onLinkCandidate(candidate.knowledge_base_id)}
                  disabled={
                    isSaving || candidate.disabled || !canMutateMembership
                  }
                  className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  추가
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

type CollectionPermissionsPanelProps = {
  canDelegatePermissions: boolean;
  collection: KnowledgeCollectionResponse;
  grantForm: GrantFormState;
  hasSubjectLoadError: boolean;
  isSubjectLoading: boolean;
  isSaving: boolean;
  permissions: KnowledgeCollectionPermissionResponse[];
  selectedBulkCount: number;
  subjectQuery: string;
  subjects: KnowledgeDelegationSubjectsResponse;
  onBulkPermission: (operation: 'grant' | 'revoke') => void;
  onGrantPermission: () => void;
  onLoadMoreSubjects: () => void;
  onRetrySubjects: () => void;
  onRevokePermission: (permissionId: string) => void;
  onRevokePermissionBundle: () => void;
  onSubjectQueryChange: (query: string) => void;
  setGrantForm: Dispatch<SetStateAction<GrantFormState>>;
};

function CollectionPermissionsPanel({
  canDelegatePermissions,
  collection,
  grantForm,
  hasSubjectLoadError,
  isSubjectLoading,
  isSaving,
  permissions,
  selectedBulkCount,
  subjectQuery,
  subjects,
  onBulkPermission,
  onGrantPermission,
  onLoadMoreSubjects,
  onRetrySubjects,
  onRevokePermission,
  onRevokePermissionBundle,
  onSubjectQueryChange,
  setGrantForm,
}: CollectionPermissionsPanelProps) {
  const canManagePermissions = collection.can_manage || canDelegatePermissions;
  const activeSubjectOptions = subjects.subjects ?? [];
  const activeSubjectIds = new Set(
    activeSubjectOptions.map((subject) => subject.subject_id),
  );
  const existingSubjectOptions = permissions
    .filter(
      (permission, index, rows) =>
        permission.subject_type === grantForm.subject_type &&
        !activeSubjectIds.has(permission.subject_id) &&
        rows.findIndex(
          (candidate) =>
            candidate.subject_type === permission.subject_type &&
            candidate.subject_id === permission.subject_id,
        ) === index,
    )
    .map((permission) => ({
      subject_type: permission.subject_type,
      subject_id: permission.subject_id,
      subject_safe_label:
        permission.subject_safe_label ||
        (permission.subject_type === 'team' ? 'Team' : 'User'),
      existingPermissionOnly: true,
    }));
  const subjectOptions = [
    ...activeSubjectOptions.map((subject) => ({
      ...subject,
      existingPermissionOnly: false,
    })),
    ...existingSubjectOptions,
  ];
  const selectedSubjectIsActive = activeSubjectIds.has(grantForm.subject_id);
  return (
    <section className="rounded-lg border border-slate-200 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Users className="h-4 w-4 text-slate-600" />
        <h3 className="text-sm font-bold text-slate-900">권한</h3>
      </div>
      {canManagePermissions ? (
        <div className="mb-4 grid gap-2 md:grid-cols-[120px_1fr_160px_auto]">
          <select
            value={grantForm.subject_type}
            onChange={(event) =>
              setGrantForm((current) => ({
                ...current,
                subject_type: event.target.value as 'team' | 'user',
                subject_id: '',
              }))
            }
            className="rounded-md border border-slate-200 px-3 py-2 text-sm"
          >
            <option value="team">team</option>
            <option value="user">user</option>
          </select>
          <div className="space-y-2">
            <label className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                value={subjectQuery}
                onChange={(event) => onSubjectQueryChange(event.target.value)}
                maxLength={100}
                placeholder="이름으로 검색"
                className="min-w-0 flex-1 text-sm outline-none"
              />
            </label>
            <select
              value={grantForm.subject_id}
              onChange={(event) =>
                setGrantForm((current) => ({
                  ...current,
                  subject_id: event.target.value,
                }))
              }
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
            >
              <option value="">
                {isSubjectLoading ? '검색 중…' : '대상 선택'}
              </option>
              {subjectOptions.map((subject) => (
                <option key={subject.subject_id} value={subject.subject_id}>
                  {subject.subject_safe_label}
                  {subject.existingPermissionOnly ? ' (기존 권한)' : ''}
                </option>
              ))}
            </select>
            {subjects.next_cursor && (
              <button
                type="button"
                onClick={onLoadMoreSubjects}
                disabled={isSubjectLoading}
                className="text-xs font-semibold text-blue-700 disabled:text-slate-400"
              >
                대상 더 보기
              </button>
            )}
            {hasSubjectLoadError && (
              <button
                type="button"
                onClick={onRetrySubjects}
                disabled={isSubjectLoading}
                className="text-xs font-semibold text-red-700 disabled:text-slate-400"
              >
                대상 조회 다시 시도
              </button>
            )}
          </div>
          <select
            value={grantForm.role_bundle}
            onChange={(event) =>
              setGrantForm((current) => ({
                ...current,
                role_bundle: event.target
                  .value as KnowledgeCollectionRoleBundle,
              }))
            }
            className="rounded-md border border-slate-200 px-3 py-2 text-sm"
          >
            {collectionRoleBundles.map((bundle) => (
              <option key={bundle.value} value={bundle.value}>
                {bundle.label}
              </option>
            ))}
          </select>
          <div className="flex flex-col gap-2">
            <button
              type="button"
              onClick={onGrantPermission}
              disabled={
                isSaving ||
                !grantForm.subject_id.trim() ||
                !selectedSubjectIsActive
              }
              className="rounded-md bg-slate-900 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              Bundle 부여
            </button>
            <button
              type="button"
              onClick={onRevokePermissionBundle}
              disabled={isSaving || !grantForm.subject_id.trim()}
              className="rounded-md border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 disabled:text-slate-300"
            >
              Bundle 회수
            </button>
          </div>
        </div>
      ) : (
        <p className="mb-4 text-sm text-slate-500">
          권한 관리는 collection.manage 또는 Knowledge permission_delegate가
          필요합니다.
        </p>
      )}
      {canManagePermissions && selectedBulkCount > 0 && (
        <div className="mb-4 rounded-md border border-blue-100 bg-blue-50 px-3 py-3 text-xs text-slate-700">
          <p>
            선택한 Collection {selectedBulkCount}개에 같은 대상과 bundle을
            all-or-nothing으로 적용합니다.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => onBulkPermission('grant')}
              disabled={
                isSaving ||
                !grantForm.subject_id.trim() ||
                !selectedSubjectIsActive
              }
              className="rounded-md bg-blue-700 px-3 py-1.5 font-semibold text-white disabled:bg-slate-300"
            >
              선택 KC 일괄 부여
            </button>
            <button
              type="button"
              onClick={() => onBulkPermission('revoke')}
              disabled={isSaving || !grantForm.subject_id.trim()}
              className="rounded-md border border-blue-200 bg-white px-3 py-1.5 font-semibold text-blue-700 disabled:text-slate-300"
            >
              선택 KC 일괄 회수
            </button>
          </div>
        </div>
      )}
      {permissions.length === 0 ? (
        <p className="text-sm text-slate-500">표시할 권한이 없습니다.</p>
      ) : (
        <div className="space-y-2">
          {permissions.map((permission) => (
            <div
              key={permission.permission_id}
              className="flex items-center justify-between gap-3 rounded-md border border-slate-100 px-3 py-2"
            >
              <div className="min-w-0 text-sm">
                <span className="font-semibold text-slate-800">
                  {permission.subject_safe_label ||
                    (permission.subject_type === 'team' ? 'Team' : 'User')}
                </span>
                <span className="ml-2 text-slate-500">
                  {permission.subject_type} · {permission.permission_action}
                </span>
              </div>
              <button
                type="button"
                onClick={() => onRevokePermission(permission.permission_id)}
                disabled={isSaving || !canManagePermissions}
                className="rounded-md p-2 text-slate-500 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:text-slate-300"
                aria-label="권한 회수"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function PublicCollectionWarning() {
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <span>
        public Collection은 anonymous public-only RAG 후보에 포함될 수 있습니다.
        인증 사용자 KB use 권한을 부여하는 것은 아닙니다.
      </span>
    </div>
  );
}
