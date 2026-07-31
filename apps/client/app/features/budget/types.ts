export type BudgetUsageStatus = 'normal' | 'at_risk' | 'exceeded';

export const budgetStatusLabel: Record<BudgetUsageStatus, string> = {
  normal: '정상',
  at_risk: '위험',
  exceeded: '초과',
};

export const isBudgetAtRisk = (
  status?: BudgetUsageStatus | null,
) => status === 'at_risk' || status === 'exceeded';

export type BudgetStatusPayload = {
  usage_ratio: number;
  status: BudgetUsageStatus;
};

export type WorkflowBudget = {
  workflow_id: string;
  workflow_name?: string;
  monthly_budget_usd: number;
  is_enabled: boolean;
  current_month_cost?: number | null;
  usage_ratio?: number | null;
  status?: BudgetUsageStatus | null;
  usage_data_complete?: boolean | null;
  unresolved_provider_call_count?: number | null;
};

export type WorkflowBudgetUpsertPayload = {
  monthly_budget_usd: number;
  is_enabled: boolean;
};
