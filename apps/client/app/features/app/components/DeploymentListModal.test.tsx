import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { appApi, type Deployment } from '../api/appApi';
import DeploymentListModal from './DeploymentListModal';

vi.mock('../api/appApi', () => ({
  appApi: {
    getDeployments: vi.fn(),
    toggleDeployment: vi.fn(),
    createBrowserAccessRevision: vi.fn(),
  },
}));

const chatbotDeployment: Deployment = {
  id: 'deployment-1',
  app_id: 'app-1',
  version: 1,
  type: 'chatbot',
  description: 'public chatbot',
  is_active: true,
  created_at: '2026-07-14T00:00:00Z',
  created_by: 'user-1',
  graph_snapshot: {},
  browser_access_policy: null,
};

const mockedAppApi = vi.mocked(appApi);

beforeEach(() => {
  vi.clearAllMocks();
  mockedAppApi.getDeployments.mockResolvedValue([chatbotDeployment]);
  mockedAppApi.createBrowserAccessRevision.mockResolvedValue({
    ...chatbotDeployment,
    id: 'deployment-2',
    version: 2,
    is_active: false,
    browser_access_policy: {
      contract_version: 'deployment_browser_access.v1',
      embedding: {
        enabled: true,
        parent_origins: ['https://portal.example.com'],
      },
    },
  });
});

afterEach(cleanup);

describe('DeploymentListModal browser access revision', () => {
  it('creates a new inactive revision instead of mutating or toggling the source', async () => {
    render(
      <DeploymentListModal
        appId="app-1"
        appName="Support bot"
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(
      await screen.findByRole('button', {
        name: '버전 1 브라우저 접근 정책 새 버전',
      }),
    );
    fireEvent.click(
      screen.getByRole('switch', { name: '외부 사이트에 삽입 허용' }),
    );
    fireEvent.change(screen.getByLabelText('부모 origin 1'), {
      target: { value: 'https://portal.example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: '새 비활성 버전 만들기' }),
    );

    await waitFor(() =>
      expect(mockedAppApi.createBrowserAccessRevision).toHaveBeenCalledWith(
        'deployment-1',
        {
          browser_access_policy: {
            contract_version: 'deployment_browser_access.v1',
            embedding: {
              enabled: true,
              parent_origins: ['https://portal.example.com'],
            },
          },
          is_active: false,
        },
      ),
    );
    expect(mockedAppApi.toggleDeployment).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        '새 비활성 버전 v2을 만들었습니다. 검토 후 활성화하세요.',
      ),
    ).toBeVisible();
  });

  it('does not expose browser access controls for an internal chatbot', async () => {
    mockedAppApi.getDeployments.mockResolvedValueOnce([
      { ...chatbotDeployment, type: 'internal_chatbot' },
    ]);

    render(
      <DeploymentListModal
        appId="app-1"
        appName="Internal bot"
        onClose={vi.fn()}
      />,
    );

    await screen.findByText('버전 1');
    expect(
      screen.queryByRole('button', {
        name: /브라우저 접근 정책 새 버전/,
      }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/iframe 표시/)).not.toBeInTheDocument();
  });

  it('marks an inactive enabled revision as pending activation', async () => {
    mockedAppApi.getDeployments.mockResolvedValueOnce([
      {
        ...chatbotDeployment,
        is_active: false,
        browser_access_policy: {
          contract_version: 'deployment_browser_access.v1',
          embedding: {
            enabled: true,
            parent_origins: ['https://portal.example.com'],
          },
        },
      },
    ]);

    render(
      <DeploymentListModal
        appId="app-1"
        appName="Support bot"
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByText('허용 origin 1개 · 활성화 후 적용'),
    ).toBeVisible();
    expect(screen.queryByText(/집행 중/)).not.toBeInTheDocument();
  });
});
