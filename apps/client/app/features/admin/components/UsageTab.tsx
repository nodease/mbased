'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { isAxiosError } from 'axios';
import { ArrowUpRight, BarChart3, RotateCcw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { DashboardPanel } from '../../dashboard/components/DashboardSurface';
import { BudgetEditModal } from '../../budget/components/BudgetEditModal';
import { BudgetStatusBadge } from '../../budget/components/BudgetStatusBadge';
import { adminApi } from '../api/adminApi';
import type {
  AdminUsagePeriod,
  AdminWorkflowUsageItem,
} from '../types/AdminUsage';
import { AdminPagination } from './AdminPagination';

const PAGE_SIZE = 20;

// 비용은 원본 정밀도로 받아 표시 직전에만 USD 2자리로 반올림한다.
const formatCost = (value: number) => `$${value.toFixed(2)}`;

export function UsageTab() {
  const [form, setForm] = useState({ startAt: '', endAt: '' });
  const [applied, setApplied] = useState<{
    startAt?: string;
    endAt?: string;
    page: number;
  }>({ page: 1 });
  const [items, setItems] = useState<AdminWorkflowUsageItem[]>([]);
  const [total, setTotal] = useState(0);
  const [period, setPeriod] = useState<AdminUsagePeriod | null>(null);
  const [unresolvedProviderCallCount, setUnresolvedProviderCallCount] =
    useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<
    { kind: 'forbidden' | 'unknown'; message: string } | null
  >(null);
  const [budgetEditor, setBudgetEditor] = useState<{
    workflowId: string;
    workflowName: string;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listWorkflowUsage({
        startAt: applied.startAt,
        endAt: applied.endAt,
        page: applied.page,
        limit: PAGE_SIZE,
      });
      setItems(data.items);
      setTotal(data.total);
      setPeriod(data.period);
      setUnresolvedProviderCallCount(data.unresolved_provider_call_count);
    } catch (err) {
      setItems([]);
      setTotal(0);
      setPeriod(null);
      setUnresolvedProviderCallCount(0);
      if (isAxiosError(err) && err.response?.status === 403) {
        setError({
          kind: 'forbidden',
          message: '비용 조회 권한이 없습니다. 조직 관리자만 접근할 수 있습니다.',
        });
      } else {
        setError({
          kind: 'unknown',
          message: '사용량/비용 집계를 불러오지 못했습니다.',
        });
      }
    } finally {
      setLoading(false);
    }
  }, [applied]);

  useEffect(() => {
    load();
  }, [load]);

  const search = () => {
    // Gateway는 기간 한쪽만 제공하면 400으로 닫는다 — 호출 전에 안내한다.
    if (Boolean(form.startAt) !== Boolean(form.endAt)) {
      toast.error('기간은 시작과 끝을 함께 입력하거나 모두 비워야 합니다.');
      return;
    }
    setApplied({
      startAt: form.startAt || undefined,
      endAt: form.endAt || undefined,
      page: 1,
    });
  };

  const reset = () => {
    setForm({ startAt: '', endAt: '' });
    setApplied({ page: 1 });
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <DashboardPanel
      title="비용"
      icon={BarChart3}
      aside={
        period && (
          <span className="text-xs font-medium text-slate-500">
            적용 기간: {new Date(period.startAt).toLocaleString()} ~{' '}
            {new Date(period.endAt).toLocaleString()}
          </span>
        )
      }
    >
      <form
        className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <label className="flex items-center gap-2 text-xs text-slate-500">
          시작 (KST)
          <input
            type="datetime-local"
            value={form.startAt}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, startAt: event.target.value }))
            }
            aria-label="기간 시작"
            className="h-10 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          끝 (KST)
          <input
            type="datetime-local"
            value={form.endAt}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, endAt: event.target.value }))
            }
            aria-label="기간 끝"
            className="h-10 rounded-md border border-slate-300 px-2 text-sm text-slate-900"
          />
        </label>
        <button
          type="submit"
          className="flex h-10 items-center gap-1.5 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800"
        >
          <Search className="h-4 w-4" />
          조회
        </button>
        <button
          type="button"
          onClick={reset}
          className="flex h-10 items-center gap-1.5 rounded-md border border-slate-300 px-3 text-sm font-semibold text-slate-600 hover:bg-slate-50"
        >
          <RotateCcw className="h-4 w-4" />
          이번 달 기본
        </button>
      </form>

      {unresolvedProviderCallCount > 0 && (
        <p
          role="status"
          className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm font-medium text-amber-900"
        >
          비용 미확정 provider 호출 {unresolvedProviderCallCount}건이 있어 합계가
          변경될 수 있습니다.
        </p>
      )}

      {loading ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          사용량을 불러오는 중...
        </p>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
          <p className="text-sm text-slate-600">{error.message}</p>
          {error.kind === 'unknown' && (
            <button
              onClick={load}
              className="h-9 rounded-md border border-slate-300 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            >
              다시 시도
            </button>
          )}
        </div>
      ) : items.length === 0 ? (
        <p className="px-5 py-12 text-center text-sm text-slate-500">
          표시할 워크플로우가 없습니다.
        </p>
      ) : (
        <>
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              workflow별 LLM 사용량/비용 (비용 내림차순)
            </caption>
            <thead>
              <tr className="border-b border-slate-100 text-xs uppercase text-slate-500">
                <th scope="col" className="px-5 py-2 font-semibold">
                  Workflow
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">
                  호출 수
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">
                  Prompt Tokens
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">
                  Completion Tokens
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">
                  비용 (USD) ↓
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  예산
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  이동
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.workflow_id} className="border-b border-slate-50">
                  <td className="px-5 py-3 font-medium text-slate-900">
                    {item.workflow_name}
                  </td>
                  <td className="px-3 py-3 text-right text-slate-700">
                    {item.call_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right text-slate-700">
                    {item.prompt_tokens.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right text-slate-700">
                    {item.completion_tokens.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-semibold text-slate-900">
                    <p>{formatCost(item.total_cost)}</p>
                    {!item.usage_data_complete && (
                      <p className="mt-1 text-xs font-medium text-amber-700">
                        미확정 {item.unresolved_provider_call_count}건
                      </p>
                    )}
                    <div
                      className="mt-1 space-y-0.5 text-xs font-normal text-slate-500"
                      aria-label={`${item.workflow_name} 비용 구성`}
                    >
                      <p>
                        워크플로 실행 {formatCost(item.workflow_execution_cost)}
                      </p>
                      <p>
                        Agent Builder {formatCost(item.agent_builder_cost)}
                      </p>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-col items-start gap-2">
                      {item.budget ? (
                        <>
                          <span className="text-sm font-semibold text-slate-900">
                            {formatCost(item.budget.monthly_budget_usd)}
                          </span>
                          <BudgetStatusBadge
                            status={item.budget.status}
                            usageRatio={item.budget.usage_ratio}
                          />
                        </>
                      ) : (
                        <span className="text-sm font-medium text-slate-500">
                          미설정
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          setBudgetEditor({
                            workflowId: item.workflow_id,
                            workflowName: item.workflow_name,
                          })
                        }
                        className="rounded-md border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                      >
                        예산 설정
                      </button>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <Link
                      href={`/modules/${item.workflow_id}`}
                      className="flex w-fit items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                    >
                      workflow로 이동
                      <ArrowUpRight className="h-3 w-3" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <AdminPagination
            page={applied.page}
            totalPages={totalPages}
            total={total}
            onPageChange={(page) => setApplied((prev) => ({ ...prev, page }))}
          />
        </>
      )}
      {budgetEditor && (
        <BudgetEditModal
          workflowId={budgetEditor.workflowId}
          workflowName={budgetEditor.workflowName}
          onClose={() => setBudgetEditor(null)}
          onSaved={() => {
            setBudgetEditor(null);
            toast.success('예산을 저장했습니다.');
            load();
          }}
        />
      )}
    </DashboardPanel>
  );
}
