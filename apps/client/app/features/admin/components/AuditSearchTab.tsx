'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Activity, RotateCcw, Search, UserRoundCog } from 'lucide-react';
import { ActiveOrganizationMemberPicker } from '../../organization/components/ActiveOrganizationMemberPicker';
import type { OrganizationMember } from '../../organization/types/Organization';
import { DashboardPanel } from '../../dashboard/components/DashboardSurface';
import { adminApi } from '../api/adminApi';
import type {
  AuditLogItem,
  AuditLogSearchFilters,
  AuditLogStatus,
} from '../types/AdminAudit';
import { auditActionLabel } from '../utils/auditActionLabel';
import {
  auditStatusLabel,
  auditTargetLabel,
} from '../utils/auditPresentation';
import { AdminPagination } from './AdminPagination';
import { AuditDetailDrawer } from './AuditDetailDrawer';
import { AuditReferenceDisplay } from './AuditReferenceDisplay';
import { ActorAccessDrawer } from './ActorAccessDrawer';
import type {
  ActorAccessResourceCatalogItem,
  ActorAccessTeamCatalogItem,
} from '../types/ActorAccess';

const PAGE_SIZE = 20;

type FilterForm = {
  actorId: string;
  action: string;
  targetType: string;
  targetId: string;
  status: '' | AuditLogStatus;
  startAt: string;
  endAt: string;
};

const EMPTY_FORM: FilterForm = {
  actorId: '',
  action: '',
  targetType: '',
  targetId: '',
  status: '',
  startAt: '',
  endAt: '',
};

const toFilters = (form: FilterForm): AuditLogSearchFilters => ({
  actorId: form.actorId || undefined,
  action: form.action || undefined,
  targetType: form.targetType || undefined,
  targetId: form.targetId || undefined,
  status: form.status || undefined,
  startAt: form.startAt || undefined,
  endAt: form.endAt || undefined,
});

type AuditSearchTabProps = {
  members: OrganizationMember[];
  organizationId?: string;
  canManageActors?: boolean;
  teams?: ActorAccessTeamCatalogItem[];
  resources?: ActorAccessResourceCatalogItem[];
  onActorAccessChanged?: () => void;
};

