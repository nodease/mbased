'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import {
  AppWindow,
  ArrowLeft,
  Check,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  UserRoundCog,
  Users,
  X,
} from 'lucide-react';
import { organizationApi } from '../../organization/api/organizationApi';
import type {
  AccessControl,
  ActorAccessResourceCatalogItem,
  ActorAccessTeamCatalogItem,
  ActorGrantAuthState,
  ActorMembershipState,
  ActorOrganizationAuthState,
  ActorResourceType,
  MemberAccessAction,
  MemberAccessProfile,
  MemberResourceAccess,
  MemberTeamMembership,
} from '../types/ActorAccess';
import { AdminPagination } from './AdminPagination';

const RESOURCE_TYPES: Array<{
  value: ActorResourceType;
  label: string;
}> = [
  { value: 'workflow', label: 'Workflow' },
  { value: 'knowledge_base', label: 'Knowledge Base' },
  { value: 'llm_credential', label: 'LLM Credential' },
  { value: 'mail_credential', label: 'Mail Credential' },
];
const GRANT_STATES: ActorGrantAuthState[] = [
  'viewer',
  'operator',
  'builder',
  'manager',
];
const PAGE_SIZE = 20;
const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const MEMBERSHIP_STATE_LABELS: Record<ActorMembershipState, string> = {
  active: '활성',
  suspended: '정지',
};
const ORGANIZATION_ROLE_LABELS: Record<ActorOrganizationAuthState, string> = {
  member: '멤버',
  manager: '관리자',
};

type ActionDraft = {
  title: string;
  description: string;
  payload: MemberAccessAction;
  tone?: 'default' | 'danger';
  details?: Array<{ label: string; value: string }>;
};

type ActorAccessDrawerProps = {
  organizationId: string;
  userId: string;
  teams: ActorAccessTeamCatalogItem[];
  resources: ActorAccessResourceCatalogItem[];
  onClose: () => void;
  onChanged?: () => void;
  returnLabel?: string;
};

