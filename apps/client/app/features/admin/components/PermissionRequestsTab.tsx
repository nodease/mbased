'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { isAxiosError } from 'axios';
import { ShieldQuestion } from 'lucide-react';
import { toast } from 'sonner';
import type { OrganizationMember } from '../../organization/types/Organization';
import { DashboardPanel } from '../../dashboard/components/DashboardSurface';
import { adminApi } from '../api/adminApi';
import type {
  AppCreationPermissionItem,
  PermissionRequestItem,
  PermissionRequestStatus,
} from '../types/AdminPermissionRequest';
import { AdminPagination } from './AdminPagination';

const PAGE_SIZE = 20;

// ADR-0016: 요청 권한의 실체는 조직 수준 App 생성 능력이다.
const REQUESTED_PERMISSION_LABELS: Record<string, string> = {
  'app.create': '워크플로우 생성/배포',
};

const STATUS_LABELS: Record<PermissionRequestStatus, string> = {
  pending: '대기',
  approved: '승인됨',
  rejected: '거절됨',
};

const STATUS_BADGE_CLASSES: Record<PermissionRequestStatus, string> = {
  pending: 'bg-amber-50 text-amber-700',
  approved: 'bg-emerald-50 text-emerald-700',
  rejected: 'bg-red-50 text-red-700',
};

const requestedPermissionLabel = (permission: string) =>
  REQUESTED_PERMISSION_LABELS[permission] || permission;

type ConfirmState = {
  action: 'approve' | 'reject';
  request: PermissionRequestItem;
};

type PermissionRequestsTabProps = {
  members: OrganizationMember[];
};

