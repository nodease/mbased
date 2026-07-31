'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowLeft,
  Clock3,
  GitCommitHorizontal,
} from 'lucide-react';
import { workflowApi } from '../../api/workflowApi';
import type {
  CostOptimizerBaselineListParams,
  CostOptimizerBaselineRow,
} from '../../types/Api';
import { CostOptimizerPreviewViewer } from './CostOptimizerPreviewViewer';
import { fieldLabelsFromOutputFormat } from './costOptimizerPreviewLabels';

interface CostOptimizerBaselineSelectionProps {
  workflowId: string;
  nodeId: string;
  onBaselineSelected: (baseline: CostOptimizerBaselineRow) => void;
  onClose: () => void;
}

const DEFAULT_LIMIT = 20;

const unavailableMessage =
  '입력 기록이 보관 기간 만료 또는 보안 정책으로 인해 이 실행 로그로는 A/B 테스트를 시작할 수 없습니다.';

const formatCost = (cost: number) => {
  if (!Number.isFinite(cost)) return '-';
  return `$${cost.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;
};

const formatLatency = (latencyMs: number) => {
  if (!Number.isFinite(latencyMs)) return '-';
  if (latencyMs < 1000) return `${latencyMs}ms`;
  return `${(latencyMs / 1000).toFixed(1)}s`;
};

const formatRunTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
};

const previewValueOf = (payload: unknown, preview: string) =>
  payload === null || payload === undefined ? preview : payload;

export function CostOptimizerBaselineSelection({
  workflowId,
  nodeId,
  onBaselineSelected,
  onClose,
}: CostOptimizerBaselineSelectionProps) {
  const [rows, setRows] = useState<CostOptimizerBaselineRow[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [q, setQ] = useState('');
  const [model, setModel] = useState('');
  const [compareAvailable, setCompareAvailable] = useState('');
  const [sort, setSort] = useState('started_at_desc');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  const listParams = useMemo<CostOptimizerBaselineListParams>(
    () => ({
      q: q || undefined,
      model: model || undefined,
      date_from: dateFrom ? `${dateFrom}T00:00:00` : undefined,
      date_to: dateTo ? `${dateTo}T23:59:59` : undefined,
      compare_available:
        compareAvailable === '' ? undefined : compareAvailable === 'true',
      sort,
      limit: DEFAULT_LIMIT,
      offset,
    }),
    [compareAvailable, dateFrom, dateTo, model, offset, q, sort],
  );

  const loadRows = useCallback(async () => {
    setIsLoading(true);
    setMessage(null);
    try {
      const response = await workflowApi.listCostOptimizerBaselines(
        workflowId,
        nodeId,
        listParams,
      );
      setRows((current) =>
        listParams.offset && listParams.offset > 0
          ? [...current, ...response.items]
          : response.items,
      );
      setTotal(response.total);
    } finally {
      setIsLoading(false);
    }
  }, [listParams, nodeId, workflowId]);

  useEffect(() => {
    void loadRows();
  }, [loadRows]);

  const handleRowSelect = (row: CostOptimizerBaselineRow) => {
    if (!row.compare_available) {
      setMessage(unavailableMessage);
      return;
    }
    onBaselineSelected(row);
  };

  const resetPagination = () => {
    setOffset(0);
    setTotal(0);
    setRows([]);
  };

  const updateQuery = (value: string) => {
    resetPagination();
    setQ(value);
  };

  const updateModel = (value: string) => {
    resetPagination();
    setModel(value);
  };

  const updateCompareAvailable = (value: string) => {
    resetPagination();
    setCompareAvailable(value);
  };

  const updateSort = (value: string) => {
    resetPagination();
    setSort(value);
  };

  const updateDateFrom = (value: string) => {
    resetPagination();
    setDateFrom(value);
  };

  const updateDateTo = (value: string) => {
    resetPagination();
    setDateTo(value);
  };

  const hasMore = rows.length < total;
  const modelOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [...rows.map((row) => row.model), model].filter(
            (modelId): modelId is string =>
              typeof modelId === 'string' && modelId.trim().length > 0,
          ),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [model, rows],
  );

  return (
    <section aria-label="비용 최적화 baseline 선택" className="space-y-4">
      <div>
        <h3 className="text-sm font-bold text-slate-950">
          A/B 테스트 기준 선택
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-slate-500">
          같은 입력으로 비교할 A 기준 실행 로그를 선택합니다.
        </p>
      </div>

      <button
        type="button"
        className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50"
        onClick={onClose}
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        닫기
      </button>

      {message ? (
        <p
          role="status"
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        >
          {message}
        </p>
      ) : null}
      {isLoading ? (
        <p className="text-xs text-gray-500">실행 로그를 불러오는 중입니다.</p>
      ) : null}

      <div className="space-y-3">
          <div className="grid gap-2">
            <input
              aria-label="baseline 검색"
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-xs outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
              type="search"
              value={q}
              onChange={(event) => updateQuery(event.target.value)}
              placeholder="입력 또는 출력 검색"
            />
            <label className="grid gap-1 text-[11px] font-semibold text-slate-500">
              <span>모델</span>
              <select
                className="rounded-md border border-slate-200 px-2 py-2 text-xs font-medium text-slate-700"
                value={model}
                onChange={(event) => updateModel(event.target.value)}
              >
                <option value="">전체 모델</option>
                {modelOptions.map((modelId) => (
                  <option key={modelId} value={modelId}>
                    {modelId}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-[11px] font-semibold text-slate-500">
              <span>비교 가능 여부</span>
              <select
                className="rounded-md border border-slate-200 px-2 py-2 text-xs font-medium text-slate-700"
                value={compareAvailable}
                onChange={(event) => updateCompareAvailable(event.target.value)}
              >
                <option value="">전체</option>
                <option value="true">비교 가능</option>
                <option value="false">비교 불가</option>
              </select>
            </label>
            <label className="grid gap-1 text-[11px] font-semibold text-slate-500">
              <span>정렬</span>
              <select
                className="rounded-md border border-slate-200 px-2 py-2 text-xs font-medium text-slate-700"
                value={sort}
                onChange={(event) => updateSort(event.target.value)}
              >
                <option value="started_at_desc">최신순</option>
                <option value="cost_desc">비용 높은순</option>
                <option value="cost_asc">비용 낮은순</option>
                <option value="tokens_desc">토큰 많은순</option>
                <option value="latency_desc">실행 시간 긴순</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="grid gap-1 text-[11px] font-semibold text-slate-500">
                <span>시작일</span>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(event) => updateDateFrom(event.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-2 text-xs font-medium text-slate-700"
                />
              </label>
              <label className="grid gap-1 text-[11px] font-semibold text-slate-500">
                <span>종료일</span>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(event) => updateDateTo(event.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-2 text-xs font-medium text-slate-700"
                />
              </label>
            </div>
          </div>

          <div className="grid gap-2">
            {rows.map((row) => (
              <button
                key={row.baseline_id}
                type="button"
                className="block w-full rounded-lg border border-slate-200 bg-white p-3 text-left shadow-sm transition-colors hover:border-emerald-200 hover:bg-emerald-50/40"
                onClick={() => handleRowSelect(row)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-bold text-slate-950">
                      {row.compare_available ? row.model : '모델 확인 불가'}
                    </div>
                    <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500">
                      <Clock3 className="h-3 w-3" />
                      {formatRunTime(row.run_started_at) || '실행 시간 없음'}
                    </div>
                  </div>
                  <span
                    className={
                      row.compare_available
                        ? 'shrink-0 rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700'
                        : 'shrink-0 rounded-full bg-amber-50 px-2 py-1 text-[11px] font-bold text-amber-700'
                    }
                  >
                    {row.compare_available
                      ? row.downstream_compatibility?.label || '검증 가능'
                      : '비교 불가'}
                  </span>
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-semibold text-slate-600">
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1">
                    <Activity className="h-3 w-3" />
                    Run {row.workflow_run_status}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1">
                    <GitCommitHorizontal className="h-3 w-3" />
                    Node {row.node_status}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1">
                    Trace {row.trace_available || row.has_trace ? '있음' : '없음'}
                  </span>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <div className="rounded-md bg-slate-50 px-2 py-1">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                      Tokens
                    </div>
                    <div className="font-semibold text-slate-700">
                      {row.compare_available ? row.total_tokens : '-'}
                    </div>
                  </div>
                  <div className="rounded-md bg-slate-50 px-2 py-1">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                      Time
                    </div>
                    <div className="font-semibold text-slate-700">
                      {row.compare_available
                        ? formatLatency(row.latency_ms)
                        : '-'}
                    </div>
                  </div>
                  <div className="rounded-md bg-slate-50 px-2 py-1">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                      Cost
                    </div>
                    <div className="font-semibold text-slate-700">
                      {row.compare_available ? formatCost(row.cost) : '-'}
                    </div>
                  </div>
                </div>

                <div className="mt-3 space-y-2">
                  <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
                    <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                      입력
                    </div>
                    <CostOptimizerPreviewViewer
                      value={previewValueOf(row.input, row.input_preview)}
                      emptyText="입력 미보관"
                      className="border border-slate-100 bg-slate-50"
                    />
                  </div>
                  <div className="rounded-md border border-slate-100 bg-white px-3 py-2">
                    <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                      출력
                    </div>
                    <CostOptimizerPreviewViewer
                      value={previewValueOf(row.output, row.output_preview)}
                      emptyText="출력 미보관"
                      className="border border-slate-100 bg-white"
                      fieldLabels={fieldLabelsFromOutputFormat(
                        row.node_options?.output_format,
                      )}
                    />
                  </div>
                </div>
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>
              {total > 0
                ? `총 ${total.toLocaleString('ko-KR')}개 중 ${rows.length.toLocaleString('ko-KR')}개 표시`
                : '표시할 실행 로그가 없습니다'}
            </span>
            {hasMore ? (
              <button
                type="button"
                disabled={isLoading}
                onClick={() => setOffset(rows.length)}
                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                더 보기
              </button>
            ) : null}
          </div>
      </div>
    </section>
  );
}
