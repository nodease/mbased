// API 요청 & 응답과 관련된 타입들을 정의합니다.

import type { JudgeFirstActivePolicy } from './ModelRouting';

export interface WorkflowCreateRequest {
  app_id: string;
}

export interface WorkflowResponse {
  id: string;
  app_id: string;
  created_at: string;
  updated_at: string;
}

export type WorkflowPermissionSource =
  | {
      type: 'team';
      team_id: string;
      team_name: string;
      auth_state: string;
    }
  | {
      type: 'user';
      user_id: string;
      user_name?: string | null;
      auth_state: string;
    };

export interface WorkflowPermissionSummary {
  workflow_id: string;
  organization_id?: string | null;
  auth_state: string;
  can_read: boolean;
  can_write: boolean;
  can_execute: boolean;
  can_deploy: boolean;
  can_manage: boolean;
}

export interface WorkflowPermissionResponse extends WorkflowPermissionSummary {
  sources: WorkflowPermissionSource[];
}

export interface WorkflowCompareVariant {
  label: 'A' | 'B';
  value: string;
  status: 'success' | 'failed';
  error?: string | null;
  outputs?: Record<string, any>;
  node_output?: Record<string, any> | null;
  model?: string | null;
  total_tokens?: number;
  total_cost?: number;
  latency_ms?: number;
}

export interface WorkflowCompareResponse {
  workflow_id: string;
  node_id: string;
  compare_type: 'model' | 'prompt';
  variants: WorkflowCompareVariant[];
}

export interface CostOptimizerAvailabilityResponse {
  available: boolean;
  reason?: string | null;
  workflow_id: string;
  node_id: string;
  node_type: string;
  permission: {
    can_compare: boolean;
    can_apply: boolean;
    required_auth_state: string;
  };
}

export interface CostOptimizerBaselineRow {
  baseline_id: string;
  baseline_source: string;
  source_workflow_node_run_id: string;
  workflow_run_id: string;
  workflow_id: string;
  node_id: string;
  run_started_at: string;
  workflow_run_status: string;
  node_status: string;
  model: string;
  cost: number;
  total_tokens: number;
  latency_ms: number;
  input_available: boolean;
  output_available: boolean;
  usage_available: boolean;
  trace_available: boolean;
  compare_available: boolean;
  unavailable_reason?: string | null;
  input_preview: string;
  output_preview: string;
  input?: unknown;
  output?: unknown;
  has_trace: boolean;
  node_options?: Record<string, unknown>;
  downstream_compatibility?: CostOptimizerDownstreamCompatibility;
}

export interface CostOptimizerLatestBaselineResponse {
  baseline: CostOptimizerBaselineRow;
}

export interface CostOptimizerBaselineListResponse {
  total: number;
  limit: number;
  offset: number;
  items: CostOptimizerBaselineRow[];
}

export interface CostOptimizerBaselineListParams {
  q?: string;
  model?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  compare_available?: boolean;
  limit?: number;
  offset?: number;
}

export interface CostOptimizerCandidateRequest {
  label?: string;
  model_id: string;
  fallback_model_id?: string | null;
  auto_model_routing?: boolean | null;
  model_routing_policy?: Record<string, unknown> | null;
  task_type?: string | null;
  system_prompt?: string | null;
  user_prompt?: string | null;
  assistant_prompt?: string | null;
  referenced_variables?: Array<{
    name: string;
    value_selector: string[];
  }>;
  parameters?: Record<string, unknown>;
  output_format?: {
    type: 'text' | 'json';
    schema?: Record<string, unknown> | null;
  };
  knowledge?: {
    knowledge_base_ids?: string[];
    top_k?: number;
    score_threshold?: number;
    dedupe_retrieved_context?: boolean;
    retrieved_context_max_chars?: number | null;
    retrieved_context_compression?: 'off' | 'light' | 'strong';
    answer_grounding_check?: 'off' | 'basic' | 'strict';
  };
}

export interface CostOptimizerCompareRequest {
  baseline_id: string;
  candidate: CostOptimizerCandidateRequest;
}

export interface WorkflowGraphCASExpectation {
  expected_graph_hash: string;
  expected_updated_at: string;
}

export interface CostOptimizerApplyRequest extends WorkflowGraphCASExpectation {
  comparison_id: string;
  candidate_settings: CostOptimizerCandidateRequest;
  acknowledge_downstream_warning?: boolean;
}

export interface CostOptimizerRecommendationApplyRequest
  extends WorkflowGraphCASExpectation {
  recommendation_ids: string[];
}

