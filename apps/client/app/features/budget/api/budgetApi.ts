import { apiClient } from '@/lib/apiClient';
import type { WorkflowBudget, WorkflowBudgetUpsertPayload } from '../types';

export const budgetApi = {
  getWorkflowBudget: async (workflowId: string): Promise<WorkflowBudget> => {
    const response = await apiClient.get(
      `/admin/workflow-budgets/${workflowId}`,
    );
    return response.data;
  },

  upsertWorkflowBudget: async (
    workflowId: string,
    payload: WorkflowBudgetUpsertPayload,
  ): Promise<WorkflowBudget> => {
    const response = await apiClient.put(
      `/admin/workflow-budgets/${workflowId}`,
      payload,
    );
    return response.data;
  },
};
