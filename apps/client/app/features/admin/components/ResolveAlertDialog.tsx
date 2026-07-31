'use client';

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { SecurityAlertResolutionType } from '../types/SecurityAlert';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const normalizeReason = (value: string) =>
  value.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();

const hasForbiddenControlCharacter = (value: string) =>
  Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return (
      (codePoint < 32 && character !== '\t' && character !== '\n') ||
      (codePoint >= 127 && codePoint <= 159) ||
      (codePoint >= 0x202a && codePoint <= 0x202e) ||
      (codePoint >= 0x2066 && codePoint <= 0x2069)
    );
  });

type ResolveAlertDialogProps = {
  pending: boolean;
  onCancel: () => void;
  onSubmit: (
    resolutionType: SecurityAlertResolutionType,
    reason: string,
  ) => Promise<boolean>;
};

export function ResolveAlertDialog({
  pending,
  onCancel,
  onSubmit,
}: ResolveAlertDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [resolutionType, setResolutionType] = useState<
    '' | SecurityAlertResolutionType
  >('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  const submit = async () => {
    if (!resolutionType) {
      setError('처리 결과를 선택해 주세요.');
      return;
    }
    const normalizedReason = normalizeReason(reason);
    if (!normalizedReason) {
      setError('처리 사유를 입력해 주세요.');
      return;
    }
    if (Array.from(normalizedReason).length > 500) {
      setError('처리 사유는 500자 이하여야 합니다.');
      return;
    }
    if (hasForbiddenControlCharacter(normalizedReason)) {
      setError('처리 사유에 허용되지 않는 문자가 있습니다.');
      return;
    }
    setError(null);
    const shouldClose = await onSubmit(resolutionType, normalizedReason);
    if (shouldClose) onCancel();
    else setError('보안 알림 해결에 실패했습니다. 다시 시도해 주세요.');
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && !pending) {
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
      FOCUSABLE_SELECTOR,
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === dialogRef.current)
    ) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fixed inset-0 z-[160] grid place-items-center bg-slate-950/40 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="보안 알림 해결"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg rounded-lg bg-white shadow-xl outline-none"
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h3 className="text-sm font-semibold text-slate-950">
            보안 알림 해결
          </h3>
          <button
            type="button"
            aria-label="해결 창 닫기"
            disabled={pending}
            onClick={onCancel}
            className="rounded p-1 text-slate-500 disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="space-y-4 px-5 py-5">
          <label className="block text-sm font-medium text-slate-700">
            처리 결과
            <select
              aria-label="처리 결과"
              value={resolutionType}
              disabled={pending}
              onChange={(event) =>
                setResolutionType(
                  event.target.value as '' | SecurityAlertResolutionType,
                )
              }
              className="mt-2 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
            >
              <option value="">선택해 주세요</option>
              <option value="mitigated">대응 완료</option>
              <option value="false_positive">오탐</option>
              <option value="accepted_risk">위험 수용</option>
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">
            처리 사유
            <textarea
              aria-label="처리 사유"
              value={reason}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
              rows={5}
              className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
        </div>
        <footer className="flex justify-end gap-2 border-t border-slate-200 px-5 py-4">
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 disabled:opacity-40"
          >
            취소
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={submit}
            className="h-9 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white disabled:opacity-40"
          >
            {pending ? '처리 중...' : '해결 확정'}
          </button>
        </footer>
      </div>
    </div>
  );
}
