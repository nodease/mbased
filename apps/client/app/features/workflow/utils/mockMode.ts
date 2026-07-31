export const MOCK_WORKFLOW_ID = 'mock';

const isWorkflowMockEnabled = () => process.env.NODE_ENV !== 'production';

export const isMockWorkflowId = (workflowId?: string | null) =>
  isWorkflowMockEnabled() && workflowId === MOCK_WORKFLOW_ID;

export const isMockWorkflowPath = (pathname?: string | null) =>
  isWorkflowMockEnabled() && pathname === `/modules/${MOCK_WORKFLOW_ID}`;
