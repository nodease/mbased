const { test, expect } = require('@playwright/test');
const {
  config,
  loginAndOpenWorkflow,
  openAgentBuilder,
  requireEnvironment,
  sendAgentBuilderPrompt,
} = require('./helpers');

const shortcutModifier = process.platform === 'darwin' ? 'Meta' : 'Control';

function workflowSetupGroup(panel) {
  return panel.getByTestId('workflow-result-group').last();
}

function completionStatus(panel) {
  return workflowSetupGroup(panel).locator('[role="status"].bg-emerald-50').last();
}

async function isVisible(locator) {
  return locator.isVisible().catch(() => false);
}

async function selectFirstAvailableOption(select) {
  const options = await select.locator('option').evaluateAll((items) =>
    items.map((item) => ({
      disabled: item.disabled,
      value: item.value,
    })),
  );
  const option = options.find((item) => item.value && !item.disabled);
  if (!option) return false;
  await select.selectOption(option.value);
  return true;
}

async function clickPrimarySetupAction(card) {
  await card.locator('button.bg-blue-600:visible:not([disabled])').first().click();
}

async function resolveCurrentSetupItem(panel) {
  const setup = workflowSetupGroup(panel);
  const cards = setup.getByTestId('agent-builder-parameter-card');
  const cardCount = await cards.count();
  let actionableCard = null;

  for (let index = 0; index < cardCount; index += 1) {
    const card = cards.nth(index);
    const hasAction = await isVisible(
      card
        .locator(
          [
            'button.bg-blue-600:visible:not([disabled])',
            'input:visible:not([disabled])',
            'textarea:visible:not([disabled])',
            'select:visible:not([disabled])',
          ].join(', '),
        )
        .first(),
    );
    if (hasAction) actionableCard = card;
  }

  if (!actionableCard) {
    return null;
  }

  const confirmRecommendation = actionableCard
    .getByRole('button', { name: '추천값 확인' })
    .first();
  if (await isVisible(confirmRecommendation)) {
    await confirmRecommendation.click();
    return 'confirm';
  }

  const deferAction = actionableCard
    .getByRole('button', { name: '나중에 설정' })
    .first();
  if (await isVisible(deferAction)) {
    await deferAction.click();
    return 'defer';
  }

  const select = actionableCard.locator('select:visible:not([disabled])').first();
  if ((await isVisible(select)) && (await selectFirstAvailableOption(select))) {
    await clickPrimarySetupAction(actionableCard);
    return 'set';
  }

  const textarea = actionableCard
    .locator('textarea:visible:not([disabled])')
    .first();
  if (await isVisible(textarea)) {
    await textarea.fill('Agent Builder E2E value');
    await clickPrimarySetupAction(actionableCard);
    return 'set';
  }

  const input = actionableCard
    .locator(
      [
        'input:visible:not([disabled])',
        ':not([type="search"])',
        ':not([type="checkbox"])',
        ':not([type="radio"])',
      ].join(''),
    )
    .first();
  if (await isVisible(input)) {
    const type = await input.getAttribute('type');
    await input.fill(type === 'number' ? '1' : 'Agent Builder E2E value');
    await clickPrimarySetupAction(actionableCard);
    return 'set';
  }

  const checkbox = actionableCard
    .locator('input[type="checkbox"]:visible:not([disabled])')
    .first();
  if (await isVisible(checkbox)) {
    await checkbox.check();
    await clickPrimarySetupAction(actionableCard);
    return 'set';
  }

  const disabledSelect = actionableCard.locator('select:visible[disabled]').first();
  if (await isVisible(disabledSelect)) {
    const secondary = actionableCard
      .locator('button:visible:not([disabled]):not(.bg-blue-600)')
      .last();
    if (await isVisible(secondary)) {
      await secondary.click();
      return 'defer';
    }
  }

  const primary = actionableCard
    .locator('button.bg-blue-600:visible:not([disabled])')
    .first();
  if (await isVisible(primary)) {
    await primary.click();
    return 'confirm';
  }

  const secondary = actionableCard
    .locator('button:visible:not([disabled]):not(.bg-blue-600)')
    .last();
  if (await isVisible(secondary)) {
    await secondary.click();
    return 'skip';
  }

  return null;
}

