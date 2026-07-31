import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { appApi } from '@/app/features/app/api/appApi';

import { WebhookTriggerNodePanel } from './WebhookTriggerNodePanel';

const updateNodeData = vi.fn();
const writeClipboard = vi.fn();

vi.mock('../../../../store/useWorkflowStore', () => ({
  useWorkflowStore: (selector: (state: unknown) => unknown) =>
    selector({
      updateNodeData,
      workflows: [{ id: 'workflow-1', appId: 'app-1' }],
      activeWorkflowId: 'workflow-1',
    }),
}));

vi.mock('@/app/features/app/api/appApi', () => ({
  appApi: {
    getApp: vi.fn(),
  },
}));

vi.mock(
  '@/app/features/app/components/AppAuthSecretControl',
  () => ({
    AppAuthSecretControl: () => <div>App Secret lifecycle</div>,
  }),
);

vi.mock('@/app/features/workflow/api/webhookApi', () => ({
  webhookApi: {
    startCapture: vi.fn(),
    getCaptureStatus: vi.fn(),
    cancelCapture: vi.fn().mockResolvedValue({ status: 'cancelled' }),
  },
}));

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: writeClipboard },
  });
  vi.mocked(appApi.getApp).mockResolvedValue({
    id: 'app-1',
    name: 'Incident hook',
    icon: { type: 'emoji', content: 'hook', background_color: '#ffffff' },
    url_slug: 'incident-hook',
    is_market: false,
    created_at: '2026-07-14T00:00:00Z',
    updated_at: '2026-07-14T00:00:00Z',
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WebhookTriggerNodePanel', () => {
  it('shows a header-authenticated endpoint without creating an integrated secret URL', async () => {
    render(
      <WebhookTriggerNodePanel
        nodeId="webhook-node"
        data={{
          title: 'Webhook Trigger',
          provider: 'custom',
          variable_mappings: [],
        }}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByDisplayValue(
          'http://localhost:3000/api/v1/hooks/incident-hook',
        ),
      ).toBeVisible();
    });

    expect(screen.getByText('App Secret lifecycle')).toBeVisible();
    expect(screen.queryByText('통합 URL')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(/\?token=/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('URL 복사'));
    expect(writeClipboard).toHaveBeenCalledWith(
      'http://localhost:3000/api/v1/hooks/incident-hook',
    );
    expect(screen.queryByText('webhook-secret-value')).not.toBeInTheDocument();
  });
});
