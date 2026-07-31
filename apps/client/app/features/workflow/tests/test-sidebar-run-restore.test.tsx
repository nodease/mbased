import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TestSidebar } from '../components/editor/TestSidebar';
import { useWorkflowStore } from '../store/useWorkflowStore';

const mocks = vi.hoisted(() => ({
  getWorkflowRun: vi.fn(),
  openTestPanel: vi.fn(),
  restoreTestExecution: vi.fn(),
  resetTestExecution: vi.fn(),
  selectTestExecutionNode: vi.fn(),
}));

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setCenter: vi.fn(),
    getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  }),
}));

vi.mock('../api/workflowApi', () => ({
  workflowApi: { getWorkflowRun: mocks.getWorkflowRun },
}));

vi.mock('../store/useWorkflowStore', () => {
  const state = {
    isTestPanelOpen: false,
    toggleTestPanel: vi.fn(),
    openTestPanel: mocks.openTestPanel,
    nodes: [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '문의 분류' },
      },
    ],
    activeWorkflowId: 'workflow-1',
    setNodes: vi.fn(),
    updateNodeData: vi.fn(),
    workflowAccess: { can_execute: true },
    edges: [],
    features: {},
    envVariables: [],
    runtimeVariables: [],
    canonicalDraftMetadata: {
      'workflow-1': {
        workflowId: 'workflow-1',
        graphHash: 'a'.repeat(64),
        updatedAt: '2026-07-14T00:00:00.000Z',
      },
    },
    testExecutionStatus: 'idle',
    testExecutionRunId: null,
    testSelectedNodeId: null,
    testExecutionStartedAt: null,
    testExecutionFinishedAt: null,
    testExecutionResult: null,
    testNodeResults: [],
    testExecutionError: null,
    currentExecutingNodeId: null,
    isTestUploading: false,
    beginTestExecution: vi.fn(),
    setTestUploading: vi.fn(),
    setCurrentExecutingNode: vi.fn(),
    setTestExecutionRunId: vi.fn(),
    selectTestExecutionNode: mocks.selectTestExecutionNode,
    addTestNodeResult: vi.fn(),
    finishTestExecution: vi.fn(),
    failTestExecution: vi.fn(),
    restoreTestExecution: mocks.restoreTestExecution,
    resetTestExecution: mocks.resetTestExecution,
  };
  const useWorkflowStore = Object.assign(vi.fn(() => state), {
    getState: vi.fn(() => state),
  });
  return { useWorkflowStore };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mocks.getWorkflowRun.mockReset();
  mocks.openTestPanel.mockReset();
  mocks.restoreTestExecution.mockReset();
  mocks.resetTestExecution.mockReset();
  mocks.selectTestExecutionNode.mockReset();
  Object.assign(useWorkflowStore.getState(), {
    activeWorkflowId: 'workflow-1',
    isTestPanelOpen: false,
    nodes: [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '문의 분류' },
      },
    ],
    testExecutionStatus: 'idle',
    testExecutionRunId: null,
    testSelectedNodeId: null,
    testExecutionStartedAt: null,
    testExecutionFinishedAt: null,
    testExecutionResult: null,
    testNodeResults: [],
    testExecutionError: null,
    canonicalDraftMetadata: {
      'workflow-1': {
        workflowId: 'workflow-1',
        graphHash: 'a'.repeat(64),
        updatedAt: '2026-07-14T00:00:00.000Z',
      },
    },
  });
  window.history.replaceState({}, '', '/modules/workflow-1');
});

