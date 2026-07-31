import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExecutionComparisonPanel } from '../components/editor/ExecutionComparisonPanel';
import { TestSidebar } from '../components/editor/TestSidebar';

const mocks = vi.hoisted(() => ({
  getWorkflowRuns: vi.fn(),
  getWorkflowRun: vi.fn(),
  getWorkflowRunLlmTraces: vi.fn(),
  workflowState: {} as Record<string, unknown>,
}));

vi.mock('../api/workflowApi', () => ({
  workflowApi: mocks,
}));

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setCenter: vi.fn(),
    getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  }),
}));

vi.mock('../store/useWorkflowStore', () => {
  const state = Object.assign(mocks.workflowState, {
    isTestPanelOpen: true,
    toggleTestPanel: vi.fn(),
    openTestPanel: vi.fn(),
    nodes: [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '문의 분류', observability: { status: 'success' } },
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
  });
  const useWorkflowStore = Object.assign(
    vi.fn(() => state),
    {
      getState: vi.fn(() => state),
    },
  );
  return { useWorkflowStore };
});

const runSummary = (id: string, startedAt: string) => ({
  id,
  workflow_id: 'workflow-1',
  user_id: 'user-1',
  status: 'success',
  trigger_mode: 'manual',
  started_at: startedAt,
  finished_at: startedAt,
  duration: 2,
  total_tokens: 120,
  total_cost: 0.0012,
  inputs: {
    customerTier: 'enterprise',
    message: `${id} 고객 문의 내용`,
  },
});

const runDetail = (
  id: string,
  model: string,
  input: string,
  output: string,
) => {
  const isBaseline = id === 'baseline-run';

  return {
    ...runSummary(
      id,
      isBaseline ? '2026-07-13T01:00:00Z' : '2026-07-14T01:00:00Z',
    ),
    duration: isBaseline ? 2 : 1,
    total_tokens: isBaseline ? 120 : 60,
    total_cost: isBaseline ? 0.0012 : 0.0006,
    inputs: { message: input },
    outputs: { answer: output },
    node_runs: [
      {
        id: `${id}-node-run`,
        node_id: 'llm-triage',
        node_type: 'llmNode',
        status: 'success',
        inputs: { message: input },
        outputs: {
          text: output,
          model,
          usage: { total_tokens: id === 'baseline-run' ? 120 : 80 },
          cost: id === 'baseline-run' ? 0.0012 : 0.0006,
        },
        trace_metadata: {
          selected_model: model,
          matched_rule_id: id === 'baseline-run' ? 'high-risk' : 'general',
          policy_version: id === 'baseline-run' ? 'policy-v1' : 'policy-v2',
          policy_source: 'active_deployment',
          included_in_routing_learning: false,
          judge_called: false,
        },
        started_at: '2026-07-14T01:00:00Z',
        finished_at: '2026-07-14T01:00:02Z',
        duration: id === 'baseline-run' ? 2 : 1,
      },
    ],
  };
};

function ComparisonHarness() {
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);
  const [currentRunId, setCurrentRunId] = useState('current-run');
  const [reloadRequestKey, setReloadRequestKey] = useState(0);

  return (
    <>
      <button type="button" onClick={() => setCurrentRunId('current-run-2')}>
        새 현재 실행
      </button>
      <button
        type="button"
        onClick={() => setReloadRequestKey((value) => value + 1)}
      >
        상위 재시도
      </button>
      <ExecutionComparisonPanel
        workflowId="workflow-1"
        nodes={
          [
            {
              id: 'llm-triage',
              type: 'llmNode',
              position: { x: 0, y: 0 },
              data: { title: '문의 분류' },
            },
          ] as never
        }
        baselineRunId={baselineRunId}
        currentRunId={currentRunId}
        reloadRequestKey={reloadRequestKey}
        onBaselineRunIdChange={setBaselineRunId}
      />
    </>
  );
}

function NonLlmComparisonHarness() {
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);

  return (
    <ExecutionComparisonPanel
      workflowId="workflow-1"
      nodes={
        [
          {
            id: 'webhook-start',
            type: 'webhookTrigger',
            position: { x: 0, y: 0 },
            data: { title: '고객 티켓 수신' },
          },
        ] as never
      }
      baselineRunId={baselineRunId}
      currentRunId="current-run"
      onBaselineRunIdChange={setBaselineRunId}
    />
  );
}

