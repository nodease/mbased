'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import {
  knowledgeApi,
  type KnowledgeCollectionResponse,
  type KnowledgeCollectionSyncJobResponse,
} from '@/app/features/knowledge/api/knowledgeApi';

const ACTIVE_STATUSES = new Set(['queued', 'running']);

const STATUS_LABEL: Record<
  KnowledgeCollectionSyncJobResponse['status'],
  string
> = {
  queued: '대기 중',
  running: '동기화 중',
  succeeded: '완료',
  partially_failed: '일부 실패',
  failed: '실패',
  cancelled: '취소됨',
};

const PROGRESS_LABEL: Record<
  KnowledgeCollectionSyncJobResponse['progress'],
  string
> = {
  none: '시작 전',
  started: '시작됨',
  progressing: '진행 중',
  most: '마무리 중',
  complete: '처리 종료',
};

const REASON_LABEL: Record<string, string> = {
  'sync.configuration_invalid': '동기화 설정을 확인해 주세요.',
  'sync.internal_error': '내부 처리 오류가 발생했습니다.',
  'sync.no_eligible_targets': '동기화할 수 있는 DB 문서가 없습니다.',
  'sync.not_supported': '현재 지원하지 않는 Collection 유형입니다.',
  'sync.permission_revoked': '실행 권한이 회수되어 취소되었습니다.',
  'sync.target_limit_exceeded': '한 번에 처리할 수 있는 대상을 초과했습니다.',
  'sync.targets_changed': '요청 후 대상 구성이 변경되었습니다.',
  'sync.temporarily_unavailable': '외부 서비스가 일시적으로 응답하지 않습니다.',
  'sync.timeout': '제한 시간 안에 작업을 완료하지 못했습니다.',
  'sync.worker_interrupted': '작업을 복구하고 있습니다.',
};

type KnowledgeCollectionSyncPanelProps = {
  collection: KnowledgeCollectionResponse | null;
  canManageSync: boolean;
};

export function KnowledgeCollectionSyncPanel({
  collection,
  canManageSync,
}: KnowledgeCollectionSyncPanelProps) {
  const [job, setJob] = useState<KnowledgeCollectionSyncJobResponse | null>(
    null,
  );
  const [isRequesting, setIsRequesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const scopeVersion = useRef(0);
  const canInvoke = Boolean(
    collection && (collection.can_sync || canManageSync),
  );
  const isActive = Boolean(job && ACTIVE_STATUSES.has(job.status));

  useEffect(() => {
    const version = ++scopeVersion.current;
    requestInFlight.current = false;
    setJob(null);
    setIsRequesting(false);
    setMessage(null);
    if (!collection || !canInvoke) return;

    void knowledgeApi
      .getLatestKnowledgeCollectionSyncJob(collection.id)
      .then((response) => {
        if (scopeVersion.current === version) setJob(response.job ?? null);
      })
      .catch(() => {
        if (scopeVersion.current === version) {
          setMessage('최근 동기화 상태를 불러오지 못했습니다.');
        }
      });
  }, [canInvoke, collection]);

  useEffect(() => {
    if (!collection || !job || !isActive || !canInvoke) return;
    const version = scopeVersion.current;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const next = await knowledgeApi.getKnowledgeCollectionSyncJob(
          collection.id,
          job.job_id,
        );
        if (cancelled || scopeVersion.current !== version) return;
        setJob(next);
        setMessage(null);
        if (ACTIVE_STATUSES.has(next.status)) {
          timer = setTimeout(poll, 3000);
        }
      } catch {
        if (!cancelled && scopeVersion.current === version) {
          setMessage('동기화 상태 확인이 지연되고 있습니다.');
          timer = setTimeout(poll, 3000);
        }
      }
    };

    timer = setTimeout(poll, 3000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [canInvoke, collection, isActive, job]);

  const reasonText = useMemo(() => {
    if (!job?.safe_reason_code) return null;
    return REASON_LABEL[job.safe_reason_code] ?? '동기화 작업을 확인해 주세요.';
  }, [job?.safe_reason_code]);

  if (!collection || !canInvoke) return null;

  const requestSync = async () => {
    if (
      requestInFlight.current ||
      isActive ||
      !collection.sync_supported ||
      collection.lifecycle_state !== 'active'
    ) {
      return;
    }
    requestInFlight.current = true;
    setIsRequesting(true);
    setMessage(null);
    const version = scopeVersion.current;
    try {
      const response = await knowledgeApi.requestKnowledgeCollectionSync(
        collection.id,
        crypto.randomUUID(),
      );
      if (scopeVersion.current === version) {
        setJob(response.job);
        if (response.dispatch_deferred) {
          setMessage(
            '요청이 저장되었으며 실행 대기열 복구를 기다리고 있습니다.',
          );
        }
      }
    } catch {
      if (scopeVersion.current === version) {
        setMessage(
          '동기화 요청을 처리하지 못했습니다. 설정과 권한을 확인해 주세요.',
        );
      }
    } finally {
      requestInFlight.current = false;
      if (scopeVersion.current === version) setIsRequesting(false);
    }
  };

  const disabled =
    isRequesting ||
    isActive ||
    !collection.sync_supported ||
    collection.lifecycle_state !== 'active';

  return (
    <section className="border-b border-slate-200 bg-slate-50/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold text-slate-900">
            Collection 동기화
          </h3>
          <p className="mt-1 text-xs text-slate-600">
            연결된 DB 문서를 백그라운드에서 다시 수집하고 검색 인덱스를
            갱신합니다.
          </p>
        </div>
        <button
          type="button"
          onClick={requestSync}
          disabled={disabled}
          className="inline-flex items-center gap-2 rounded-md bg-blue-700 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isRequesting || isActive ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {isActive ? '동기화 진행 중' : '지금 동기화'}
        </button>
      </div>

      {!collection.sync_supported && (
        <p className="mt-3 text-xs font-medium text-amber-700">
          현재는 직접 관리하는 Collection의 DB 문서 동기화만 지원합니다.
        </p>
      )}
      {job && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-700">
          <span>상태: {STATUS_LABEL[job.status]}</span>
          <span>진행: {PROGRESS_LABEL[job.progress]}</span>
          {job.status === 'failed' && job.retryable && <span>재시도 가능</span>}
        </div>
      )}
      {reasonText && (
        <p className="mt-2 text-xs text-amber-700">{reasonText}</p>
      )}
      {message && <p className="mt-2 text-xs text-slate-600">{message}</p>}
    </section>
  );
}