export function AuditSearchTab({
  members,
  organizationId,
  canManageActors = false,
  teams = [],
  resources = [],
  onActorAccessChanged,
}: AuditSearchTabProps) {
  const [form, setForm] = useState<FilterForm>(EMPTY_FORM);
  const [appliedFilters, setAppliedFilters] =
    useState<AuditLogSearchFilters>({});
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([
    null,
  ]);
  const [pageIndex, setPageIndex] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [items, setItems] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<
    { kind: 'forbidden' | 'unknown'; message: string } | null
  >(null);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null);
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const actorTriggerRef = useRef<HTMLElement | null>(null);

  const memberNamesByUserId = useMemo(
    () =>
      new Map(
        members.map((member) => [
          member.user_id,
          `${member.user_name} (${member.user_email})`,
        ]),
      ),
    [members],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listAuditLogs({
        ...appliedFilters,
        ...(cursorHistory[pageIndex]
          ? { cursor: cursorHistory[pageIndex] as string }
          : {}),
        limit: PAGE_SIZE,
      });
      setItems(data.items);
      if (data.total !== null) {
        setTotal(data.total);
      }
      setNextCursor(data.next_cursor ?? null);
    } catch (err) {
      setItems([]);
      if (pageIndex === 0) {
        setTotal(0);
      }
      setNextCursor(null);
      if (isAxiosError(err) && err.response?.status === 403) {
        setError({
          kind: 'forbidden',
          message:
            '감사 로그 조회 권한이 없습니다. 조직 관리자 또는 감사 권한(auditor)이 필요합니다.',
        });
      } else {
        setError({
          kind: 'unknown',
          message: '감사 로그를 불러오지 못했습니다.',
        });
      }
    } finally {
      setLoading(false);
    }
  }, [appliedFilters, cursorHistory, pageIndex]);

  useEffect(() => {
    load();
  }, [load]);

  const resetPagination = () => {
    setCursorHistory([null]);
    setPageIndex(0);
    setNextCursor(null);
  };

  const search = () => {
    setAppliedFilters(toFilters(form));
    resetPagination();
  };

  const reset = () => {
    setForm(EMPTY_FORM);
    setAppliedFilters({});
    resetPagination();
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const setField = <K extends keyof FilterForm>(key: K, value: FilterForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <DashboardPanel title="감사 로그" icon={Activity}>
      <form
        className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-4">
          <ActiveOrganizationMemberPicker
            members={members}
            value={form.actorId}
            onChange={(userId) => setField('actorId', userId)}
            placeholder="행위자 전체"
            className="h-10"
          />
          <input
            value={form.action}
            onChange={(event) => setField('action', event.target.value)}
            placeholder="action (예: workflow.deploy)"
            aria-label="action 필터"
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          />
          <input
            value={form.targetType}
            onChange={(event) => setField('targetType', event.target.value)}
            placeholder="대상 타입 (예: workflow)"
            aria-label="대상 타입 필터"
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          />
          <input
            value={form.targetId}
            onChange={(event) => setField('targetId', event.target.value)}
            placeholder="대상 ID"
            aria-label="대상 ID 필터"
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          />
          <select
            value={form.status}
            onChange={(event) =>
              setField('status', event.target.value as FilterForm['status'])
            }
            aria-label="status 필터"
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          >
            <option value="">status 전체</option>
            <option value="success">success</option>
            <option value="failure">failure</option>
          </select>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            시작 (KST)
            <input
              type="datetime-local"
              value={form.startAt}
              onChange={(event) => setField('startAt', event.target.value)}
              aria-label="기간 시작"
              className="h-10 flex-1 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            끝 (KST)
            <input
              type="datetime-local"
              value={form.endAt}
              onChange={(event) => setField('endAt', event.target.value)}
              aria-label="기간 끝"
              className="h-10 flex-1 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
            />
          </label>
          <div className="flex items-center gap-2">
            <button
              type="submit"
              className="flex h-10 items-center gap-1.5 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800"
            >
              <Search className="h-4 w-4" />
              조회
            </button>
            <button
              type="button"
              onClick={reset}
              className="flex h-10 items-center gap-1.5 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-600 hover:bg-slate-50"
            >
              <RotateCcw className="h-4 w-4" />
              초기화
            </button>
          </div>
        </div>
      </form>

      {loading ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          감사 로그를 불러오는 중...
        </p>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
          <p className="text-sm text-slate-600">{error.message}</p>
          {error.kind === 'unknown' && (
            <button
              onClick={load}
              className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            >
              다시 시도
            </button>
          )}
        </div>
      ) : items.length === 0 ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          조건에 맞는 감사 로그가 없습니다.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
            <caption className="sr-only">
              조직 감사 로그 목록 (발생 시각 내림차순)
            </caption>
            <thead>
              <tr className="border-b border-slate-100 text-xs uppercase text-slate-500">
                <th scope="col" className="px-5 py-2 font-semibold">
                  발생 시각
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  행위자
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  작업
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  대상
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  상태
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const label = auditActionLabel(item.action);
                const actorMember =
                  item.actor_type === 'user' && item.actor_id
                  ? members.find(
                      (member) =>
                        member.user_id === item.actor_id &&
                        (member.membership_state === 'active' ||
                          member.membership_state === 'suspended'),
                    )
                  : null;
                const actorLabel = item.actor_display?.label ||
                  (item.actor_id
                    ? memberNamesByUserId.get(item.actor_id)
                    : undefined);
                return (
                  <tr
                    key={item.id}
                    tabIndex={0}
                    onClick={(event) => {
                      detailTriggerRef.current = event.currentTarget;
                      setSelectedLogId(item.id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        detailTriggerRef.current = event.currentTarget;
                        setSelectedLogId(item.id);
                      }
                    }}
                    className="cursor-pointer border-b border-slate-50 hover:bg-slate-50"
                  >
                    <td className="px-5 py-3 text-xs text-slate-500">
                      <time dateTime={item.occurred_at}>
                        {new Date(item.occurred_at).toLocaleString()}
                      </time>
                    </td>
                    <td className="px-3 py-3 text-slate-700">
                      <span className="flex items-center gap-1.5">
                        {actorLabel ? (
                          item.actor_id ? (
                            <AuditReferenceDisplay
                              label={actorLabel}
                              id={item.actor_id}
                              copyLabel="행위자 ID 복사"
                            />
                          ) : (
                            <span className="min-w-0 truncate text-slate-800">
                              {actorLabel}
                            </span>
                          )
                        ) : (
                          <span className="min-w-0 truncate">
                            {item.actor_id || item.actor_type}
                          </span>
                        )}
                        {canManageActors && organizationId && actorMember && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              actorTriggerRef.current = event.currentTarget;
                              setSelectedActorId(actorMember.user_id);
                            }}
                            onKeyDown={(event) => event.stopPropagation()}
                            className="shrink-0 rounded-md p-1 text-slate-500 hover:bg-slate-200 hover:text-slate-900"
                            aria-label={`${actorMember.user_name} 접근 관리`}
                            title="행위자 접근 관리"
                          >
                            <UserRoundCog className="h-4 w-4" />
                          </button>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <span className="flex flex-wrap items-center gap-1.5">
                        {label && (
                          <span className="text-slate-800">{label}</span>
                        )}
                        <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                          {item.action}
                        </code>
                      </span>
                    </td>
                    <td className="px-3 py-3 text-slate-700">
                      {item.target_display && item.target_id ? (
                        <AuditReferenceDisplay
                          label={item.target_display.label}
                          id={item.target_id}
                          copyLabel="대상 ID 복사"
                        />
                      ) : (
                        auditTargetLabel(
                          item.target_type,
                          item.target_id,
                          organizationId,
                        )
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <span
                        className={`w-fit rounded-md px-2 py-0.5 text-xs font-semibold ${
                          item.status === 'failure'
                            ? 'bg-red-50 text-red-700'
                            : 'bg-emerald-50 text-emerald-700'
                        }`}
                      >
                        {auditStatusLabel(item.status, item.action)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
            </table>
          </div>
          <AdminPagination
            page={pageIndex + 1}
            totalPages={totalPages}
            total={total}
            hasNext={Boolean(nextCursor)}
            onPageChange={(page) => {
              const targetIndex = page - 1;
              if (targetIndex < pageIndex) {
                setPageIndex(targetIndex);
                return;
              }
              if (targetIndex === pageIndex + 1 && nextCursor) {
                setCursorHistory((current) => [
                  ...current.slice(0, pageIndex + 1),
                  nextCursor,
                ]);
                setPageIndex(targetIndex);
              }
            }}
          />
        </>
      )}

      {selectedLogId && (
        <AuditDetailDrawer
          auditLogId={selectedLogId}
          currentOrganizationId={organizationId}
          actorName={
            items.find((item) => item.id === selectedLogId)?.actor_id
              ? memberNamesByUserId.get(
                  items.find((item) => item.id === selectedLogId)!.actor_id!,
                )
              : null
          }
          onClose={() => setSelectedLogId(null)}
          onAfterClose={() => detailTriggerRef.current?.focus()}
        />
      )}
      {selectedActorId && organizationId && (
        <ActorAccessDrawer
          organizationId={organizationId}
          userId={selectedActorId}
          teams={teams}
          resources={resources}
          onChanged={onActorAccessChanged}
          onClose={() => {
            setSelectedActorId(null);
            requestAnimationFrame(() => actorTriggerRef.current?.focus());
          }}
        />
      )}
    </DashboardPanel>
  );
}
