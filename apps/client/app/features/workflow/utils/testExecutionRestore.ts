import type { WorkflowNodeRun, WorkflowRun } from '../types/Api';
import type { Node } from '../types/Workflow';
import type { RestoredTestExecution, TestNodeResult } from '../store/useWorkflowStore';
import { readCost, readTokenUsage } from './testExecutionSummary';

const toTimestamp = (value?: string | null) => {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
};

const restoredStatus = (
  status: string,
): RestoredTestExecution['status'] => {
  switch (status.toLowerCase()) {
    case 'running':
      return 'running';
    case 'success':
      return 'success';
    default:
      return 'failure';
  }
};

const withTraceMetadata = (nodeRun: WorkflowNodeRun): unknown => {
  const output = nodeRun.outputs;
  if (!output || typeof output !== 'object' || Array.isArray(output)) {
    return output;
  }

  if (!nodeRun.trace_metadata || Object.keys(nodeRun.trace_metadata).length === 0) {
    return output;
  }

  const metadata =
    'metadata' in output &&
    output.metadata &&
    typeof output.metadata === 'object' &&
    !Array.isArray(output.metadata)
      ? output.metadata
      : {};

  return {
    ...output,
    metadata: {
      ...metadata,
      ...nodeRun.trace_metadata,
    },
  };
};

const toRestoredNodeResult = (
  nodeRun: WorkflowNodeRun,
  nodes: Node[],
): TestNodeResult => {
  const node = nodes.find((item) => item.id === nodeRun.node_id);
  const nodeData = node?.data as { title?: unknown; name?: unknown } | undefined;
  const title =
    (typeof nodeData?.title === 'string' && nodeData.title.trim()) ||
    (typeof nodeData?.name === 'string' && nodeData.name.trim()) ||
    nodeRun.node_id;
  const output = withTraceMetadata(nodeRun);

  return {
    nodeId: nodeRun.node_id,
    nodeType: nodeRun.node_type,
    title,
    status: restoredStatus(nodeRun.status),
    output,
    traceMetadata: nodeRun.trace_metadata,
    latencyMs:
      typeof nodeRun.duration === 'number'
        ? Math.max(0, Math.round(nodeRun.duration * 1000))
        : undefined,
    totalTokens: readTokenUsage(output),
    totalCost: readCost(output),
  };
};

export const restoreTestExecutionFromWorkflowRun = (
  run: WorkflowRun,
  nodes: Node[],
): RestoredTestExecution => ({
  runId: run.id,
  status: restoredStatus(run.status),
  startedAt: toTimestamp(run.started_at),
  finishedAt: toTimestamp(run.finished_at),
  workflowResult: {
    run_id: run.id,
    output: run.outputs ?? {},
    duration: run.duration,
    total_tokens: run.total_tokens,
    total_cost: run.total_cost,
  },
  nodeResults: (run.node_runs ?? []).map((nodeRun) =>
    toRestoredNodeResult(nodeRun, nodes),
  ),
  error: run.error_message ?? null,
});
