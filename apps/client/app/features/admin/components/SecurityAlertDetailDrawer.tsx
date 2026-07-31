'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { ShieldAlert, UserRoundCog, X } from 'lucide-react';
import type { OrganizationMember } from '../../organization/types/Organization';
import { adminApi } from '../api/adminApi';
import type {
  SecurityAlertAuditLogItem,
  SecurityAlertDetail,
  SecurityAlertResolutionType,
  SecurityAlertRuleId,
  SecurityAlertSeverity,
  SecurityAlertStatus,
} from '../types/SecurityAlert';
import { AdminPagination } from './AdminPagination';
import { AuditDetailDrawer } from './AuditDetailDrawer';
import { ResolveAlertDialog } from './ResolveAlertDialog';
import {
  auditEventSummary,
  auditStatusLabel,
  auditTargetLabel,
} from '../utils/auditPresentation';

const PAGE_SIZE = 20;
const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

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
const RESOLUTION_LABELS: Record<SecurityAlertResolutionType, string> = {
  mitigated: '대응 완료',
  false_positive: '오탐',
  accepted_risk: '위험 수용',
};
const POLICY_REASON_LABELS: Record<string, string> = {
  'access_management.self_control_forbidden': '본인 접근 상태 변경 제한',
  'access_management.last_active_manager': '마지막 관리자 보호',
  'access_management.manager_override_active': '관리자 권한 보호',
  'access_management.member_state_not_manageable':
    '현재 멤버 상태에서 허용되지 않은 작업',
  'access_management.target_user_inactive': '비활성 사용자 대상 작업 제한',
  'access_management.stale_state': '최신 상태와 다른 요청',
  'rag.pii_evidence_detected': '개인정보 포함 근거 감지',
};

const formatDateTime = (value: string) => new Date(value).toLocaleString();

type SecurityAlertDetailDrawerProps = {
  alertId: string;
  members: OrganizationMember[];
  onClose: () => void;
  onAfterClose?: () => void;
  onNotFound: () => void;
  onManageActor?: (actorId: string) => void;
  onChanged?: (detail: SecurityAlertDetail) => void;
  refreshToken?: number;
};