async function completeSetupFlow(panel, page) {
  await expect(workflowSetupGroup(panel)).toBeVisible({ timeout: 60000 });
  const actions = [];

  for (let attempt = 0; attempt < 8; attempt += 1) {
    if (await isVisible(completionStatus(panel))) return actions;

    const action = await resolveCurrentSetupItem(panel);
    if (!action) {
      await expect(completionStatus(panel)).toBeVisible({ timeout: 60000 });
      return actions;
    }
    actions.push(action);
    await page.waitForTimeout(750);
  }

  await expect(completionStatus(panel)).toBeVisible({ timeout: 60000 });
  return actions;
}

const waitForDraftMutation = (page, action) =>
  page.waitForResponse(
    (response) => {
      const request = response.request();
      if (
        request.method() !== 'POST' ||
        !request.url().includes('/api/v1/workflows/') ||
        !request.url().endsWith('/draft')
      ) {
        return false;
      }
      return request.postDataJSON()?.mutation_context?.action === action;
    },
    { timeout: 60000 },
  );

async function exerciseCompletedUndoRedo(panel, page) {
  await page.locator('.react-flow').first().click({ position: { x: 12, y: 12 } });
  await page.keyboard.press(`${shortcutModifier}+Z`);
  const reopenedCard = workflowSetupGroup(panel)
    .getByTestId('agent-builder-parameter-card')
    .first();
  await expect(reopenedCard).toBeVisible({ timeout: 30000 });

  const cardButton = reopenedCard.locator('button:visible').first();
  if (await isVisible(cardButton)) {
    await cardButton.focus();
    await page.keyboard.press(`${shortcutModifier}+Z`);
    await expect(reopenedCard).toBeVisible();
  }

  await page.locator('.react-flow').first().click({ position: { x: 12, y: 12 } });
  const revertResponsePromise = waitForDraftMutation(page, 'revert');
  await page.keyboard.press(`${shortcutModifier}+Z`);
  expect((await revertResponsePromise).status()).toBe(200);
  await expect(reopenedCard).toHaveCount(0);

  await page.locator('.react-flow').first().click({ position: { x: 12, y: 12 } });
  const redoResponsePromise = waitForDraftMutation(page, 'redo');
  await page.keyboard.press(`${shortcutModifier}+Shift+Z`);
  expect((await redoResponsePromise).status()).toBe(200);
  await expect(completionStatus(panel)).toBeVisible({ timeout: 30000 });

  await page.locator('.react-flow').first().click({ position: { x: 12, y: 12 } });
  const secondRevertResponsePromise = waitForDraftMutation(page, 'revert');
  await page.keyboard.press(`${shortcutModifier}+Z`);
  expect((await secondRevertResponsePromise).status()).toBe(200);
  await expect(reopenedCard).toHaveCount(0);

  await page.locator('.react-flow').first().click({ position: { x: 12, y: 12 } });
  const finalRedoResponsePromise = waitForDraftMutation(page, 'redo');
  await page.keyboard.press(`${shortcutModifier}+Shift+Z`);
  expect((await finalRedoResponsePromise).status()).toBe(200);
}

