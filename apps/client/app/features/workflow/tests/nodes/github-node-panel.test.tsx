import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { workflowApi } from '../../api/workflowApi';
import { GithubNodePanel } from '../../components/nodes/github/components/GithubNodePanel';
import type { GithubNodeData } from '../../types/Nodes';

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
    <textarea value={value} readOnly />
  ),
}));

const data = (): GithubNodeData => ({
  title: 'GitHub',
  action: 'get_pr',
  api_token: '',
  repo_owner: 'owner',
  repo_name: 'repo',
  pr_number: '1',
  referenced_variables: [],
});

describe('GithubNodePanel secret boundary', () => {
  beforeEach(() => {
    updateNodeDataMock.mockReset();
    vi.mocked(workflowApi.storeNodeSecret).mockReset();
  });

  it('stores the API token as an opaque graph reference', async () => {
    vi.mocked(workflowApi.storeNodeSecret).mockResolvedValue({
      secret_reference:
        'workflow-node-secret://00000000-0000-4000-8000-000000000002',
      configured: true,
    });
    render(<GithubNodePanel nodeId="github-1" data={data()} />);

    fireEvent.change(screen.getByPlaceholderText('ghp_xxxxxxxxxxxx'), {
      target: { value: 'synthetic-input-value' },
    });
    expect(updateNodeDataMock).not.toHaveBeenCalledWith('github-1', {
      api_token: 'synthetic-input-value',
    });
    fireEvent.click(screen.getByRole('button', { name: 'GitHub Token 적용' }));

    expect(await screen.findByText('GitHub Token이 저장되었습니다.')).toBeInTheDocument();
    expect(workflowApi.storeNodeSecret).toHaveBeenCalledWith('workflow-1', {
      node_id: 'github-1',
      node_type: 'githubNode',
      parameter_key: 'api_token',
      secret_value: 'synthetic-input-value',
    });
    expect(updateNodeDataMock).toHaveBeenCalledWith('github-1', {
      api_token:
        'workflow-node-secret://00000000-0000-4000-8000-000000000002',
    });
  });

  it('does not apply a late secret response over newer input', async () => {
    let resolveRequest!: (value: {
      secret_reference: string;
      configured: true;
    }) => void;
    vi.mocked(workflowApi.storeNodeSecret).mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    render(<GithubNodePanel nodeId="github-1" data={data()} />);
    const input = screen.getByPlaceholderText('ghp_xxxxxxxxxxxx');

    fireEvent.change(input, { target: { value: 'first-value' } });
    fireEvent.click(screen.getByRole('button', { name: 'GitHub Token 적용' }));
    fireEvent.change(input, { target: { value: 'newer-value' } });
    await act(async () => {
      resolveRequest({
        secret_reference:
          'workflow-node-secret://00000000-0000-4000-8000-000000000003',
        configured: true,
      });
    });

    expect(input).toHaveValue('newer-value');
    expect(updateNodeDataMock).not.toHaveBeenCalled();
  });
});