export function ActorAccessDrawer({
  organizationId,
  userId,
  teams,
  resources,
  onClose,
  onChanged,
  returnLabel,
}: ActorAccessDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const confirmTriggerRef = useRef<HTMLElement | null>(null);
  const loadSequenceRef = useRef(0);
  const selectedTeamSequenceRef = useRef(0);
  const selectedLookupSequenceRef = useRef(0);
  const [profile, setProfile] = useState<MemberAccessProfile | null>(null);
  const [teamMemberships, setTeamMemberships] = useState<
    MemberTeamMembership[]
  >([]);
  const [resourceAccess, setResourceAccess] = useState<
    Record<ActorResourceType, MemberResourceAccess[]>
  >({
    workflow: [],
    knowledge_base: [],
    llm_credential: [],
    mail_credential: [],
  });
  const [resourceType, setResourceType] =
    useState<ActorResourceType>('workflow');
  const [resourceSource, setResourceSource] = useState<
    'all' | 'direct' | 'team'
  >('all');
  const [teamPage, setTeamPage] = useState(1);
  const [teamTotal, setTeamTotal] = useState(0);
  const [resourcePages, setResourcePages] = useState<
    Record<ActorResourceType, number>
  >({ workflow: 1, knowledge_base: 1, llm_credential: 1, mail_credential: 1 });
  const [resourceTotals, setResourceTotals] = useState<
    Record<ActorResourceType, number>
  >({ workflow: 0, knowledge_base: 0, llm_credential: 0, mail_credential: 0 });
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const [selectedTeamMembership, setSelectedTeamMembership] =
    useState<MemberTeamMembership | null>(null);
  const [selectedTeamLoading, setSelectedTeamLoading] = useState(false);
  const [selectedTeamError, setSelectedTeamError] = useState<string | null>(
    null,
  );
  const [selectedResourceId, setSelectedResourceId] = useState('');
  const [selectedAccess, setSelectedAccess] =
    useState<MemberResourceAccess | null>(null);
  const [selectedAccessLoading, setSelectedAccessLoading] = useState(false);
  const [selectedAccessError, setSelectedAccessError] = useState<string | null>(
    null,
  );
  const [selectedAuthState, setSelectedAuthState] =
    useState<ActorGrantAuthState>('viewer');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<ActionDraft | null>(null);
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const resourcePage = resourcePages[resourceType];

  const load = useCallback(async () => {
    const sequence = ++loadSequenceRef.current;
    setLoading(true);
    setError(null);
    try {
      const nextProfile = await organizationApi.getMemberAccessProfile(
        organizationId,
        userId,
      );
      const [nextTeams, nextResources] = await Promise.all([
        organizationApi.listMemberTeamMemberships(organizationId, userId, {
          page: teamPage,
          limit: PAGE_SIZE,
        }),
        organizationApi.listMemberResourceAccess(organizationId, userId, {
          resourceType,
          source: resourceSource,
          page: resourcePage,
          limit: PAGE_SIZE,
        }),
      ]);
      if (sequence !== loadSequenceRef.current) return;
      setProfile(nextProfile);
      setTeamMemberships(nextTeams.items);
      setTeamTotal(nextTeams.total);
      setResourceAccess((previous) => ({
        ...previous,
        [resourceType]: nextResources.items,
      }));
      setResourceTotals((previous) => ({
        ...previous,
        [resourceType]: nextResources.total,
      }));
      const lastTeamPage = Math.max(1, Math.ceil(nextTeams.total / PAGE_SIZE));
      if (teamPage > lastTeamPage) setTeamPage(lastTeamPage);
      const lastResourcePage = Math.max(
        1,
        Math.ceil(nextResources.total / PAGE_SIZE),
      );
      if (resourcePage > lastResourcePage) {
        setResourcePages((previous) => ({
          ...previous,
          [resourceType]: lastResourcePage,
        }));
      }
    } catch (loadError) {
      if (sequence !== loadSequenceRef.current) return;
      const status = isAxiosError(loadError)
        ? loadError.response?.status
        : undefined;
      setError(
        status === 404
          ? '현재 조직에서 관리할 수 없는 행위자입니다.'
          : status === 403
            ? '행위자 접근 관리 권한이 없습니다.'
            : '행위자 접근 정보를 불러오지 못했습니다.',
      );
    } finally {
      if (sequence === loadSequenceRef.current) setLoading(false);
    }
  }, [
    organizationId,
    resourcePage,
    resourceSource,
    resourceType,
    teamPage,
    userId,
  ]);

  const loadSelectedAccess = useCallback(async () => {
    const sequence = ++selectedLookupSequenceRef.current;
    if (!selectedResourceId) {
      setSelectedAccess(null);
      setSelectedAccessError(null);
      setSelectedAccessLoading(false);
      return;
    }
    setSelectedAccessLoading(true);
    setSelectedAccessError(null);
    try {
      const result = await organizationApi.listMemberResourceAccess(
        organizationId,
        userId,
        {
          resourceType,
          resourceId: selectedResourceId,
          source: 'all',
          page: 1,
          limit: 1,
        },
      );
      if (sequence !== selectedLookupSequenceRef.current) return;
      setSelectedAccess(result.items[0] ?? null);
    } catch {
      if (sequence !== selectedLookupSequenceRef.current) return;
      setSelectedAccess(null);
      setSelectedAccessError(
        '선택한 Resource의 최신 접근 상태를 확인하지 못했습니다.',
      );
    } finally {
      if (sequence === selectedLookupSequenceRef.current) {
        setSelectedAccessLoading(false);
      }
    }
  }, [organizationId, resourceType, selectedResourceId, userId]);

  const loadSelectedTeam = useCallback(async () => {
    const sequence = ++selectedTeamSequenceRef.current;
    if (!selectedTeamId) {
      setSelectedTeamMembership(null);
      setSelectedTeamError(null);
      setSelectedTeamLoading(false);
      return;
    }
    setSelectedTeamLoading(true);
    setSelectedTeamError(null);
    try {
      const result = await organizationApi.listMemberTeamMemberships(
        organizationId,
        userId,
        { teamId: selectedTeamId, page: 1, limit: 1 },
      );
      if (sequence !== selectedTeamSequenceRef.current) return;
      setSelectedTeamMembership(result.items[0] ?? null);
    } catch {
      if (sequence !== selectedTeamSequenceRef.current) return;
      setSelectedTeamMembership(null);
      setSelectedTeamError('선택한 팀 소속의 최신 상태를 확인하지 못했습니다.');
    } finally {
      if (sequence === selectedTeamSequenceRef.current) {
        setSelectedTeamLoading(false);
      }
    }
  }, [organizationId, selectedTeamId, userId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadSelectedAccess();
  }, [loadSelectedAccess]);

  useEffect(() => {
    loadSelectedTeam();
  }, [loadSelectedTeam]);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    if (draft) reasonRef.current?.focus();
  }, [draft]);

  const commonAction = useCallback(
    () => {
      if (!profile) throw new Error('profile is required');
      return {
        expected_membership_id: profile.member.membership_id,
        expected_user_active: profile.member.user_active,
        expected_membership_state: profile.member.membership_state,
        expected_organization_auth_state:
          profile.member.organization_auth_state,
      };
    },
    [profile],
  );

  const openAction = (nextDraft: ActionDraft) => {
    confirmTriggerRef.current = document.activeElement as HTMLElement | null;
    setReason('');
    setReasonError(null);
    setDraft(nextDraft);
  };

  const closeDraft = () => {
    setDraft(null);
    setReasonError(null);
    requestAnimationFrame(() => confirmTriggerRef.current?.focus());
  };

  const executeAction = async () => {
    if (!draft) return;
    const normalized = normalizeReason(reason);
    if (normalized.error) {
      setReasonError(normalized.error);
      return;
    }
    setSubmitting(true);
    setReasonError(null);
    try {
      const result = await organizationApi.executeMemberAccessAction(
        organizationId,
        userId,
        { ...draft.payload, reason: normalized.value },
      );
      setNotice(
        result.status === 'unchanged'
          ? '최신 상태에 이미 반영되어 있습니다.'
          : result.affected_resource_source_count != null
            ? `접근 설정을 변경했습니다. 관련 source ${result.affected_resource_source_count}개를 확인했습니다.`
            : '접근 설정을 변경했습니다.',
      );
      closeDraft();
      await load();
      await loadSelectedTeam();
      await loadSelectedAccess();
      onChanged?.();
    } catch (actionError) {
      const status = isAxiosError(actionError)
        ? actionError.response?.status
        : undefined;
      const code = isAxiosError(actionError)
        ? actionError.response?.data?.error?.code
        : undefined;
      const message = actionErrorMessage(code, status);
      if (status === 404 || status === 409) {
        setNotice(message);
        closeDraft();
        await load();
        await loadSelectedTeam();
        await loadSelectedAccess();
      } else {
        setReasonError(message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && !draft) {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab' || !panelRef.current || draft) return;
    const focusable = panelRef.current.querySelectorAll<HTMLElement>(
      FOCUSABLE_SELECTOR,
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (
      event.shiftKey &&
      (document.activeElement === first || document.activeElement === panelRef.current)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const handleConfirmKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && !submitting) {
      event.stopPropagation();
      closeDraft();
      return;
    }
    if (event.key !== 'Tab' || !confirmRef.current) return;
    const focusable = confirmRef.current.querySelectorAll<HTMLElement>(
      FOCUSABLE_SELECTOR,
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === confirmRef.current)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const availableTeams = useMemo(() => {
    return teams.filter((team) => team.is_active);
  }, [teams]);

  const currentCatalog = useMemo(
    () => resources.filter((item) => item.resourceType === resourceType),
    [resourceType, resources],
  );
  const teamTotalPages = Math.max(1, Math.ceil(teamTotal / PAGE_SIZE));
  const resourceTotalPages = Math.max(
    1,
    Math.ceil(resourceTotals[resourceType] / PAGE_SIZE),
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-slate-950/30"
        onClick={draft ? undefined : onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="행위자 접근 관리"
        tabIndex={-1}
        aria-hidden={draft ? true : undefined}
        inert={draft ? true : undefined}
        onKeyDown={handleKeyDown}
        className="relative flex h-full w-full max-w-4xl flex-col overflow-y-auto bg-white shadow-xl outline-none [&_.text-xs]:text-sm [&_.text-sm]:text-base [&_.text-base]:text-lg [&_.text-lg]:text-xl"
      >
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white px-5 py-4">
          <div className="flex min-w-0 items-center gap-2">
            <UserRoundCog className="h-5 w-5 shrink-0 text-slate-700" />
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-slate-950">
                행위자 접근 관리
              </h2>
              {profile && (
                <p className="truncate text-xs text-slate-500">
                  {profile.member.name} · {profile.member.email}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="rounded-md p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-40"
              title="새로고침"
              aria-label="행위자 접근 새로고침"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            {returnLabel ? (
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-9 items-center gap-1.5 rounded-md px-2 text-sm font-semibold text-slate-600 hover:bg-slate-100"
              >
                <ArrowLeft className="h-4 w-4" />
                {returnLabel}
              </button>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="rounded-md p-2 text-slate-500 hover:bg-slate-100"
                aria-label="행위자 접근 관리 닫기"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>

        {loading && !profile ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            불러오는 중...
          </div>
        ) : error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-sm text-red-700">{error}</p>
            <button
              type="button"
              onClick={load}
              className="h-9 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-700"
            >
              다시 시도
            </button>
          </div>
        ) : profile ? (
          <div className="divide-y divide-slate-200">
            {notice && (
              <div className="flex items-center justify-between bg-emerald-50 px-5 py-3 text-sm text-emerald-800">
                <span>{notice}</span>
                <button
                  type="button"
                  onClick={() => setNotice(null)}
                  aria-label="알림 닫기"
                  className="rounded-md p-1 hover:bg-emerald-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            )}

            <section className="px-5 py-5">
              <SectionTitle icon={Shield} title="조직 멤버십" />
              <div className="mt-4 grid gap-3 sm:grid-cols-4">
                <Metric
                  label="상태"
                  value={MEMBERSHIP_STATE_LABELS[profile.member.membership_state]}
                />
                <Metric
                  label="조직 역할"
                  value={
                    ORGANIZATION_ROLE_LABELS[
                      profile.member.organization_auth_state
                    ]
                  }
                />
                <Metric
                  label="계정"
                  value={profile.member.user_active ? '활성' : '비활성'}
                />
                <Metric
                  label="유효 접근"
                  value={profile.effective_access_enabled ? '허용' : '차단'}
                />
              </div>
              <p className="mt-3 text-xs text-slate-500">
                팀 {profile.team_membership_count}개 · direct source{' '}
                {profile.permission_counts.direct}개 · team source{' '}
                {profile.permission_counts.team_inherited}개
              </p>
              <ActorPolicySummary profile={profile} />
              <div className="mt-4 flex flex-wrap gap-2">
                {profile.member.membership_state === 'active' ? (
                  <ActionButton
                    label="정지"
                    control={profile.control.actions.membership_suspend}
                    tone="danger"
                    onClick={() =>
                      openAction({
                        title: '멤버십 정지',
                        description: '조직 범위의 유효 접근을 중지합니다.',
                        tone: 'danger',
                        details: [
                          {
                            label: '변경',
                            value: `${profile.member.membership_state} → suspended`,
                          },
                          {
                            label: '영향',
                            value: '저장된 권한 source는 보존하고 유효 접근만 중지',
                          },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'membership.suspend',
                        },
                      })
                    }
                  />
                ) : (
                  <ActionButton
                    label="재활성화"
                    control={profile.control.actions.membership_reactivate}
                    onClick={() =>
                      openAction({
                        title: '멤버십 재활성화',
                        description: '보존된 접근 source를 다시 유효하게 합니다.',
                        details: [
                          {
                            label: '변경',
                            value: `${profile.member.membership_state} → active`,
                          },
                          {
                            label: '영향',
                            value: '보존된 direct/team source를 다시 평가',
                          },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'membership.reactivate',
                        },
                      })
                    }
                  />
                )}
                {profile.member.organization_auth_state === 'member' ? (
                  <ActionButton
                    label="관리자로 승격"
                    control={
                      profile.control.actions.organization_role_set.manager
                    }
                    onClick={() =>
                      openAction({
                        title: '조직 관리자로 승격',
                        description: '조직의 모든 operational resource에 manager override가 적용됩니다.',
                        details: [
                          {
                            label: '변경',
                            value: `${profile.member.organization_auth_state} → manager`,
                          },
                          {
                            label: '영향',
                            value: '조직 범위 manager override 활성화',
                          },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'organization_role.set',
                          role: 'manager',
                        },
                      })
                    }
                  />
                ) : (
                  <ActionButton
                    label="멤버로 강등"
                    tone="danger"
                    control={profile.control.actions.organization_role_set.member}
                    onClick={() =>
                      openAction({
                        title: '조직 멤버로 강등',
                        description: 'manager override를 제거하고 저장된 source만 평가합니다.',
                        tone: 'danger',
                        details: [
                          {
                            label: '변경',
                            value: `${profile.member.organization_auth_state} → member`,
                          },
                          {
                            label: '영향',
                            value: '저장된 direct/team source 기준으로 재평가',
                          },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'organization_role.set',
                          role: 'member',
                        },
                      })
                    }
                  />
                )}
              </div>
            </section>

            <section className="px-5 py-5">
              <SectionTitle icon={AppWindow} title="App 생성 권한" />
              <div className="mt-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">
                    {profile.app_creation.effective ? '허용' : '허용 안 함'}
                  </p>
                  <p className="text-xs text-slate-500">
                    source: {profile.app_creation.effective_source}
                  </p>
                </div>
                {profile.app_creation.direct_permission ? (
                  <ActionButton
                    label="직접 권한 회수"
                    tone="danger"
                    control={profile.control.actions.app_creation_revoke}
                    onClick={() =>
                      openAction({
                        title: 'App 생성 직접 권한 회수',
                        description: '저장된 App 생성 권한 row를 삭제합니다.',
                        tone: 'danger',
                        details: [
                          { label: '변경', value: 'direct → none' },
                          {
                            label: '현재 유효 source',
                            value: profile.app_creation.effective_source,
                          },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'app_creation.revoke',
                          expected_permission_id:
                            profile.app_creation.direct_permission!.permission_id,
                        },
                      })
                    }
                  />
                ) : (
                  <ActionButton
                    label="직접 권한 부여"
                    control={profile.control.actions.app_creation_grant}
                    onClick={() =>
                      openAction({
                        title: 'App 생성 직접 권한 부여',
                        description: '행위자에게 App 생성 권한을 부여합니다.',
                        details: [
                          { label: '변경', value: 'none → direct' },
                        ],
                        payload: {
                          ...commonAction(),
                          action: 'app_creation.grant',
                          expected_absent: true,
                        },
                      })
                    }
                  />
                )}
              </div>
            </section>

            <section className="px-5 py-5">
              <SectionTitle icon={Users} title="팀 소속" />
              <div className="mt-3 flex gap-2">
                <select
                  value={selectedTeamId}
                  onChange={(event) => {
                    setSelectedTeamId(event.target.value);
                    setSelectedTeamMembership(null);
                  }}
                  aria-label="추가할 팀"
                  className="h-9 min-w-0 flex-1 rounded-md border border-slate-300 px-2 text-sm"
                >
                  <option value="">팀 선택</option>
                  {availableTeams.map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
                </select>
                <ActionButton
                  label="추가"
                  icon={Plus}
                  control={
                    selectedTeamId &&
                    !selectedTeamLoading &&
                    !selectedTeamError &&
                    !selectedTeamMembership
                      ? profile.control.actions.team_membership_add
                      : {
                          allowed: false,
                          reason: selectedTeamLoading
                            ? 'selection_loading'
                            : selectedTeamError
                              ? 'selection_failed'
                              : selectedTeamMembership
                                ? 'team_already_assigned'
                                : 'team_required',
                        }
                  }
                  onClick={() =>
                    openAction({
                      title: '팀 소속 추가',
                      description: '선택한 팀의 resource permission source가 적용됩니다.',
                      details: [
                        {
                          label: '팀',
                          value:
                            availableTeams.find(
                              (team) => team.id === selectedTeamId,
                            )?.name || selectedTeamId,
                        },
                        {
                          label: '영향',
                          value: '팀에 부여된 operational source를 추가',
                        },
                      ],
                      payload: {
                        ...commonAction(),
                        action: 'team_membership.add',
                        team_id: selectedTeamId,
                        expected_absent: true,
                      },
                    })
                  }
                />
              </div>
              {selectedTeamMembership && (
                <p className="mt-2 text-xs text-slate-500">
                  이미 소속된 팀입니다. 아래 목록에서 해당 row를 제거할 수 있습니다.
                </p>
              )}
              {selectedTeamError && (
                <div className="mt-2 flex items-center justify-between gap-3 text-xs text-red-700">
                  <span>{selectedTeamError}</span>
                  <button
                    type="button"
                    onClick={loadSelectedTeam}
                    className="shrink-0 font-semibold underline"
                  >
                    다시 확인
                  </button>
                </div>
              )}
              <div className="mt-4 divide-y divide-slate-100 border-y border-slate-100">
                {teamMemberships.length === 0 ? (
                  <p className="py-6 text-center text-sm text-slate-500">
                    소속 팀이 없습니다.
                  </p>
                ) : (
                  teamMemberships.map((membership) => (
                    <div
                      key={membership.team_membership_id}
                      className="flex items-center justify-between gap-3 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-slate-900">
                          {membership.name}
                        </p>
                        <p className="text-xs text-slate-500">
                          {membership.is_active ? 'active' : 'inactive'} · source{' '}
                          {membership.inherited_resource_counts.total}
                        </p>
                      </div>
                      <ActionButton
                        label="제거"
                        icon={Trash2}
                        tone="danger"
                        control={profile.control.actions.team_membership_remove}
                        onClick={() =>
                          openAction({
                            title: '팀 소속 제거',
                            description: `${membership.name} 팀의 inherited source를 제거합니다.`,
                            tone: 'danger',
                            details: [
                              { label: '팀', value: membership.name },
                              {
                                label: 'source 영향',
                                value: [
                                  `workflow ${membership.inherited_resource_counts.workflow}`,
                                  `KB ${membership.inherited_resource_counts.knowledge_base}`,
                                  `LLM ${membership.inherited_resource_counts.llm_credential}`,
                                  `Mail ${membership.inherited_resource_counts.mail_credential || 0}`,
                                  `총 ${membership.inherited_resource_counts.total}`,
                                ].join(' · '),
                              },
                            ],
                            payload: {
                              ...commonAction(),
                              action: 'team_membership.remove',
                              team_id: membership.team_id,
                              expected_team_membership_id:
                                membership.team_membership_id,
                            },
                          })
                        }
                      />
                    </div>
                  ))
                )}
              </div>
              <AdminPagination
                page={teamPage}
                totalPages={teamTotalPages}
                total={teamTotal}
                onPageChange={setTeamPage}
              />
            </section>

            <section className="px-5 py-5">
              <SectionTitle icon={KeyRound} title="Resource 직접 권한" />
              <div className="mt-3 flex flex-wrap gap-1" role="tablist">
                {RESOURCE_TYPES.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    role="tab"
                    aria-selected={resourceType === item.value}
                    onClick={() => {
                      setResourceType(item.value);
                      setSelectedResourceId('');
                      setSelectedAccess(null);
                    }}
                    className={`h-8 rounded-md px-3 text-xs font-semibold ${
                      resourceType === item.value
                        ? 'bg-slate-950 text-white'
                        : 'border border-slate-200 text-slate-600'
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-1" aria-label="Resource source 필터">
                {(['all', 'direct', 'team'] as const).map((source) => (
                  <button
                    key={source}
                    type="button"
                    aria-pressed={resourceSource === source}
                    onClick={() => {
                      setResourceSource(source);
                      setResourcePages((previous) => ({
                        ...previous,
                        [resourceType]: 1,
                      }));
                    }}
                    className={`h-8 rounded-md px-3 text-xs font-semibold ${
                      resourceSource === source
                        ? 'bg-slate-200 text-slate-950'
                        : 'text-slate-500 hover:bg-slate-100'
                    }`}
                  >
                    {source}
                  </button>
                ))}
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_120px_auto]">
                <select
                  value={selectedResourceId}
                  onChange={(event) => setSelectedResourceId(event.target.value)}
                  aria-label="직접 권한 resource"
                  className="h-9 min-w-0 rounded-md border border-slate-300 px-2 text-sm"
                >
                  <option value="">Resource 선택</option>
                  {currentCatalog.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
                <select
                  value={selectedAuthState}
                  onChange={(event) =>
                    setSelectedAuthState(
                      event.target.value as ActorGrantAuthState,
                    )
                  }
                  aria-label="직접 권한 상태"
                  className="h-9 rounded-md border border-slate-300 px-2 text-sm"
                >
                  {GRANT_STATES.map((state) => (
                    <option key={state} value={state}>
                      {state}
                    </option>
                  ))}
                </select>
                <ActionButton
                  label={
                    selectedAccess?.direct_permission ? '변경' : '부여'
                  }
                  icon={Check}
                  control={
                    selectedResourceId &&
                    !selectedAccessLoading &&
                    !selectedAccessError
                      ? profile.control.actions.direct_permission_grant
                      : {
                          allowed: false,
                          reason: selectedAccessLoading
                            ? 'selection_loading'
                            : selectedAccessError
                              ? 'selection_failed'
                              : 'resource_required',
                        }
                  }
                  onClick={() => {
                    const resource = currentCatalog.find(
                      (item) => item.id === selectedResourceId,
                    );
                    if (!resource) return;
                    const direct = selectedAccess?.direct_permission;
                    const precondition = direct
                      ? {
                          expected_permission_id: direct.permission_id,
                          expected_auth_state: direct.auth_state,
                        }
                      : { expected_absent: true as const };
                    openAction({
                      title: `직접 권한 ${direct ? '변경' : '부여'}`,
                      description: `${resource.name}의 직접 권한을 ${selectedAuthState}(으)로 설정합니다.`,
                      details: [
                        { label: 'Resource', value: resource.name },
                        {
                          label: '변경',
                          value: `${direct?.auth_state || 'none'} → ${selectedAuthState}`,
                        },
                        {
                          label: '현재 effective',
                          value: selectedAccess?.effective_auth_state || 'none',
                        },
                      ],
                      payload: {
                        ...commonAction(),
                        action: 'direct_permission.grant',
                        resource_type: resourceType,
                        resource_id: resource.id,
                        auth_state: selectedAuthState,
                        ...precondition,
                      },
                    });
                  }}
                />
              </div>
              {selectedAccessError && (
                <div className="mt-2 flex items-center justify-between gap-3 text-xs text-red-700">
                  <span>{selectedAccessError}</span>
                  <button
                    type="button"
                    onClick={loadSelectedAccess}
                    className="shrink-0 font-semibold underline"
                  >
                    다시 확인
                  </button>
                </div>
              )}
              <div className="mt-4 divide-y divide-slate-100 border-y border-slate-100">
                {resourceAccess[resourceType].length === 0 ? (
                  <p className="py-6 text-center text-sm text-slate-500">
                    저장된 접근 source가 없습니다.
                  </p>
                ) : (
                  resourceAccess[resourceType].map((item) => (
                    <div key={item.resource_id} className="py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-900">
                            {item.resource_name}
                          </p>
                          <p className="text-xs text-slate-500">
                            effective: {item.effective_auth_state} · direct:{' '}
                            {item.direct_permission?.auth_state || 'none'}
                          </p>
                        </div>
                        {item.direct_permission && (
                          <ActionButton
                            label="회수"
                            icon={Trash2}
                            tone="danger"
                            control={
                              profile.control.actions.direct_permission_revoke
                            }
                            onClick={() =>
                              openAction({
                                title: '직접 권한 회수',
                                description: `${item.resource_name}의 direct source를 삭제합니다.`,
                                tone: 'danger',
                                details: [
                                  {
                                    label: 'Resource',
                                    value: item.resource_name,
                                  },
                                  {
                                    label: '변경',
                                    value: `${item.direct_permission!.auth_state} → none`,
                                  },
                                  {
                                    label: '남는 team source',
                                    value:
                                      item.team_sources.length > 0
                                        ? item.team_sources
                                            .map(
                                              (source) =>
                                                `${source.team_name} (${source.auth_state})`,
                                            )
                                            .join(', ')
                                        : '없음',
                                  },
                                ],
                                payload: {
                                  ...commonAction(),
                                  action: 'direct_permission.revoke',
                                  resource_type: item.resource_type,
                                  resource_id: item.resource_id,
                                  expected_permission_id:
                                    item.direct_permission!.permission_id,
                                  expected_auth_state:
                                    item.direct_permission!.auth_state,
                                },
                              })
                            }
                          />
                        )}
                      </div>
                      {item.team_sources.length > 0 && (
                        <p className="mt-1 text-xs text-slate-500">
                          team:{' '}
                          {item.team_sources
                            .map(
                              (source) =>
                                `${source.team_name} (${source.auth_state})`,
                            )
                            .join(', ')}
                        </p>
                      )}
                    </div>
                  ))
                )}
              </div>
              <AdminPagination
                page={resourcePage}
                totalPages={resourceTotalPages}
                total={resourceTotals[resourceType]}
                onPageChange={(page) =>
                  setResourcePages((previous) => ({
                    ...previous,
                    [resourceType]: page,
                  }))
                }
              />
            </section>
          </div>
        ) : null}
      </div>

      {draft && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/40 px-4">
          <div
            ref={confirmRef}
            role="dialog"
            aria-modal="true"
            aria-label={draft.title}
            tabIndex={-1}
            onKeyDown={handleConfirmKeyDown}
            className="w-full max-w-md rounded-md bg-white p-5 shadow-xl"
          >
            <h3 className="text-base font-semibold text-slate-950">
              {draft.title}
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {draft.description}
            </p>
            {profile && (
              <dl className="mt-4 divide-y divide-slate-100 border-y border-slate-200 text-xs">
                <ConfirmDetail
                  label="대상"
                  value={`${profile.member.name} (${profile.member.email})`}
                />
                {draft.details?.map((detail) => (
                  <ConfirmDetail
                    key={detail.label}
                    label={detail.label}
                    value={detail.value}
                  />
                ))}
              </dl>
            )}
            <label className="mt-4 block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-800">
                사유
              </span>
              <textarea
                ref={reasonRef}
                value={reason}
                onChange={(event) => {
                  setReason(event.target.value);
                  setReasonError(null);
                }}
                rows={3}
                aria-label="접근 변경 사유"
                className="w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </label>
            {reasonError && (
              <p className="mt-2 text-sm text-red-700">{reasonError}</p>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeDraft}
                disabled={submitting}
                className="h-9 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-700 disabled:opacity-40"
              >
                취소
              </button>
              <button
                type="button"
                onClick={executeAction}
                disabled={submitting}
                className={`flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-semibold text-white disabled:opacity-40 ${
                  draft.tone === 'danger'
                    ? 'bg-red-600 hover:bg-red-700'
                    : 'bg-slate-950 hover:bg-slate-800'
                }`}
              >
                {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SectionTitle({
  icon: Icon,
  title,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
}) {
  return (
    <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
      <Icon className="h-4 w-4 text-slate-600" />
      {title}
    </h3>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-l-2 border-slate-200 pl-3">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function ConfirmDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[100px_minmax(0,1fr)] gap-3 py-2">
      <dt className="font-semibold text-slate-500">{label}</dt>
      <dd className="break-words text-slate-800">{value}</dd>
    </div>
  );
}

function ActorPolicySummary({ profile }: { profile: MemberAccessProfile }) {
  const messages: string[] = [];
  if (!profile.member.user_active) {
    messages.push('비활성 계정입니다. 회수와 강등 같은 정리 작업만 허용됩니다.');
  }
  if (profile.member.membership_state === 'suspended') {
    messages.push('정지된 멤버십입니다. 저장된 source는 보존되지만 유효 접근은 없습니다.');
  }
  if (profile.control.is_self) {
    messages.push('본인 계정은 직접 정지하거나 강등할 수 없습니다.');
  }
  if (profile.control.is_last_active_manager) {
    messages.push('마지막 활성 관리자는 정지하거나 강등할 수 없습니다.');
  }
  if (profile.control.manager_override) {
    messages.push('관리자 override가 활성화되어 source 변경 전에 멤버로 강등해야 합니다.');
  }
  if (messages.length === 0) return null;
  return (
    <ul className="mt-3 space-y-1 border-l-2 border-amber-300 pl-3 text-xs leading-5 text-amber-800">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

function ActionButton({
  label,
  control,
  onClick,
  tone = 'default',
  icon: Icon,
}: {
  label: string;
  control: AccessControl;
  onClick: () => void;
  tone?: 'default' | 'danger';
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!control.allowed}
      title={control.allowed ? label : controlReasonLabel(control.reason)}
      aria-label={
        control.allowed
          ? label
          : `${label}: ${controlReasonLabel(control.reason)}`
      }
      className={`flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40 ${
        tone === 'danger'
          ? 'border-red-200 text-red-700 hover:bg-red-50'
          : 'border-slate-300 text-slate-700 hover:bg-slate-50'
      }`}
    >
      {Icon && <Icon className="h-4 w-4" />}
      {label}
    </button>
  );
}

function controlReasonLabel(reason: string | null) {
  const labels: Record<string, string> = {
    self_control_forbidden: '본인 계정에는 수행할 수 없습니다.',
    last_active_manager: '마지막 활성 관리자에는 수행할 수 없습니다.',
    manager_override_active: '먼저 조직 역할을 멤버로 변경해야 합니다.',
    member_state_not_manageable: '현재 멤버십 상태에서는 수행할 수 없습니다.',
    member_state_not_applicable: '현재 상태에는 적용되지 않는 작업입니다.',
    target_user_inactive: '비활성 계정에는 접근을 추가할 수 없습니다.',
    team_required: '팀을 먼저 선택해야 합니다.',
    team_already_assigned: '이미 소속된 팀입니다.',
    resource_required: 'Resource를 선택하고 최신 상태를 확인해야 합니다.',
    selection_loading: '최신 상태를 확인하는 중입니다.',
    selection_failed: '최신 상태 확인에 실패했습니다. 다시 확인해야 합니다.',
  };
  return reason ? labels[reason] || reason : '수행할 수 없습니다.';
}

function normalizeReason(value: string): {
  value: string | null;
  error: string | null;
} {
  const normalized = value.replace(/\r\n?/g, '\n').trim();
  if (!normalized) return { value: null, error: null };
  if (Array.from(normalized).length > 500) {
    return { value: null, error: '사유는 500자 이하여야 합니다.' };
  }
  for (const character of normalized) {
    const codePoint = character.codePointAt(0) ?? 0;
    const forbidden =
      (codePoint < 32 && character !== '\t' && character !== '\n') ||
      (codePoint >= 127 && codePoint <= 159) ||
      (codePoint >= 0x202a && codePoint <= 0x202e) ||
      (codePoint >= 0x2066 && codePoint <= 0x2069);
    if (forbidden) {
      return { value: null, error: '사유에 허용되지 않는 제어 문자가 있습니다.' };
    }
  }
  return { value: normalized, error: null };
}

function actionErrorMessage(code?: string, status?: number) {
  const messages: Record<string, string> = {
    self_control_forbidden: '자기 자신의 정지 또는 강등은 허용되지 않습니다.',
    last_active_manager: '마지막 활성 관리자는 정지하거나 강등할 수 없습니다.',
    manager_override_active: '먼저 조직 관리자 역할을 멤버로 변경해야 합니다.',
    member_state_not_manageable: '최신 멤버십 상태에서 이 작업을 수행할 수 없습니다.',
    target_user_inactive: '비활성 사용자에게 접근을 늘릴 수 없습니다.',
    stale_state: '접근 상태가 변경되었습니다. 최신 상태를 확인해 다시 시도하세요.',
    'audit.persistence_failed': '감사 기록을 저장하지 못해 변경을 적용하지 않았습니다.',
  };
  if (code && messages[code]) return messages[code];
  if (status === 404) return '대상이 더 이상 현재 조직에서 관리 가능한 상태가 아닙니다.';
  if (status === 403) return '행위자 접근 관리 권한이 없습니다.';
  return '접근 설정을 변경하지 못했습니다.';
}