function RunningComparisonHarness() {
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);
  const [currentExecutionStatus, setCurrentExecutionStatus] =
    useState('running');

  return (
    <>
      <button
        type="button"
        onClick={() => setCurrentExecutionStatus('success')}
      >
        현재 실행 완료
      </button>
      <ExecutionComparisonPanel
        workflowId="workflow-1"
        nodes={
          [
            {
              id: 'llm-triage',
              type: 'llmNode',
              position: { x: 0, y: 0 },
              data: { title: '문의 분류' },
            },
          ] as never
        }
        baselineRunId={baselineRunId}
        currentRunId="current-run"
        currentExecutionStatus={currentExecutionStatus}
        onBaselineRunIdChange={setBaselineRunId}
      />
    </>
  );
}

function LiveExecutionComparisonHarness() {
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);
  const [nodes, setNodes] = useState([
    {
      id: 'llm-triage',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: { title: '문의 분류', status: 'idle' },
    },
  ]);

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setNodes((currentNodes) =>
            currentNodes.map((node) => ({
              ...node,
              data: { ...node.data, status: 'running' },
            })),
          );
        }}
      >
        실시간 상태 갱신
      </button>
      <ExecutionComparisonPanel
        workflowId="workflow-1"
        nodes={nodes as never}
        baselineRunId={baselineRunId}
        currentRunId="current-run"
        onBaselineRunIdChange={setBaselineRunId}
      />
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/');
  mocks.workflowState.testExecutionStatus = 'success';
  mocks.workflowState.testExecutionResult = { answer: '현재 답변' };
  mocks.workflowState.testNodeResults = [];
  mocks.workflowState.nodes = [
    {
      id: 'llm-triage',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: { title: '문의 분류', observability: { status: 'success' } },
    },
  ];
});

