import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DeploymentFlowModal } from './DeploymentFlowModal';

vi.mock('@/app/features/app/components/AppAuthSecretControl', () => ({
  AppAuthSecretControl: ({
    appId,
    issuedSecret,
    onSecretAvailable,
    onReadinessChange,
  }: {
    appId: string;
    issuedSecret?: { value: string; version: number } | null;
    onSecretAvailable?: (
      secret: { value: string; version: number } | null,
    ) => void;
    onReadinessChange?: (readiness: 'ready') => void;
  }) => (
    <div>
      <span>Secret 발급 준비: {appId}</span>
      <span>현재 Secret: {issuedSecret?.value || '없음'}</span>
      <button
        type="button"
        onClick={() => {
          onSecretAvailable?.({ value: 'one-time-secret', version: 1 });
          onReadinessChange?.('ready');
        }}
      >
        Secret 발급
      </button>
      <button type="button" onClick={() => onReadinessChange?.('ready')}>
        Secret 상태 확인
      </button>
    </div>
  ),
}));

afterEach(cleanup);

describe('DeploymentFlowModal', () => {
  it('deploys directly without showing the automatic optimization step', async () => {
    const onDeploy = vi.fn().mockResolvedValue({ success: true, version: 1 });

    render(
      <DeploymentFlowModal
        isOpen
        onClose={vi.fn()}
        appId="app-1"
        deploymentType="api"
        llmNodes={[{ id: 'llm-1', title: '티켓 분류' }]}
        onDeploy={onDeploy}
      />,
    );

    expect(screen.getByText('REST API 배포')).toBeVisible();
    expect(screen.getByText('1/2')).toBeVisible();
    expect(screen.getByText('Secret 발급 준비: app-1')).toBeVisible();
    expect(screen.queryByText('운영 비용 자동 최적화')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '배포' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Secret 발급' }));
    fireEvent.click(screen.getByRole('button', { name: '배포' }));

    await waitFor(() =>
      expect(onDeploy).toHaveBeenCalledWith('', {
        enabled: false,
        node_ids: ['llm-1'],
        check_every_runs: 50,
        monthly_validation_budget_usd: 3,
      }),
    );
    expect(screen.getByText('2/2')).toBeVisible();
    expect(screen.queryByText('운영 비용 자동 최적화')).not.toBeInTheDocument();
  });

  it('preserves a one-time App secret through deployment steps in memory', async () => {
    const onDeploy = vi.fn().mockResolvedValue({
      success: true,
      appId: 'app-1',
      version: 1,
      url_slug: 'demo-api',
    });

    const renderModal = (isOpen: boolean) => (
      <DeploymentFlowModal
        isOpen={isOpen}
        onClose={vi.fn()}
        appId="app-1"
        deploymentType="api"
        llmNodes={[]}
        onDeploy={onDeploy}
      />
    );
    const { rerender } = render(renderModal(true));

    fireEvent.click(screen.getByRole('button', { name: 'Secret 발급' }));
    expect(screen.getByText('현재 Secret: one-time-secret')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '배포' }));

    expect(await screen.findByText('배포 성공 (v1)')).toBeVisible();
    expect(screen.getByText('현재 Secret: one-time-secret')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Secret 상태 확인' }));
    expect(screen.getByRole('button', { name: '테스트 실행' })).toBeEnabled();

    rerender(renderModal(false));
    rerender(renderModal(true));
    expect(await screen.findByText('현재 Secret: 없음')).toBeVisible();
  });

  it('requires an explicit history consumer when a chatbot has multiple LLM nodes', async () => {
    const onDeploy = vi.fn().mockResolvedValue({ success: true, version: 1 });

    render(
      <DeploymentFlowModal
        isOpen
        onClose={vi.fn()}
        appId="app-1"
        deploymentType="chatbot"
        llmNodes={[
          { id: 'classifier', title: '분류기' },
          { id: 'answer', title: '최종 답변' },
        ]}
        onDeploy={onDeploy}
      />,
    );

    expect(screen.getByRole('button', { name: '배포' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('대화 기록을 사용할 LLM 노드'), {
      target: { value: 'answer' },
    });
    fireEvent.click(screen.getByRole('button', { name: '배포' }));

    await waitFor(() =>
      expect(onDeploy).toHaveBeenCalledWith(
        '',
        expect.any(Object),
        expect.any(Object),
        {
          contract_version: 'public_chat_conversation.v1',
          history_consumer: { node_id: 'answer', container_path: [] },
        },
      ),
    );
  });
});
