import { apiClient } from '@/lib/apiClient';
import type { WorkflowPermissionSummary } from '../../workflow/types/Api';
import type { AppIcon } from './appApi';
import type { BudgetStatusPayload } from '../../budget/types';

export type ModulePermissionSource = {
  type: 'team' | 'user';
  team_id?: string;
  team_name?: string;
  user_id?: string;
  user_name?: string;
  auth_state: string;
};

export type ModuleRunState =
  | 'running'
  | 'success'
  | 'failed'
  | 'not_started'
  | 'unavailable';

export type ModuleOperationAppSummary = {
  id: string;
  name: string;
  description?: string;
  icon?: AppIcon;
  workflow_id?: string;
  budget_status?: BudgetStatusPayload | null;
  operation_metrics?: {
    current_month_cost: number;
    current_month_workflow_execution_cost: number;
    current_month_agent_builder_cost: number;
    projected_month_cost?: number | null;
    projected_month_workflow_execution_cost?: number | null;
    projected_month_agent_builder_cost?: number | null;
    previous_month_cost: number;
    trend_percent?: number | null;
    usage_data_complete: boolean;
    unresolved_provider_call_count: number;
  } | null;
  owner_name?: string;
  created_at: string;
  updated_at: string;
};

export type ModuleOperationDeployment = {
  state: 'active' | 'inactive' | 'undeployed';
  deployment_id?: string;
  type?: string;
  is_active?: boolean;
};

export type ModuleAutomaticOptimizationStatus =
  | 'disabled'
  | 'collecting'
  | 'ready'
  | 'paused'
  | 'budget_exhausted'
  | 'failed';

export type ModuleAutomaticOptimizationSummary = {
  enabled: boolean;
  status: ModuleAutomaticOptimizationStatus;
  node_count: number;
  collected_runs: number;
  check_every_runs: number;
  validation_spend_usd: number;
  monthly_validation_budget_usd: number;
};

export type ModuleOperationRow = {
  app: ModuleOperationAppSummary;
  permission?: WorkflowPermissionSummary;
  permissionStatus: ModulePermissionStatus;
  permissionSources: ModulePermissionSource[];
  permissionError?: string;
  deployment: ModuleOperationDeployment;
  deploymentState: ModuleOperationDeployment['state'];
  automaticOptimization?: ModuleAutomaticOptimizationSummary | null;
  latestRun: {
    state: ModuleRunState;
    started_at?: string;
    finished_at?: string;
    error_message?: string;
  };
  dataQuality: {
    permissionSourcesUnavailable: boolean;
    latestRunUnavailable: boolean;
  };
};

export type ModulePermissionStatus = 'loaded' | 'failed' | 'not_available';

export type ModuleOperationCapabilityFilter = 'execute' | 'write' | 'manage';

export type ModuleOperationsListParams = {
  q?: string;
  capability?: ModuleOperationCapabilityFilter;
  deployment_state?: ModuleOperationDeployment['state'];
  run_state?: ModuleRunState;
  limit?: number;
  offset?: number;
};

export type ModuleOperationsCostSummary = {
  active_workflow_count: number;
  projected_month_cost: number;
  projected_month_workflow_execution_cost: number;
  projected_month_agent_builder_cost: number;
  usage_data_complete: boolean;
  unresolved_provider_call_count: number;
};

type OperationsApiRow = {
  app: ModuleOperationAppSummary;
  permission?: WorkflowPermissionSummary;
  permission_status?: ModulePermissionStatus;
  permissionStatus?: ModulePermissionStatus;
  permission_error?: string;
  permissionError?: string;
  permission_sources?: ModulePermissionSource[];
  permissionSources?: ModulePermissionSource[];
  deployment?: ModuleOperationDeployment;
  automatic_optimization?: ModuleAutomaticOptimizationSummary | null;
  automaticOptimization?: ModuleAutomaticOptimizationSummary | null;
  latest_run?: ModuleOperationRow['latestRun'];
  latestRun?: ModuleOperationRow['latestRun'];
};

const normalizeOperationsApiRow = (row: OperationsApiRow): ModuleOperationRow => {
  const app = row.app;
  const permissionSources = row.permission_sources || row.permissionSources || [];
  const deployment = row.deployment || { state: 'undeployed' };
  const latestRun = row.latest_run ||
    row.latestRun || {
      state: deployment.deployment_id ? 'unavailable' : 'not_started',
    };

  return {
    app,
    permission: row.permission,
    permissionStatus:
      row.permission_status ||
      row.permissionStatus ||
      (row.permission ? 'loaded' : 'not_available'),
    permissionSources,
    permissionError: row.permission_error || row.permissionError,
    deployment,
    deploymentState: deployment.state,
    automaticOptimization:
      row.automatic_optimization ?? row.automaticOptimization ?? null,
    latestRun,
    dataQuality: {
      permissionSourcesUnavailable: false,
      latestRunUnavailable: latestRun.state === 'unavailable',
    },
  };
};

export const moduleOperationsApi = {
  listModuleOperations: async (
    params: ModuleOperationsListParams = {},
  ): Promise<ModuleOperationRow[]> => {
    const response = await apiClient.get<OperationsApiRow[]>('/apps/operations', {
      params,
    });
    return response.data.map(normalizeOperationsApiRow);
  },

  getModuleOperationsCostSummary:
    async (): Promise<ModuleOperationsCostSummary> => {
      const response = await apiClient.get<ModuleOperationsCostSummary>(
        '/apps/operations/cost-summary',
      );
      return response.data;
    },
};
