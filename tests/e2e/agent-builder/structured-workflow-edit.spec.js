const { test, expect } = require('@playwright/test');
const {
  config,
  loginAndOpenWorkflow,
  openAgentBuilder,
  requireEnvironment,
  sendAgentBuilderPrompt,
} = require('./helpers');

test('existing GitHub target receives only the requested LLM node through typed operations', async ({
  page,
}) => {
  requireEnvironment(
    'email',
    'password',
    'organizationId',
    'structuredEditWorkflowId',
  );
  await loginAndOpenWorkflow(page, config.structuredEditWorkflowId);
  const panel = await openAgentBuilder(page);
  await panel.getByRole('button', { name: '구조만 생성' }).click();
  const response = await sendAgentBuilderPrompt(
    panel,
    page,
    'GitHub PR 조회 노드 뒤에 LLM 노드를 삽입해줘',
  );

  const payload = await response.json();
  expect(payload.status).toBe('graph_mutation_ready');
  expect(payload.structured_plan.request_type).toBe('modify_workflow');
  expect(payload.graph_mutation.kind).toBe('graph_edit');
  expect(payload).not.toHaveProperty('draft_preview');

  const operations = payload.graph_mutation.operations;
  const addedNodes = operations
    .filter((operation) => operation.op === 'add_node')
    .map((operation) => operation.node);
  expect(addedNodes.map((node) => node.type)).toEqual(['llmNode']);
  const generatedLlmId = addedNodes[0].id;
  const addedEdges = operations
    .filter((operation) => operation.op === 'add_edge')
    .map((operation) => operation.edge);
  expect(addedEdges.map((edge) => [edge.source, edge.target])).toEqual(
    expect.arrayContaining([
      ['smoke-github', generatedLlmId],
      [generatedLlmId, 'smoke-answer'],
    ]),
  );
  expect(
    operations.some(
      (operation) =>
        operation.op === 'remove_edge' && operation.edge_id === 'smoke-github-answer',
    ),
  ).toBe(true);
  await expect(panel.getByText('Workflow 생성 완료')).toBeVisible({
    timeout: 60000,
  });
});
