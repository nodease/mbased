import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SuccessStep } from './SuccessStep';

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
  },
}));

vi.mock(
  '@/app/features/app/components/AppAuthSecretControl',
  () => ({
    AppAuthSecretControl: () => <div>App Secret lifecycle</div>,
  }),
);

const writeClipboard = vi.fn();
const secretProps = {
  issuedSecret: null,
  onSecretAvailable: vi.fn(),
};

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: writeClipboard },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('SuccessStep', () => {
  it('keeps the webhook secret out of the URL and requires header authentication', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="webhook"
        onClose={vi.fn()}
        result={{
          success: true,
          appId: 'app-1',
          version: 1,
          url_slug: 'incident-hook',
        }}
      />,
    );

    expect(
      screen.getByText('http://localhost:3000/api/v1/hooks/incident-hook'),
    ).toBeVisible();
    expect(screen.getByText('App Secret lifecycle')).toBeVisible();
    expect(screen.queryByText(/\?token=/)).not.toBeInTheDocument();
    expect(screen.queryByText(/통합 URL/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Webhook URL 복사'));
    expect(writeClipboard).toHaveBeenCalledWith(
      'http://localhost:3000/api/v1/hooks/incident-hook',
    );
    expect(screen.queryByText('webhook-secret-value')).not.toBeInTheDocument();
  });

  it('shows a non-blocking preflight warning after a successful deployment', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="api"
        onClose={vi.fn()}
        result={{
          success: true,
          version: 1,
          message:
            '배포 전 검사 경고가 있습니다.\n실행 시 지식 후보가 제한될 수 있습니다.',
        }}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent(
      '배포 전 검사 경고가 있습니다.',
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '실행 시 지식 후보가 제한될 수 있습니다.',
    );
  });

  it('uses an existing App secret only in component memory for REST testing', async () => {
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem');
    const sessionSecret = `test-${Math.random().toString(36).slice(2)}`;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ status: 'ok' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <SuccessStep
        {...secretProps}
        deploymentType="api"
        onClose={vi.fn()}
        result={{
          success: true,
          appId: 'app-1',
          version: 1,
          url_slug: 'existing-app',
        }}
      />,
    );

    expect(
      screen.getByRole('button', { name: 'Secret 입력 후 테스트' }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByLabelText('테스트용 기존 App secret'),
      { target: { value: sessionSecret } },
    );
    fireEvent.click(screen.getByRole('button', { name: '테스트 실행' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/run/existing-app', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionSecret}`,
        },
        credentials: 'include',
        body: JSON.stringify({ inputs: {} }),
      }),
    );
    expect(storageWrite).not.toHaveBeenCalled();
    storageWrite.mockRestore();
  });

  it('public chatbot shows only the anonymous public link', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="chatbot"
        onClose={vi.fn()}
        result={{
          success: true,
          version: 1,
          url_slug: 'onboarding-bot',
          webAppUrl: 'http://localhost:3000/embed/chat/onboarding-bot',
          internalRunUrl:
            'http://localhost:3000/modules/workflow-1/run?deploymentId=deployment-1',
        }}
      />,
    );

    expect(screen.getByText('공개 챗봇 공유 링크')).toBeVisible();
    expect(
      screen.getByText(
        '인증 없이 접근하며 공개 Collection에 연결된 지식만 검색됩니다.',
      ),
    ).toBeVisible();
    expect(screen.queryByText('사내 인증 실행 링크')).not.toBeInTheDocument();
    expect(
      screen.getByText('http://localhost:3000/embed/chat/onboarding-bot'),
    ).toBeVisible();
  });

  it('shows iframe code and the authoritative parent origins only when enabled', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="chatbot"
        onClose={vi.fn()}
        result={{
          success: true,
          version: 2,
          url_slug: 'onboarding-bot',
          webAppUrl: 'http://localhost:3000/embed/chat/onboarding-bot',
          embedUrl: 'http://localhost:3000/embed/chat/onboarding-bot',
          browser_access_policy: {
            contract_version: 'deployment_browser_access.v1',
            embedding: {
              enabled: true,
              parent_origins: ['https://portal.example.com'],
            },
          },
        }}
      />,
    );

    expect(screen.getByText('웹사이트 임베딩 코드')).toBeVisible();
    expect(screen.getByText(/<iframe src=/)).toHaveTextContent(
      'http://localhost:3000/embed/chat/onboarding-bot',
    );
    expect(screen.getByText('https://portal.example.com')).toBeVisible();
  });

  it('keeps a disabled widget limited to its direct link', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="widget"
        onClose={vi.fn()}
        result={{
          success: true,
          version: 1,
          webAppUrl: 'http://localhost:3000/embed/chat/support-widget',
          browser_access_policy: {
            contract_version: 'deployment_browser_access.v1',
            embedding: { enabled: false, parent_origins: [] },
          },
        }}
      />,
    );

    expect(screen.getByText('위젯 직접 링크')).toBeVisible();
    expect(screen.queryByText('웹사이트 임베딩 코드')).not.toBeInTheDocument();
  });

  it('internal chatbot shows only the authenticated run link', () => {
    render(
      <SuccessStep
        {...secretProps}
        deploymentType="internal_chatbot"
        onClose={vi.fn()}
        result={{
          success: true,
          version: 1,
          url_slug: 'onboarding-bot',
          internalRunUrl:
            'http://localhost:3000/modules/workflow-1/run?deploymentId=deployment-1',
        }}
      />,
    );

    expect(screen.queryByText('공개 챗봇 공유 링크')).not.toBeInTheDocument();
    expect(screen.getByText('사내 인증 실행 링크')).toBeVisible();
    expect(
      screen.getByText(
        '로그인한 사용자 권한으로 실행됩니다. 사내 private Knowledge/RAG는 이 링크에서 검증하세요.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        'http://localhost:3000/modules/workflow-1/run?deploymentId=deployment-1',
      ),
    ).toBeVisible();
    expect(screen.queryByText('API Endpoint URL')).not.toBeInTheDocument();
    expect(screen.queryByText('API Secret Key')).not.toBeInTheDocument();
  });
});