export interface CostOptimizerRecommendationVerifyRequest {
  recommendation_ids: string[];
  baseline_mode: 'latest_success';
  recommendation_policy_version: string;
  recommendation_fingerprint: string;
  node_config_fingerprint: string;
}

export interface CostOptimizerMetricComparison {
  baseline: number | null;
  candidate: number | null;
  delta?: number | null;
  change_rate?: number | null;
}

export interface CostOptimizerQualityEvaluation {
  status: string;
  baseline?: { score?: number | null };
  candidate?: { score?: number | null };
  delta?: number | null;
  dimensions?: Record<
    string,
    {
      baseline?: number | null;
      candidate?: number | null;
      delta?: number | null;
    }
  >;
  confidence?: string | null;
  confidence_score?: number | null;
  safe_summary?: string | null;
  judge_cost?: number | null;
  judge_usage_log_id?: string | null;
}

interface CostOptimizerRecommendationVerificationCommon {
  applied_recommendation_ids?: string[];
  metrics: Record<string, CostOptimizerMetricComparison>;
  quality_evaluation: CostOptimizerQualityEvaluation;
  schema_validation: {
    status: string;
    issues?: Array<{ code?: string; message?: string } | string>;
  };
  downstream_compatibility: CostOptimizerDownstreamCompatibility;
  incurred_cost: {
    candidate_execution_cost?: number | null;
    quality_judge_cost?: number | null;
    total_new_cost?: number | null;
    currency?: string | null;
  };
  apply: {
    allowed: boolean;
    requires_confirmation: boolean;
    reasons: string[];
  };
  verification_context?: {
    node_config_fingerprint?: string | null;
    recommendation_policy_version?: string | null;
    recommendation_fingerprint?: string | null;
  };
}

interface CostOptimizerRecommendationVerificationResult
  extends CostOptimizerRecommendationVerificationCommon {
  verification_status: 'completed' | 'partial' | 'failed';
  comparison_id: string | null;
  candidate_id: string | null;
  baseline: {
    label: string;
    workflow_node_run_id?: string | null;
    executed_at?: string | null;
    model?: string | null;
    deployment_id?: string | null;
    metrics: Record<string, number | null | undefined>;
  };
  candidate: {
    status: string;
    model?: string | null;
    metrics: Record<string, number | null | undefined>;
  };
}

interface CostOptimizerRecommendationVerificationStale
  extends CostOptimizerRecommendationVerificationCommon {
  verification_status: 'stale';
  comparison_id: null;
  candidate_id: null;
  baseline: null;
  candidate: null;
}

export type CostOptimizerRecommendationVerificationResponse =
  | CostOptimizerRecommendationVerificationResult
  | CostOptimizerRecommendationVerificationStale;

export interface ModelRoutingPolicyResponse {
  enabled: boolean;
  status: 'off' | 'collecting' | 'active' | 'refreshing' | 'pending_review' | 'failed';
  policy_id: string | null;
  bootstrap_id?: string | null;
  policy_version: string | null;
  active_policy: JudgeFirstActivePolicy | null;
  learner: ModelRoutingLearnerSummary | null;
  pending_policy: Record<string, unknown> | null;
  refresh: {
    refresh_every_runs: number;
    eligible_runs_since_last_refresh: number;
    next_refresh_after_runs: number;
    last_refresh_result: string | null;
    last_refresh_at: string | null;
  };
  last_decision: {
    selected_model_id: string;
    fallback_model_id: string | null;
    fallback_used: boolean;
    decision_source: string | null;
    reason_code: string | null;
    reason_label: string;
    created_at: string | null;
  } | null;
}

export interface ModelRoutingLearnerSummary {
  id: string;
  status: 'collecting' | 'ready' | 'degraded' | 'stale' | 'failed';
  mode: 'judge_first' | 'local_first';
  task_fingerprint: string;
  judged_request_count: number;
  pending_count: number;
  accepted_count: number;
  rejected_count: number;
  active_version: number | null;
  recent_evaluation: {
    judge_match_rate?: number;
    contract_pass_rate?: number;
    axis_mean_errors?: Record<string, number>;
  };
}

export interface ModelRoutingPolicyPatchResponse
  extends ModelRoutingPolicyResponse {
  graph_hash: string;
  updated_at: string;
}

export interface ModelRoutingPolicyPatchRequest extends WorkflowGraphCASExpectation {
  enabled: boolean;
  refresh_every_runs: number;
  /** 규칙과 매칭되지 않은 입력에 사용하는 사용자가 지정한 기본 모델 */
  default_model_id?: string;
  /** 기본 모델 호출 실패 시 사용하는 사용자가 지정한 대체 모델 */
  fallback_model_id?: string | null;
}

