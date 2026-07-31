import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TestSidebar } from '../components/editor/TestSidebar';

const mocks = vi.hoisted(() => ({
  getWorkflowRun: vi.fn(),
  getWorkflowRunLlmTraces: vi.fn(),
}));

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setCenter: vi.fn(),
    getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  }),
}));

vi.mock('../api/workflowApi', () => ({
  workflowApi: mocks,
}));

vi.mock('../store/useWorkflowStore', () => {
  const state = {
    isTestPanelOpen: true,
    toggleTestPanel: vi.fn(),
    openTestPanel: vi.fn(),
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
    testExecutionStatus: 'success',
    testExecutionRunId: 'current-run',
    testSelectedNodeId: null,
    testExecutionStartedAt: 1_000,
    testExecutionFinishedAt: 2_000,
    testExecutionResult: { answer: '현재 답변' },
    testNodeResults: [],
    testExecutionError: null,
    currentExecutingNodeId: null,
    isTestUploading: false,
    beginTestExecution: vi.fn(),
    setTestUploading: vi.fn(),
    setCurrentExecutingNode: vi.fn(),
    setTestExecutionRunId: vi.fn(),
    selectTestExecutionNode: vi.fn(),
    addTestNodeResult: vi.fn(),
    finishTestExecution: vi.fn(),
    failTestExecution: vi.fn(),
    restoreTestExecution: vi.fn(),
    resetTestExecution: vi.fn(),
  };
  const useWorkflowStore = Object.assign(vi.fn(() => state), {
    getState: vi.fn(() => state),
  });
  return { useWorkflowStore };
});

const runDetail = (id: string, model: string) => ({
  id,
  workflow_id: 'workflow-1',
  user_id: 'user-1',
  status: 'success',
  trigger_mode: 'manual',
  started_at: '2026-07-14T01:00:00Z',
  finished_at: '2026-07-14T01:00:02Z',
  duration: 2,
  total_tokens: 120,
  total_cost: 0.0012,
  inputs: { message: id === 'baseline-run' ? '기준 문의' : '현재 문의' },
  outputs: { answer: id === 'baseline-run' ? '기준 답변' : '현재 답변' },
  node_runs: [
    {
      id: `${id}-node-run`,
      node_id: 'llm-triage',
      node_type: 'llmNode',
      status: 'success',
      inputs: { message: id === 'baseline-run' ? '기준 문의' : '현재 문의' },
      outputs: {
        text: id === 'baseline-run' ? '기준 답변' : '현재 답변',
        model,
        usage: { total_tokens: id === 'baseline-run' ? 120 : 80 },
        cost: id === 'baseline-run' ? 0.0012 : 0.0006,
      },
      started_at: '2026-07-14T01:00:00Z',
      finished_at: '2026-07-14T01:00:02Z',
      duration: 2,
    },
  ],
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/modules/workflow-1');
});

describe('TestSidebar comparison restore', () => {
  it('보고 화면에서 돌아온 URL의 비교 상태와 선택 상세를 복원한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1?testRun=current-run&testComparison=1&comparisonBaseline=baseline-run&comparisonNode=llm-triage',
    );
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1')
            : runDetail('current-run', 'gpt-4.1-mini'),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<TestSidebar />);

    expect(
      await screen.findByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
  });
});
