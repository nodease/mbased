import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Pin,
  RefreshCw,
} from 'lucide-react';

import { workflowApi } from '../../api/workflowApi';
import { getNodeDefinitionByType } from '../../config/nodeRegistry';
import type { LLMTrace, WorkflowNodeRun, WorkflowRun } from '../../types/Api';
import type { Node } from '../../types/Workflow';
import {
  formatCost,
  formatLatency,
  formatTokens,
  readCost,
  readTokenUsage,
} from '../../utils/testExecutionSummary';
import { ModelRoutingDecisionDetails } from '../modelRouting/ModelRoutingDecisionDetails';

type ExecutionComparisonPanelProps = {
  workflowId: string;
  nodes: Node[];
  baselineRunId: string | null;
  currentRunId: string | null;
  currentExecutionStatus?: string | null;
  currentExecutionError?: string | null;
  reloadRequestKey?: number;
  selectedNodeId?: string | null;
  onBaselineRunIdChange: (runId: string | null) => void;
  onSelectedNodeIdChange?: (nodeId: string | null) => void;
};

type NodeSnapshot = {
  nodeId: string;
  nodeType: string;
  title: string;
  status: string;
  durationMs?: number;
  totalTokens?: number;
  totalCost?: number;
  inputs?: unknown;
  outputs?: unknown;
  traceMetadata?: unknown;
};

type RunBundle = {
  run: WorkflowRun;
  nodes: Map<string, NodeSnapshot>;
  traceAvailability: 'available' | 'not_recorded' | 'unavailable';
};

const PAGE_SIZE = 10;
const CURRENT_RUN_COMPARISON_RETRY_DELAYS_MS = [
  400, 800, 1_200, 2_000, 3_000, 4_000,
] as const;
const RUN_DETAIL_RETRY_DELAYS_MS = [200] as const;

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const getHttpStatus = (error: unknown) => {
  if (
    isRecord(error) &&
    isRecord(error.response) &&
    typeof error.response.status === 'number'
  ) {
    return error.response.status;
  }
  return undefined;
};

const isRetryableRunDetailError = (error: unknown) => {
  const status = getHttpStatus(error);
  return (
    status === undefined ||
    status === 404 ||
    status === 408 ||
    status === 429 ||
    status >= 500
  );
};

const runComparisonErrorMessage = (error: unknown) => {
  const status = getHttpStatus(error);
  if (status === 401) return '로그인이 만료되었습니다. 다시 로그인해 주세요.';
  if (status === 403) return '이 실행 기록을 조회할 권한이 없습니다.';
  if (status === 404) {
    return '선택한 실행 기록을 찾을 수 없습니다. 기준 실행을 다시 선택해 주세요.';
  }
  return '실행 비교 데이터를 불러오지 못했습니다.';
};

class RunBundleNotReadyError extends Error {
  constructor() {
    super('workflow run node logs are not ready');
    this.name = 'RunBundleNotReadyError';
  }
}

const modelFromOutput = (output: unknown) => {
  if (!isRecord(output)) return undefined;
  return typeof output.model === 'string' && output.model.trim()
    ? output.model.trim()
    : undefined;
};

const containsRoutingDecision = (value: unknown) => {
  if (!isRecord(value)) return false;
  return [
    'decision_source',
    'fallback_model',
    'matched_rule_id',
    'policy_id',
    'policy_version',
    'reason_code',
    'selected_model',
  ].some((key) => key in value);
};

const hasRoutingTrace = (snapshot?: NodeSnapshot) => {
  if (isRecord(snapshot?.traceMetadata)) {
    const llm = snapshot.traceMetadata.llm;
    if (
      containsRoutingDecision(llm) ||
      containsRoutingDecision(snapshot.traceMetadata)
    ) {
      return true;
    }
  }
  if (!isRecord(snapshot?.outputs)) return false;
  const metadata = isRecord(snapshot.outputs.metadata)
    ? snapshot.outputs.metadata
    : null;
  return Boolean(
    metadata &&
    (isRecord(metadata.model_routing) ||
      isRecord(metadata.model_routing_metadata)),
  );
};

const nodeTitle = (node: Node | undefined, nodeRun: WorkflowNodeRun) => {
  const data = node?.data as { title?: unknown; name?: unknown } | undefined;
  if (typeof data?.title === 'string' && data.title.trim()) return data.title;
  if (typeof data?.name === 'string' && data.name.trim()) return data.name;
  return nodeRun.node_id;
};

const nodeTypeLabel = (baseline?: NodeSnapshot, current?: NodeSnapshot) => {
  const nodeType = current?.nodeType || baseline?.nodeType;
  if (!nodeType) return '유형 정보 없음';
  return getNodeDefinitionByType(nodeType)?.name || nodeType;
};

const comparisonNodeDefinitionKey = (nodes: Node[]) =>
  nodes
    .map((node) => {
      const data = node.data as { title?: unknown; name?: unknown } | undefined;
      const title =
        typeof data?.title === 'string'
          ? data.title
          : typeof data?.name === 'string'
            ? data.name
            : '';
      return JSON.stringify([node.id, node.type ?? '', title]);
    })
    .sort()
    .join('\u001f');