describe('TestSidebar saved run restore', () => {
  it('초기 placeholder workflow에는 저장된 실행 복원 API를 호출하지 않는다', async () => {
    Object.assign(useWorkflowStore.getState(), {
      activeWorkflowId: 'default',
      isTestPanelOpen: true,
    });
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=11111111-1111-1111-1111-111111111111',
    );

    render(<TestSidebar />);

    await act(async () => {});
    expect(mocks.getWorkflowRun).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: '테스트 실행하기' }),
    ).toBeDisabled();
  });

  it('대상 workflow draft가 아직 로딩 중이면 URL 실행 복원을 기다린다', async () => {
    Object.assign(useWorkflowStore.getState(), {
      nodes: [],
      canonicalDraftMetadata: {},
    });
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=10111111-1111-4111-8111-111111111111',
    );

    render(<TestSidebar />);

    await act(async () => {});
    expect(mocks.getWorkflowRun).not.toHaveBeenCalled();
  });

  it('로드가 끝난 빈 draft에서도 URL의 과거 실행을 복원한다', async () => {
    Object.assign(useWorkflowStore.getState(), { nodes: [] });
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=20222222-2222-4222-8222-222222222222',
    );
    mocks.getWorkflowRun.mockResolvedValue({
      id: '20222222-2222-4222-8222-222222222222',
      workflow_id: 'workflow-1',
      user_id: 'user-1',
      status: 'success',
      trigger_mode: 'manual',
      outputs: { answer: '빈 draft 과거 응답' },
      started_at: '2026-07-14T01:00:00.000Z',
      finished_at: '2026-07-14T01:00:01.000Z',
      node_runs: [
        {
          id: 'historical-node-run',
          node_id: 'deleted-node',
          node_type: 'llmNode',
          status: 'success',
          outputs: { text: '삭제된 노드의 과거 결과' },
          started_at: '2026-07-14T01:00:00.000Z',
          finished_at: '2026-07-14T01:00:01.000Z',
        },
      ],
    });

    render(<TestSidebar />);

    await waitFor(() => {
      expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
        'workflow-1',
        '20222222-2222-4222-8222-222222222222',
      );
    });
    expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
      expect.objectContaining({
        runId: '20222222-2222-4222-8222-222222222222',
        status: 'success',
        nodeResults: [
          expect.objectContaining({
            nodeId: 'deleted-node',
            output: { text: '삭제된 노드의 과거 결과' },
          }),
        ],
      }),
    );
  });

  it('새로고침 URL의 testRun을 권한 있는 run 상세 조회로 복원한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=11111111-1111-1111-1111-111111111111&testNode=llm-triage',
    );
    mocks.getWorkflowRun.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      workflow_id: 'workflow-1',
      user_id: 'user-1',
      status: 'success',
      trigger_mode: 'manual',
      outputs: { answer: '복원됨' },
      started_at: '2026-07-14T01:00:00.000Z',
      finished_at: '2026-07-14T01:00:01.000Z',
      node_runs: [],
    });

    render(<TestSidebar />);

    await waitFor(() => {
      expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
        'workflow-1',
        '11111111-1111-1111-1111-111111111111',
      );
    });
    expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
      expect.objectContaining({
        runId: '11111111-1111-1111-1111-111111111111',
        status: 'success',
      }),
    );
  });

  it('실행 기록 생성 전 404를 받으면 제한된 재시도 후 복원한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=22222222-2222-2222-2222-222222222222',
    );
    mocks.getWorkflowRun
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({
        id: '22222222-2222-2222-2222-222222222222',
        workflow_id: 'workflow-1',
        user_id: 'user-1',
        status: 'success',
        trigger_mode: 'manual',
        outputs: { answer: '나중에 기록됨' },
        started_at: '2026-07-14T01:00:00.000Z',
        finished_at: '2026-07-14T01:00:01.000Z',
        node_runs: [],
      });

    render(<TestSidebar />);

    await waitFor(
      () => {
        expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
      },
      { timeout: 1_500 },
    );
    expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
      expect.objectContaining({
        runId: '22222222-2222-2222-2222-222222222222',
        status: 'success',
      }),
    );
  });

  it('URL에 testNode가 없으면 실행 전체 결과를 위해 선택 노드를 비운다', async () => {
    const runId = '28222222-2222-2222-2222-222222222222';
    Object.assign(useWorkflowStore.getState(), {
      testExecutionStatus: 'success',
      testExecutionRunId: runId,
      testSelectedNodeId: 'llm-triage',
      testNodeResults: [
        {
          nodeId: 'llm-triage',
          nodeType: 'llmNode',
          status: 'success',
          duration: 10,
          output: { answer: '노드 결과' },
        },
      ],
    });
    window.history.replaceState(
      {},
      '',
      `/modules/workflow-1?testRun=${runId}`,
    );

    render(<TestSidebar />);

    await waitFor(() => {
      expect(mocks.selectTestExecutionNode).toHaveBeenCalledWith(null);
    });
    expect(mocks.getWorkflowRun).not.toHaveBeenCalled();
  });

  it('복원 기록이 아직 없을 때에도 테스트 실행 패널을 열어 재시도 상태를 보여준다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=29222222-2222-2222-2222-222222222222',
    );
    mocks.getWorkflowRun.mockRejectedValue({ response: { status: 404 } });

    render(<TestSidebar />);

    await waitFor(() => {
      expect(mocks.openTestPanel).toHaveBeenCalledTimes(1);
    });
  });

  it('진행 중인 실행은 실행 중으로 표시하고 완료될 때까지 다시 조회한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=33333333-3333-3333-3333-333333333333',
    );
    mocks.getWorkflowRun
      .mockResolvedValueOnce({
        id: '33333333-3333-3333-3333-333333333333',
        workflow_id: 'workflow-1',
        user_id: 'user-1',
        status: 'running',
        trigger_mode: 'manual',
        outputs: {},
        started_at: '2026-07-14T01:00:00.000Z',
        finished_at: null,
        node_runs: [],
      })
      .mockResolvedValueOnce({
        id: '33333333-3333-3333-3333-333333333333',
        workflow_id: 'workflow-1',
        user_id: 'user-1',
        status: 'success',
        trigger_mode: 'manual',
        outputs: { answer: '완료됨' },
        started_at: '2026-07-14T01:00:00.000Z',
        finished_at: '2026-07-14T01:00:01.000Z',
        node_runs: [],
      });

    render(<TestSidebar />);

    await waitFor(() => {
      expect(mocks.restoreTestExecution).toHaveBeenNthCalledWith(
        1,
        expect.objectContaining({ status: 'running' }),
      );
    });
    await waitFor(
      () => {
        expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
      },
      { timeout: 1_500 },
    );
    expect(mocks.restoreTestExecution).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'success' }),
    );
  });

  it('초기 Worker 지연이 길어도 제한된 복원 시간 안에서 terminal run을 다시 불러온다', async () => {
    vi.useFakeTimers();
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=44444444-4444-4444-4444-444444444444',
    );
    mocks.getWorkflowRun
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({
        id: '44444444-4444-4444-4444-444444444444',
        workflow_id: 'workflow-1',
        user_id: 'user-1',
        status: 'success',
        trigger_mode: 'manual',
        outputs: { answer: '늦게 준비됨' },
        started_at: '2026-07-14T01:00:00.000Z',
        finished_at: '2026-07-14T01:00:12.000Z',
        node_runs: [],
      });

    render(<TestSidebar />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });

    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(7);
    expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
      expect.objectContaining({
        runId: '44444444-4444-4444-4444-444444444444',
        status: 'success',
      }),
    );
  });

  it('브라우저 앞으로/뒤로가기 URL 변경을 새 실행 기록으로 다시 복원한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=55555555-5555-4555-8555-555555555555',
    );
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve({
          id: runId,
          workflow_id: 'workflow-1',
          user_id: 'user-1',
          status: 'success',
          trigger_mode: 'manual',
          outputs: { answer: runId },
          started_at: '2026-07-14T01:00:00.000Z',
          finished_at: '2026-07-14T01:00:01.000Z',
          node_runs: [],
        }),
    );

    render(<TestSidebar />);
    await waitFor(() => {
      expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
        expect.objectContaining({
          runId: '55555555-5555-4555-8555-555555555555',
        }),
      );
    });

    window.history.pushState(
      {},
      '',
      '/modules/workflow-1?testRun=66666666-6666-4666-8666-666666666666',
    );
    window.dispatchEvent(new PopStateEvent('popstate'));

    await waitFor(() => {
      expect(mocks.restoreTestExecution).toHaveBeenCalledWith(
        expect.objectContaining({
          runId: '66666666-6666-4666-8666-666666666666',
        }),
      );
    });
  });

  it('브라우저 뒤로가기로 testRun이 사라지면 이전 실행 결과를 초기화한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=77777777-7777-4777-8777-777777777777',
    );
    mocks.getWorkflowRun.mockResolvedValue({
      id: '77777777-7777-4777-8777-777777777777',
      workflow_id: 'workflow-1',
      user_id: 'user-1',
      status: 'success',
      trigger_mode: 'manual',
      outputs: { answer: '복원됨' },
      started_at: '2026-07-14T01:00:00.000Z',
      finished_at: '2026-07-14T01:00:01.000Z',
      node_runs: [],
    });

    render(<TestSidebar />);
    await waitFor(() => {
      expect(mocks.restoreTestExecution).toHaveBeenCalled();
    });

    window.history.pushState({}, '', '/modules/workflow-1');
    window.dispatchEvent(new PopStateEvent('popstate'));

    await waitFor(() => {
      expect(mocks.resetTestExecution).toHaveBeenCalledTimes(1);
    });
  });
});