export function PermissionRequestsTab({ members }: PermissionRequestsTabProps) {
  const [status, setStatus] = useState<PermissionRequestStatus>('pending');
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<PermissionRequestItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<
    { kind: 'forbidden' | 'unknown'; message: string } | null
  >(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [processing, setProcessing] = useState(false);

  // 보유 권한 섹션 (FR-014 회수 확장)
  const [holders, setHolders] = useState<AppCreationPermissionItem[]>([]);
  const [holdersTotal, setHoldersTotal] = useState(0);
  const [holdersPage, setHoldersPage] = useState(1);
  const [holdersLoading, setHoldersLoading] = useState(true);
  const [holdersError, setHoldersError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] =
    useState<AppCreationPermissionItem | null>(null);
  const [revoking, setRevoking] = useState(false);

  const memberNamesByUserId = useMemo(
    () => new Map(members.map((member) => [member.user_id, member.user_name])),
    [members],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listPermissionRequests({
        status,
        page,
        limit: PAGE_SIZE,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setItems([]);
      setTotal(0);
      if (isAxiosError(err) && err.response?.status === 403) {
        setError({
          kind: 'forbidden',
          message: '권한 신청 관리 권한이 없습니다. 조직 관리자만 접근할 수 있습니다.',
        });
      } else {
        setError({
          kind: 'unknown',
          message: '권한 신청 목록을 불러오지 못했습니다.',
        });
      }
    } finally {
      setLoading(false);
    }
  }, [status, page]);

  useEffect(() => {
    load();
  }, [load]);

  const loadHolders = useCallback(async () => {
    setHoldersLoading(true);
    setHoldersError(null);
    try {
      const data = await adminApi.listAppCreationPermissions({
        page: holdersPage,
        limit: PAGE_SIZE,
      });
      setHolders(data.items);
      setHoldersTotal(data.total);
    } catch {
      setHolders([]);
      setHoldersTotal(0);
      setHoldersError('App 생성 권한 보유 목록을 불러오지 못했습니다.');
    } finally {
      setHoldersLoading(false);
    }
  }, [holdersPage]);

  useEffect(() => {
    loadHolders();
  }, [loadHolders]);

  const revokeConfirmed = async () => {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await adminApi.revokeAppCreationPermission(revokeTarget.id);
      toast.success('권한을 회수했습니다.');
      setRevokeTarget(null);
      // 회수된 사용자는 재신청할 수 있으므로 신청 목록도 함께 갱신한다.
      await Promise.all([loadHolders(), load()]);
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 404) {
        toast.error('이미 회수된 권한입니다.');
        setRevokeTarget(null);
        await Promise.all([loadHolders(), load()]);
      } else {
        toast.error('권한 회수에 실패했습니다.');
      }
    } finally {
      setRevoking(false);
    }
  };

  const processConfirmed = async () => {
    if (!confirmState) return;
    setProcessing(true);
    try {
      if (confirmState.action === 'approve') {
        await adminApi.approvePermissionRequest(confirmState.request.id);
        toast.success('권한 신청을 승인했습니다.');
      } else {
        await adminApi.rejectPermissionRequest(confirmState.request.id);
        toast.success('권한 신청을 거절했습니다.');
      }
      setConfirmState(null);
      await load();
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 409) {
        toast.error('이미 처리된 신청입니다.');
        setConfirmState(null);
        await load();
      } else {
        toast.error(
          confirmState.action === 'approve'
            ? '승인 처리에 실패했습니다.'
            : '거절 처리에 실패했습니다.',
        );
      }
    } finally {
      setProcessing(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <DashboardPanel title="권한 신청" icon={ShieldQuestion}>
      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
        <label
          htmlFor="permission-request-status"
          className="text-xs font-medium text-slate-500"
        >
          상태
        </label>
        <select
          id="permission-request-status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as PermissionRequestStatus);
            setPage(1);
          }}
          className="h-9 rounded-md border border-slate-300 px-3 text-sm"
        >
          {(Object.keys(STATUS_LABELS) as PermissionRequestStatus[]).map(
            (value) => (
              <option key={value} value={value}>
                {STATUS_LABELS[value]}
              </option>
            ),
          )}
        </select>
      </div>

      {loading ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          권한 신청을 불러오는 중...
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
          {STATUS_LABELS[status]} 상태의 권한 신청이 없습니다.
        </p>
      ) : (
        <>
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              권한 신청 목록 (신청일 내림차순)
            </caption>
            <thead>
              <tr className="border-b border-slate-100 text-xs uppercase text-slate-500">
                <th scope="col" className="px-5 py-2 font-semibold">
                  요청자
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  요청 권한
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  신청 사유
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  신청일
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  상태
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  처리
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-slate-50">
                  <td className="px-5 py-3">
                    {item.user ? (
                      <span className="flex flex-col">
                        <span className="font-medium text-slate-900">
                          {item.user.name}
                        </span>
                        <span className="text-xs text-slate-500">
                          {item.user.email}
                        </span>
                      </span>
                    ) : (
                      <span className="text-slate-500">알 수 없는 사용자</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <span className="flex flex-col">
                      <span className="text-slate-900">
                        {requestedPermissionLabel(item.requested_permission)}
                      </span>
                      <code className="w-fit rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                        {item.requested_permission}
                      </code>
                    </span>
                  </td>
                  <td className="max-w-xs px-3 py-3 text-slate-700">
                    <span className="line-clamp-2">{item.reason}</span>
                  </td>
                  <td className="px-3 py-3 text-xs text-slate-500">
                    <time dateTime={item.created_at}>
                      {new Date(item.created_at).toLocaleString()}
                    </time>
                  </td>
                  <td className="px-3 py-3">
                    <span
                      className={`w-fit rounded-md px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE_CLASSES[item.status]}`}
                    >
                      {STATUS_LABELS[item.status]}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    {item.status === 'pending' ? (
                      <span className="flex gap-2">
                        <button
                          onClick={() =>
                            setConfirmState({ action: 'approve', request: item })
                          }
                          className="h-8 rounded-md bg-slate-950 px-3 text-xs font-semibold text-white hover:bg-slate-800"
                        >
                          승인
                        </button>
                        <button
                          onClick={() =>
                            setConfirmState({ action: 'reject', request: item })
                          }
                          className="h-8 rounded-md border border-red-200 px-3 text-xs font-semibold text-red-700 hover:bg-red-50"
                        >
                          거절
                        </button>
                      </span>
                    ) : (
                      <span className="flex flex-col text-xs text-slate-500">
                        <span>
                          {(item.decided_by &&
                            memberNamesByUserId.get(item.decided_by)) ||
                            '처리자 확인 불가'}
                        </span>
                        {item.decided_at && (
                          <time dateTime={item.decided_at}>
                            {new Date(item.decided_at).toLocaleString()}
                          </time>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <AdminPagination
            page={page}
            totalPages={totalPages}
            total={total}
            onPageChange={setPage}
          />
        </>
      )}

      {error?.kind !== 'forbidden' && (
        <section className="border-t border-slate-100">
          <h3 className="px-5 pt-4 text-sm font-semibold text-slate-950">
            보유 권한
          </h3>
          <p className="px-5 pt-1 text-xs text-slate-500">
            App 생성 권한을 보유한 멤버입니다. 조직 관리자(owner/manager)는 별도
            부여 없이 허용되므로 표시되지 않습니다.
          </p>
          {holdersLoading ? (
            <p className="px-5 py-8 text-center text-sm text-slate-500">
              보유 권한을 불러오는 중...
            </p>
          ) : holdersError ? (
            <div className="flex flex-col items-center gap-3 px-5 py-8 text-center">
              <p className="text-sm text-slate-600">{holdersError}</p>
              <button
                onClick={loadHolders}
                className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                다시 시도
              </button>
            </div>
          ) : holders.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-slate-500">
              부여된 App 생성 권한이 없습니다.
            </p>
          ) : (
            <>
              <table className="mt-2 w-full text-left text-sm">
                <caption className="sr-only">
                  App 생성 권한 보유 목록 (부여일 내림차순)
                </caption>
                <thead>
                  <tr className="border-b border-slate-100 text-xs uppercase text-slate-500">
                    <th scope="col" className="px-5 py-2 font-semibold">
                      보유자
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      부여자
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      부여일
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      처리
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {holders.map((item) => (
                    <tr key={item.id} className="border-b border-slate-50">
                      <td className="px-5 py-3">
                        {item.user ? (
                          <span className="flex flex-col">
                            <span className="font-medium text-slate-900">
                              {item.user.name}
                            </span>
                            <span className="text-xs text-slate-500">
                              {item.user.email}
                            </span>
                          </span>
                        ) : (
                          <span className="text-slate-500">
                            알 수 없는 사용자
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-slate-700">
                        {memberNamesByUserId.get(item.assigned_by) ||
                          '확인 불가'}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500">
                        <time dateTime={item.assigned_at}>
                          {new Date(item.assigned_at).toLocaleString()}
                        </time>
                      </td>
                      <td className="px-3 py-3">
                        <button
                          onClick={() => setRevokeTarget(item)}
                          className="h-8 rounded-md border border-red-200 px-3 text-xs font-semibold text-red-700 hover:bg-red-50"
                        >
                          회수
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <AdminPagination
                page={holdersPage}
                totalPages={Math.max(1, Math.ceil(holdersTotal / PAGE_SIZE))}
                total={holdersTotal}
                onPageChange={setHoldersPage}
              />
            </>
          )}
        </section>
      )}

      {confirmState && (
        <ProcessConfirmDialog
          state={confirmState}
          processing={processing}
          onConfirm={processConfirmed}
          onCancel={() => {
            if (!processing) setConfirmState(null);
          }}
        />
      )}

      {revokeTarget && (
        <RevokeConfirmDialog
          target={revokeTarget}
          processing={revoking}
          onConfirm={revokeConfirmed}
          onCancel={() => {
            if (!revoking) setRevokeTarget(null);
          }}
        />
      )}
    </DashboardPanel>
  );
}

function RevokeConfirmDialog({
  target,
  processing,
  onConfirm,
  onCancel,
}: {
  target: AppCreationPermissionItem;
  processing: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <div
        className="absolute inset-0 bg-slate-950/30"
        onClick={onCancel}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="App 생성 권한 회수 확인"
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
        }}
        className="relative w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 className="text-sm font-semibold text-slate-950">
          App 생성 권한을 회수할까요?
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          회수하면 해당 멤버의 새 모듈 생성이 다시 차단됩니다. 멤버는 권한을
          재신청할 수 있습니다.
        </p>
        <dl className="mt-4 flex flex-col gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs font-semibold text-slate-500">
              보유자
            </dt>
            <dd className="text-slate-900">
              {target.user
                ? `${target.user.name} (${target.user.email})`
                : '알 수 없는 사용자'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs font-semibold text-slate-500">
              권한
            </dt>
            <dd className="text-slate-900">
              {requestedPermissionLabel('app.create')}
            </dd>
          </div>
        </dl>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={processing}
            className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            취소
          </button>
          <button
            onClick={onConfirm}
            disabled={processing}
            autoFocus
            className="h-9 rounded-md bg-red-600 px-4 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {processing ? '처리 중...' : '회수 확정'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProcessConfirmDialog({
  state,
  processing,
  onConfirm,
  onCancel,
}: {
  state: ConfirmState;
  processing: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const isApprove = state.action === 'approve';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <div
        className="absolute inset-0 bg-slate-950/30"
        onClick={onCancel}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isApprove ? '권한 신청 승인 확인' : '권한 신청 거절 확인'}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
        }}
        className="relative w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 className="text-sm font-semibold text-slate-950">
          {isApprove ? '권한 신청을 승인할까요?' : '권한 신청을 거절할까요?'}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          {isApprove
            ? '승인하면 요청자에게 조직 수준 App 생성 능력이 부여됩니다. 되돌리려면 별도 권한 회수가 필요합니다.'
            : '거절된 신청자는 다시 신청할 수 있습니다.'}
        </p>
        <dl className="mt-4 flex flex-col gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs font-semibold text-slate-500">
              요청자
            </dt>
            <dd className="text-slate-900">
              {state.request.user
                ? `${state.request.user.name} (${state.request.user.email})`
                : '알 수 없는 사용자'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs font-semibold text-slate-500">
              요청 권한
            </dt>
            <dd className="text-slate-900">
              {requestedPermissionLabel(state.request.requested_permission)}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs font-semibold text-slate-500">
              신청 사유
            </dt>
            <dd className="break-all text-slate-900">{state.request.reason}</dd>
          </div>
        </dl>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={processing}
            className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            취소
          </button>
          <button
            onClick={onConfirm}
            disabled={processing}
            autoFocus
            className={`h-9 rounded-md px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 ${
              isApprove
                ? 'bg-slate-950 hover:bg-slate-800'
                : 'bg-red-600 hover:bg-red-500'
            }`}
          >
            {processing
              ? '처리 중...'
              : isApprove
                ? '승인 확정'
                : '거절 확정'}
          </button>
        </div>
      </div>
    </div>
  );
}
