'use client';

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { adminApi } from '../api/adminApi';
import type { AuditLogDetailResponse } from '../types/AdminAudit';
import { auditActionLabel } from '../utils/auditActionLabel';
import {
  auditEventSummary,
  auditMetadataLabel,
  auditMetadataValueLabel,
  auditStatusLabel,
  auditTargetLabel,
} from '../utils/auditPresentation';
import { AuditReferenceDisplay } from './AuditReferenceDisplay';

type AuditDetailDrawerProps = {
  auditLogId: string;
  actorName?: string | null;
  currentOrganizationId?: string | null;
  stacked?: boolean;
  onClose: () => void;
  onAfterClose?: () => void;
};

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

const formatMetadataValue = (value: unknown) =>
  typeof value === 'string' ? value : JSON.stringify(value);

export function AuditDetailDrawer({
  auditLogId,
  actorName,
  currentOrganizationId,
  stacked = false,
  onClose,
  onAfterClose,
}: AuditDetailDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [detail, setDetail] = useState<AuditLogDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    adminApi
      .getAuditLogDetail(auditLogId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) setError('감사 로그 상세를 불러오지 못했습니다.');
      });
    return () => {
      cancelled = true;
    };
  }, [auditLogId]);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

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
      (document.activeElement === first || document.activeElement === panelRef.current)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const label = detail ? auditActionLabel(detail.action) : null;
  const metadataEntries = detail ? Object.entries(detail.audit_metadata) : [];

  return (
    <div
      className={`fixed inset-0 flex justify-end ${stacked ? 'z-[140]' : 'z-50'}`}
    >
      <div
        className="absolute inset-0 bg-slate-950/30"
        onClick={close}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="감사 로그 상세"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="relative flex h-full w-full max-w-xl flex-col overflow-y-auto bg-white shadow-xl outline-none [&_.text-xs]:text-sm [&_.text-sm]:text-base [&_.text-base]:text-lg [&_.text-lg]:text-xl"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="text-sm font-semibold text-slate-950">
            감사 로그 상세
          </h2>
          <button
            onClick={close}
            className="rounded p-1 text-slate-500 hover:bg-slate-100"
            aria-label="닫기"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <p className="px-5 py-6 text-sm text-red-700">{error}</p>
        )}
        {!error && !detail && (
          <p className="px-5 py-6 text-sm text-slate-500">불러오는 중...</p>
        )}
        {detail && (
          <dl className="flex flex-col gap-4 px-5 py-5 text-sm">
            <div>
              <dt className="sr-only">설명</dt>
              <dd className="rounded-md bg-blue-50 px-3 py-3 text-sm font-medium text-blue-950">
                {auditEventSummary({
                  action: detail.action,
                  status: detail.status,
                  targetType: detail.target_type,
                  targetId: detail.target_id,
                  targetDisplayLabel: detail.target_display?.label,
                  currentOrganizationId,
                  requiredPermission:
                    typeof detail.audit_metadata.required_permission ===
                    'string'
                      ? detail.audit_metadata.required_permission
                      : null,
                  requestedOperation:
                    typeof detail.audit_metadata.requested_operation ===
                    'string'
                      ? detail.audit_metadata.requested_operation
                      : null,
                })}
              </dd>
            </div>
            <DetailField
              label="발생 시각"
              value={
                <time dateTime={detail.occurred_at}>
                  {new Date(detail.occurred_at).toLocaleString()}
                </time>
              }
            />
            <DetailField
              label="행위자"
              value={
                detail.actor_display?.label || actorName ? (
                  detail.actor_id ? (
                    <AuditReferenceDisplay
                      label={detail.actor_display?.label || actorName!}
                      id={detail.actor_id}
                      copyLabel="행위자 ID 복사"
                    />
                  ) : (
                    <span className="text-slate-800">
                      {detail.actor_display?.label || actorName}
                    </span>
                  )
                ) : (
                  detail.actor_id || `${detail.actor_type} (id 없음)`
                )
              }
            />
            <DetailField
              label="작업"
              value={
                <span className="flex flex-wrap items-center gap-2">
                  {label && <span className="text-slate-900">{label}</span>}
                  <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                    {detail.action}
                  </code>
                </span>
              }
            />
            <DetailField
              label="대상"
              value={
                detail.target_display && detail.target_id ? (
                  <AuditReferenceDisplay
                    label={detail.target_display.label}
                    id={detail.target_id}
                    copyLabel="대상 ID 복사"
                  />
                ) : (
                  auditTargetLabel(
                    detail.target_type,
                    detail.target_id,
                    currentOrganizationId,
                  )
                )
              }
            />
            <DetailField
              label="상태"
              value={
                <span
                  className={`w-fit rounded-md px-2 py-0.5 text-xs font-semibold ${
                    detail.status === 'failure'
                      ? 'bg-red-50 text-red-700'
                      : 'bg-emerald-50 text-emerald-700'
                  }`}
                >
                  {auditStatusLabel(detail.status, detail.action)}
                </span>
              }
            />
            {detail.change_summary && (
              <div>
                <dt className="text-xs font-semibold uppercase text-slate-500">
                  변경 요약
                </dt>
                <dd className="mt-2 divide-y divide-slate-100 border-y border-slate-200">
                  <Snapshot
                    label="변경 전"
                    value={detail.change_summary.before}
                    resolvedReferences={detail.resolved_references}
                  />
                  <Snapshot
                    label="변경 후"
                    value={detail.change_summary.after}
                    resolvedReferences={detail.resolved_references}
                  />
                </dd>
              </div>
            )}
            {metadataEntries.length > 0 && (
              <div>
                <dt className="text-xs font-semibold uppercase text-slate-500">
                  추가 정보
                </dt>
                <dd className="mt-2 flex flex-col gap-1 rounded-md border border-slate-200 bg-slate-50 p-3">
                  {metadataEntries.map(([key, value]) => (
                    <div key={key} className="flex gap-2 text-xs">
                      <span className="shrink-0 font-medium text-slate-500">
                        {auditMetadataLabel(key)}
                      </span>
                      <ResolvedValue
                        fieldKey={key}
                        value={value}
                        resolvedReferences={detail.resolved_references}
                        fallback={auditMetadataValueLabel(key, value)}
                      />
                    </div>
                  ))}
                </dd>
              </div>
            )}
          </dl>
        )}
      </div>
    </div>
  );
}