const aggregateTraces = (traces: LLMTrace[]) => {
  const byNode = new Map<
    string,
    { totalTokens: number; totalCost: number; latencyMs: number; count: number }
  >();

  for (const trace of traces) {
    if (!trace.node_id) continue;
    const current = byNode.get(trace.node_id) ?? {
      totalTokens: 0,
      totalCost: 0,
      latencyMs: 0,
      count: 0,
    };
    current.totalTokens += isFiniteNumber(trace.total_tokens)
      ? trace.total_tokens
      : 0;
    current.totalCost += isFiniteNumber(trace.total_cost)
      ? trace.total_cost
      : 0;
    current.latencyMs += isFiniteNumber(trace.latency_ms)
      ? trace.latency_ms
      : 0;
    current.count += 1;
    byNode.set(trace.node_id, current);
  }

  return byNode;
};

const toRunBundle = (
  run: WorkflowRun,
  workflowNodes: Node[],
  traces: LLMTrace[],
  traceAvailability: RunBundle['traceAvailability'],
): RunBundle => {
  const traceByNode = aggregateTraces(traces);
  const snapshots = new Map<string, NodeSnapshot>();

  for (const nodeRun of run.node_runs ?? []) {
    const trace = traceByNode.get(nodeRun.node_id);
    const outputTokens = readTokenUsage(nodeRun.outputs);
    const outputCost = readCost(nodeRun.outputs);
    snapshots.set(nodeRun.node_id, {
      nodeId: nodeRun.node_id,
      nodeType: nodeRun.node_type,
      title: nodeTitle(
        workflowNodes.find((node) => node.id === nodeRun.node_id),
        nodeRun,
      ),
      status: nodeRun.status,
      durationMs: isFiniteNumber(nodeRun.duration)
        ? Math.max(0, Math.round(nodeRun.duration * 1000))
        : trace && trace.latencyMs > 0
          ? trace.latencyMs
          : undefined,
      totalTokens:
        trace && trace.totalTokens > 0 ? trace.totalTokens : outputTokens,
      totalCost: trace && trace.totalCost > 0 ? trace.totalCost : outputCost,
      inputs: nodeRun.inputs,
      outputs: nodeRun.outputs,
      traceMetadata: nodeRun.trace_metadata,
    });
  }

  return { run, nodes: snapshots, traceAvailability };
};

const wait = (durationMs: number) =>
  new Promise((resolve) => setTimeout(resolve, durationMs));

const loadWorkflowRunDetail = async (
  workflowId: string,
  runId: string,
): Promise<WorkflowRun> => {
  let lastError: unknown;

  for (
    let attempt = 0;
    attempt <= RUN_DETAIL_RETRY_DELAYS_MS.length;
    attempt += 1
  ) {
    try {
      return await workflowApi.getWorkflowRun(workflowId, runId);
    } catch (error) {
      lastError = error;
      const retryDelay = RUN_DETAIL_RETRY_DELAYS_MS[attempt];
      if (!isRetryableRunDetailError(error) || retryDelay === undefined) {
        throw error;
      }
      await wait(retryDelay);
    }
  }

  throw lastError;
};

const loadRunBundle = async (
  workflowId: string,
  runId: string,
  nodes: Node[],
  options: {
    requireTerminal?: boolean;
    retryDelaysMs?: readonly number[];
  } = {},
): Promise<RunBundle> => {
  let lastError: unknown;
  const retryDelaysMs = options.retryDelaysMs ?? [];

  for (let attempt = 0; attempt <= retryDelaysMs.length; attempt += 1) {
    try {
      const run = await workflowApi.getWorkflowRun(workflowId, runId);
      if (
        options.requireTerminal &&
        (run.status === 'running' || (run.node_runs ?? []).length === 0)
      ) {
        throw new RunBundleNotReadyError();
      }
      let traces: LLMTrace[] = [];
      let traceAvailability: RunBundle['traceAvailability'] = 'available';
      try {
        const response = await workflowApi.getWorkflowRunLlmTraces(
          workflowId,
          runId,
          { limit: 100 },
        );
        traces = response.items;
        if (traces.length === 0) {
          traceAvailability = 'not_recorded';
        }
      } catch (traceError) {
        traceAvailability =
          getHttpStatus(traceError) === 404 ? 'not_recorded' : 'unavailable';
      }
      return toRunBundle(run, nodes, traces, traceAvailability);
    } catch (error) {
      lastError = error;
      const retryDelay = retryDelaysMs[attempt];
      const shouldRetry =
        error instanceof RunBundleNotReadyError ||
        isRetryableRunDetailError(error);
      if (retryDelay === undefined || !shouldRetry) throw error;
      await wait(retryDelay);
    }
  }
  throw lastError;
};

const runStatusLabel = (status: string | undefined) => {
  switch ((status ?? '').toLowerCase()) {
    case 'success':
      return '성공';
    case 'failed':
    case 'failure':
      return '실패';
    case 'running':
      return '실행 중';
    case 'stopped':
      return '중지';
    default:
      return '실행 안 됨';
  }
};

const formatRunTime = (value?: string) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
};

const runTriggerLabel = (triggerMode: WorkflowRun['trigger_mode']) => {
  switch (triggerMode) {
    case 'manual':
      return '빌더 테스트';
    case 'app':
      return '내부 배포';
    case 'webhook':
      return 'Webhook';
    case 'api':
      return 'API';
    case 'scheduler':
      return '스케줄';
    default:
      return '실행 방식 없음';
  }
};

