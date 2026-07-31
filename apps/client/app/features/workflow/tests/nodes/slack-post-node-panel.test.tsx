import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SlackPostNodePanel } from '../../components/nodes/slack/components/SlackPostNodePanel';
import type { SlackPostNodeData } from '../../types/Nodes';
import { workflowApi } from '../../api/workflowApi';

const updateNodeDataMock = vi.hoisted(() => vi.fn());

vi.mock('@/app/features/workflow/store/useWorkflowStore', () => ({
  useWorkflowStore: () => ({
    activeWorkflowId: 'workflow-1',
    updateNodeData: updateNodeDataMock,
    nodes: [],
    edges: [],
  }),
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: { storeNodeSecret: vi.fn() },
}));

vi.mock('../../components/nodes/ui/VariableTokenEditor', () => ({
  VariableTokenEditor: ({ value }: { value: string }) => (
    <textarea aria-label="Slack 메시지" value={value} readOnly />
  ),
}));

const data = (
  overrides: Partial<SlackPostNodeData> = {},
): SlackPostNodeData => ({
  title: 'Slack',
  slackMode: 'api',
  channel: 'C123',
  message: 'hello',
  authConfig: { token: 'fixture-token' },
  referenced_variables: [],
  ...overrides,
});

describe('SlackPostNodePanel', () => {
  beforeEach(() => {
    updateNodeDataMock.mockReset();
    vi.mocked(workflowApi.storeNodeSecret).mockReset();
  });

  it('stores the Bot Token through the secret endpoint without putting raw input in the graph', async () => {
    vi.mocked(workflowApi.storeNodeSecret).mockResolvedValue({
      secret_reference:
        'workflow-node-secret://00000000-0000-4000-8000-000000000001',
      configured: true,
    });
    const { container } = render(
      <SlackPostNodePanel nodeId="slack-1" data={data({ authConfig: {} })} />,
    );
    const input = container.querySelector('input[type="password"]');
    expect(input).not.toBeNull();

    fireEvent.change(input!, { target: { value: 'synthetic-input-value' } });
    expect(updateNodeDataMock).not.toHaveBeenCalledWith('slack-1', {
      authConfig: { token: 'synthetic-input-value' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Bot Token 적용' }));

    expect(await screen.findByText('Bot Token이 저장되었습니다.')).toBeInTheDocument();
    expect(workflowApi.storeNodeSecret).toHaveBeenCalledWith('workflow-1', {
      node_id: 'slack-1',
      node_type: 'slackPostNode',
      parameter_key: 'bot_token',
      secret_value: 'synthetic-input-value',
    });
    expect(updateNodeDataMock).toHaveBeenCalledWith('slack-1', {
      authConfig: {
        token:
          'workflow-node-secret://00000000-0000-4000-8000-000000000001',
      },
    });
  });

  it('does not apply a late Bot Token response over newer input', async () => {
    let resolveRequest!: (value: {
      secret_reference: string;
      configured: true;
    }) => void;
    vi.mocked(workflowApi.storeNodeSecret).mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    const { container } = render(
      <SlackPostNodePanel nodeId="slack-1" data={data({ authConfig: {} })} />,
    );
    const input = container.querySelector('input[type="password"]');
    expect(input).not.toBeNull();

    fireEvent.change(input!, { target: { value: 'first-value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Bot Token 적용' }));
    fireEvent.change(input!, { target: { value: 'newer-value' } });
    await act(async () => {
      resolveRequest({
        secret_reference:
          'workflow-node-secret://00000000-0000-4000-8000-000000000004',
        configured: true,
      });
    });

    expect(input).toHaveValue('newer-value');
    expect(updateNodeDataMock).not.toHaveBeenCalled();
  });

  it('API endpoint를 webhook credential로 잘못 보존하지 않는다', () => {
    render(
      <SlackPostNodePanel
        nodeId="slack-1"
        data={data({ url: 'https://slack.com/api/chat.postMessage' })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Incoming Webhook' }));

    expect(updateNodeDataMock).toHaveBeenCalledWith('slack-1', {
      slackMode: 'webhook',
      channel: '',
      url: undefined,
      authConfig: {},
      authType: 'none',
    });
  });

  it('보관된 Webhook secret reference를 유효한 설정으로 보존한다', () => {
    const secretReference =
      'workflow-node-secret://00000000-0000-4000-8000-000000000003';
    const { rerender } = render(
      <SlackPostNodePanel
        nodeId="slack-1"
        data={data({
          slackMode: 'webhook',
          url: secretReference,
          authConfig: {},
        })}
      />,
    );

    expect(
      screen.queryByText('Slack 전달 설정을 완료해야 실행할 수 있습니다.'),
    ).not.toBeInTheDocument();

    rerender(
      <SlackPostNodePanel
        nodeId="slack-1"
        data={data({
          slackMode: 'api',
          url: secretReference,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Incoming Webhook' }));

    expect(updateNodeDataMock).toHaveBeenCalledWith('slack-1', {
      slackMode: 'webhook',
      channel: '',
      url: secretReference,
      authConfig: {},
      authType: 'none',
    });
  });

  it('blocks 안의 미등록 template 변수도 경고한다', () => {
    render(
      <SlackPostNodePanel
        nodeId="slack-1"
        data={data({
          blocks:
            '[{"type":"section","text":{"type":"plain_text","text":"{{block_value}}"}}]',
        })}
      />,
    );

    expect(screen.getByText('block_value')).toBeInTheDocument();
  });

  it('API mode의 legacy URL과 잘못된 authType을 migration 경고로 표시한다', () => {
    render(
      <SlackPostNodePanel
        nodeId="slack-1"
        data={data({
          url: 'https://example.invalid/slack',
          authType: 'none',
        })}
      />,
    );

    expect(
      screen.getByText('기존 HTTP 설정은 Slack 전송에 사용되지 않습니다.'),
    ).toBeInTheDocument();
  });

  it('attachments JSON을 canonical 전송 필드로 수정한다', () => {
    render(<SlackPostNodePanel nodeId="slack-1" data={data()} />);

    fireEvent.change(screen.getByLabelText('Slack 첨부 JSON'), {
      target: { value: '[{"text":"alert"}]' },
    });

    expect(updateNodeDataMock).toHaveBeenCalledWith('slack-1', {
      attachments: '[{"text":"alert"}]',
    });
  });
});
