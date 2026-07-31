'use client';

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

import type {
  DeploymentParameterOptimizationConfig,
  DeploymentParameterOptimizationSummary,
} from '../../types/Deployment';

interface AutomaticOptimizationManagementModalProps {
  workflowName: string;
  summary: DeploymentParameterOptimizationSummary;
  onClose: () => void;
  onSave: (config: DeploymentParameterOptimizationConfig) => Promise<void>;
}

const formatBudget = (value: number) => `$${value.toFixed(2)}`;

const statusLabel = {
  disabled: '미사용',
  collecting: '수집 중',
  ready: '점검 대기',
  paused: '일시 중지',
  budget_exhausted: '월 예산 도달',
  failed: '점검 실패',
} as const;

export function AutomaticOptimizationManagementModal({
  workflowName,
  summary,
  onClose,
  onSave,
}: AutomaticOptimizationManagementModalProps) {
  const [enabled, setEnabled] = useState(summary.enabled);
  const [checkEveryRuns, setCheckEveryRuns] = useState(
    summary.check_every_runs,
  );
  const [monthlyBudget, setMonthlyBudget] = useState(
    summary.monthly_validation_budget_usd,
  );
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setEnabled(summary.enabled);
    setCheckEveryRuns(summary.check_every_runs);
    setMonthlyBudget(summary.monthly_validation_budget_usd);
  }, [summary]);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave({
        enabled,
        node_ids: summary.node_ids,
        check_every_runs: checkEveryRuns,
        monthly_validation_budget_usd: monthlyBudget,
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="자동 최적화 관리"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <p className="text-xs font-semibold text-emerald-700">
              배포별 설정
            </p>
            <h2 className="mt-1 text-xl font-semibold text-slate-950">
              자동 최적화 관리
            </h2>
            <p className="mt-1 text-sm text-slate-600">{workflowName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="grid h-9 w-9 place-items-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5 px-6 py-5">
          <label className="flex items-start justify-between gap-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
            <span>
              <span className="block text-sm font-semibold text-slate-900">
                운영 비용 자동 최적화 사용
              </span>
              <span className="mt-1 block text-xs leading-5 text-slate-600">
                응답 길이와 RAG 컨텍스트 설정만 검증합니다. 모델 라우팅과
                프롬프트는 변경하지 않습니다.
              </span>
            </span>
            <input
              aria-label="운영 비용 자동 최적화 사용"
              type="checkbox"
              role="switch"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
              className="mt-1 h-4 w-4 accent-emerald-600"
            />
          </label>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-md border border-slate-200 px-3 py-2.5">
              <p className="text-xs font-medium text-slate-500">대상 LLM 노드</p>
              <p className="mt-1 font-semibold text-slate-900">
                {summary.node_ids.length || summary.node_count}개
              </p>
            </div>
            <div className="rounded-md border border-slate-200 px-3 py-2.5">
              <p className="text-xs font-medium text-slate-500">운영 로그</p>
              <p className="mt-1 font-semibold text-slate-900">
                {summary.collected_runs} / {summary.check_every_runs}회
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {statusLabel[summary.status]}
              </p>
            </div>
          </div>

          <section aria-labelledby="automatic-optimization-check-interval">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3
                  id="automatic-optimization-check-interval"
                  className="text-sm font-semibold text-slate-900"
                >
                  자동 점검 주기
                </h3>
                <p className="mt-1 text-xs text-slate-600">
                  성공한 배포 후 운영 실행을 이 횟수만큼 모은 뒤 다음
                  검증 대상으로 만듭니다.
                </p>
              </div>
              <output className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-sm font-semibold text-emerald-800">
                {checkEveryRuns}회
              </output>
            </div>
            <input
              aria-label="자동 점검 주기"
              type="range"
              min="20"
              max="200"
              step="10"
              value={checkEveryRuns}
              onChange={(event) => setCheckEveryRuns(Number(event.target.value))}
              className="mt-3 w-full accent-emerald-600"
            />
            <div className="flex justify-between text-[11px] text-slate-500">
              <span>자주 점검</span>
              <span>권장: 50회</span>
              <span>보수적 점검</span>
            </div>
          </section>

          <section aria-labelledby="automatic-optimization-budget">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3
                  id="automatic-optimization-budget"
                  className="text-sm font-semibold text-slate-900"
                >
                  월간 검증 예산
                </h3>
                <p className="mt-1 text-xs text-slate-600">
                  후보 설정을 실제로 다시 실행할 때만 이 예산을 사용합니다.
                </p>
                <p className="mt-1 text-xs font-medium text-slate-500">
                  이번 달 사용 {formatBudget(summary.validation_spend_usd)}
                </p>
              </div>
              <output className="rounded border border-violet-200 bg-violet-50 px-2 py-1 text-sm font-semibold text-violet-800">
                {formatBudget(monthlyBudget)}
              </output>
            </div>
            <input
              aria-label="월간 검증 예산"
              type="range"
              min="0.5"
              max="10"
              step="0.5"
              value={monthlyBudget}
              onChange={(event) => setMonthlyBudget(Number(event.target.value))}
              className="mt-3 w-full accent-violet-600"
            />
            <div className="flex justify-between text-[11px] text-slate-500">
              <span>최소 $0.5</span>
              <span>권장: $3</span>
              <span>최대 $10</span>
            </div>
          </section>
        </div>

        <div className="flex justify-end gap-3 border-t border-slate-200 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            취소
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-emerald-400"
          >
            {isSaving ? '저장 중...' : '저장'}
          </button>
        </div>
      </div>
    </div>
  );
}