describe('TestSidebar execution comparison', () => {
  it('현재 graph에 없는 과거 노드 실행 결과도 오류 없이 표시한다', () => {
    mocks.workflowState.nodes = [];
    mocks.workflowState.testNodeResults = [
      {
        nodeId: 'deleted-node',
        nodeType: 'llmNode',
        title: '삭제된 노드',
        status: 'success',
        latencyMs: 250,
        output: { text: '과거 실행 결과' },
      },
    ];

    expect(() => render(<TestSidebar />)).not.toThrow();
    expect(screen.getByText('삭제된 노드')).toBeVisible();
  });

  it('실행 중에는 노드 카드와 중복되는 전역 실행 중 제목을 표시하지 않는다', () => {
    mocks.workflowState.testExecutionStatus = 'running';
    mocks.workflowState.testExecutionResult = null;

    render(<TestSidebar />);

    expect(
      screen.queryByRole('heading', { name: '테스트 실행 중...' }),
    ).not.toBeInTheDocument();
  });

  it('실행 비교 중의 노드 대기와 비교 준비 상태를 한 패널에 표시한다', async () => {
    mocks.workflowState.testExecutionStatus = 'running';
    mocks.workflowState.testExecutionResult = null;
    mocks.workflowState.nodes = [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '문의 분류' },
      },
    ];
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockResolvedValue(
      runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변'),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));

    const statusPanel = await screen.findByTestId('execution-progress-status');
    expect(statusPanel).toHaveTextContent(
      '첫 노드 실행 결과를 기다리는 중입니다.',
    );
    expect(statusPanel).toHaveTextContent(
      '실행이 완료되면 비교 결과를 준비합니다.',
    );
    expect(screen.getAllByTestId('execution-progress-status')).toHaveLength(1);
  });

  it('기준 실행 목록은 전체 상태와 전체 방식으로 시작한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({ total: 0, items: [] });

    render(<ComparisonHarness />);

    expect(
      screen.getByRole('combobox', { name: '기준 실행 상태 필터' }),
    ).toHaveValue('all');
    expect(
      screen.getByRole('combobox', { name: '기준 실행 방식 필터' }),
    ).toHaveValue('all');
    await waitFor(() => {
      expect(mocks.getWorkflowRuns).toHaveBeenCalledWith(
        'workflow-1',
        1,
        10,
        {},
      );
    });
  });

  it('목록 재조회 실패 시 기존 실행을 유지하고 상위 재시도로 다시 조회한다', async () => {
    mocks.getWorkflowRuns
      .mockResolvedValueOnce({
        total: 1,
        items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
      })
      .mockRejectedValueOnce(new Error('temporary list failure'))
      .mockResolvedValueOnce({
        total: 2,
        items: [
          runSummary('recovered-run', '2026-07-15T01:00:00Z'),
          runSummary('baseline-run', '2026-07-13T01:00:00Z'),
        ],
      });

    render(<ComparisonHarness />);

    expect(await screen.findByText('baseline-run 고객 문의 내용')).toBeVisible();

    fireEvent.change(
      screen.getByRole('combobox', { name: '기준 실행 상태 필터' }),
      { target: { value: 'failed' } },
    );

    expect(
      await screen.findByText('실행 기록 목록을 불러오지 못했습니다.'),
    ).toBeVisible();
    expect(screen.getByText('baseline-run 고객 문의 내용')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '상위 재시도' }));

    expect(await screen.findByText('recovered-run 고객 문의 내용')).toBeVisible();
    expect(
      screen.queryByText('실행 기록 목록을 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();
    expect(mocks.getWorkflowRuns).toHaveBeenCalledTimes(3);
  });

  it('워크플로우 화면을 벗어나지 않고 TestSidebar를 실행 비교 모드로 전환한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({ total: 0, items: [] });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));

    expect(await screen.findByText('기준 실행 선택')).toBeVisible();
    expect(screen.getByTestId('test-execution-sidebar')).toBeVisible();
    expect(screen.getByTestId('test-execution-sidebar')).toHaveStyle({
      width: '720px',
    });
    expect(screen.queryByText('노드별 실행 결과')).not.toBeInTheDocument();
  });

  it('실행 비교 탭을 누를 때마다 최신 실행 목록을 다시 조회한다', async () => {
    let listRequestCount = 0;
    mocks.getWorkflowRuns.mockImplementation(() => {
      listRequestCount += 1;
      return Promise.resolve(
        listRequestCount === 1
          ? {
              total: 1,
              items: [runSummary('old-run', '2026-07-13T01:00:00Z')],
            }
          : {
              total: 1,
              items: [runSummary('latest-run', '2026-07-14T01:00:00Z')],
            },
      );
    });

    render(<TestSidebar />);
    const comparisonTab = screen.getByRole('button', { name: '실행 비교' });

    fireEvent.click(comparisonTab);
    expect(await screen.findByText('old-run 고객 문의 내용')).toBeVisible();

    fireEvent.click(comparisonTab);

    expect(await screen.findByText('latest-run 고객 문의 내용')).toBeVisible();
    expect(mocks.getWorkflowRuns).toHaveBeenCalledTimes(2);
  });

  it.each(['success', 'failure'])(
    '새 테스트 실행이 %s 상태로 끝나면 열린 실행 비교 목록을 다시 조회한다',
    async (terminalStatus) => {
      mocks.workflowState.testExecutionStatus = 'running';
      mocks.workflowState.testExecutionResult = null;
      let listRequestCount = 0;
      mocks.getWorkflowRuns.mockImplementation(() => {
        listRequestCount += 1;
        return Promise.resolve(
          listRequestCount === 1
            ? {
                total: 1,
                items: [runSummary('old-run', '2026-07-13T01:00:00Z')],
              }
            : {
                total: 1,
                items: [runSummary('completed-run', '2026-07-14T01:00:00Z')],
              },
        );
      });

      const { rerender } = render(<TestSidebar />);
      fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));
      expect(await screen.findByText('old-run 고객 문의 내용')).toBeVisible();

      mocks.workflowState.testExecutionStatus = terminalStatus;
      mocks.workflowState.testExecutionResult =
        terminalStatus === 'success' ? { answer: '새 실행 답변' } : null;
      rerender(<TestSidebar />);

      expect(
        await screen.findByText('completed-run 고객 문의 내용'),
      ).toBeVisible();
      expect(mocks.getWorkflowRuns).toHaveBeenCalledTimes(2);
    },
  );

  it('단일 결과로 전환해도 이전 실행 비교 분석을 유지한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );
    expect(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    );
    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: '단일 결과' }));
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));

    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
  });

  it('최신 실행을 자동 선택하지 않고 사용자가 기준 실행을 명시적으로 고정한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 2,
      items: [
        runSummary('current-run', '2026-07-14T01:00:00Z'),
        runSummary('baseline-run', '2026-07-13T01:00:00Z'),
      ],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);

    expect(await screen.findByText('기준 실행 선택')).toBeVisible();
    expect(mocks.getWorkflowRun).not.toHaveBeenCalled();

    const pinButtons = screen.getAllByRole('button', { name: '기준으로 고정' });
    fireEvent.click(pinButtons[1]);

    await waitFor(() => {
      expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
        'workflow-1',
        'baseline-run',
      );
      expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
        'workflow-1',
        'current-run',
      );
    });
    expect(screen.getByText('기준 실행 고정됨')).toBeVisible();
  });

  it.each([404, 500])(
    '기준으로 고정한 실행 상세가 일시적인 %s 오류여도 재조회 후 비교를 표시한다',
    async (status) => {
      mocks.getWorkflowRuns.mockResolvedValue({
        total: 2,
        items: [
          runSummary('current-run', '2026-07-14T01:00:00Z'),
          runSummary('baseline-run', '2026-07-13T01:00:00Z'),
        ],
      });
      mocks.getWorkflowRun
        .mockRejectedValueOnce({ response: { status } })
        .mockResolvedValueOnce(
          runDetail('current-run', 'gpt-4.1-mini', '현재 문의', '현재 답변'),
        )
        .mockResolvedValueOnce(
          runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변'),
        );
      mocks.getWorkflowRunLlmTraces.mockResolvedValue({
        total: 0,
        limit: 100,
        offset: 0,
        items: [],
      });

      render(<ComparisonHarness />);

      const pinButtons = await screen.findAllByRole('button', {
        name: '기준으로 고정',
      });
      fireEvent.click(pinButtons[1]);

      expect(
        await screen.findByRole('button', {
          name: '문의 분류 노드 상세 비교하기',
        }),
      ).toBeVisible();
      expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(3);
      expect(
        screen.queryByText('실행 비교 데이터를 불러오지 못했습니다.'),
      ).not.toBeInTheDocument();
    },
  );

  it('기준 실행 상세 권한 오류는 재시도하지 않고 원인을 안내한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 2,
      items: [
        runSummary('current-run', '2026-07-14T01:00:00Z'),
        runSummary('baseline-run', '2026-07-13T01:00:00Z'),
      ],
    });
    mocks.getWorkflowRun
      .mockRejectedValueOnce({ response: { status: 403 } })
      .mockResolvedValueOnce(
        runDetail('current-run', 'gpt-4.1-mini', '현재 문의', '현재 답변'),
      );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);

    const pinButtons = await screen.findAllByRole('button', {
      name: '기준으로 고정',
    });
    fireEvent.click(pinButtons[1]);

    expect(
      await screen.findByText('이 실행 기록을 조회할 권한이 없습니다.'),
    ).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
  });

  it('기준 실행을 얇은 식별 행으로 표시하고 요청한 실행의 상세만 펼친다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [
        {
          ...runSummary('baseline-run', '2026-07-13T01:00:00Z'),
          trigger_mode: 'app',
          inputs: {
            customerTier: 'enterprise',
            message: 'SLA 위반 가능성이 있는 장애의 보상안을 검토해 주세요.',
          },
        },
      ],
    });
    mocks.getWorkflowRun.mockResolvedValue(
      runDetail(
        'baseline-run',
        'gpt-4.1',
        'SLA 위반 가능성이 있는 장애의 보상안을 검토해 주세요.',
        '검토 결과',
      ),
    );

    render(<ComparisonHarness />);

    expect(
      await screen.findByText(
        'SLA 위반 가능성이 있는 장애의 보상안을 검토해 주세요.',
      ),
    ).toBeVisible();
    const runRow = screen.getByRole('article', {
      name: /내부 배포 실행$/,
    });
    expect(within(runRow).getByText('내부 배포')).toBeVisible();
    expect(mocks.getWorkflowRun).not.toHaveBeenCalled();
    expect(screen.queryByText('실행 입력')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /실행 상세보기$/ }));

    expect(await screen.findByText('실행 입력')).toBeVisible();
    expect(screen.getByText('실행 지표')).toBeVisible();
    expect(screen.getByText('모델 라우팅')).toBeVisible();
    expect(screen.getByText('gpt-4.1')).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(1);
    expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
      'workflow-1',
      'baseline-run',
    );

    fireEvent.click(screen.getByRole('button', { name: /실행 상세 닫기$/ }));
    expect(screen.queryByText('실행 입력')).not.toBeInTheDocument();
  });

  it('실행 상세가 아직 저장되지 않은 404는 재조회한 뒤 상세를 표시한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce(
        runDetail(
          'baseline-run',
          'gpt-4.1',
          '저장이 지연된 실행 입력',
          '복원된 실행 출력',
        ),
      );

    render(<ComparisonHarness />);

    fireEvent.click(
      await screen.findByRole('button', { name: /실행 상세보기$/ }),
    );

    expect(await screen.findByText('저장이 지연된 실행 입력')).toBeVisible();
    expect(screen.getByText('실행 지표')).toBeVisible();
    expect(
      screen.queryByText('실행 상세를 불러오지 못했습니다. 다시 시도해 주세요.'),
    ).not.toBeInTheDocument();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
  });

  it('실행 상세 조회 실패 뒤 다시 불러오기를 제공한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun
      .mockRejectedValueOnce({ response: { status: 500 } })
      .mockRejectedValueOnce({ response: { status: 500 } })
      .mockResolvedValueOnce(
        runDetail(
          'baseline-run',
          'gpt-4.1',
          '재시도한 실행 입력',
          '재시도한 실행 출력',
        ),
      );

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: /실행 상세보기$/ }),
    );

    expect(await screen.findByText('실행 상세를 불러오지 못했습니다.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));

    expect(await screen.findByText('재시도한 실행 입력')).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(3);
  });

  it('노드별 비교 목록에서 노드 이름과 유형을 함께 표시한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(await screen.findByText('문의 분류')).toBeVisible();
    expect(screen.getByText('LLM')).toBeVisible();
  });

  it('전체 실행 비교에서 수치 지표는 세로로 쌓은 가로 막대로, 상태는 별도 배지로 표시한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(
      await screen.findByRole('heading', { name: '전체 실행 비교' }),
    ).toBeVisible();
    expect(screen.getByTestId('overall-execution-metrics')).toHaveClass(
      'flex-col',
    );
    expect(
      screen.getByTestId('overall-execution-metric-cost'),
    ).toHaveTextContent('50.0% 감소');
    expect(
      screen.getByTestId('overall-execution-metric-cost-baseline-bar'),
    ).toHaveStyle({ width: '100%' });
    expect(
      screen.getByTestId('overall-execution-metric-cost-current-bar'),
    ).toHaveStyle({ width: '50%' });
    expect(
      screen.getByTestId('overall-execution-metric-duration'),
    ).toBeVisible();
    expect(screen.getByTestId('overall-execution-metric-tokens')).toBeVisible();
    expect(screen.getByText('기준 실행: 성공')).toBeVisible();
    expect(screen.getByText('현재 실행: 성공')).toBeVisible();
  });

  it('현재 실행 중에는 비교 조회를 미루고 완료 후 비교한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<RunningComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(
      screen.queryByText('현재 실행이 완료되면 비교 결과를 준비합니다.'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('실행 비교 데이터를 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '현재 실행 완료' }));

    expect(
      await screen.findByRole('heading', { name: '전체 실행 비교' }),
    ).toBeVisible();
  });

  it('현재 테스트를 다시 실행해도 고정한 기준 실행을 유지한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(runId, 'gpt-4.1-mini', '현재 문의', '현재 답변'),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );
    expect(await screen.findByText('기준 실행 고정됨')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '새 현재 실행' }));

    await waitFor(() => {
      expect(mocks.getWorkflowRun).toHaveBeenCalledWith(
        'workflow-1',
        'current-run-2',
      );
    });
    expect(screen.getByText('기준 실행 고정됨')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: '기준으로 고정' }),
    ).not.toBeInTheDocument();
  });

  it('노드 상세 비교에서 다시 테스트하면 전체 노드 비교 목록으로 돌아간다', async () => {
    window.history.replaceState({}, '', '/');
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );
    fireEvent.click(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    );
    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '다시 테스트하기' }));

    expect(
      screen.queryByRole('heading', { name: '문의 분류 상세 비교' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    ).toBeVisible();
    expect(
      new URLSearchParams(window.location.search).has('testCompareNode'),
    ).toBe(false);
  });

  it('노드 상세 비교를 열면 TestSidebar 본문을 맨 위로 올린다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: '실행 비교' }));
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    const content = screen.getByTestId('test-execution-content');
    content.scrollTop = 640;

    fireEvent.click(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    );

    expect(content.scrollTop).toBe(0);
    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
  });

  it('실시간 노드 상태 갱신으로 실행 비교를 다시 로드하지 않는다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<LiveExecutionComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );
    expect(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    );
    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: '실시간 상태 갱신' }));

    expect(mocks.getWorkflowRun).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByText('현재 실행 기록을 동기화하는 중입니다.'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
  });

  it('LLM trace 조회 실패를 비교 근거 누락 경고로 표시한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockRejectedValue({
      response: { status: 500 },
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(
      await screen.findByText('일부 LLM trace를 불러오지 못했습니다.'),
    ).toBeVisible();
    expect(
      screen.queryByText('실행 비교 데이터를 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();
  });

  it('LLM trace 404는 기록 없음으로 처리하고 장애 경고를 표시하지 않는다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockRejectedValue({
      response: { status: 404 },
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    ).toBeVisible();
    expect(
      screen.queryByText('일부 LLM trace를 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('LLM trace 기록 없음: 기준 실행, 현재 실행'),
    ).toBeVisible();
  });

  it('노드 목록은 상태·비용·시간·토큰만 보여주고 상세에서 입력·출력·라우팅을 비교한다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) =>
        Promise.resolve(
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '기존 답변')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '현재 답변',
              ),
        ),
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<ComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    expect(
      await screen.findByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    ).toBeVisible();
    expect(screen.getAllByText('상태').length).toBeGreaterThan(0);
    expect(screen.getAllByText('비용').length).toBeGreaterThan(0);
    expect(screen.getAllByText('실행 시간').length).toBeGreaterThan(0);
    expect(screen.getAllByText('토큰').length).toBeGreaterThan(0);
    expect(screen.queryByText('기존 문의')).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', {
        name: '문의 분류 노드 상세 비교하기',
      }),
    );

    expect(
      screen.getByRole('heading', { name: '문의 분류 상세 비교' }),
    ).toBeVisible();
    expect(screen.getByText('입력 비교')).toBeVisible();
    expect(screen.getByText('기존 문의')).toBeVisible();
    expect(screen.getByText('현재 문의')).toBeVisible();
    expect(screen.getByText('출력 비교')).toBeVisible();
    expect(screen.getByText('기존 답변')).toBeVisible();
    expect(screen.getByText('현재 답변')).toBeVisible();
    expect(screen.getByText('모델 라우팅 비교')).toBeVisible();
    expect(
      screen.queryByText(
        '테스트 실행은 활성 배포 정책을 미리 보지만 정책 학습 횟수에는 포함되지 않습니다.',
      ),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText('gpt-4.1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gpt-4.1-mini').length).toBeGreaterThan(0);

    const detailSections = [
      screen.getByTestId('node-comparison-status-panels'),
      screen.getByText('입력 비교'),
      screen.getByText('모델 라우팅 비교'),
      screen.getByText('출력 비교'),
    ];
    for (let index = 0; index < detailSections.length - 1; index += 1) {
      expect(
        detailSections[index].compareDocumentPosition(
          detailSections[index + 1],
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it('LLM 노드가 아닌 비교 카드에는 비용과 토큰을 표시하지 않는다', async () => {
    mocks.getWorkflowRuns.mockResolvedValue({
      total: 1,
      items: [runSummary('baseline-run', '2026-07-13T01:00:00Z')],
    });
    mocks.getWorkflowRun.mockImplementation(
      (_workflowId: string, runId: string) => {
        const detail =
          runId === 'baseline-run'
            ? runDetail('baseline-run', 'gpt-4.1', '기존 문의', '수신 완료')
            : runDetail(
                'current-run',
                'gpt-4.1-mini',
                '현재 문의',
                '수신 완료',
              );

        return Promise.resolve({
          ...detail,
          node_runs: detail.node_runs.map((nodeRun) => ({
            ...nodeRun,
            node_id: 'webhook-start',
            node_type: 'webhookTrigger',
            outputs: { received: true },
          })),
        });
      },
    );
    mocks.getWorkflowRunLlmTraces.mockResolvedValue({
      total: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    render(<NonLlmComparisonHarness />);
    fireEvent.click(
      await screen.findByRole('button', { name: '기준으로 고정' }),
    );

    const nodeCard = (await screen.findByText('고객 티켓 수신')).closest(
      'article',
    );
    expect(nodeCard).not.toBeNull();
    expect(within(nodeCard as HTMLElement).getAllByText('상태')).toHaveLength(
      2,
    );
    expect(
      within(nodeCard as HTMLElement).getAllByText('실행 시간'),
    ).toHaveLength(2);
    expect(within(nodeCard as HTMLElement).queryAllByText('비용')).toHaveLength(
      0,
    );
    expect(within(nodeCard as HTMLElement).queryAllByText('토큰')).toHaveLength(
      0,
    );
  });
});
