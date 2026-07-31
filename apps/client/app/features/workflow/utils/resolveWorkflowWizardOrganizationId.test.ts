import { describe, expect, it } from 'vitest';

import type { WorkflowPermissionResponse } from '../types/Api';
import { resolveWorkflowWizardOrganizationId } from './resolveWorkflowWizardOrganizationId';

const createWorkflowAccess = (
  overrides: Partial<WorkflowPermissionResponse> = {},
): WorkflowPermissionResponse => ({
  workflow_id: 'workflow-active',
  organization_id: 'org-active',
  auth_state: 'manager',
  can_read: true,
  can_write: true,
  can_execute: true,
  can_deploy: true,
  can_manage: true,
  sources: [],
  ...overrides,
});

describe('resolveWorkflowWizardOrganizationId', () => {
  it('blocks fallback while workflow access is missing', () => {
    expect(resolveWorkflowWizardOrganizationId(null, 'workflow-active')).toBe(
      null,
    );
  });

  it('blocks fallback when workflow access belongs to another workflow', () => {
    const workflowAccess = createWorkflowAccess({
      workflow_id: 'workflow-previous',
      organization_id: 'org-previous',
    });

    expect(
      resolveWorkflowWizardOrganizationId(workflowAccess, 'workflow-active'),
    ).toBe(null);
  });

  it('uses the workflow organization when access matches the active workflow', () => {
    const workflowAccess = createWorkflowAccess({
      organization_id: 'org-current',
    });

    expect(
      resolveWorkflowWizardOrganizationId(workflowAccess, 'workflow-active'),
    ).toBe('org-current');
  });

  it('allows default fallback for the current legacy workflow without organization', () => {
    const workflowAccess = createWorkflowAccess({
      organization_id: null,
    });

    expect(
      resolveWorkflowWizardOrganizationId(workflowAccess, 'workflow-active'),
    ).toBeUndefined();
  });
});
