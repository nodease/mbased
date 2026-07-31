'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { HelpCircle } from 'lucide-react';
import { isMockWorkflowPath } from '@/app/features/workflow/utils/mockMode';
import {
  activeOrganizationHeaders,
  getStoredActiveOrganizationId,
} from '@/lib/activeOrganization';

type MemoryModeModalsProps = {
  showMemoryConfirm: boolean;
  showKeyPrompt: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onGoToKey: () => void;
  onCloseKey: () => void;
};

function MemoryModeModals({
  showMemoryConfirm,
  showKeyPrompt,
  onConfirm,
  onCancel,
  onGoToKey,
  onCloseKey,
}: MemoryModeModalsProps) {
  return (
    <>
      {showMemoryConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="기억모드 비용 확인"
          data-canvas-shortcut-scope="blocked"
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/40 px-4"
        >
          <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 max-w-md w-full p-6 space-y-4">
            <div className="flex items-center gap-2">
              <div className="h-10 w-10 rounded-full bg-blue-50 text-blue-600 flex items-center justify-center text-xl">
                🧠
              </div>
              <div>
                <p className="text-base font-semibold text-gray-900 leading-relaxed">
                  추가 LLM 호출이 발생해 비용이 증가할 수 있습니다.
                  <br />
                  동의하시면 계속 진행합니다.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span className="text-amber-600">⚠️</span>
              <span>
                기억 기능을 켜면 최근 실행을 요약해 다음 실행 흐름을 이어줍니다.
              </span>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={onConfirm}
                className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                사용하겠습니다
              </button>
              <button
                onClick={onCancel}
                className="px-4 py-2 rounded-lg bg-gray-100 text-gray-700 text-sm font-medium hover:bg-gray-200 transition-colors"
              >
                취소
              </button>
            </div>
          </div>
        </div>
      )}

      {showKeyPrompt && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="LLM Provider 키 등록 안내"
          data-canvas-shortcut-scope="blocked"
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/40 px-4"
        >
          <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 max-w-md w-full p-6 space-y-4">
            <div className="flex items-center gap-2">
              <div className="h-10 w-10 rounded-full bg-amber-50 text-amber-600 flex items-center justify-center text-xl">
                🔑
              </div>
              <div>
                <p className="text-base font-semibold text-gray-900 leading-relaxed">
                  LLM Provider 키를 등록해야 기억모드를 켤 수 있습니다.
                </p>
                <p className="text-sm text-gray-600 mt-1">
                  설정에서 키를 등록하면 비용 동의 후 기억모드를 사용할 수
                  있습니다.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={onGoToKey}
                className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                키 등록하기
              </button>
              <button
                onClick={onCloseKey}
                className="px-4 py-2 rounded-lg bg-gray-100 text-gray-700 text-sm font-medium hover:bg-gray-200 transition-colors"
              >
                나중에 할게요
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

type MemoryModeToggleProps = {
  isEnabled: boolean;
  hasProviderKey: boolean | null;
  providerKeyStatus?: ProviderKeyStatus;
  description: string;
  onToggle: () => void;
};

export type ProviderKeyStatus =
  | 'checking'
  | 'available'
  | 'missing'
  | 'unavailable';

export function MemoryModeToggle({
  isEnabled,
  hasProviderKey,
  providerKeyStatus,
  description,
  onToggle,
}: MemoryModeToggleProps) {
  return (
    <>
      <div className="flex items-center gap-1 mr-2">
        <span className="text-xs font-semibold text-gray-700 hidden lg:inline">
          기억모드
        </span>
        <div className="group relative inline-block">
          <HelpCircle className="w-4 h-4 text-gray-400 cursor-help" />
          <div className="absolute z-50 hidden group-hover:block w-60 p-2 text-[11px] leading-relaxed text-gray-600 bg-white border border-gray-200 rounded-lg shadow-lg left-0 top-5">
            {description}
            <div className="absolute -top-1 left-3 w-2 h-2 bg-white border-l border-t border-gray-200 rotate-45"></div>
          </div>
        </div>
        {hasProviderKey === false && (
          <span className="text-[10px] text-amber-600 bg-amber-50 border border-amber-100 px-1.5 py-0.5 rounded-full font-medium">
            키 필요
          </span>
        )}
        {providerKeyStatus === 'unavailable' && (
          <span className="rounded-full border border-red-100 bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
            확인 실패
          </span>
        )}
      </div>
      <button
        onClick={onToggle}
        className={`relative w-9 h-5 rounded-full transition-colors duration-200 ${
          isEnabled ? 'bg-blue-600' : 'bg-gray-200'
        } ${hasProviderKey === false ? 'opacity-60 cursor-not-allowed' : ''}`}
        aria-pressed={isEnabled}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
            isEnabled ? 'translate-x-4' : ''
          }`}
        />
      </button>
    </>
  );
}

export function useMemoryMode(
  routerOverride?: ReturnType<typeof useRouter>,
  toaster = toast,
) {
  const defaultRouter = useRouter();
  const router = routerOverride ?? defaultRouter;
  const pathname = usePathname();
  const [isMemoryModeEnabled, setIsMemoryModeEnabled] = useState(false);
  const [showMemoryConfirm, setShowMemoryConfirm] = useState(false);
  const [showKeyPrompt, setShowKeyPrompt] = useState(false);
  const [providerKeyStatus, setProviderKeyStatus] =
    useState<ProviderKeyStatus>('checking');
  const hasProviderKey =
    providerKeyStatus === 'available'
      ? true
      : providerKeyStatus === 'missing'
        ? false
        : null;

  // 기억 모드 설명 (툴팁)
  const memoryModeDescription =
    '최근 실행 기록을 요약해 다음 실행에 컨텍스트로 반영합니다. 추가 LLM 호출로 비용이 늘 수 있으니 켜기 전에 확인해주세요.';

  // 키 상태 조회 (최소 침습)
  useEffect(() => {
    const controller = new AbortController();

    const fetchKeyStatus = async () => {
      if (isMockWorkflowPath(pathname)) {
        setProviderKeyStatus('available');
        return;
      }

      setProviderKeyStatus('checking');
      try {
        const res = await fetch('/api/v1/llm/credentials', {
          credentials: 'include',
          headers: activeOrganizationHeaders(getStoredActiveOrganizationId()),
          signal: controller.signal,
        });
        if (!res.ok) {
          setProviderKeyStatus('unavailable');
          return;
        }
        const data = await res.json();
        setProviderKeyStatus(
          Array.isArray(data) && data.length > 0 ? 'available' : 'missing',
        );
      } catch {
        if (!controller.signal.aborted) {
          setProviderKeyStatus('unavailable');
        }
      }
    };

    void fetchKeyStatus();
    return () => controller.abort();
  }, [pathname]);

  // 키 해제 시 자동 OFF
  useEffect(() => {
    if (hasProviderKey === false && isMemoryModeEnabled) {
      setIsMemoryModeEnabled(false);
      setShowMemoryConfirm(false);
      toaster.info('프로바이더 키가 없어 기억모드를 끕니다.', {
        duration: 2000,
      });
    }
  }, [hasProviderKey, isMemoryModeEnabled, toaster]);

  const toggleMemoryMode = useCallback(() => {
    if (providerKeyStatus === 'unavailable') {
      toaster.error(
        '프로바이더 키 상태를 확인하지 못했습니다. 로그인 상태와 서버 연결을 확인해 주세요.',
        { duration: 3000 },
      );
      return;
    }
    if (hasProviderKey === false) {
      setShowKeyPrompt(true);
      return;
    }
    if (hasProviderKey === null) return; // still loading

    setShowMemoryConfirm((prev) => {
      if (!isMemoryModeEnabled) {
        return true;
      }
      setIsMemoryModeEnabled(false);
      return prev;
    });
  }, [hasProviderKey, isMemoryModeEnabled, providerKeyStatus, toaster]);

  const handleConfirmMemoryMode = useCallback(() => {
    setIsMemoryModeEnabled(true);
    setShowMemoryConfirm(false);
  }, []);

  const handleCancelMemoryMode = useCallback(() => {
    setIsMemoryModeEnabled(false);
    setShowMemoryConfirm(false);
  }, []);

  const openProviderSettings = useCallback(() => {
    if (typeof window !== 'undefined') {
      window.open('/dashboard/settings', '_blank', 'noopener,noreferrer');
      return;
    }
    router.push('/dashboard/settings');
  }, [router]);

  const handleGoToProviderSettings = useCallback(() => {
    setShowKeyPrompt(false);
    openProviderSettings();
  }, [openProviderSettings]);

  const appendMemoryFlag = useCallback(
    (inputs: Record<string, any> | FormData) => {
      // 프론트 실행 payload에 기억모드 플래그를 추가. (FormData/JSON 모두 지원)
      if (inputs instanceof FormData) {
        const formCopy = new FormData();
        inputs.forEach((value, key) => {
          if (value instanceof File) {
            formCopy.append(key, value);
          } else {
            formCopy.append(key, value as string);
          }
        });
        formCopy.append('memory_mode', String(isMemoryModeEnabled));
        return formCopy;
      }
      return {
        ...(inputs as Record<string, any>),
        memory_mode: isMemoryModeEnabled,
      };
    },
    [isMemoryModeEnabled],
  );

  const modals = useMemo(
    () => (
      <MemoryModeModals
        showMemoryConfirm={showMemoryConfirm}
        showKeyPrompt={showKeyPrompt}
        onConfirm={handleConfirmMemoryMode}
        onCancel={handleCancelMemoryMode}
        onGoToKey={handleGoToProviderSettings}
        onCloseKey={() => setShowKeyPrompt(false)}
      />
    ),
    [
      showMemoryConfirm,
      showKeyPrompt,
      handleConfirmMemoryMode,
      handleCancelMemoryMode,
      handleGoToProviderSettings,
    ],
  );

  return {
    isMemoryModeEnabled,
    hasProviderKey,
    providerKeyStatus,
    memoryModeDescription,
    toggleMemoryMode,
    appendMemoryFlag,
    modals,
  };
}
