import type {
  CostOptimizerBaselineRow,
  CostOptimizerCandidateSummary,
  CostOptimizerExperimentCandidateDetail,
  CostOptimizerExperimentListParams,
  CostOptimizerExperimentSummary,
} from '../../types/Api';

export type SelectedHistoryTarget =
  | { type: 'current' }
  | { type: 'history'; experimentId: string; candidateId: string }
  | null;

export interface CostOptimizerHistoryRow {
  experiment: CostOptimizerExperimentSummary;
  candidate: CostOptimizerCandidateSummary;
}

export interface CostOptimizerHistoryFilters {
  dateFrom: string;
  dateTo: string;
  createdBy: string;
  isApplied: string;
  candidateStatus: string;
  model: string;
  schemaStatus: string;
  downstreamState: string;
}

export const createDefaultCostOptimizerHistoryFilters =
  (): CostOptimizerHistoryFilters => ({
    dateFrom: '',
    dateTo: '',
    createdBy: '',
    isApplied: '',
    candidateStatus: 'success',
    model: '',
    schemaStatus: '',
    downstreamState: '',
  });

export const costOptimizerHistoryListParams = (
  baselineId: string,
  filters: CostOptimizerHistoryFilters,
): CostOptimizerExperimentListParams => ({
  baseline_id: baselineId,
  date_from: filters.dateFrom ? `${filters.dateFrom}T00:00:00` : undefined,
  date_to: filters.dateTo ? `${filters.dateTo}T23:59:59` : undefined,
  created_by: filters.createdBy.trim() || undefined,
  candidate_status: filters.candidateStatus || undefined,
  model: filters.model.trim() || undefined,
  is_applied:
    filters.isApplied === '' ? undefined : filters.isApplied === 'true',
  schema_status: filters.schemaStatus || undefined,
  downstream_state: filters.downstreamState || undefined,
  limit: 20,
  offset: 0,
});

export const experimentFromCandidateDetail = (
  detail: CostOptimizerExperimentCandidateDetail,
): CostOptimizerExperimentSummary => ({
  ...detail,
  candidates: [detail.candidate],
});

export const baselineFromExperimentSummary = (
  workflowId: string,
  nodeId: string,
  experiment: CostOptimizerExperimentSummary,
): CostOptimizerBaselineRow | null => {
  const summary = experiment.baseline_summary;
  const baselineId = summary?.baseline_id || experiment.baseline_node_run_id;
  if (!summary || !baselineId) return null;

  return {
    baseline_id: baselineId,
    baseline_source: 'cost_optimizer_experiment_history',
    source_workflow_node_run_id: baselineId,
    workflow_run_id:
      summary.workflow_run_id || experiment.baseline_workflow_run_id || '',
    workflow_id: workflowId,
    node_id: nodeId,
    run_started_at: experiment.created_at || '',
    workflow_run_status: 'success',
    node_status: 'success',
    model: summary.model || '-',
    cost: summary.cost || 0,
    total_tokens: summary.total_tokens || 0,
    latency_ms: summary.latency_ms || 0,
    input_available: false,
    output_available: Boolean(summary.output_available),
    usage_available: true,
    trace_available: false,
    compare_available: false,
    input_preview: '',
    output_preview: summary.output_preview || '',
    output: summary.output,
    has_trace: false,
  };
};

export const flattenCostOptimizerHistory = (
  experiments: CostOptimizerExperimentSummary[],
): CostOptimizerHistoryRow[] =>
  experiments
    .flatMap((experiment) =>
      experiment.candidates.map((candidate) => ({ experiment, candidate })),
    )
    .sort((a, b) => {
      const aTime = new Date(
        a.candidate.created_at || a.experiment.created_at || 0,
      ).getTime();
      const bTime = new Date(
        b.candidate.created_at || b.experiment.created_at || 0,
      ).getTime();
      return bTime - aTime;
    });