function Snapshot({
  label,
  value,
  resolvedReferences,
}: {
  label: string;
  value: Record<string, unknown> | null;
  resolvedReferences?: AuditLogDetailResponse['resolved_references'];
}) {
  if (!value) return null;
  return (
    <div className="py-3">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <div className="mt-2 space-y-1">
        {Object.entries(value).map(([key, fieldValue]) => (
          <div
            key={key}
            className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] gap-2 text-xs"
          >
            <span className="truncate font-medium text-slate-500">{key}</span>
            <ResolvedValue
              fieldKey={key}
              value={fieldValue}
              resolvedReferences={resolvedReferences}
              fallback={formatMetadataValue(fieldValue)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function ResolvedValue({
  fieldKey,
  value,
  resolvedReferences,
  fallback,
}: {
  fieldKey: string;
  value: unknown;
  resolvedReferences?: AuditLogDetailResponse['resolved_references'];
  fallback: string;
}) {
  const reference =
    typeof value === 'string' ? resolvedReferences?.[value] : undefined;
  if (reference && typeof value === 'string') {
    return (
      <AuditReferenceDisplay
        label={reference.label}
        id={value}
        copyLabel={`${fieldKey} ID 복사`}
      />
    );
  }
  return <span className="break-all text-slate-800">{fallback}</span>;
}

function DetailField({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-slate-900">{value}</dd>
    </div>
  );
}