const SENSITIVE_INPUT_KEY_PATTERN =
  /(?:authorization|credential|password|secret|token|api[_-]?key)/i;

const collectInputTextCandidates = (
  value: unknown,
  candidates: string[],
  depth = 0,
) => {
  if (depth > 4 || candidates.length >= 30) return;
  if (typeof value === 'string') {
    const text = value.trim();
    if (text) candidates.push(text);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 10)) {
      collectInputTextCandidates(item, candidates, depth + 1);
    }
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, item] of Object.entries(value)) {
    if (SENSITIVE_INPUT_KEY_PATTERN.test(key)) continue;
    collectInputTextCandidates(item, candidates, depth + 1);
  }
};

const runInputPreview = (inputs: unknown) => {
  const candidates: string[] = [];
  collectInputTextCandidates(inputs, candidates);
  return (
    candidates.sort((left, right) => right.length - left.length)[0] ||
    '식별할 수 있는 입력 내용이 없습니다.'
  );
};

function ValueViewer({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <p className="text-xs text-gray-500">기록 없음</p>;
  }
  if (typeof value === 'string') {
    return (
      <p className="whitespace-pre-wrap break-words text-xs leading-5 text-gray-800 dark:text-gray-100">
        {value || '빈 문자열'}
      </p>
    );
  }
  if (typeof value !== 'object') {
    return (
      <p className="break-words text-xs text-gray-800 dark:text-gray-100">
        {String(value)}
      </p>
    );
  }
  if (Array.isArray(value)) {
    return (
      <ol className="space-y-2">
        {value.map((item, index) => (
          <li key={index} className="rounded border border-gray-100 p-2">
            <span className="mb-1 block text-[10px] font-semibold text-gray-400">
              {index + 1}
            </span>
            <ValueViewer value={item} />
          </li>
        ))}
      </ol>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) {
    return <p className="text-xs text-gray-500">빈 객체</p>;
  }
  return (
    <dl className="space-y-2">
      {entries.map(([key, item]) => (
        <div
          key={key}
          className="rounded border border-gray-100 bg-white p-2 dark:border-gray-700 dark:bg-gray-900"
        >
          <dt className="mb-1 text-[10px] font-semibold uppercase text-gray-500">
            {key}
          </dt>
          <dd>
            <ValueViewer value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function RunSelectionDetails({
  run,
  nodes,
}: {
  run: WorkflowRun;
  nodes: Node[];
}) {
  const snapshots = Array.from(
    toRunBundle(run, nodes, [], 'not_recorded').nodes.values(),
  );
  const llmSnapshots = snapshots.filter(
    (snapshot) => snapshot.nodeType === 'llmNode',
  );

  return (
    <div className="border-t border-gray-100 bg-slate-50/70 px-3 py-3 dark:border-gray-700 dark:bg-gray-800/40">
      <div className="grid gap-3">
        <section>
          <h4 className="text-xs font-semibold text-gray-800 dark:text-gray-100">
            실행 입력
          </h4>
          <div className="mt-2 max-h-48 overflow-y-auto rounded-md border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
            <ValueViewer value={run.inputs} />
          </div>
        </section>

        <section>
          <h4 className="text-xs font-semibold text-gray-800 dark:text-gray-100">
            모델 라우팅
          </h4>
          <div className="mt-2 space-y-2">
            {llmSnapshots.length > 0 ? (
              llmSnapshots.map((snapshot) => (
                <div key={snapshot.nodeId}>
                  <p className="mb-1 text-[11px] font-semibold text-gray-600 dark:text-gray-300">
                    {snapshot.title}
                  </p>
                  <RoutingSide snapshot={snapshot} />
                </div>
              ))
            ) : (
              <p className="rounded-md border border-dashed border-gray-300 bg-white p-2 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-900">
                이 실행에는 LLM 노드 기록이 없습니다.
              </p>
            )}
          </div>
        </section>

        <section>
          <h4 className="text-xs font-semibold text-gray-800 dark:text-gray-100">
            실행 지표
          </h4>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
            <div>
              <dt className="text-gray-500">상태</dt>
              <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
                {runStatusLabel(run.status)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">실행 시간</dt>
              <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
                {formatLatency(runDurationMs(run))}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">비용</dt>
              <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
                {formatCost(run.total_cost)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">전체 토큰</dt>
              <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
                {formatTokens(run.total_tokens)}
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  );
}

function MetricGrid({ snapshot }: { snapshot?: NodeSnapshot }) {
  const hasLlmUsageMetrics = snapshot?.nodeType === 'llmNode';

  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
      <div>
        <dt className="text-gray-500">상태</dt>
        <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
          {runStatusLabel(snapshot?.status)}
        </dd>
      </div>
      {hasLlmUsageMetrics ? (
        <div>
          <dt className="text-gray-500">비용</dt>
          <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
            {formatCost(snapshot?.totalCost)}
          </dd>
        </div>
      ) : null}
      <div>
        <dt className="text-gray-500">실행 시간</dt>
        <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
          {formatLatency(snapshot?.durationMs)}
        </dd>
      </div>
      {hasLlmUsageMetrics ? (
        <div>
          <dt className="text-gray-500">토큰</dt>
          <dd className="mt-0.5 font-semibold text-gray-900 dark:text-gray-100">
            {formatTokens(snapshot?.totalTokens)}
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

const runDurationMs = (run: WorkflowRun) =>
  isFiniteNumber(run.duration) ? run.duration * 1000 : undefined;

const comparisonBarWidth = (value: number | undefined, maxValue: number) => {
  if (!isFiniteNumber(value) || value <= 0 || maxValue <= 0) return '0%';
  return `${Math.max((value / maxValue) * 100, 6)}%`;
};

const comparisonDelta = (
  baselineValue: number | undefined,
  candidateValue: number | undefined,
) => {
  if (!isFiniteNumber(baselineValue) || !isFiniteNumber(candidateValue)) {
    return { label: '변화 계산 불가', tone: 'text-gray-500' };
  }
  if (baselineValue === 0) {
    return candidateValue === 0
      ? { label: '변화 없음', tone: 'text-gray-500' }
      : { label: '기준값 없음', tone: 'text-gray-500' };
  }

  const percent = ((candidateValue - baselineValue) / baselineValue) * 100;
  if (Math.abs(percent) < 0.05) {
    return { label: '변화 없음', tone: 'text-gray-500' };
  }

  return {
    label: `${Math.abs(percent).toFixed(1)}% ${percent < 0 ? '감소' : '증가'}`,
    tone: percent < 0 ? 'text-emerald-700' : 'text-amber-700',
  };
};

const runStatusBadgeClass = (status: string | undefined) => {
  switch ((status ?? '').toLowerCase()) {
    case 'success':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'failed':
    case 'failure':
      return 'border-red-200 bg-red-50 text-red-700';
    case 'running':
      return 'border-blue-200 bg-blue-50 text-blue-700';
    default:
      return 'border-gray-200 bg-gray-50 text-gray-600';
  }
};

type OverallMetricComparisonProps = {
  label: string;
  testId: string;
  baselineValue: number | undefined;
  candidateValue: number | undefined;
  format: (value: number | undefined) => string;
  isLowerBetter?: boolean;
};

function OverallMetricComparison({
  label,
  testId,
  baselineValue,
  candidateValue,
  format,
  isLowerBetter = false,
}: OverallMetricComparisonProps) {
  const maxValue = Math.max(baselineValue ?? 0, candidateValue ?? 0);
  const delta = comparisonDelta(baselineValue, candidateValue);
  const deltaTone = isLowerBetter ? delta.tone : 'text-slate-600';

  return (
    <div
      data-testid={testId}
      role="group"
      aria-label={`${label} 기준 실행과 현재 실행 비교`}
      className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
          {label}
        </p>
        <span className={`text-xs font-semibold ${deltaTone}`}>
          {delta.label}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        <div className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-2 text-[11px]">
          <span className="font-semibold text-slate-500">기준 실행</span>
          <div className="h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div
              data-testid={`${testId}-baseline-bar`}
              aria-label={`${label} 기준 실행 가로 막대`}
              className="h-full rounded-full bg-slate-400 transition-[width] dark:bg-slate-500"
              style={{ width: comparisonBarWidth(baselineValue, maxValue) }}
            />
          </div>
          <span className="font-semibold text-slate-700 dark:text-slate-200">
            {format(baselineValue)}
          </span>
        </div>
        <div className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-2 text-[11px]">
          <span className="font-semibold text-blue-700 dark:text-blue-300">
            현재 실행
          </span>
          <div className="h-3 overflow-hidden rounded-full bg-blue-50 dark:bg-blue-950/40">
            <div
              data-testid={`${testId}-current-bar`}
              aria-label={`${label} 현재 실행 가로 막대`}
              className="h-full rounded-full bg-blue-500 transition-[width] dark:bg-blue-400"
              style={{ width: comparisonBarWidth(candidateValue, maxValue) }}
            />
          </div>
          <span className="font-semibold text-blue-800 dark:text-blue-200">
            {format(candidateValue)}
          </span>
        </div>
      </div>
    </div>
  );
}

function OverallExecutionComparison({
  baseline,
  current,
}: {
  baseline: RunBundle;
  current: RunBundle;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-slate-50/60 p-3 dark:border-gray-700 dark:bg-gray-800/30">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500">
          <span>기준 실행 · {formatRunTime(baseline.run.started_at)}</span>
          <span>현재 실행 · {formatRunTime(current.run.started_at)}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <span
            className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${runStatusBadgeClass(
              baseline.run.status,
            )}`}
          >
            기준 실행: {runStatusLabel(baseline.run.status)}
          </span>
          <span
            className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${runStatusBadgeClass(
              current.run.status,
            )}`}
          >
            현재 실행: {runStatusLabel(current.run.status)}
          </span>
        </div>
      </div>
      <div
        data-testid="overall-execution-metrics"
        className="mt-3 flex flex-col gap-3"
      >
        <OverallMetricComparison
          testId="overall-execution-metric-cost"
          label="비용"
          baselineValue={baseline.run.total_cost}
          candidateValue={current.run.total_cost}
          format={formatCost}
          isLowerBetter
        />
        <OverallMetricComparison
          testId="overall-execution-metric-duration"
          label="실행 시간"
          baselineValue={runDurationMs(baseline.run)}
          candidateValue={runDurationMs(current.run)}
          format={formatLatency}
          isLowerBetter
        />
        <OverallMetricComparison
          testId="overall-execution-metric-tokens"
          label="전체 토큰"
          baselineValue={baseline.run.total_tokens}
          candidateValue={current.run.total_tokens}
          format={formatTokens}
        />
      </div>
    </div>
  );
}

function RoutingSide({ snapshot }: { snapshot?: NodeSnapshot }) {
  if (!snapshot) {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-800">
        실행 기록 없음
      </div>
    );
  }
  if (hasRoutingTrace(snapshot)) {
    return (
      <ModelRoutingDecisionDetails
        output={snapshot.outputs}
        traceMetadata={snapshot.traceMetadata}
      />
    );
  }
  return (
    <dl className="grid gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-xs dark:border-gray-700 dark:bg-gray-800">
      <div>
        <dt className="font-semibold text-gray-700 dark:text-gray-200">
          실행 방식
        </dt>
        <dd className="mt-1 text-gray-900 dark:text-gray-100">직접 선택</dd>
      </div>
      <div>
        <dt className="text-gray-500">사용 모델</dt>
        <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
          {modelFromOutput(snapshot.outputs) ?? '-'}
        </dd>
      </div>
      <p className="text-[11px] leading-5 text-gray-500">
        이 실행은 저장된 자동 라우팅 정책을 사용하지 않았습니다.
      </p>
    </dl>
  );
}

export function ExecutionComparisonPanel({
  workflowId,
  nodes,
  baselineRunId,
  currentRunId,
  currentExecutionStatus,
  currentExecutionError,
  reloadRequestKey = 0,
  selectedNodeId: selectedNodeIdProp,
  onBaselineRunIdChange,
  onSelectedNodeIdChange,
}: ExecutionComparisonPanelProps) {
  const latestNodesRef = useRef(nodes);
  const nodeDefinitionKey = comparisonNodeDefinitionKey(nodes);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('all');
  const [triggerFilter, setTriggerFilter] = useState('all');
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [totalRuns, setTotalRuns] = useState(0);
  const [isListLoading, setIsListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [listReloadKey, setListReloadKey] = useState(0);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [runDetails, setRunDetails] = useState<Record<string, WorkflowRun>>({});
  const [detailLoadingRunId, setDetailLoadingRunId] = useState<string | null>(
    null,
  );
  const [detailErrorRunId, setDetailErrorRunId] = useState<string | null>(null);
  const [detailErrorStatus, setDetailErrorStatus] = useState<number | null>(
    null,
  );
  const [baselineBundle, setBaselineBundle] = useState<RunBundle | null>(null);
  const [currentBundle, setCurrentBundle] = useState<RunBundle | null>(null);
  const [isComparisonLoading, setIsComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [internalSelectedNodeId, setInternalSelectedNodeId] = useState<
    string | null
  >(null);
  const [reloadKey, setReloadKey] = useState(0);
  const isSelectedNodeControlled = selectedNodeIdProp !== undefined;
  const selectedNodeId = isSelectedNodeControlled
    ? selectedNodeIdProp
    : internalSelectedNodeId;
  const isCurrentExecutionRunning = currentExecutionStatus === 'running';
  const selectNodeId = (nodeId: string | null) => {
    if (!isSelectedNodeControlled) {
      setInternalSelectedNodeId(nodeId);
    }
    onSelectedNodeIdChange?.(nodeId);
  };

  const loadRunDetails = async (run: WorkflowRun) => {
    setDetailErrorRunId(null);
    setDetailErrorStatus(null);

    setDetailLoadingRunId(run.id);
    try {
      const detail = await loadWorkflowRunDetail(workflowId, run.id);
      setRunDetails((current) => ({ ...current, [run.id]: detail }));
    } catch (error) {
      setDetailErrorRunId(run.id);
      setDetailErrorStatus(getHttpStatus(error) ?? null);
    } finally {
      setDetailLoadingRunId((current) => (current === run.id ? null : current));
    }
  };

  const toggleRunDetails = (run: WorkflowRun) => {
    if (expandedRunId === run.id) {
      setExpandedRunId(null);
      return;
    }

    setExpandedRunId(run.id);
    if (runDetails[run.id]) return;
    void loadRunDetails(run);
  };

  useEffect(() => {
    latestNodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    if (baselineRunId) return;
    let cancelled = false;
    setIsListLoading(true);
    setListError(null);
    workflowApi
      .getWorkflowRuns(workflowId, page, PAGE_SIZE, {
        ...(statusFilter === 'all' ? {} : { status: statusFilter }),
        ...(triggerFilter === 'all' ? {} : { trigger_mode: triggerFilter }),
      })
      .then((response) => {
        if (cancelled) return;
        setRuns(response.items);
        setTotalRuns(response.total);
        setExpandedRunId(null);
      })
      .catch(() => {
        if (!cancelled) setListError('실행 기록 목록을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!cancelled) setIsListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    baselineRunId,
    listReloadKey,
    page,
    reloadRequestKey,
    statusFilter,
    triggerFilter,
    workflowId,
  ]);

  useEffect(() => {
    if (!isSelectedNodeControlled) {
      setInternalSelectedNodeId(null);
    }
    if (!baselineRunId) {
      setBaselineBundle(null);
      setCurrentBundle(null);
      setComparisonError(null);
      return;
    }

    let cancelled = false;
    setIsComparisonLoading(true);
    setComparisonError(null);
    setBaselineBundle(null);
    setCurrentBundle(null);
    const comparisonNodes = latestNodesRef.current;
    const baselineRequest = loadRunBundle(
      workflowId,
      baselineRunId,
      comparisonNodes,
      { retryDelaysMs: RUN_DETAIL_RETRY_DELAYS_MS },
    );
    const currentRequest =
      !isCurrentExecutionRunning &&
      currentRunId &&
      currentRunId !== baselineRunId
        ? loadRunBundle(workflowId, currentRunId, comparisonNodes, {
            requireTerminal: true,
            retryDelaysMs: CURRENT_RUN_COMPARISON_RETRY_DELAYS_MS,
          })
        : Promise.resolve(null);

    Promise.all([baselineRequest, currentRequest])
      .then(([baseline, current]) => {
        if (cancelled) return;
        setBaselineBundle(baseline);
        setCurrentBundle(current);
      })
      .catch((error) => {
        if (!cancelled) {
          setComparisonError(runComparisonErrorMessage(error));
        }
      })
      .finally(() => {
        if (!cancelled) setIsComparisonLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    baselineRunId,
    currentExecutionStatus,
    currentRunId,
    isCurrentExecutionRunning,
    isSelectedNodeControlled,
    nodeDefinitionKey,
    reloadKey,
    workflowId,
  ]);

  const comparisonNodes = useMemo(() => {
    if (!baselineBundle || !currentBundle) return [];
    const ids = new Set([
      ...nodes.map((node) => node.id),
      ...baselineBundle.nodes.keys(),
      ...currentBundle.nodes.keys(),
    ]);
    return Array.from(ids)
      .map((nodeId) => ({
        nodeId,
        baseline: baselineBundle.nodes.get(nodeId),
        current: currentBundle.nodes.get(nodeId),
      }))
      .filter(({ baseline, current }) => baseline || current);
  }, [baselineBundle, currentBundle, nodes]);

  if (!baselineRunId) {
    const maxPage = Math.max(1, Math.ceil(totalRuns / PAGE_SIZE));
    return (
      <section className="mb-5 rounded-lg border border-blue-200 bg-blue-50/60 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div className="flex items-start gap-2">
          <Pin className="mt-0.5 h-4 w-4 text-blue-600" />
          <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              기준 실행 선택
            </h3>
            <p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
              최신 실행은 자동으로 선택하지 않습니다. 비교할 실행을 직접
              고정하세요.
            </p>
            {currentExecutionError ? (
              <p className="mt-2 text-xs font-medium text-red-600">
                현재 실행 실패: {currentExecutionError}
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <select
            aria-label="기준 실행 상태 필터"
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              setPage(1);
            }}
            className="rounded-md border border-gray-200 bg-white px-2 py-2 text-xs dark:border-gray-700 dark:bg-gray-900"
          >
            <option value="success">성공 실행</option>
            <option value="failed">실패 실행</option>
            <option value="all">전체 상태</option>
          </select>
          <select
            aria-label="기준 실행 방식 필터"
            value={triggerFilter}
            onChange={(event) => {
              setTriggerFilter(event.target.value);
              setPage(1);
            }}
            className="rounded-md border border-gray-200 bg-white px-2 py-2 text-xs dark:border-gray-700 dark:bg-gray-900"
          >
            <option value="manual">빌더 테스트</option>
            <option value="app">내부 배포</option>
            <option value="webhook">Webhook</option>
            <option value="api">API</option>
            <option value="scheduler">스케줄</option>
            <option value="all">전체 방식</option>
          </select>
        </div>

        {listError ? (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
            <p>{listError}</p>
            <button
              type="button"
              onClick={() => setListReloadKey((value) => value + 1)}
              className="shrink-0 rounded-md border border-red-200 bg-white px-2.5 py-1.5 font-semibold hover:bg-red-100 dark:bg-gray-900"
            >
              다시 불러오기
            </button>
          </div>
        ) : null}

        {isListLoading && runs.length === 0 ? (
          <div className="mt-4 flex items-center gap-2 text-xs text-gray-600">
            <Loader2 className="h-4 w-4 animate-spin" /> 실행 기록을 불러오는
            중입니다.
          </div>
        ) : runs.length === 0 ? (
          listError ? null : (
            <p className="mt-4 rounded-md border border-dashed border-gray-300 bg-white p-3 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-900">
              조건에 맞는 실행 기록이 없습니다.
            </p>
          )
        ) : (
          <div className="mt-3 space-y-2">
            {runs.map((run) => {
              const isExpanded = expandedRunId === run.id;
              const detail = runDetails[run.id];
              const formattedTime = formatRunTime(run.started_at);
              return (
                <article
                  key={run.id}
                  aria-label={`${formattedTime} ${runTriggerLabel(run.trigger_mode)} 실행`}
                  className="overflow-hidden rounded-md border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
                >
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <span
                      aria-label={`실행 상태 ${runStatusLabel(run.status)}`}
                      className={`h-2 w-2 shrink-0 rounded-full ${
                        run.status.toLowerCase() === 'success'
                          ? 'bg-emerald-500'
                          : run.status.toLowerCase() === 'failed'
                            ? 'bg-red-500'
                            : 'bg-blue-500'
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-2 text-[11px]">
                        <span className="shrink-0 font-semibold text-gray-900 dark:text-gray-100">
                          {formattedTime}
                        </span>
                        <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {runTriggerLabel(run.trigger_mode)}
                        </span>
                      </div>
                      <p
                        title={runInputPreview(run.inputs)}
                        className="mt-1 truncate text-xs text-gray-700 dark:text-gray-200"
                      >
                        {runInputPreview(run.inputs)}
                      </p>
                    </div>
                    <button
                      type="button"
                      aria-expanded={isExpanded}
                      aria-label={`${formattedTime} 실행 ${isExpanded ? '상세 닫기' : '상세보기'}`}
                      onClick={() => void toggleRunDetails(run)}
                      className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md border border-gray-200 px-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
                    >
                      상세
                      {isExpanded ? (
                        <ChevronUp className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => onBaselineRunIdChange(run.id)}
                      className="h-8 shrink-0 rounded-md bg-blue-600 px-2.5 text-xs font-semibold text-white hover:bg-blue-700"
                    >
                      기준으로 고정
                    </button>
                  </div>

                  {isExpanded ? (
                    detailLoadingRunId === run.id ? (
                      <div className="flex items-center gap-2 border-t border-gray-100 px-3 py-3 text-xs text-gray-500 dark:border-gray-700">
                        <Loader2 className="h-4 w-4 animate-spin" /> 실행 상세를
                        불러오는 중입니다.
                      </div>
                    ) : detailErrorRunId === run.id ? (
                      <div className="flex items-center justify-between gap-3 border-t border-gray-100 px-3 py-3 text-xs text-red-600 dark:border-gray-700">
                        <p>
                          {detailErrorStatus === 404
                            ? '실행 기록이 아직 저장되지 않았거나 더 이상 존재하지 않습니다.'
                            : '실행 상세를 불러오지 못했습니다.'}
                        </p>
                        <button
                          type="button"
                          onClick={() => void loadRunDetails(run)}
                          className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md border border-red-200 px-2 font-semibold hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950/30"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          다시 불러오기
                        </button>
                      </div>
                    ) : detail ? (
                      <RunSelectionDetails run={detail} nodes={nodes} />
                    ) : null
                  ) : null}
                </article>
              );
            })}
          </div>
        )}

        {maxPage > 1 ? (
          <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              className="rounded border border-gray-200 bg-white px-2 py-1 disabled:opacity-40"
            >
              이전
            </button>
            <span>
              {page} / {maxPage}
            </span>
            <button
              type="button"
              disabled={page >= maxPage}
              onClick={() => setPage((value) => Math.min(maxPage, value + 1))}
              className="rounded border border-gray-200 bg-white px-2 py-1 disabled:opacity-40"
            >
              다음
            </button>
          </div>
        ) : null}
      </section>
    );
  }

  const selectedComparison = selectedNodeId
    ? comparisonNodes.find((item) => item.nodeId === selectedNodeId)
    : null;
  const hasUnavailableTrace = [baselineBundle, currentBundle].some(
    (bundle) => bundle?.traceAvailability === 'unavailable',
  );
  const traceNotRecordedLabels = [
    baselineBundle?.traceAvailability === 'not_recorded' ? '기준 실행' : null,
    currentBundle?.traceAvailability === 'not_recorded' ? '현재 실행' : null,
  ].filter((label): label is string => label !== null);

  return (
    <section className="mb-5 space-y-4">
      <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              기준 실행 고정됨
            </h3>
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
              {baselineBundle
                ? formatRunTime(baselineBundle.run.started_at)
                : '기준 실행을 불러오는 중입니다.'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => onBaselineRunIdChange(null)}
            className="rounded-md border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 dark:bg-gray-900"
          >
            기준 변경
          </button>
        </div>
      </div>

      {currentExecutionError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          현재 실행 실패: {currentExecutionError}
        </div>
      ) : null}

      {traceNotRecordedLabels.length > 0 ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
          <p className="font-semibold text-slate-800 dark:text-slate-100">
            LLM trace 기록 없음: {traceNotRecordedLabels.join(', ')}
          </p>
          <p className="mt-1 leading-5">
            해당 실행은 노드 실행 기록으로 비교합니다. 모델·토큰·비용·라우팅
            근거는 일부 표시되지 않을 수 있습니다.
          </p>
        </div>
      ) : null}

      {hasUnavailableTrace ? (
        <div
          role="alert"
          className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <div className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-semibold">
                일부 LLM trace를 불러오지 못했습니다.
              </p>
              <p className="mt-1 leading-5">
                노드 실행 기록으로 비교는 계속 표시하지만, 모델·토큰·비용 또는
                라우팅 근거 일부가 빠질 수 있습니다.
              </p>
            </div>
          </div>
        </div>
      ) : null}

      {!currentRunId || currentRunId === baselineRunId ? (
        <div className="rounded-lg border border-dashed border-gray-300 bg-white p-4 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
          기준 실행은 유지됩니다. 노드를 수정한 뒤 현재 설정으로 다시
          테스트하세요.
        </div>
      ) : isCurrentExecutionRunning ? null : isComparisonLoading ? (
        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900">
          <Loader2 className="h-4 w-4 animate-spin" /> 현재 실행 기록을
          동기화하는 중입니다.
        </div>
      ) : comparisonError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <div className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4" />
            <span>{comparisonError}</span>
          </div>
          <button
            type="button"
            onClick={() => setReloadKey((value) => value + 1)}
            className="mt-3 inline-flex items-center gap-1 rounded-md border border-red-200 bg-white px-2.5 py-1.5 text-xs font-semibold"
          >
            <RefreshCw className="h-3.5 w-3.5" /> 다시 불러오기
          </button>
        </div>
      ) : baselineBundle && currentBundle ? (
        selectedComparison ? (
          <div className="space-y-4">
            <button
              type="button"
              onClick={() => selectNodeId(null)}
              className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> 노드 비교 목록으로
            </button>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              {selectedComparison.current?.title ??
                selectedComparison.baseline?.title ??
                selectedComparison.nodeId}{' '}
              상세 비교
            </h3>

            <div
              data-testid="node-comparison-status-panels"
              className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3"
            >
              <div className="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <p className="mb-3 text-xs font-semibold text-gray-500">
                  기준 실행
                </p>
                <MetricGrid snapshot={selectedComparison.baseline} />
              </div>
              <div className="rounded-lg border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900 dark:bg-blue-950/10">
                <p className="mb-3 text-xs font-semibold text-blue-700">
                  현재 실행
                </p>
                <MetricGrid snapshot={selectedComparison.current} />
              </div>
            </div>

            <div>
              <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                입력 비교
              </h4>
              <div className="mt-2 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <div className="max-h-80 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/60">
                  <p className="mb-2 text-[11px] font-semibold text-gray-500">
                    기준 실행
                  </p>
                  <ValueViewer value={selectedComparison.baseline?.inputs} />
                </div>
                <div className="max-h-80 overflow-auto rounded-lg border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900 dark:bg-blue-950/10">
                  <p className="mb-2 text-[11px] font-semibold text-blue-700">
                    현재 실행
                  </p>
                  <ValueViewer value={selectedComparison.current?.inputs} />
                </div>
              </div>
            </div>

            {selectedComparison.baseline?.nodeType === 'llmNode' ||
            selectedComparison.current?.nodeType === 'llmNode' ? (
              <div>
                <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  모델 라우팅 비교
                </h4>
                <div className="mt-2 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                  <div>
                    <p className="mb-2 text-[11px] font-semibold text-gray-500">
                      기준 실행
                    </p>
                    <RoutingSide snapshot={selectedComparison.baseline} />
                  </div>
                  <div>
                    <p className="mb-2 text-[11px] font-semibold text-blue-700">
                      현재 실행
                    </p>
                    <RoutingSide snapshot={selectedComparison.current} />
                  </div>
                </div>
              </div>
            ) : null}

            <div>
              <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                출력 비교
              </h4>
              <div className="mt-2 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <div className="max-h-96 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/60">
                  <p className="mb-2 text-[11px] font-semibold text-gray-500">
                    기준 실행
                  </p>
                  <ValueViewer value={selectedComparison.baseline?.outputs} />
                </div>
                <div className="max-h-96 overflow-auto rounded-lg border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900 dark:bg-blue-950/10">
                  <p className="mb-2 text-[11px] font-semibold text-blue-700">
                    현재 실행
                  </p>
                  <ValueViewer value={selectedComparison.current?.outputs} />
                </div>
              </div>
            </div>

          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                전체 실행 비교
              </h3>
              <div className="mt-2">
                <OverallExecutionComparison
                  baseline={baselineBundle}
                  current={currentBundle}
                />
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                노드별 비교
              </h3>
              <div className="mt-2 space-y-3">
                {comparisonNodes.map(({ nodeId, baseline, current }) => {
                  const title = current?.title ?? baseline?.title ?? nodeId;
                  const typeLabel = nodeTypeLabel(baseline, current);
                  const statusFailed = [baseline?.status, current?.status].some(
                    (status) =>
                      ['failed', 'failure'].includes(
                        (status ?? '').toLowerCase(),
                      ),
                  );
                  const StatusIcon = statusFailed ? AlertCircle : CheckCircle;
                  return (
                    <article
                      key={nodeId}
                      className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900"
                    >
                      <div className="flex items-center gap-2">
                        <StatusIcon
                          className={`h-4 w-4 ${statusFailed ? 'text-red-500' : 'text-green-600'}`}
                        />
                        <div className="min-w-0 flex-1">
                          <h4 className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                            {title}
                          </h4>
                          <p
                            aria-label={`${title} 노드 유형`}
                            className="mt-0.5 text-[11px] font-medium text-gray-500 dark:text-gray-400"
                          >
                            {typeLabel}
                          </p>
                        </div>
                      </div>
                      <div className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                        <div className="rounded border border-gray-100 bg-gray-50 p-2 dark:border-gray-700 dark:bg-gray-800/60">
                          <p className="mb-2 text-[11px] font-semibold text-gray-500">
                            기준 실행
                          </p>
                          <MetricGrid snapshot={baseline} />
                        </div>
                        <div className="rounded border border-blue-100 bg-blue-50/50 p-2 dark:border-blue-900 dark:bg-blue-950/10">
                          <p className="mb-2 text-[11px] font-semibold text-blue-700">
                            현재 실행
                          </p>
                          <MetricGrid snapshot={current} />
                        </div>
                      </div>
                      <button
                        type="button"
                        aria-label={`${title} 노드 상세 비교하기`}
                        onClick={() => selectNodeId(nodeId)}
                        className="mt-3 w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
                      >
                        노드 상세 비교하기
                      </button>
                    </article>
                  );
                })}
              </div>
            </div>
          </div>
        )
      ) : null}
    </section>
  );
}