export interface ModelRoutingBootstrapRequest
  extends WorkflowGraphCASExpectation {
  task_description: string;
  default_model_id: string;
  fallback_model_id?: string | null;
}

export interface ModelRoutingBootstrapResponse {
  id: string;
  status: 'ready' | 'generating' | 'failed' | 'stale' | string;
  source: 'history' | 'judge_first';
  task_fingerprint: string;
  task_description: string;
  default_model_id: string;
  fallback_model_id?: string | null;
  generation_summary: Record<string, unknown>;
  stale_reason?: string | null;
  created_at?: string | null;
}

export interface ModelRoutingBootstrapWriteResponse
  extends ModelRoutingBootstrapResponse {
  graph_hash: string;
  updated_at: string;
}

export interface ModelRoutingBootstrapPreview {
  task_fingerprint: string;
  history_mode: 'history' | 'judge_first';
  available_history_count: number;
  excluded_history_count: number;
  excluded_reason_summary: Record<string, number>;
  bootstrap: ModelRoutingBootstrapResponse | null;
}

export interface ModelRoutingPolicyRefreshResponse {
  policy_id: string;
  status: 'refreshing';
  trigger: 'manual_refresh';
  scheduled: boolean;
}

export interface CostOptimizerDownstreamCompatibility {
  state: string;
  label?: string;
  message?: string;
  baseline_downstream_hash?: string | null;
  current_downstream_hash?: string | null;
  first_consumer_status?: string | null;
  contract_check?: {
    status?: string | null;
    checked_node_ids?: string[];
    warnings?: string[];
  };
}

export interface CostOptimizerUsageSummary extends Record<string, unknown> {
  model?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  cost?: number | null;
  total_cost?: number | null;
  cost_unavailable?: boolean;
  latency_ms?: number | null;
  status?: string | null;
}

export interface CostOptimizerCompareResponse {
  comparison_id: string;
  workflow_id: string;
  node_id: string;
  baseline: {
    baseline_id: string;
    label?: string;
    settings?: Record<string, unknown>;
    input?: unknown;
    output?: unknown;
    usage?: CostOptimizerUsageSummary;
    trace?: Record<string, unknown>;
  };
  candidate: {
    label?: string;
    settings?: Record<string, unknown>;
    status: 'success' | 'failed' | 'schema_failed' | string;
    output?: unknown;
    usage?: CostOptimizerUsageSummary;
    schema_validation?: {
      status?: string | null;
      errors?: unknown[];
    };
    latency_ms?: number | null;
    trace?: Record<string, unknown>;
    error_message?: string | null;
  };
  diff?: Record<string, unknown>;
  quality_evaluation?: CostOptimizerQualityEvaluation;
  downstream_compatibility?: CostOptimizerDownstreamCompatibility;
}

export interface CostOptimizerApplyResponse {
  workflow_id: string;
  node_id: string;
  applied: boolean;
  downstream_compatibility: CostOptimizerDownstreamCompatibility;
  graph_hash: string;
  updated_at: string;
}

export interface CostOptimizerExperimentListParams {
  baseline_id?: string;
  date_from?: string;
  date_to?: string;
  created_by?: string;
  candidate_status?: 'success' | 'failed' | 'schema_failed' | 'running' | string;
  model?: string;
  is_applied?: boolean;
  schema_status?: 'not_checked' | 'pass' | 'failed' | string;
  downstream_state?: 'compatible' | 'warning' | 'incompatible' | 'unknown' | string;
  limit?: number;
  offset?: number;
}

export interface CostOptimizerCandidateSummary {
  candidate_id: string;
  name?: string | null;
  status?: string | null;
  model_id?: string | null;
  fallback_model_id?: string | null;
  task_type?: string | null;
  total_cost?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  latency_ms?: number | null;
  output_available?: boolean;
  output?: unknown;
  output_preview?: string | null;
  schema_status?: string | null;
  downstream_state?: string | null;
  quality_evaluation?: CostOptimizerQualityEvaluation;
  is_applied?: boolean;
  created_at?: string | null;
}

export interface CostOptimizerExperimentBaselineSummary {
  baseline_id?: string | null;
  workflow_run_id?: string | null;
  model?: string | null;
  cost?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  latency_ms?: number | null;
  output_available?: boolean;
  output?: unknown;
  output_preview?: string | null;
  output_format?: unknown;
  max_tokens?: number | null;
  temperature?: number | null;
}

