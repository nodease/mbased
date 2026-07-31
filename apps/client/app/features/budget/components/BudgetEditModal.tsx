import { type FormEvent, useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { budgetApi } from '../api/budgetApi';

const MAX_MONTHLY_BUDGET_USD_LABEL = '9999999999.99';
const MAX_MONTHLY_BUDGET_USD = Number(MAX_MONTHLY_BUDGET_USD_LABEL);

type BudgetEditModalProps = {
  workflowId: string;
  workflowName: string;
  onClose: () => void;
  onSaved: () => void;
};

export function BudgetEditModal({
  workflowId,
  workflowName,
  onClose,
  onSaved,
}: BudgetEditModalProps) {
  const [monthlyBudget, setMonthlyBudget] = useState('');
  const [isEnabled, setIsEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;

    budgetApi
      .getWorkflowBudget(workflowId)
      .then((budget) => {
        if (cancelled) return;
        setMonthlyBudget(String(budget.monthly_budget_usd));
        setIsEnabled(budget.is_enabled);
      })
      .catch((err) => {
        if (cancelled) return;
        if (isAxiosError(err) && err.response?.status === 404) {
          setMonthlyBudget('');
          setIsEnabled(true);
          return;
        }
        setError('예산을 불러오지 못했습니다');
      });

    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    const amount = Number(monthlyBudget);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError('0보다 큰 금액을 입력해주세요');
      return;
    }
    if (amount > MAX_MONTHLY_BUDGET_USD) {
      setError(
        `월 예산은 ${MAX_MONTHLY_BUDGET_USD_LABEL} USD 이하로 입력해주세요`,
      );
      return;
    }
    const decimalPlaces = monthlyBudget.trim().split('.')[1]?.length ?? 0;
    if (decimalPlaces > 2) {
      setError('소수점은 최대 2자리까지 입력해주세요');
      return;
    }

    setSaving(true);
    try {
      await budgetApi.upsertWorkflowBudget(workflowId, {
        monthly_budget_usd: amount,
        is_enabled: isEnabled,
      });
      onSaved();
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 403) {
        setError('예산을 관리할 권한이 없습니다');
      } else if (isAxiosError(err) && err.response?.status === 422) {
        setError('예산 입력값을 확인해주세요');
      } else {
        setError('예산 저장에 실패했습니다');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 px-4">
      <form
        role="dialog"
        aria-modal="true"
        aria-label={`${workflowName} 예산 설정`}
        noValidate
        onSubmit={submit}
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-950">
              예산 설정
            </h2>
            <p className="mt-1 text-sm text-slate-500">{workflowName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-200 px-2 py-1 text-sm font-semibold text-slate-600 hover:bg-slate-50"
          >
            닫기
          </button>
        </div>

        <div className="mt-5 space-y-4">
          <label className="block text-sm font-medium text-slate-700">
            월 예산(USD)
            <input
              type="number"
              min="0"
              max={MAX_MONTHLY_BUDGET_USD_LABEL}
              step="0.01"
              value={monthlyBudget}
              onChange={(event) => setMonthlyBudget(event.target.value)}
              className="mt-2 h-10 w-full rounded-md border border-slate-300 px-3 text-sm text-slate-900"
            />
          </label>

          <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
            <input
              type="checkbox"
              checked={isEnabled}
              onChange={(event) => setIsEnabled(event.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
            활성화
          </label>

          {error && (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700">
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="h-10 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={saving}
            className="h-10 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            저장
          </button>
        </div>
      </form>
    </div>
  );
}
