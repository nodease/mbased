'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { RotateCcw, Search, ShieldAlert } from 'lucide-react';
import type { OrganizationMember } from '../../organization/types/Organization';
import { DashboardPanel } from '../../dashboard/components/DashboardSurface';
import { adminApi } from '../api/adminApi';
import type {
  ActorAccessResourceCatalogItem,
  ActorAccessTeamCatalogItem,
} from '../types/ActorAccess';
import type {
  SecurityAlertListItem,
  SecurityAlertListParams,
  SecurityAlertRuleId,
  SecurityAlertSeverity,
  SecurityAlertStatus,
} from '../types/SecurityAlert';
import { AdminPagination } from './AdminPagination';
import { ActorAccessDrawer } from './ActorAccessDrawer';
import { SecurityAlertDetailDrawer } from './SecurityAlertDetailDrawer';
import { NOTIFICATIONS_REFRESH_EVENT } from '../../notifications/api/notificationsApi';

const PAGE_SIZE = 20;

const RULE_LABELS: Record<SecurityAlertRuleId, string> = {
  repeated_permission_denied: '반복된 권한 거부',
  multi_resource_permission_probe: '여러 리소스 접근 시도',
  repeated_policy_block: '반복된 정책 차단',
};

const SEVERITY_LABELS: Record<SecurityAlertSeverity, string> = {
  medium: '보통',
  high: '높음',
};

const STATUS_LABELS: Record<SecurityAlertStatus, string> = {
  open: '미확인',
  acknowledged: '확인됨',
  resolved: '해결됨',
};

type FilterForm = {
  severity: '' | SecurityAlertSeverity;
  status: '' | SecurityAlertStatus;
  ruleId: '' | SecurityAlertRuleId;
  actorId: string;
  startAt: string;
  endAt: string;
};

const EMPTY_FORM: FilterForm = {
  severity: '',
  status: '',
  ruleId: '',
  actorId: '',
  startAt: '',
  endAt: '',
};

const toFilters = (form: FilterForm): SecurityAlertListParams => ({
  severity: form.severity || undefined,
  status: form.status || undefined,
  ruleId: form.ruleId || undefined,
  actorId: form.actorId || undefined,
  startAt: form.startAt || undefined,
  endAt: form.endAt || undefined,
});

const formatDateTime = (value: string) => new Date(value).toLocaleString();

type SecurityAlertsTabProps = {
  members: OrganizationMember[];
  organizationId: string;
  selectedAlertId?: string | null;
  onSelectAlert?: (alertId: string) => void;
  onCloseAlert?: () => void;
  onAlertNotFound?: () => void;
  teams?: ActorAccessTeamCatalogItem[];
  resources?: ActorAccessResourceCatalogItem[];
};

