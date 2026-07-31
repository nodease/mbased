'use client';

import { useRouter } from 'next/navigation';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { isAxiosError } from 'axios';
import { toast } from 'sonner';
import { appApi } from '../../api/appApi';
import AppCreatePermissionRequestForm from '../../../organization/components/AppCreatePermissionRequestForm';
import { AppIcon } from './AppIcon';
import { AppIconPicker } from './AppIconPicker';
import { AppIconSelection, CreateAppProps } from './types';
import { twMerge } from 'tailwind-merge';

type ApiErrorResponse = {
  detail?: string;
  error?: {
    code?: string;
    message?: string;
  };
};

const getApiErrorDetail = (error: unknown) =>
  isAxiosError<ApiErrorResponse>(error)
    ? error.response?.data?.detail
    : undefined;

const isAppCreationPermissionDenied = (error: unknown) => {
  if (!isAxiosError<ApiErrorResponse>(error)) return false;
  if (error.response?.status !== 403) return false;

  const code = error.response.data?.error?.code;
  return !code || code === 'permission.denied';
};

/**
 * 앱 생성 모달 컴포넌트
 *
 * 사용자가 새로운 앱을 생성할 때 사용하는 팝업창입니다.
 * 앱 이름, 설명, 아이콘을 입력받아 생성을 요청합니다.
 */