test('direct-edit model selection, Knowledge selection, save, acknowledgement, and completion', async ({
  page,
}) => {
  requireEnvironment('email', 'password', 'organizationId', 'workflowId');
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await loginAndOpenWorkflow(page, config.workflowId);

  const panel = await openAgentBuilder(page);
  const modelOptionsResponse = await page.request.get(
    `${config.baseUrl}/api/v1/agent-builder/model-options`,
    { headers: { 'X-Organization-Id': config.organizationId } },
  );
  expect(modelOptionsResponse.status()).toBe(200);
  const modelGroups = await modelOptionsResponse.json();
  expect(modelGroups.map((group) => group.provider_name)).toEqual([
    'openai',
    'anthropic',
    'google',
    'llamaparse',
  ]);
  const selectedOption = modelGroups.flatMap((group) => group.options)[0];
  expect(selectedOption).toBeTruthy();

  await panel.locator('header svg.lucide-chevron-down').locator('..').click();
  await panel
    .getByRole('button', {
      name: `${selectedOption.model.name}, ${selectedOption.credential.credential_name}`,
      exact: true,
    })
    .click();

  const prompt =
    '사내 문서 Knowledge Base를 참고하고 결과를 Slack으로 보내는 웹훅 워크플로우를 만들어줘';
  const responsePromise = sendAgentBuilderPrompt(panel, page, prompt);
  const messageRequest = await page.waitForRequest(
    (request) =>
      request.url().includes('/api/v1/agent-builder/sessions/') &&
      request.url().endsWith('/messages') &&
      request.method() === 'POST',
    { timeout: 60000 },
  );
  const messageResponse = await responsePromise;
  expect(messageResponse.status()).toBe(200);
  expect(messageRequest.postDataJSON()).toEqual(
    expect.objectContaining({
      intent_model_selection: {
        credential_id: selectedOption.credential.id,
        model_id: selectedOption.model.id,
      },
    }),
  );
  expect(messageRequest.postDataJSON()).not.toHaveProperty('selected_knowledge_candidate');
  expect(messageRequest.postDataJSON()).not.toHaveProperty('selected_knowledge_candidates');

  const responsePayload = await messageResponse.json();
  expect(responsePayload.knowledge_resolution.timing).toBe('before_graph');
  expect(responsePayload.knowledge_resolution.candidates.length).toBeGreaterThan(0);
  expect(
    responsePayload.clarification_options.some((option) => option.candidate_id),
  ).toBe(false);

  const candidateList = panel.getByTestId('agent-builder-kb-candidate-list').last();
  const candidates = candidateList.locator('input[type="checkbox"]');
  const candidateCount = await candidates.count();
  expect(candidateCount).toBeGreaterThanOrEqual(1);
  expect(candidateCount).toBeLessThanOrEqual(20);
  await candidates.first().check();

  const selectionRequestPromise = page.waitForRequest(
    (request) =>
      request.url().includes('/api/v1/agent-builder/sessions/') &&
      request.url().endsWith('/knowledge-selection') &&
      request.method() === 'POST',
    { timeout: 60000 },
  );
  await panel
    .getByRole('button', { name: '선택한 Knowledge Base로 생성' })
    .click();
  const selectionRequest = await selectionRequestPromise;
  expect(selectionRequest.postDataJSON().selected_candidates).toHaveLength(1);

  await expect(workflowSetupGroup(panel)).toBeVisible({ timeout: 60000 });
  await expect(
    workflowSetupGroup(panel).getByTestId('agent-builder-parameter-card').first(),
  ).toBeVisible({ timeout: 60000 });
  const setupActions = await completeSetupFlow(panel, page);
  expect(setupActions[0]).toBe('confirm');
  expect(setupActions.some((action) => action === 'set' || action === 'defer')).toBe(
    true,
  );

  await expect(panel.getByText('Workflow 생성 완료')).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByText(/Agent Builder 초안 보기/)).toHaveCount(0);

  await exerciseCompletedUndoRedo(panel, page);

  const draftResponse = await page.request.get(
    `${config.baseUrl}/api/v1/workflows/${config.workflowId}/draft`,
    { headers: { 'X-Organization-Id': config.organizationId } },
  );
  expect(draftResponse.status()).toBe(200);
  const savedDraft = await draftResponse.json();
  expect(savedDraft.workflow_id).toBe(config.workflowId);
  expect(savedDraft.graph_hash).toMatch(/^[a-f0-9]{64}$/);
  expect(Date.parse(savedDraft.updated_at)).not.toBeNaN();
  expect(savedDraft.nodes.length).toBeGreaterThan(0);
  expect(pageErrors).toEqual([]);
});