export function SecurityAlertsTab({
  members,
  organizationId,
  selectedAlertId = null,
  onSelectAlert,
  onCloseAlert,
  onAlertNotFound,
  teams = [],
  resources = [],
}: SecurityAlertsTabProps) {
  const previousOrganizationIdRef = useRef(organizationId);
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const onCloseAlertRef = useRef(onCloseAlert);
  const listRequestSequenceRef = useRef(0);
  const loadedListRequestContextRef = useRef<string | null>(null);
  const [form, setForm] = useState<FilterForm>(EMPTY_FORM);
  const [applied, setApplied] = useState<{
    filters: SecurityAlertListParams;
    page: number;
  }>({ filters: {}, page: 1 });
  const [items, setItems] = useState<SecurityAlertListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [error, setError] = useState<'forbidden' | 'unknown' | null>(null);
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const listRequestContext = `${organizationId}:${applied.page}:${JSON.stringify(applied.filters)}`;
  const currentListRequestContextRef = useRef(listRequestContext);

  useEffect(() => {
    currentListRequestContextRef.current = listRequestContext;
  }, [listRequestContext]);

  useEffect(() => {
    onCloseAlertRef.current = onCloseAlert;
  }, [onCloseAlert]);

  useEffect(() => {
    if (previousOrganizationIdRef.current !== organizationId) {
      previousOrganizationIdRef.current = organizationId;
      setSelectedActorId(null);
      onCloseAlert?.();
    }
  }, [onCloseAlert, organizationId]);

  useEffect(() => {
    if (!selectedAlertId) setSelectedActorId(null);
  }, [selectedAlertId]);

  const load = useCallback(async (preserveCurrentData = false) => {
    const sequence = ++listRequestSequenceRef.current;
    const requestContext = listRequestContext;
    const canPreserveCurrentData =
      preserveCurrentData &&
      loadedListRequestContextRef.current === requestContext;
    const isCurrentRequest = () =>
      sequence === listRequestSequenceRef.current &&
      requestContext === currentListRequestContextRef.current;
    if (!canPreserveCurrentData) {
      loadedListRequestContextRef.current = null;
      setLoading(true);
      setItems([]);
      setTotal(0);
    }
    setError(null);
    if (!organizationId) {
      loadedListRequestContextRef.current = null;
      setLoading(false);
      return;
    }
    try {
      const data = await adminApi.listSecurityAlerts({
        ...applied.filters,
        page: applied.page,
        limit: PAGE_SIZE,
      });
      if (!isCurrentRequest()) return;
      const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
      if (applied.page > totalPages) {
        setApplied((current) => ({ ...current, page: totalPages }));
        return;
      }
      setItems(data.items);
      setTotal(data.total);
      loadedListRequestContextRef.current = requestContext;
    } catch (loadError) {
      if (!isCurrentRequest()) return;
      if (isAxiosError(loadError) && loadError.response?.status === 403) {
        loadedListRequestContextRef.current = null;
        setItems([]);
        setTotal(0);
        setError('forbidden');
        setSelectedActorId(null);
        onCloseAlertRef.current?.();
      } else if (!canPreserveCurrentData) {
        setError('unknown');
      }
    } finally {
      if (isCurrentRequest()) setLoading(false);
    }
  }, [applied, listRequestContext, organizationId]);

  useEffect(() => {
    load(false);
  }, [load]);

  useEffect(() => {
    const refresh = () => {
      load(true);
      setRefreshToken((current) => current + 1);
    };
    window.addEventListener(NOTIFICATIONS_REFRESH_EVENT, refresh);
    return () =>
      window.removeEventListener(NOTIFICATIONS_REFRESH_EVENT, refresh);
  }, [load]);

  const setField = <K extends keyof FilterForm>(key: K, value: FilterForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const search = () => {
    if (
      form.startAt &&
      form.endAt &&
      new Date(form.endAt).getTime() <= new Date(form.startAt).getTime()
    ) {
      setValidationError('종료 시각은 시작 시각보다 늦어야 합니다.');
      return;
    }
    setValidationError(null);
    setApplied({ filters: toFilters(form), page: 1 });
  };

  const reset = () => {
    setForm(EMPTY_FORM);
    setValidationError(null);
    setApplied({ filters: {}, page: 1 });
  };

  const hasAppliedFilters = Object.values(applied.filters).some(Boolean);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <DashboardPanel title="보안 알림" icon={ShieldAlert}>
      <div className="border-b border-slate-100 px-5 py-4">
        <p className="text-sm text-slate-700">
          반복된 권한 거부와 정책 차단에서 탐지된 위험 신호입니다.
        </p>
        <p className="mt-1 text-xs text-slate-500">
          실제 침해가 확정되었다는 의미는 아니며 관리자의 확인이 필요합니다.
        </p>
      </div>

      <form
        className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-4">
          <select
            aria-label="심각도"
            value={form.severity}
            onChange={(event) =>
              setField('severity', event.target.value as FilterForm['severity'])
            }
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          >
            <option value="">심각도 전체</option>
            <option value="medium">보통</option>
            <option value="high">높음</option>
          </select>
          <select
            aria-label="상태"
            value={form.status}
            onChange={(event) =>
              setField('status', event.target.value as FilterForm['status'])
            }
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          >
            <option value="">상태 전체</option>
            <option value="open">미확인</option>
            <option value="acknowledged">확인됨</option>
            <option value="resolved">해결됨</option>
          </select>
          <select
            aria-label="탐지 규칙"
            value={form.ruleId}
            onChange={(event) =>
              setField('ruleId', event.target.value as FilterForm['ruleId'])
            }
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          >
            <option value="">탐지 규칙 전체</option>
            {Object.entries(RULE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="사용자"
            value={form.actorId}
            onChange={(event) => setField('actorId', event.target.value)}
            className="h-10 rounded-md border border-slate-300 px-3 text-sm"
          >
            <option value="">사용자 전체</option>
            {members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.user_name || '이름 없는 사용자'}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            시작
            <input
              type="datetime-local"
              aria-label="시작 시각"
              value={form.startAt}
              onChange={(event) => setField('startAt', event.target.value)}
              className="h-10 flex-1 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            종료
            <input
              type="datetime-local"
              aria-label="종료 시각"
              value={form.endAt}
              onChange={(event) => setField('endAt', event.target.value)}
              className="h-10 flex-1 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
            />
          </label>
          <div className="flex items-center gap-2">
            <button
              type="submit"
              className="flex h-10 items-center gap-1.5 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white"
            >
              <Search className="h-4 w-4" />
              조회
            </button>
            <button
              type="button"
              onClick={reset}
              className="flex h-10 items-center gap-1.5 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700"
            >
              <RotateCcw className="h-4 w-4" />
              초기화
            </button>
          </div>
        </div>
        {validationError && (
          <p role="alert" className="text-sm text-red-600">
            {validationError}
          </p>
        )}
      </form>

      {loading ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          보안 알림을 불러오는 중...
        </p>
      ) : error === 'forbidden' ? (
        <p className="px-5 py-12 text-center text-sm text-slate-600">
          보안 알림 관리 권한이 없습니다.
        </p>
      ) : error === 'unknown' ? (
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
          <p className="text-sm text-slate-600">
            보안 알림을 불러오지 못했습니다.
          </p>
          <button
            type="button"
            onClick={() => load(false)}
            className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700"
          >
            다시 시도
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-slate-500">
          <p>
            {hasAppliedFilters
              ? '조건에 맞는 보안 알림이 없습니다.'
              : '탐지된 보안 알림이 없습니다.'}
          </p>
          {hasAppliedFilters && (
            <button
              type="button"
              onClick={reset}
              className="mt-3 h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700"
            >
              초기화
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-3">심각도</th>
                  <th className="px-5 py-3">탐지 유형</th>
                  <th className="px-5 py-3">사용자</th>
                  <th className="px-5 py-3">상태</th>
                  <th className="px-5 py-3">발생 횟수</th>
                  <th className="px-5 py-3">최초 탐지</th>
                  <th className="px-5 py-3">최근 탐지</th>
                  <th className="px-5 py-3"><span className="sr-only">작업</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr
                    key={item.id}
                    tabIndex={0}
                    onClick={(event) => {
                      detailTriggerRef.current = event.currentTarget;
                      onSelectAlert?.(item.id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        detailTriggerRef.current = event.currentTarget;
                        onSelectAlert?.(item.id);
                      }
                    }}
                    className="cursor-pointer hover:bg-slate-50"
                  >
                    <td className="px-5 py-3 font-medium">
                      {SEVERITY_LABELS[item.severity] ?? '알 수 없는 심각도'}
                    </td>
                    <td className="px-5 py-3">
                      {RULE_LABELS[item.rule_id] ?? '알 수 없는 탐지 규칙'}
                    </td>
                    <td className="px-5 py-3">
                      {item.actor.display_name ||
                        (item.actor.state === 'deleted'
                          ? '삭제된 사용자'
                          : '이름 없는 사용자')}
                    </td>
                    <td className="px-5 py-3">
                      {STATUS_LABELS[item.status] ?? '알 수 없는 상태'}
                    </td>
                    <td className="px-5 py-3">{item.occurrence_count}</td>
                    <td className="px-5 py-3">
                      <time dateTime={item.first_detected_at}>
                        {formatDateTime(item.first_detected_at)}
                      </time>
                    </td>
                    <td className="px-5 py-3">
                      <time dateTime={item.last_detected_at}>
                        {formatDateTime(item.last_detected_at)}
                      </time>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          detailTriggerRef.current = event.currentTarget;
                          onSelectAlert?.(item.id);
                        }}
                        className="text-xs font-semibold text-blue-700"
                      >
                        상세 보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <AdminPagination
            page={applied.page}
            totalPages={totalPages}
            total={total}
            onPageChange={(page) =>
              setApplied((current) => ({ ...current, page }))
            }
          />
        </>
      )}
      </DashboardPanel>

      {selectedAlertId && !selectedActorId && (
        <SecurityAlertDetailDrawer
          alertId={selectedAlertId}
          members={members}
          refreshToken={refreshToken}
          onClose={() => onCloseAlert?.()}
          onAfterClose={() => detailTriggerRef.current?.focus()}
          onNotFound={() => onAlertNotFound?.()}
          onChanged={() => load(true)}
          onManageActor={(actorId) => {
            setSelectedActorId(actorId);
          }}
        />
      )}

      {selectedActorId && (
        <ActorAccessDrawer
          organizationId={organizationId}
          userId={selectedActorId}
          teams={teams}
          resources={resources}
          returnLabel="보안 알림 상세로 돌아가기"
          onClose={() => setSelectedActorId(null)}
        />
      )}
    </>
  );
}