export interface CostOptimizerExperimentSummary {
  experiment_id: string;
  workflow_id: string;
  app_id?: string | null;
  node_id: string;
  baseline_node_run_id?: string | null;
  baseline_workflow_run_id?: string | null;
  status?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  baseline_summary?: CostOptimizerExperimentBaselineSummary;
  usage_summary?: CostOptimizerUsageSummary;
  candidates: CostOptimizerCandidateSummary[];
}

export interface CostOptimizerExperimentListResponse {
  total: number;
  limit: number;
  offset: number;
  items: CostOptimizerExperimentSummary[];
}

export type CostOptimizerExperimentCandidateDetail = Omit<
  CostOptimizerExperimentSummary,
  'candidates'
> & {
  candidate: CostOptimizerCandidateSummary;
};

export interface CostOptimizerParameterRecommendation {
  recommendation_type: 'llm_parameter' | string;
  parameter_key: string;
  current_value?: unknown;
  suggested_value?: unknown;
  confidence?: 'low' | 'medium' | 'high' | string;
  risk?: 'low' | 'medium' | 'high' | string;
  reason?: string;
  evidence?: Record<string, unknown>;
  apply_mode?: 'experiment_required' | string;
  candidate_patch?: Record<string, unknown>;
}

export interface CostOptimizerParameterRecommendationsResponse {
  analysis_stage: 'insufficient_logs' | 'recommendations_available' | string;
  policy_version: string;
  recommendation_fingerprint: string;
  recommendations: CostOptimizerParameterRecommendation[];
  warnings?: Array<{
    code?: string;
    message?: string;
  }>;
  profile?: Record<string, unknown>;
}

// 로그 관련 타입 (Backend Schemas와 일치)
export interface WorkflowNodeRun {
  id: string;
  node_id: string;
  node_type: string;
  status: string;
  inputs?: Record<string, any>;
  process_data?: Record<string, any>; // 노드 옵션 스냅샷 (실행 시점 설정)
  outputs?: Record<string, any>;
  trace_metadata?: Record<string, unknown>;
  error_message?: string;
  started_at: string;
  finished_at?: string | null;
  duration?: number;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  app_id?: string | null;
  user_id: string | null;
  status: string;
  trigger_mode: 'manual' | 'scheduler' | 'api' | 'app' | 'webhook';
  inputs?: Record<string, any>;
  outputs?: Record<string, any>;
  error_message?: string;
  started_at: string;
  finished_at?: string | null;
  duration?: number;
  workflow_version?: number;
  deployment_id?: string | null;
  total_tokens?: number;
  total_cost?: number;
  redaction_applied?: boolean;
  pii_detected?: boolean;
  payload_storage_mode?: string;
  node_runs?: WorkflowNodeRun[];
}

// Dashboard Stats Types
export interface StatsSummary {
  totalRuns: number;
  successRate: number;
  avgDuration: number;
  totalCost: number;
  avgTokenPerRun: number;
  avgCostPerRun: number;
}

export interface DailyRunStat {
  date: string;
  count: number;
  total_cost: number;
  total_tokens: number;
}

export interface RunCostStat {
  run_id: string;
  started_at: string;
  total_tokens: number;
  total_cost: number;
}

export interface FailureStat {
  node_id: string;
  node_name: string;
  count: number;
  reason: string;
  rate: string;
}

export interface RecentFailure {
  run_id: string;
  failed_at: string; // ISO date
  node_id: string;
  error_message: string;
}

export interface DashboardStatsResponse {
  summary: StatsSummary;
  runsOverTime: DailyRunStat[];
  minCostRuns: RunCostStat[];
  maxCostRuns: RunCostStat[];
  failureAnalysis: FailureStat[];
  recentFailures: RecentFailure[];
}

export interface WorkflowRunListResponse {
  total: number;
  items: WorkflowRun[];
}

export interface LLMTrace {
  id: string;
  workflow_id?: string | null;
  workflow_run_id: string;
  node_id?: string | null;
  model_id?: string | null;
  model_name?: string | null;
  provider?: string | null;
  credential_id?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost?: number | null;
  latency_ms?: number | null;
  status: string;
  created_at: string;
}

export interface LLMTraceListResponse {
  total: number;
  limit: number;
  offset: number;
  items: LLMTrace[];
}

export interface TopExpensiveModel {
  model_name: string;
  provider_name: string;
  total_cost: number;
  total_tokens: number;
}
