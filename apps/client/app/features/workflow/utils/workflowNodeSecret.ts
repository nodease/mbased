const WORKFLOW_NODE_SECRET_REFERENCE =
  /^workflow-node-secret:\/\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export const isWorkflowNodeSecretReference = (value: unknown): boolean =>
  typeof value === 'string' && WORKFLOW_NODE_SECRET_REFERENCE.test(value);