export function SecurityAlertDetailDrawer({
  alertId,
  members,
  onClose,
  onAfterClose,
  onNotFound,
  onManageActor,
  onChanged,
  refreshToken = 0,
}: SecurityAlertDetailDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const auditTriggerRef = useRef<HTMLElement | null>(null);
  const detailSequenceRef = useRef(0);
  const evidenceSequenceRef = useRef(0);
  const loadedDetailAlertIdRef = useRef<string | null>(null);
  const loadedEvidenceScopeRef = useRef<string | null>(null);
  const onNotFoundRef = useRef(onNotFound);
  const [detail, setDetail] = useState<SecurityAlertDetail | null>(null);
  const [detailError, setDetailError] = useState<'forbidden' | 'unknown' | null>(
    null,
  );
  const [evidence, setEvidence] = useState<SecurityAlertAuditLogItem[]>([]);
  const [evidenceTotal, setEvidenceTotal] = useState(0);
  const [evidencePage, setEvidencePage] = useState(1);
  const [evidenceLoading, setEvidenceLoading] = useState(true);
  const [evidenceError, setEvidenceError] = useState(false);
  const [selectedAuditId, setSelectedAuditId] = useState<string | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const [mutationFeedback, setMutationFeedback] = useState<string | null>(null);
  const [resolveOpen, setResolveOpen] = useState(false);
  const evidenceRequestContext = `${alertId}:${evidencePage}:${refreshToken}`;
  const currentEvidenceRequestContextRef = useRef(evidenceRequestContext);

  useEffect(() => {
    currentEvidenceRequestContextRef.current = evidenceRequestContext;
  }, [evidenceRequestContext]);

  useEffect(() => {
    onNotFoundRef.current = onNotFound;
  }, [onNotFound]);

  const loadDetail = useCallback(async (mode: 'background' | 'authoritative') => {
    const sequence = ++detailSequenceRef.current;
    const canPreserveCurrentDetail =
      mode === 'background' &&
      loadedDetailAlertIdRef.current === alertId;
    if (!canPreserveCurrentDetail) {
      loadedDetailAlertIdRef.current = null;
      setDetail(null);
    }
    setDetailError(null);
    try {
      const data = await adminApi.getSecurityAlertDetail(alertId);
      if (sequence !== detailSequenceRef.current) return null;
      setDetail(data);
      loadedDetailAlertIdRef.current = alertId;
      return data;
    } catch (loadError) {
      if (sequence !== detailSequenceRef.current) return null;
      if (isAxiosError(loadError) && loadError.response?.status === 404) {
        loadedDetailAlertIdRef.current = null;
        setDetail(null);
        onNotFoundRef.current();
        return null;
      }
      if (isAxiosError(loadError) && loadError.response?.status === 403) {
        loadedDetailAlertIdRef.current = null;
        setDetail(null);
      }
      if (
        canPreserveCurrentDetail &&
        !(isAxiosError(loadError) && loadError.response?.status === 403)
      ) {
        return null;
      }
      setDetailError(
        isAxiosError(loadError) && loadError.response?.status === 403
          ? 'forbidden'
          : 'unknown',
      );
      return null;
    }
  }, [alertId]);

  useEffect(() => {
    loadDetail('background');
    return () => {
      detailSequenceRef.current += 1;
    };
  }, [loadDetail, refreshToken]);

  useEffect(() => {
    setEvidencePage(1);
  }, [alertId]);

  const loadEvidence = useCallback(async () => {
    const sequence = ++evidenceSequenceRef.current;
    const requestContext = evidenceRequestContext;
    const evidenceScope = `${alertId}:${evidencePage}`;
    const canPreserveCurrentEvidence =
      loadedEvidenceScopeRef.current === evidenceScope;
    const isCurrentRequest = () =>
      sequence === evidenceSequenceRef.current &&
      requestContext === currentEvidenceRequestContextRef.current;
    if (!canPreserveCurrentEvidence) {
      loadedEvidenceScopeRef.current = null;
      setEvidenceLoading(true);
      setEvidence([]);
      setEvidenceTotal(0);
    }
    setEvidenceError(false);
    try {
      const data = await adminApi.listSecurityAlertAuditLogs(alertId, {
        page: evidencePage,
        limit: PAGE_SIZE,
      });
      if (!isCurrentRequest()) return;
      setEvidence(data.items);
      setEvidenceTotal(data.total);
      loadedEvidenceScopeRef.current = evidenceScope;
    } catch {
      if (isCurrentRequest() && !canPreserveCurrentEvidence) {
        setEvidenceError(true);
      }
    } finally {
      if (isCurrentRequest()) setEvidenceLoading(false);
    }
  }, [alertId, evidencePage, evidenceRequestContext]);

  useEffect(() => {
    loadEvidence();
  }, [loadEvidence, refreshToken]);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  const manageableActorId = useMemo(() => {
    const actorId = detail?.actor.id;
    if (!actorId) return null;
    const member = members.find(
      (item) =>
        item.user_id === actorId &&
        (item.membership_state === 'active' ||
          item.membership_state === 'suspended'),
    );
    return member ? actorId : null;
  }, [detail?.actor.id, members]);

  const close = () => {
    onClose();
    requestAnimationFrame(() => onAfterClose?.());
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab' || !panelRef.current) return;
    const focusable = panelRef.current.querySelectorAll<HTMLElement>(
      FOCUSABLE_SELECTOR,
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === panelRef.current)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const evidenceTotalPages = Math.max(
    1,
    Math.ceil(evidenceTotal / PAGE_SIZE),
  );

  const runMutation = async (
    mutate: () => Promise<SecurityAlertDetail>,
    successMessage: string,
  ) => {
    if (mutationPending) return false;
    setMutationPending(true);
    setMutationFeedback(null);
    try {
      const updated = await mutate();
      setDetail(updated);
      setMutationFeedback(successMessage);
      onChanged?.(updated);
      return true;
    } catch (mutationError) {
      if (
        isAxiosError(mutationError) &&
        mutationError.response?.status === 403
      ) {
        loadedDetailAlertIdRef.current = null;
        setDetail(null);
        setMutationFeedback(null);
        setResolveOpen(false);
        close();
        return true;
      }
      if (
        isAxiosError(mutationError) &&
        mutationError.response?.status === 409
      ) {
        const latest = await loadDetail('authoritative');
        if (latest) {
          onChanged?.(latest);
          setMutationFeedback('다른 관리자가 상태를 변경했습니다.');
          return true;
        }
        setMutationFeedback(null);
        return false;
      }
      setMutationFeedback('상태 변경에 실패했습니다. 다시 시도해 주세요.');
      return false;
    } finally {
      setMutationPending(false);
    }
  };

  return (
    <>
      <div className="fixed inset-0 z-[130] flex justify-end">
        <div
          className="absolute inset-0 bg-slate-950/30"
          onClick={close}
          aria-hidden="true"
        />
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label="보안 알림 상세"
          tabIndex={-1}
          onKeyDown={handleKeyDown}
          className="relative flex h-full w-full max-w-4xl flex-col overflow-y-auto bg-white shadow-xl outline-none [&_.text-xs]:text-sm [&_.text-sm]:text-base [&_.text-base]:text-lg [&_.text-lg]:text-xl"
        >
          <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
              <ShieldAlert className="h-4 w-4 text-red-600" />
              보안 알림 상세
            </h2>
            <button
              type="button"
              onClick={close}
              aria-label="보안 알림 상세 닫기"
              className="rounded p-1 text-slate-500 hover:bg-slate-100"
            >
              <X className="h-4 w-4" />
            </button>
          </header>

          {detailError ? (
            <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
              <p className="text-sm text-slate-600">
                {detailError === 'forbidden'
                  ? '보안 알림 관리 권한이 없습니다.'
                  : '보안 알림 상세를 불러오지 못했습니다.'}
              </p>
            </div>
          ) : !detail ? (
            <p className="px-5 py-12 text-center text-sm text-slate-500">
              보안 알림 상세를 불러오는 중...
            </p>
          ) : (
            <>
              <section className="space-y-4 border-b border-slate-200 px-5 py-5">
                <div>
                  <p className="text-sm font-semibold text-slate-950">
                    {RULE_LABELS[detail.rule_id] ?? '알 수 없는 탐지 규칙'}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    침해 확정이 아닌, 관리자가 확인해야 할 위험 신호입니다.
                  </p>
                </div>
                <dl className="grid gap-4 text-sm sm:grid-cols-2">
                  <DetailField
                    label="심각도"
                    value={SEVERITY_LABELS[detail.severity] ?? '알 수 없음'}
                  />
                  <DetailField
                    label="상태"
                    value={STATUS_LABELS[detail.status] ?? '알 수 없음'}
                  />
                  <DetailField label="규칙 버전" value={detail.rule_version} />
                  <DetailField
                    label="사용자"
                    value={
                      detail.actor.display_name ||
                      (detail.actor.state === 'deleted'
                        ? '삭제된 사용자'
                        : '이름 없는 사용자')
                    }
                  />
                  <DetailField
                    label="정책 사유"
                    value={
                      detail.policy_reason
                        ? POLICY_REASON_LABELS[detail.policy_reason] ||
                          '알 수 없는 정책 사유'
                        : '-'
                    }
                  />
                  <DetailField
                    label="발생 횟수"
                    value={String(detail.occurrence_count)}
                  />
                  <DetailField
                    label="최초 탐지"
                    value={formatDateTime(detail.first_detected_at)}
                  />
                  <DetailField
                    label="최근 탐지"
                    value={formatDateTime(detail.last_detected_at)}
                  />
                  <DetailField
                    label="확인"
                    value={
                      detail.acknowledged
                        ? `${detail.acknowledged.by.display_name || '삭제된 관리자'} · ${formatDateTime(detail.acknowledged.at)}`
                        : '-'
                    }
                  />
                  <DetailField
                    label="처리 결과"
                    value={
                      detail.resolution
                        ? RESOLUTION_LABELS[detail.resolution.type]
                        : '-'
                    }
                  />
                </dl>
                {detail.resolution && (
                  <div className="rounded-md bg-slate-50 p-3 text-sm text-slate-700">
                    <p className="text-xs font-semibold text-slate-500">
                      처리 사유
                    </p>
                    <p className="mt-1 whitespace-pre-wrap break-words">
                      {detail.resolution.reason}
                    </p>
                    <p className="mt-2 text-xs text-slate-500">
                      {detail.resolution.by.display_name || '삭제된 관리자'} ·{' '}
                      {formatDateTime(detail.resolution.at)}
                    </p>
                  </div>
                )}
                {manageableActorId && onManageActor && (
                  <button
                    type="button"
                    onClick={() => onManageActor(manageableActorId)}
                    className="inline-flex h-9 items-center gap-1.5 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-700"
                  >
                    <UserRoundCog className="h-4 w-4" />
                    사용자 접근 관리
                  </button>
                )}
                {detail.status !== 'resolved' && (
                  <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                    {detail.status === 'open' ? (
                      <button
                        type="button"
                        disabled={mutationPending}
                        onClick={() =>
                          runMutation(
                            () =>
                              adminApi.acknowledgeSecurityAlert(
                                detail.id,
                                detail.version,
                              ),
                            '보안 알림을 확인 상태로 변경했습니다.',
                          )
                        }
                        className="h-9 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white disabled:opacity-40"
                      >
                        확인
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={mutationPending}
                        onClick={() =>
                          runMutation(
                            () =>
                              adminApi.reopenSecurityAlert(
                                detail.id,
                                detail.version,
                              ),
                            '보안 알림을 미확인 상태로 되돌렸습니다.',
                          )
                        }
                        className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 disabled:opacity-40"
                      >
                        미확인으로 되돌리기
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={mutationPending}
                      onClick={() => setResolveOpen(true)}
                      className="h-9 rounded-md border border-red-200 px-4 text-sm font-semibold text-red-700 disabled:opacity-40"
                    >
                      해결
                    </button>
                  </div>
                )}
                {mutationFeedback && (
                  <p aria-live="polite" className="text-sm text-slate-700">
                    {mutationFeedback}
                  </p>
                )}
              </section>

              <section aria-label="연결된 감사 기록" className="py-5">
                <h3 className="px-5 text-sm font-semibold text-slate-950">
                  연결된 감사 기록 ({detail.evidence_count})
                </h3>
                {evidenceLoading ? (
                  <p className="px-5 py-8 text-center text-sm text-slate-500">
                    감사 기록을 불러오는 중...
                  </p>
                ) : evidenceError ? (
                  <div className="flex flex-col items-center gap-3 px-5 py-8 text-center">
                    <p className="text-sm text-slate-600">
                      연결된 감사 기록을 불러오지 못했습니다.
                    </p>
                    <button
                      type="button"
                      onClick={loadEvidence}
                      aria-label="감사 기록 다시 시도"
                      className="h-9 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-700"
                    >
                      다시 시도
                    </button>
                  </div>
                ) : evidence.length === 0 ? (
                  <p className="px-5 py-8 text-center text-sm text-slate-500">
                    연결된 감사 기록이 없습니다.
                  </p>
                ) : (
                  <>
                    <div className="mt-3 overflow-x-auto">
                      <table className="min-w-full divide-y divide-slate-200 text-sm">
                        <thead className="bg-slate-50 text-left text-xs text-slate-500">
                          <tr>
                            <th className="px-5 py-3">발생 시각</th>
                            <th className="px-3 py-3">기록 내용</th>
                            <th className="px-3 py-3">상태</th>
                            <th className="px-5 py-3"><span className="sr-only">작업</span></th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {evidence.map((item) => (
                            <tr key={item.id}>
                              <td className="px-5 py-3 text-xs text-slate-500">
                                <time dateTime={item.occurred_at}>
                                  {formatDateTime(item.occurred_at)}
                                </time>
                              </td>
                              <td className="px-3 py-3">
                                <p className="font-medium text-slate-900">
                                  {auditEventSummary({
                                    action: item.action,
                                    status: item.status,
                                    targetType: item.target_type,
                                    targetId: item.target_id,
                                    currentOrganizationId: detail.organization_id,
                                    requiredPermission: item.required_permission,
                                    requestedOperation: item.requested_operation,
                                  })}
                                </p>
                                <p className="mt-1 text-xs text-slate-500">
                                  <code>{item.action}</code>
                                  {' · '}
                                  {auditTargetLabel(
                                    item.target_type,
                                    item.target_id,
                                    detail.organization_id,
                                  )}
                                </p>
                              </td>
                              <td className="px-3 py-3">
                                {auditStatusLabel(item.status, item.action)}
                              </td>
                              <td className="px-5 py-3 text-right">
                                <button
                                  type="button"
                                  aria-label="감사 기록 상세 보기"
                                  onClick={(event) => {
                                    auditTriggerRef.current = event.currentTarget;
                                    setSelectedAuditId(item.id);
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
                      page={evidencePage}
                      totalPages={evidenceTotalPages}
                      total={evidenceTotal}
                      onPageChange={setEvidencePage}
                    />
                  </>
                )}
              </section>
            </>
          )}
        </div>
      </div>

      {selectedAuditId && (
        <AuditDetailDrawer
          auditLogId={selectedAuditId}
          currentOrganizationId={detail?.organization_id}
          stacked
          onClose={() => setSelectedAuditId(null)}
          onAfterClose={() => auditTriggerRef.current?.focus()}
        />
      )}
      {resolveOpen && detail && (
        <ResolveAlertDialog
          pending={mutationPending}
          onCancel={() => setResolveOpen(false)}
          onSubmit={(resolutionType, reason) =>
            runMutation(
              () =>
                adminApi.resolveSecurityAlert(detail.id, {
                  expectedVersion: detail.version,
                  resolutionType,
                  reason,
                }),
              '보안 알림을 해결 상태로 변경했습니다.',
            )
          }
        />
      )}
    </>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-semibold text-slate-500">{label}</dt>
      <dd className="mt-1 text-slate-900">{value}</dd>
    </div>
  );
}