export default function CreateAppModal({ onSuccess, onClose }: CreateAppProps) {
  const router = useRouter();
  // --- 상태 관리 (State) ---

  // 입력 필드 상태
  const [name, setName] = useState(''); // 앱 이름
  const [description, setDescription] = useState(''); // 앱 설명

  // 앱 아이콘 상태 (이모지 및 배경색)
  const [appIcon, setAppIcon] = useState<AppIconSelection>({
    emoji: '💻',
    bg: '#FFEAD5',
  });

  // 아이콘 선택 팝업 표시 여부
  const [showAppIconPicker, setShowAppIconPicker] = useState(false);

  // 로딩 상태 (API 요청 중일 때 true)
  const [loading, setLoading] = useState(false);

  // 모달 화면 상태: App 생성 입력 또는 App 생성 권한 신청 (ORG-REQ-048)
  const [view, setView] = useState<'create' | 'permission-request'>('create');

  // 중복 생성 방지를 위한 Ref
  const isCreatingRef = useRef(false);

  // --- 생성 핸들러 (Submit Handler) ---
  const handleCreate = useCallback(async () => {
    // 이미 생성 요청 중이면 중복 실행 방지
    if (isCreatingRef.current) return;

    // 유효성 검사
    if (!name.trim()) {
      toast.error('앱 이름을 입력해주세요.');
      return;
    }

    // 생성 시작 상태 설정
    isCreatingRef.current = true;
    setLoading(true);

    try {
      // API 호출
      const response = await appApi.createApp({
        name: name.trim(),
        description: description.trim(),
        icon: {
          type: 'emoji',
          content: appIcon.emoji,
          background_color: appIcon.bg,
        },
        is_market: false, // 기본값 false (마켓플레이스 미구현)
      });

      // 성공 처리
      toast.success('앱이 성공적으로 생성되었습니다.');

      onSuccess(); // 부모 컴포넌트에 성공 알림 (목록 새로고침 등)
      onClose(); // 모달 닫기

      // 워크플로우 에디터로 리다이렉트
      if (response.workflow_id) {
        router.push(`/modules/${response.workflow_id}`);
      }
    } catch (error: unknown) {
      if (
        isAxiosError<ApiErrorResponse>(error) &&
        error.response?.status === 400 &&
        getApiErrorDetail(error) === 'App with this name already exists.'
      ) {
        toast.error('이미 존재하는 앱 이름입니다.');
        return;
      }
      if (isAppCreationPermissionDenied(error)) {
        setView('permission-request');
        return;
      }
      toast.error('앱 생성에 실패했습니다.');
    } finally {
      // 상태 초기화
      isCreatingRef.current = false;
      setLoading(false);
    }
  }, [name, description, appIcon, onSuccess, onClose, router]);

  // --- 키보드 단축키 (Keyboard Shortcuts) ---
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+Enter 또는 Ctrl+Enter로 폼 제출 (빠른 생성, 생성 화면에서만)
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && view === 'create') {
        e.preventDefault();
        handleCreate();
      }

      // Escape(ESC) 키로 모달 닫기
      if (e.key === 'Escape') {
        // 아이콘 선택 창이 열려있으면 그것만 닫기
        if (showAppIconPicker) {
          setShowAppIconPicker(false);
          e.stopPropagation(); // 이벤트 전파 중단
        } else {
          // 아니라면 모달 전체 닫기
          onClose();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleCreate, onClose, showAppIconPicker, view]);

  // --- 모달 외부 클릭 처리 (Backdrop Click) ---
  const handleBackdropClick = (e: React.MouseEvent) => {
    // e.target: 사용자가 실제로 클릭한 요소 (예: 배경, 모달 내부 글자, 버튼 등)
    // e.currentTarget: 이벤트 핸들러(onClick)가 부착된 요소 (여기서는 배경 div)

    // 클릭된 요소가 배경(dimmed layer) 자체일 때만 닫기
    // (모달 내부를 클릭했을 때는 e.target이 모달 내부 요소이므로 이 조건이 거짓이 되어 닫히지 않음)
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    // 배경 (Backdrop)
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm animate-in fade-in duration-200"
      onClick={handleBackdropClick}
    >
      {/* 모달 본문 */}
      <div
        role="dialog"
        aria-modal="true"
        className="w-[400px] bg-white dark:bg-zinc-900 rounded-xl shadow-2xl transform transition-all animate-in zoom-in-95 duration-200 border border-zinc-200 dark:border-zinc-800 relative"
      >
        <div className="p-6">
          {/* 헤더: 제목 및 닫기 버튼 */}
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-bold text-gray-900 dark:text-zinc-50">
              {view === 'permission-request' ? '앱 생성 권한 신청' : '앱 생성'}
            </h2>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          {view === 'permission-request' ? (
            <AppCreatePermissionRequestForm
              onCancel={() => setView('create')}
              onClose={onClose}
            />
          ) : (
            <>
              {/* 입력 폼 영역 */}
              <div className="space-y-5">
                {/* 앱 이름 및 아이콘 */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-2">
                    앱 이름 <span className="text-red-500">*</span>
                  </label>
                  <div className="flex gap-3">
                    {/* 아이콘 선택기 트리거 */}
                    <div className="relative group">
                      <AppIcon
                        icon={appIcon}
                        onClick={() => setShowAppIconPicker(!showAppIconPicker)}
                        className="shadow-sm border border-zinc-200 dark:border-zinc-700 w-10 h-10 group-hover:ring-2 ring-blue-100 transition-all"
                      />
                      {/* Edit Overlay Hint */}
                      <div
                        onClick={() => setShowAppIconPicker(!showAppIconPicker)}
                        className="absolute inset-0 flex items-center justify-center bg-black/40 text-white opacity-0 group-hover:opacity-100 rounded-lg cursor-pointer transition-opacity z-10"
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                        </svg>
                      </div>

                      {/* 아이콘 선택기 (Popover) */}
                      {showAppIconPicker && (
                        <AppIconPicker
                          currentIcon={appIcon}
                          onSelect={(newIcon) => {
                            setAppIcon(newIcon);
                          }}
                          onClose={() => setShowAppIconPicker(false)}
                        />
                      )}
                    </div>
                    {/* 이름 입력 필드 */}
                    <input
                      autoFocus
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="앱 이름을 입력하세요"
                      className={twMerge(
                        'flex-1 h-10 px-3 rounded-lg border bg-transparent outline-none transition-all text-sm',
                        'border-zinc-200 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/10',
                        'dark:border-zinc-700 dark:text-zinc-100 dark:placeholder-zinc-500',
                      )}
                    />
                  </div>
                </div>
                {/* 앱 설명 입력 */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-2">
                    앱 설명
                  </label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="앱에 대한 설명을 입력하세요"
                    className={twMerge(
                      'w-full h-28 px-3 py-2 rounded-lg border bg-transparent outline-none transition-all text-sm resize-none',
                      'border-zinc-200 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/10',
                      'dark:border-zinc-700 dark:text-zinc-100 dark:placeholder-zinc-500',
                    )}
                  />
                </div>
              </div>

              {/* 하단 버튼 영역 (취소 / 생성) */}
              <div className="flex items-center justify-end gap-3 mt-8">
                <button
                  onClick={onClose}
                  className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 dark:text-zinc-400 dark:hover:bg-white/5 rounded-lg transition-colors"
                >
                  취소
                </button>
                <button
                  onClick={handleCreate}
                  disabled={loading}
                  className={twMerge(
                    'px-5 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 rounded-lg shadow-sm transition-all flex items-center gap-2',
                    loading && 'opacity-70 cursor-not-allowed',
                  )}
                >
                  {loading ? (
                    <>
                      <svg
                        className="animate-spin -ml-1 mr-2 h-4 w-4 text-white"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        ></circle>
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                        ></path>
                      </svg>
                      생성 중...
                    </>
                  ) : (
                    '생성'
                  )}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
