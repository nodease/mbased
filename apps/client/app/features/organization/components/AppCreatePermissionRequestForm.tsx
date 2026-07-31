'use client';

import { useState } from 'react';
import { twMerge } from 'tailwind-merge';

import {
  permissionRequestApi,
  resolvePermissionRequestConflict,
} from '../api/permissionRequestApi';

interface AppCreatePermissionRequestFormProps {
  /** 신청을 중단하고 App 생성 입력 화면으로 돌아간다. */
  onCancel: () => void;
  /** 모달을 닫는다. */
  onClose: () => void;
}

type SubmitState = 'idle' | 'submitting' | 'submitted';

/**
 * App 생성 권한(app.create) 신청 폼 (ORG-REQ-048, ORG-REQ-049)
 *
 * App 생성이 403 permission.denied로 차단된 사용자가 신청 사유를 입력해
 * POST /permission-requests로 권한을 신청합니다. 신청 권한은 app.create로
 * 고정되며 사용자가 다른 권한을 선택할 수 없습니다.
 */
export default function AppCreatePermissionRequestForm({
  onCancel,
  onClose,
}: AppCreatePermissionRequestFormProps) {
  const [reason, setReason] = useState('');
  const [submitState, setSubmitState] = useState<SubmitState>('idle');
  // 409 응답(이미 권한 보유 / pending 중복)에 맞는 안내 (ORG-REQ-050)
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (submitState === 'submitting') return;

    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setError('신청 사유를 입력해주세요.');
      return;
    }

    setError(null);
    setNotice(null);
    setSubmitState('submitting');

    try {
      await permissionRequestApi.submitPermissionRequest({
        reason: trimmedReason,
      });
      setSubmitState('submitted');
    } catch (err) {
      const conflict = resolvePermissionRequestConflict(err);
      if (conflict === 'already-granted') {
        setNotice(
          '이미 App 생성 권한이 있습니다. 앱 생성을 다시 시도해주세요.',
        );
      } else if (conflict === 'already-pending') {
        setNotice(
          '이미 처리 대기 중인 권한 신청이 있습니다. 관리자 승인을 기다려주세요.',
        );
      } else {
        setError('권한 신청에 실패했습니다. 잠시 후 다시 시도해주세요.');
      }
      setSubmitState('idle');
    }
  };

  if (submitState === 'submitted') {
    return (
      <div className="space-y-5">
        <div className="rounded-lg bg-green-50 dark:bg-green-500/10 border border-green-200 dark:border-green-500/20 px-4 py-3 text-sm text-green-800 dark:text-green-300">
          권한 신청이 접수되었습니다. 관리자 승인 후 다시 앱을 생성할 수
          있습니다.
        </div>
        <div className="flex items-center justify-end">
          <button
            onClick={onClose}
            className="px-5 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 rounded-lg shadow-sm transition-all"
          >
            닫기
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
        App 생성 권한이 없습니다. 관리자에게 앱 생성 권한을 신청할 수 있습니다.
      </div>

      <div>
        <label
          htmlFor="app-create-permission-request-reason"
          className="block text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-2"
        >
          신청 사유 <span className="text-red-500">*</span>
        </label>
        <textarea
          id="app-create-permission-request-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="앱 생성 권한이 필요한 이유를 입력하세요"
          className={twMerge(
            'w-full h-28 px-3 py-2 rounded-lg border bg-transparent outline-none transition-all text-sm resize-none',
            'border-zinc-200 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/10',
            'dark:border-zinc-700 dark:text-zinc-100 dark:placeholder-zinc-500',
          )}
        />
      </div>

      {notice && (
        <p className="text-sm text-amber-700 dark:text-amber-300">{notice}</p>
      )}
      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      <div className="flex items-center justify-end gap-3">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 dark:text-zinc-400 dark:hover:bg-white/5 rounded-lg transition-colors"
        >
          취소
        </button>
        <button
          onClick={handleSubmit}
          disabled={submitState === 'submitting'}
          className={twMerge(
            'px-5 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 rounded-lg shadow-sm transition-all',
            submitState === 'submitting' && 'opacity-70 cursor-not-allowed',
          )}
        >
          {submitState === 'submitting' ? '신청 중...' : '권한 신청'}
        </button>
      </div>
    </div>
  );
}
