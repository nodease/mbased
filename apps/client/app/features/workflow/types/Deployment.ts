export type DeploymentType =
  | 'api'
  | 'widget'
  | 'chatbot'
  | 'internal_chatbot'
  | 'webapp'
  | 'mcp'
  | 'workflow_node'
  | 'schedule'
  | 'webhook';

export type DeploymentPreflightStatus = 'passed' | 'warning' | 'blocked';
export type DeploymentPreflightAudience =
  | 'anonymous_public'
  | 'authenticated_user'
  | 'workflow_node_inherited';

export interface DeploymentBrowserAccessPolicy {
  contract_version: 'deployment_browser_access.v1';
  embedding: {
    enabled: boolean;
    parent_origins: string[];
  };
}

export interface PublicChatConversationConfig {
  contract_version: 'public_chat_conversation.v1';
  history_consumer: {
    node_id: string;
    container_path: [];
  };
}

export interface DeploymentBrowserAccessRevisionCreate {
  browser_access_policy: DeploymentBrowserAccessPolicy;
  is_active?: boolean;
}

// 입력 변수 스키마 타입
export interface InputVariable {
  name: string;
  type: string;
  label: string;
  required?: boolean;
}

export interface InputSchema {
  variables: InputVariable[];
}

// 출력 변수 스키마 타입
export interface OutputVariable {
  variable: string;
  label: string;
}

export interface OutputSchema {
  outputs: OutputVariable[];
}

export interface DeploymentParameterOptimizationConfig {
  enabled: boolean;
  node_ids: string[];
  check_every_runs: number;
  monthly_validation_budget_usd: number;
}

export type DeploymentParameterOptimizationStatus =
  | 'disabled'
  | 'collecting'
  | 'ready'
  | 'paused'
  | 'budget_exhausted'
  | 'failed';

export interface DeploymentParameterOptimizationSummary {
  enabled: boolean;
  status: DeploymentParameterOptimizationStatus;
  node_ids: string[];
  node_count: number;
  collected_runs: number;
  check_every_runs: number;
  validation_spend_usd: number;
  monthly_validation_budget_usd: number;
}

export interface DeploymentBase {
  type: DeploymentType;
  url_slug?: string;
  description?: string | null;
  config?: Record<string, any>;
  parameter_optimization?: DeploymentParameterOptimizationConfig | null;
  is_active: boolean;
  browser_access_policy?: DeploymentBrowserAccessPolicy | null;
}

export interface DeploymentCreate extends DeploymentBase {
  app_id: string;
  graph_snapshot?: Record<string, any>;
}

export interface DeploymentPreflightRequest extends DeploymentBase {
  app_id: string;
  graph_snapshot?: Record<string, any>;
  audience?: DeploymentPreflightAudience;
}

export interface DeploymentPreflightRequiredAction {
  action: string;
  label: string;
}

export interface DeploymentPreflightResponse {
  status: DeploymentPreflightStatus;
  audience: DeploymentPreflightAudience;
  safe_summary: {
    blocked_reason?: string | null;
    affected_node_count: number;
    affected_kb_count_bucket: string;
    affected_collection_count_bucket?: string;
    candidate_budget_limited?: boolean;
  };
  required_actions: DeploymentPreflightRequiredAction[];
  warnings: string[];
  nodes: Array<{
    node_id?: string | null;
    node_type: string;
    status: DeploymentPreflightStatus;
    reason_codes: string[];
    knowledge_base_count_bucket: string;
    knowledge_collection_count_bucket?: string;
    candidate_budget_limited?: boolean;
  }>;
  normalized_browser_access_policy?: DeploymentBrowserAccessPolicy | null;
}

export interface DeploymentResponse extends DeploymentBase {
  id: string;
  app_id: string;
  version: number;
  created_by: string;
  created_at: string;
  graph_snapshot: Record<string, any>;
  input_schema?: InputSchema | null;
  output_schema?: OutputSchema | null;
}

export interface DeploymentRunInfoResponse {
  deployment_id: string;
  app_id: string;
  workflow_id: string;
  name: string;
  version: number;
  description?: string;
  type: DeploymentType;
  input_schema?: InputSchema | null;
  output_schema?: OutputSchema | null;
}
