import type { WorkflowPermissionResponse } from '../types/Api';

export type WizardOrganizationId = string | null | undefined;

export const resolveWorkflowWizardOrganizationId = (
  workflowAccess: WorkflowPermissionResponse | null,
  activeWorkflowId: string,
): WizardOrganizationId => {
  if (!workflowAccess || workflowAccess.workflow_id !== activeWorkflowId) {
    return null;
  }

  return workflowAccess.organization_id ?? undefined;
};
