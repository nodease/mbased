import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MailNodePanel } from '../../components/nodes/mail/components/MailNodePanel';
import { MailAcknowledgeNodePanel } from '../../components/nodes/mail/components/MailAcknowledgeNodePanel';
import type {
  MailAcknowledgeNodeData,
  MailNodeData,
} from '../../types/Nodes';

const updateNodeDataMock = vi.hoisted(() => vi.fn());
const listAvailableMock = vi.hoisted(() => vi.fn());
const startGoogleOAuthMock = vi.hoisted(() => vi.fn());

vi.mock('@/app/features/workflow/store/useWorkflowStore', () => ({
  useWorkflowStore: () => ({
    updateNodeData: updateNodeDataMock,
    nodes: [],
    edges: [],
  }),
}));

vi.mock('../../api/mailCredentialApi', () => ({
  mailCredentialApi: {
    listAvailable: listAvailableMock,
    startGoogleOAuth: startGoogleOAuthMock,
  },
}));

vi.mock('../../components/ui/RoundedSelect', () => ({
  RoundedSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (value: string) => void;
    options: Array<{ label: string; value: string }>;
    placeholder: string;
  }) => (
    <select
      aria-label="연결 계정"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('../../components/nodes/ui/VariableTokenEditor', () => ({
  VariableTokenEditor: () => <textarea aria-label="메일 검색 키워드" />,
}));

vi.mock('../../components/nodes/ui/VariableSelectorSlot', () => ({
  VariableSelectorSlot: ({ label }: { label: string }) => <div>{label}</div>,
}));

const data = (): MailNodeData => ({
  title: '메일 검색',
  credential_id: null,
  folder: 'INBOX',
  unread_only: true,
  mark_as_read: false,
  referenced_variables: [],
});

describe('MailNodePanel credential reference', () => {
  beforeEach(() => {
    updateNodeDataMock.mockReset();
    listAvailableMock.mockResolvedValue([
      {
        id: 'credential-1',
        credential_name: '업무 메일',
        provider: 'gmail',
        auth_type: 'app_password',
        email_preview: 'm***@example.com',
        status: 'active',
      },
    ]);
    startGoogleOAuthMock.mockResolvedValue({
      authorization_url: 'https://accounts.example.test/oauth',
    });
  });

  it('durable 처리 모드에서는 즉시 읽음 처리를 해제한다', async () => {
    render(
      <MailNodePanel
        nodeId="mail-1"
        data={{ ...data(), mark_as_read: true }}
      />,
    );

    const processingMode = await screen.findByDisplayValue('검색만');
    fireEvent.change(processingMode, { target: { value: 'durable' } });

    expect(updateNodeDataMock).toHaveBeenCalledWith('mail-1', {
      processing_mode: 'durable',
      mark_as_read: false,
    });
  });

  it('Gmail OAuth 연결은 서버가 발급한 authorization URL만 연다', async () => {
    const replace = vi.fn();
    const popup = {
      opener: window,
      location: { replace },
      close: vi.fn(),
    } as unknown as Window;
    const open = vi.spyOn(window, 'open').mockImplementation(() => popup);
    render(<MailNodePanel nodeId="mail-1" data={data()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Gmail OAuth 연결' }));

    await waitFor(() => expect(startGoogleOAuthMock).toHaveBeenCalledOnce());
    expect(open).toHaveBeenCalledWith(
      'about:blank',
      'gmail-oauth',
      'popup,width=560,height=720',
    );
    expect(replace).toHaveBeenCalledWith('https://accounts.example.test/oauth');
    open.mockRestore();
  });

  it('password 입력 없이 safe credential reference만 저장한다', async () => {
    render(<MailNodePanel nodeId="mail-1" data={data()} />);

    await waitFor(() =>
      expect(
        screen.getByRole('option', { name: /업무 메일/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText('비밀번호')).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText('앱 비밀번호'),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getAllByRole('combobox')[0], {
      target: { value: 'credential-1' },
    });

    expect(updateNodeDataMock).toHaveBeenCalledWith('mail-1', {
      credential_id: 'credential-1',
      configuration_state: 'resolved',
    });
  });
});

describe('MailAcknowledgeNodePanel required effects', () => {
  it('복수 effect를 표시하고 개별 삭제 시 나머지 selector를 보존한다', () => {
    const acknowledgeData: MailAcknowledgeNodeData = {
      title: '처리 완료',
      processing_ref_selector: ['mail', 'processing_ref'],
      required_effect_ref_selectors: [
        ['draft-1', 'draft_ref'],
        ['draft-2', 'draft_ref'],
      ],
    };

    render(
      <MailAcknowledgeNodePanel nodeId="ack-1" data={acknowledgeData} />,
    );

    expect(screen.getByText('필수 Draft effect 1')).toBeInTheDocument();
    expect(screen.getByText('필수 Draft effect 2')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '필수 Draft effect 2 제거' }),
    );

    expect(updateNodeDataMock).toHaveBeenCalledWith('ack-1', {
      required_effect_ref_selectors: [['draft-1', 'draft_ref']],
    });
  });
});
