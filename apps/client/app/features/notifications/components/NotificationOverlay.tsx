'use client';

import { useState } from 'react';
import { Bell, Check, X, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { organizationApi } from '../../organization/api/organizationApi';
import type { SecurityAlertSummaryResponse } from '../../admin/types/SecurityAlert';
import type { NotificationItem } from '../types/Notification';

const AUTH_LABELS: Record<NotificationItem['organization_auth_state'], string> = {
  member: '멤버',
  manager: '관리자',
};

const RULE_LABELS: Record<string, string> = {
  repeated_permission_denied: '반복된 권한 거부',
  multi_resource_permission_probe: '여러 리소스 접근 시도',
  repeated_policy_block: '반복된 정책 차단',
};

const SEVERITY_LABELS: Record<string, string> = {
  medium: '보통',
  high: '높음',
};

type NotificationOverlayProps = {
  notifications: NotificationItem[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  showSecurityAlerts?: boolean;
  securityAlertSummary?: SecurityAlertSummaryResponse | null;
  securityAlertsLoading?: boolean;
  securityAlertsError?: string | null;
  onRefreshSecurityAlerts?: () => Promise<void>;
  onSelectSecurityAlert?: (alertId: string) => void;
  onViewAllSecurityAlerts?: () => void;
};

export function NotificationOverlay({
  notifications,
  loading,
  error,
  onClose,
  onRefresh,
  showSecurityAlerts = false,
  securityAlertSummary = null,
  securityAlertsLoading = false,
  securityAlertsError = null,
  onRefreshSecurityAlerts,
  onSelectSecurityAlert,
  onViewAllSecurityAlerts,
}: NotificationOverlayProps) {
  const [processingId, setProcessingId] = useState<string | null>(null);

  const acceptInvitation = async (item: NotificationItem) => {
    setProcessingId(item.id);
    try {
      await organizationApi.acceptInvitation(item.organization_id);
      toast.success('조직 초대를 수락했습니다.');
      await onRefresh();
    } catch {
      toast.error('조직 초대 수락에 실패했습니다.');
    } finally {
      setProcessingId(null);
    }
  };

  const declineInvitation = async (item: NotificationItem) => {
    setProcessingId(item.id);
    try {
      await organizationApi.declineInvitation(item.organization_id);
      toast.success('조직 초대를 거절했습니다.');
      await onRefresh();
    } catch {
      toast.error('조직 초대 거절에 실패했습니다.');
    } finally {
      setProcessingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-start justify-end bg-slate-950/20 px-4 py-6">
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        onClick={onClose}
        aria-label="알림 닫기"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label="알림"
        className="relative w-full max-w-md overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl"
      >
        <header className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-slate-700" />
            <h2 className="text-sm font-semibold text-slate-950">알림</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            aria-label="닫기"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto">
          {showSecurityAlerts && (
            <section role="region" aria-label="보안 알림">
              <div className="flex items-center justify-between px-5 py-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-950">
                    보안 알림
                  </h3>
                  <p className="mt-0.5 text-xs text-slate-500">
                    미확인 {securityAlertSummary?.open_count ?? 0}개
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onViewAllSecurityAlerts?.();
                  }}
                  aria-label="보안 알림 모두 보기"
                  className="text-xs font-semibold text-blue-700"
                >
                  모두 보기
                </button>
              </div>
              {securityAlertsLoading ? (
                <p className="px-5 py-6 text-center text-sm text-slate-500">
                  보안 알림을 불러오는 중...
                </p>
              ) : securityAlertsError ? (
                <div className="flex flex-col items-center gap-3 px-5 py-6 text-center">
                  <p className="text-sm text-slate-600">
                    {securityAlertsError}
                  </p>
                  <button
                    type="button"
                    onClick={onRefreshSecurityAlerts}
                    className="h-8 rounded-md border border-slate-300 px-3 text-xs font-semibold text-slate-700"
                  >
                    보안 알림 다시 시도
                  </button>
                </div>
              ) : !securityAlertSummary ||
                securityAlertSummary.recent_items.length === 0 ? (
                <p className="px-5 py-6 text-center text-sm text-slate-500">
                  미확인 보안 알림이 없습니다.
                </p>
              ) : (
                <ul className="divide-y divide-slate-100 border-t border-slate-100">
                  {securityAlertSummary.recent_items.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => {
                          onClose();
                          onSelectSecurityAlert?.(item.id);
                        }}
                        className="w-full px-5 py-3 text-left hover:bg-slate-50"
                      >
                        <span className="block text-sm font-semibold text-slate-950">
                          {RULE_LABELS[item.rule_id] || '알 수 없는 탐지 규칙'}
                        </span>
                        <span className="mt-1 block text-xs text-slate-500">
                          {SEVERITY_LABELS[item.severity] || '알 수 없음'}
                        </span>
                        <span className="mt-0.5 block text-xs text-slate-500">
                          {item.actor.display_name ||
                            (item.actor.state === 'deleted'
                              ? '삭제된 사용자'
                              : '이름 없는 사용자')}{' '}
                          · {item.occurrence_count}회
                        </span>
                        <time
                          dateTime={item.last_detected_at}
                          className="mt-1 block text-xs text-slate-400"
                        >
                          {new Date(item.last_detected_at).toLocaleString()}
                        </time>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section role="region" aria-label="조직 초대">
            <h3 className="px-5 py-3 text-sm font-semibold text-slate-950">
              조직 초대
            </h3>
            {loading ? (
              <p className="px-5 py-6 text-center text-sm text-slate-500">
                조직 초대를 불러오는 중...
              </p>
            ) : error ? (
              <div className="flex flex-col items-center gap-3 px-5 py-6 text-center">
                <p className="text-sm text-slate-600">{error}</p>
                <button
                  type="button"
                  onClick={onRefresh}
                  className="h-8 rounded-md border border-slate-300 px-3 text-xs font-semibold text-slate-700"
                >
                  조직 초대 다시 시도
                </button>
              </div>
            ) : notifications.length === 0 ? (
              <p className="px-5 py-6 text-center text-sm text-slate-500">
                새 조직 초대가 없습니다.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100 border-t border-slate-100">
                {notifications.map((item) => {
                  const isProcessing = processingId === item.id;
                  return (
                    <li key={item.id} className="px-5 py-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-950">
                            {item.organization_name}
                          </p>
                          <p className="mt-1 text-xs text-slate-500">
                            조직 {AUTH_LABELS[item.organization_auth_state]} 초대
                          </p>
                          <time
                            dateTime={item.created_at}
                            className="mt-1 block text-xs text-slate-400"
                          >
                            {new Date(item.created_at).toLocaleString()}
                          </time>
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <button
                            type="button"
                            disabled={isProcessing}
                            onClick={() => acceptInvitation(item)}
                            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-slate-950 px-3 text-xs font-semibold text-white disabled:opacity-40"
                          >
                            <Check className="h-3.5 w-3.5" />
                            수락
                          </button>
                          <button
                            type="button"
                            disabled={isProcessing}
                            onClick={() => declineInvitation(item)}
                            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-red-200 px-3 text-xs font-semibold text-red-700 disabled:opacity-40"
                          >
                            <XCircle className="h-3.5 w-3.5" />
                            거절
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </section>
    </div>
  );
}
