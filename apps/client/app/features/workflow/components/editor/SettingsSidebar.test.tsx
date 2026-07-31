import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { workflowApi } from '../../api/workflowApi';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { SettingsSidebar } from './SettingsSidebar';

vi.mock('../../api/workflowApi', () => ({
  workflowApi: {
    getDeployments: vi.fn(),
  },
}));

vi.mock('../../store/useWorkflowStore', () => ({
  useWorkflowStore: vi.fn(),
}));

const mockedWorkflowApi = vi.mocked(workflowApi);
const mockedUseWorkflowStore = vi.mocked(useWorkflowStore);

const chatbotDeployment = {
  id: 'deployment-1',
  app_id: 'app-1',
  version: 1,
  type: 'chatbot' as const,
  is_active: true,
  created_by: 'user-1',
  created_at: '2026-07-14T00:00:00Z',
  graph_snapshot: {},
  browser_access_policy: {
    contract_version: 'deployment_browser_access.v1' as const,
    embedding: {
      enabled: false,
      parent_origins: [],
    },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedUseWorkflowStore.mockReturnValue({
    isSettingsOpen: true,
    toggleSettings: vi.fn(),
    activeWorkflowId: 'workflow-1',
    nodes: [],
    lastDeployedAt: null,
  } as ReturnType<typeof useWorkflowStore>);
});

afterEach(cleanup);

describe('SettingsSidebar deployment URLs', () => {
  it('renders a public chatbot URL from the deployment list app slug', async () => {
    mockedWorkflowApi.getDeployments.mockResolvedValueOnce([
      { ...chatbotDeployment, url_slug: 'support-bot' },
    ]);

    render(<SettingsSidebar />);

    expect(
      await screen.findByText(
        `${window.location.origin}/embed/chat/support-bot`,
      ),
    ).toBeVisible();
  });

  it('does not render an undefined public chatbot URL when the slug is absent', async () => {
    mockedWorkflowApi.getDeployments.mockResolvedValueOnce([chatbotDeployment]);

    render(<SettingsSidebar />);

    await waitFor(() =>
      expect(mockedWorkflowApi.getDeployments).toHaveBeenCalledWith(
        'workflow-1',
      ),
    );
    expect(
      screen.queryByText(/\/embed\/chat\/undefined/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '직접 링크 복사' }),
    ).not.toBeInTheDocument();
  });
});
