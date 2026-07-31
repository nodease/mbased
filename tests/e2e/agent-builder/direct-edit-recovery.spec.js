const { test, expect } = require('@playwright/test');
const {
  config,
  loginAndOpenWorkflow,
  openAgentBuilder,
  requireEnvironment,
  sendAgentBuilderPrompt,
} = require('./helpers');

test('direct-edit save response loss recovers without Preview or duplicate apply', async ({
  page,
}) => {
  requireEnvironment('email', 'password', 'organizationId', 'workflowId');
  await loginAndOpenWorkflow(page, config.workflowId);
  const panel = await openAgentBuilder(page);
  await panel.getByRole('button', { name: '구조만 생성' }).click();

  let interruptedSave = false;
  await page.route(`**/api/v1/workflows/${config.workflowId}/draft`, async (route) => {
    if (route.request().method() !== 'POST' || interruptedSave) {
      await route.continue();
      return;
    }
    interruptedSave = true;
    await route.fetch();
    await route.abort('failed');
  });

  const response = await sendAgentBuilderPrompt(
    panel,
    page,
    '입력 노드와 응답 노드로 구성된 새 워크플로우를 만들어줘',
  );
  expect(response.status()).toBe(200);
  await expect(panel.getByText(/저장 결과 확인 중|Workflow 생성 완료/).last()).toBeVisible({
    timeout: 60000,
  });
  await page.unroute(`**/api/v1/workflows/${config.workflowId}/draft`);
  await page.reload();
  await expect(page.locator('.react-flow')).toBeVisible({ timeout: 30000 });
  const restoredPanel = await openAgentBuilder(page);
  await expect(restoredPanel.getByText('Workflow 생성 완료')).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByText(/Agent Builder 초안 보기/)).toHaveCount(0);
});
