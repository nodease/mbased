import React from 'react';
import { render, screen } from '@testing-library/react';
import type { Node, NodeProps } from '@xyflow/react';
import { describe, expect, it, vi } from 'vitest';

import { SlackPostNode } from '../../components/nodes/slack/components/SlackPostNode';
import type { SlackPostNodeData } from '../../types/Nodes';

vi.mock('../../components/nodes/BaseNode', () => ({
  BaseNode: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock('../../components/ui/ValidationBadge', () => ({
  ValidationBadge: () => <div data-testid="validation-badge" />,
}));

const props = (
  data: Partial<SlackPostNodeData>,
): NodeProps<Node<SlackPostNodeData>> => ({
  id: 'slack-1',
  type: 'slackPostNode',
  selected: false,
  dragging: false,
  selectable: true,
  deletable: true,
  draggable: true,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  data: {
    title: 'Slack',
    message: 'hello',
    referenced_variables: [],
    ...data,
  },
});

describe('SlackPostNode validation summary', () => {
  it('API mode는 URL 없이 token과 channel만으로 configured 상태다', () => {
    render(
      <SlackPostNode
        {...props({
          slackMode: 'api',
          channel: 'C123',
          authConfig: { token: 'fixture-token' },
        })}
      />,
    );

    expect(screen.queryByTestId('validation-badge')).not.toBeInTheDocument();
  });

  it('Webhook mode는 noncanonical URL을 configured로 표시하지 않는다', () => {
    render(
      <SlackPostNode
        {...props({
          slackMode: 'webhook',
          url: 'https://slack.com/api/chat.postMessage',
          authConfig: {},
        })}
      />,
    );

    expect(screen.getByTestId('validation-badge')).toBeInTheDocument();
  });

  it('Webhook mode는 사용하지 않는 legacy channel template을 검증하지 않는다', () => {
    render(
      <SlackPostNode
        {...props({
          slackMode: 'webhook',
          channel: '{{legacy_channel}}',
          url: 'https://hooks.slack.com/services/T/B/secret',
          authConfig: {},
        })}
      />,
    );

    expect(screen.queryByTestId('validation-badge')).not.toBeInTheDocument();
  });

  it('API mode는 attachments-only payload를 configured 상태로 표시한다', () => {
    render(
      <SlackPostNode
        {...props({
          slackMode: 'api',
          channel: 'C123',
          authConfig: { token: 'fixture-token' },
          message: '',
          blocks: '[]',
          attachments: '[{"text":"alert"}]',
        })}
      />,
    );

    expect(screen.queryByTestId('validation-badge')).not.toBeInTheDocument();
  });
});
