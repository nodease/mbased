export type TestNodeExecutionSummary = {
  nodeId: string;
  status: 'running' | 'success' | 'failure';
  latencyMs?: number;
  totalTokens?: number;
  cost?: number;
};

type TestExecutionActionState = {
  isExecuting: boolean;
  isUploading: boolean;
  isPreparing: boolean;
  canExecute: boolean;
};

type NodeFinishMetricSource = {
  output?: unknown;
  latency_ms?: unknown;
  total_tokens?: unknown;
  total_cost?: unknown;
};

type WorkflowFinishMetricSource = {
  duration?: unknown;
  total_tokens?: unknown;
  total_cost?: unknown;
};

const readNumber = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined;

export const readTokenUsage = (output: unknown) => {
  if (!output || typeof output !== 'object') return undefined;
  const usage = (output as { usage?: Record<string, unknown> }).usage || {};
  const totalTokens = usage.total_tokens;
  if (typeof totalTokens === 'number') return totalTokens;

  const promptTokens =
    typeof usage.prompt_tokens === 'number' ? usage.prompt_tokens : 0;
  const completionTokens =
    typeof usage.completion_tokens === 'number' ? usage.completion_tokens : 0;
  const summedTokens = promptTokens + completionTokens;
  return summedTokens > 0 ? summedTokens : undefined;
};

export const readCost = (output: unknown) => {
  if (!output || typeof output !== 'object') return undefined;
  const directCost = (output as { cost?: unknown }).cost;
  if (typeof directCost === 'number') return directCost;
  const usage = (output as { usage?: Record<string, unknown> }).usage || {};
  return typeof usage.total_cost === 'number' ? usage.total_cost : undefined;
};

export const readNodeFinishExecutionSummary = (
  eventData: NodeFinishMetricSource,
  fallbackLatencyMs?: number,
) => {
  const latencyMs = readNumber(eventData.latency_ms) ?? fallbackLatencyMs;
  const totalTokens =
    readNumber(eventData.total_tokens) ?? readTokenUsage(eventData.output);
  const totalCost =
    readNumber(eventData.total_cost) ?? readCost(eventData.output);

  return {
    latencyMs,
    totalTokens,
    totalCost,
  };
};

export const readWorkflowFinishExecutionSummary = (
  result: WorkflowFinishMetricSource | null | undefined,
) => {
  if (!result || typeof result !== 'object') {
    return {
      serverDurationMs: undefined,
      totalTokens: undefined,
      totalCost: undefined,
    };
  }

  const durationSeconds = readNumber(result.duration);

  return {
    serverDurationMs:
      durationSeconds === undefined
        ? undefined
        : Math.max(0, Math.round(durationSeconds * 1000)),
    totalTokens: readNumber(result.total_tokens),
    totalCost: readNumber(result.total_cost),
  };
};

export const formatLatency = (latencyMs?: number | null) => {
  if (latencyMs === undefined || latencyMs === null) return '-';
  if (latencyMs < 1000) return `${latencyMs}ms`;
  return `${(latencyMs / 1000).toFixed(1)}s`;
};

export const formatTokens = (totalTokens?: number | null) => {
  if (totalTokens === undefined || totalTokens === null) return '-';
  return totalTokens.toLocaleString();
};

export const formatCost = (cost?: number | null) => {
  if (cost === undefined || cost === null) return '-';
  return `$${cost.toFixed(6)}`;
};

export const isTestExecutionActionDisabled = ({
  isExecuting,
  isUploading,
  isPreparing,
  canExecute,
}: TestExecutionActionState) =>
  isExecuting || isUploading || isPreparing || !canExecute;

export const summarizeWorkflowExecution = (
  summaries: TestNodeExecutionSummary[],
  startedAt: number | null,
  finishedAt: number | null,
  workflowResult?: WorkflowFinishMetricSource | null,
) => {
  const workflowSummary = readWorkflowFinishExecutionSummary(workflowResult);
  const nodeTotalTokens = summaries.reduce(
    (sum, item) => sum + (item.totalTokens || 0),
    0,
  );
  const nodeTotalCost = summaries.reduce(
    (sum, item) => sum + (item.cost || 0),
    0,
  );
  const screenCompletionDurationMs =
    typeof startedAt === 'number' && typeof finishedAt === 'number'
      ? Math.max(0, finishedAt - startedAt)
      : undefined;
  const nodeServerDurationMs = summaries.reduce(
    (sum, item) => sum + (item.latencyMs || 0),
    0,
  );

  return {
    serverDurationMs:
      workflowSummary.serverDurationMs ??
      (nodeServerDurationMs > 0 ? nodeServerDurationMs : undefined),
    screenCompletionDurationMs,
    totalLatencyMs: screenCompletionDurationMs,
    totalTokens:
      workflowSummary.totalTokens ?? (nodeTotalTokens > 0 ? nodeTotalTokens : undefined),
    totalCost:
      workflowSummary.totalCost ?? (nodeTotalCost > 0 ? nodeTotalCost : undefined),
  };
};
