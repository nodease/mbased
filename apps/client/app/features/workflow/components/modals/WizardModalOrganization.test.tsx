import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/csrfToken', () => ({
  csrfFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, init),
}));

import { CodeWizardModal } from './CodeWizardModal';
import { PromptWizardModal } from './PromptWizardModal';
import { TemplateWizardModal } from './TemplateWizardModal';

const activeOrganizationId = '11111111-1111-4111-8111-111111111111';
const staleOrganizationId = '22222222-2222-4222-8222-222222222222';

type FetchCall = {
  input: string | URL | Request;
  init?: RequestInit;
};

const fetchCalls = () =>
  (global.fetch as ReturnType<typeof vi.fn>).mock.calls.map(
    ([input, init]) => ({ input, init }) as FetchCall,
  );

const lastPostBody = () => {
  const postCall = fetchCalls().find((call) => call.init?.method === 'POST');
  expect(postCall).toBeTruthy();
  return JSON.parse(String(postCall?.init?.body));
};

const createFetchMock = () =>
  vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return {
        ok: true,
        json: async () => ({
          improved_prompt: 'Better prompt',
          generated_code: 'def main(inputs):\n    return {"result": "ok"}',
          improved_template: 'Hello {{ user_name }}',
        }),
      };
    }

    return {
      ok: true,
      json: async () => ({ has_credentials: true }),
    };
  });

describe('Wizard modals organization scope', () => {
  beforeEach(() => {
    window.localStorage.setItem(
      'moduly_active_organization_id',
      staleOrganizationId,
    );
    global.fetch = createFetchMock() as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('passes organizationId prop to prompt wizard check and improve requests', async () => {
    render(
      <PromptWizardModal
        isOpen
        onClose={vi.fn()}
        promptType="user"
        originalPrompt="Hello"
        organizationId={activeOrganizationId}
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/prompt-wizard/check-credentials?organization_id=${activeOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );

    fireEvent.click(screen.getByTestId('prompt-wizard-submit'));

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/v1/prompt-wizard/improve',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(lastPostBody()).toMatchObject({
      organization_id: activeOrganizationId,
    });
  });

  it('falls back to stored active organization when organizationId prop is omitted', async () => {
    render(
      <PromptWizardModal
        isOpen
        onClose={vi.fn()}
        promptType="user"
        originalPrompt="Hello"
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/prompt-wizard/check-credentials?organization_id=${staleOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );
  });

  it('blocks fallback when caller explicitly passes null organizationId', () => {
    render(
      <PromptWizardModal
        isOpen
        onClose={vi.fn()}
        promptType="user"
        originalPrompt="Hello"
        organizationId={null}
        onApply={vi.fn()}
      />,
    );

    const submitButton = screen.getByTestId('prompt-wizard-submit');
    expect(submitButton).toHaveProperty('disabled', true);
    fireEvent.click(submitButton);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('passes organizationId prop to code wizard check and generate requests', async () => {
    const { container } = render(
      <CodeWizardModal
        isOpen
        onClose={vi.fn()}
        inputVariables={['name']}
        organizationId={activeOrganizationId}
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/code-wizard/check-credentials?organization_id=${activeOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );

    const textarea = container.querySelector('textarea');
    expect(textarea).toBeTruthy();
    fireEvent.change(textarea!, { target: { value: 'Return greeting' } });
    fireEvent.click(screen.getByTestId('code-wizard-submit'));

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/v1/code-wizard/generate',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(lastPostBody()).toMatchObject({
      organization_id: activeOrganizationId,
    });
  });

  it('passes organizationId prop to template wizard check and improve requests', async () => {
    render(
      <TemplateWizardModal
        isOpen
        onClose={vi.fn()}
        originalTemplate="Hello {{ user_name }}"
        registeredVariables={['user_name']}
        organizationId={activeOrganizationId}
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/template-wizard/check-credentials?organization_id=${activeOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );

    fireEvent.click(screen.getByTestId('template-wizard-submit'));

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/v1/template-wizard/improve',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(lastPostBody()).toMatchObject({
      organization_id: activeOrganizationId,
    });
  });

  it('rechecks credentials when prompt wizard organizationId changes', async () => {
    const nextOrganizationId = '33333333-3333-4333-8333-333333333333';
    const { rerender } = render(
      <PromptWizardModal
        isOpen
        onClose={vi.fn()}
        promptType="user"
        originalPrompt="Hello"
        organizationId={activeOrganizationId}
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/prompt-wizard/check-credentials?organization_id=${activeOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );

    rerender(
      <PromptWizardModal
        isOpen
        onClose={vi.fn()}
        promptType="user"
        originalPrompt="Hello"
        organizationId={nextOrganizationId}
        onApply={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/v1/prompt-wizard/check-credentials?organization_id=${nextOrganizationId}`,
        expect.objectContaining({ method: 'GET' }),
      ),
    );
  });
});
