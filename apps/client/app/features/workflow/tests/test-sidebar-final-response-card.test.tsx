import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  FinalResponseCard,
  TestSidebar,
  TEST_INPUT_CLASS_NAME,
} from '../components/editor/TestSidebar';
import type { FinalResponsePreview } from '../utils/testExecutionFinalResponse';
import { agentBuilderApi } from '../api/agentBuilderApi';
import { workflowApi } from '../api/workflowApi';
import { useWorkflowStore } from '../store/useWorkflowStore';
import {
  acquireWorkflowDraftSave,
  clearWorkflowDraftSaveCoordinatorForTests,
  getWorkflowDraftSaveOwner,
} from '../utils/workflowDraftSaveCoordinator';

vi.mock('../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
    executeWorkflowStream: vi.fn(),
  },
}));

vi.mock('../api/agentBuilderApi', () => ({
  agentBuilderApi: {
    getSession: vi.fn(),
  },
}));

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setCenter: vi.fn(),
    getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  }),
}));

vi.mock('../store/useWorkflowStore', () => {
  const canonicalDraftMetadata = {
    'workflow-1': {
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-14T00:00:00Z',
    },
  };
  const state = {
    isTestPanelOpen: true,
    toggleTestPanel: vi.fn(),
    nodes: [
      {
        id: 'start',
        type: 'startNode',
        data: {
          variables: [
            {
              id: 'question-variable',
              name: 'question',
              label: '질문',
              type: 'paragraph',
              required: false,
              placeholder: '질문을 입력하세요',
            },
          ],
        },
      },
    ],
    activeWorkflowId: 'workflow-1',
    setNodes: vi.fn(),
    updateNodeData: vi.fn(),
    updateNodeExecutionData: vi.fn(),
    resetNodeExecutionData: vi.fn(),
    setHasUnsavedChanges: vi.fn((value: boolean) => {
      state.hasUnsavedChanges = value;
    }),
    workflowAccess: { can_execute: true },
    edges: [],
    features: {},
    envVariables: [],
    runtimeVariables: [],
    hasUnsavedChanges: true,
    isAgentBuilderMutationSaving: false,
    testExecutionStatus: 'idle',
    testExecutionStartedAt: null,
    testExecutionFinishedAt: null,
    testExecutionResult: null,
    testNodeResults: [],
    testExecutionError: null,
    currentExecutingNodeId: null,
    isTestUploading: false,
    beginTestExecution: vi.fn(),
    setTestExecutionRunId: vi.fn(),
    setTestUploading: vi.fn(),
    setCurrentExecutingNode: vi.fn(),
    addTestNodeResult: vi.fn(),
    finishTestExecution: vi.fn(),
    failTestExecution: vi.fn(),
    resetTestExecution: vi.fn(),
    ingestCanonicalDraftMetadata: vi.fn(),
    canonicalDraftMetadata,
    getCanonicalDraftMetadata: vi.fn(
      (workflowId: string) =>
        canonicalDraftMetadata[
          workflowId as keyof typeof canonicalDraftMetadata
        ] ?? null,
    ),
    undoStack: [
      {
        nodes: [],
        edges: [],
        agentBuilderHistory: { sessionId: 'session-1' },
      },
    ],
  };
  const useWorkflowStore = Object.assign(
    vi.fn(() => state),
    {
      getState: vi.fn(() => state),
    },
  );
  return { useWorkflowStore };
});

afterEach(() => {
  cleanup();
  clearWorkflowDraftSaveCoordinatorForTests();
  window.history.replaceState({}, '', window.location.pathname);
});

beforeEach(() => {
  vi.clearAllMocks();
  useWorkflowStore.getState().hasUnsavedChanges = true;
  useWorkflowStore.getState().isAgentBuilderMutationSaving = false;
  useWorkflowStore.getState().undoStack = [
    {
      nodes: [],
      edges: [],
      agentBuilderHistory: {
        sessionId: 'session-1',
        acknowledged: true,
        completionEligible: false,
        parameterGroup: null,
        lastManuallyConfiguredTaskId: null,
        presentation: 'none',
      },
    },
  ];
  vi.mocked(
    useWorkflowStore.getState().getCanonicalDraftMetadata,
  ).mockReturnValue({
    workflowId: 'workflow-1',
    graphHash: 'a'.repeat(64),
    updatedAt: '2026-07-14T00:00:00Z',
  });
  vi.mocked(workflowApi.executeWorkflowStream).mockResolvedValue(undefined);
});

describe('TestSidebar final response card', () => {
  it('Agent Builder 저장 중에는 테스트 저장과 실행을 시작하지 않는다', () => {
    useWorkflowStore.getState().isAgentBuilderMutationSaving = true;

    render(<TestSidebar />);

    const executeButton = screen.getByRole('button', {
      name: /Agent Builder 저장 확인 중/,
    });
    expect(executeButton).toBeDisabled();
    expect(
      screen.getByText('Agent Builder 변경사항 저장을 확인하는 중입니다.'),
    ).toBeInTheDocument();
    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it('Agent Builder graph가 저장됐지만 acknowledgement 전이면 테스트 실행을 차단한다', () => {
    const store = useWorkflowStore.getState() as ReturnType<
      typeof useWorkflowStore.getState
    > & {
      undoStack: Array<{
        nodes: unknown[];
        edges: unknown[];
        agentBuilderOperation?: {
          operationId: string;
          resultGraphHash: string;
          workflowUpdatedAt: string;
          sessionId: string;
        };
        agentBuilderHistory?: {
          sessionId?: string;
          latestOperationId?: string;
          acknowledged?: boolean;
        };
      }>;
    };
    store.isAgentBuilderMutationSaving = false;
    store.undoStack = [
      {
        nodes: [],
        edges: [],
        agentBuilderOperation: {
          operationId: 'operation-persisted-unacknowledged',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-14T00:00:01Z',
          sessionId: 'session-1',
        },
        agentBuilderHistory: {
          sessionId: 'session-1',
          latestOperationId: 'operation-persisted-unacknowledged',
          acknowledged: false,
          completionEligible: false,
          parameterGroup: null,
          lastManuallyConfiguredTaskId: null,
          presentation: 'none',
        },
      },
    ];

    render(<TestSidebar />);

    expect(
      screen.getByRole('button', {
        name: /Agent Builder 저장 결과 확인 중/,
      }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        'Agent Builder 저장 결과를 확인 중입니다. 확인이 끝난 뒤 다시 실행해주세요.',
      ),
    ).toBeInTheDocument();
    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it('테스트 실행 전 canonical metadata로 저장하고 성공 응답을 공유 상태에 반영한다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'workflow-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
        expect.objectContaining({
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        }),
      );
    });
    expect(
      useWorkflowStore.getState().ingestCanonicalDraftMetadata,
    ).toHaveBeenCalledTimes(1);
    expect(
      useWorkflowStore.getState().ingestCanonicalDraftMetadata,
    ).toHaveBeenCalledWith(
      expect.objectContaining({ graph_hash: 'b'.repeat(64) }),
      'workflow-1',
    );
  });

  it('workflow_start로 snapshot이 확정될 때까지 후속 autosync 저장을 대기시킨다', async () => {
    const store = useWorkflowStore.getState();
    store.hasUnsavedChanges = false;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
      nodes: store.nodes,
      edges: store.edges,
      viewport: { x: 0, y: 0, zoom: 1 },
      features: store.features,
    });
    vi.mocked(workflowApi.executeWorkflowStream).mockImplementation(
      async (_workflowId, _inputs, onEvent) => {
        expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('test_preflight');
        let autosyncAcquired = false;
        const waitingAutosync = acquireWorkflowDraftSave(
          'workflow-1',
          'autosync',
        ).then((release) => {
          autosyncAcquired = true;
          return release;
        });

        await Promise.resolve();
        expect(autosyncAcquired).toBe(false);
        await onEvent?.({
          type: 'workflow_start',
          data: { run_id: 'run-1' },
        });

        const releaseAutosync = await waitingAutosync;
        expect(autosyncAcquired).toBe(true);
        expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('autosync');
        releaseAutosync();
      },
    );

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(workflowApi.executeWorkflowStream).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(getWorkflowDraftSaveOwner('workflow-1')).toBeNull();
    });
  });

  it('dirty editor의 저장 기준점보다 서버가 앞서 있으면 로컬 graph를 저장하거나 실행하지 않는다', async () => {
    vi.mocked(
      useWorkflowStore.getState().getCanonicalDraftMetadata,
    ).mockReturnValue({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-14T00:00:00Z',
    });
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'c'.repeat(64),
      updated_at: '2026-07-14T00:00:02Z',
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(
        useWorkflowStore.getState().failTestExecution,
      ).toHaveBeenCalledWith(
        '서버의 Workflow가 현재 편집 기준보다 앞서 있습니다. 최신 상태를 불러온 뒤 다시 시도해주세요.',
      );
    });
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
    expect(
      useWorkflowStore.getState().ingestCanonicalDraftMetadata,
    ).not.toHaveBeenCalled();
  });

  it('clean 표시가 누락됐어도 서버 기준점이 그대로면 현재 화면을 저장하고 실행한다', async () => {
    useWorkflowStore.getState().hasUnsavedChanges = false;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'workflow-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
        expect.objectContaining({
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        }),
      );
    });
    await waitFor(() => {
      expect(workflowApi.executeWorkflowStream).toHaveBeenCalledTimes(1);
    });
    expect(useWorkflowStore.getState().failTestExecution).not.toHaveBeenCalled();
  });

  it('clean editor라도 서버 기준점이 바뀌었으면 현재 화면을 덮어쓰지 않는다', async () => {
    useWorkflowStore.getState().hasUnsavedChanges = false;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'c'.repeat(64),
      updated_at: '2026-07-14T00:00:02Z',
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(
        useWorkflowStore.getState().failTestExecution,
      ).toHaveBeenCalledWith(
        '서버의 Workflow가 현재 편집 기준보다 앞서 있습니다. 최신 상태를 불러온 뒤 다시 시도해주세요.',
      );
    });
    expect(
      useWorkflowStore.getState().ingestCanonicalDraftMetadata,
    ).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it('preflight 도중 Agent Builder 저장이 시작되면 test stream을 열지 않는다', async () => {
    let resolveCanonical!: (
      value: Awaited<ReturnType<typeof workflowApi.getDraftWorkflow>>,
    ) => void;
    vi.mocked(workflowApi.getDraftWorkflow).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCanonical = resolve;
        }),
    );

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));
    await waitFor(() =>
      expect(workflowApi.getDraftWorkflow).toHaveBeenCalled(),
    );

    useWorkflowStore.getState().isAgentBuilderMutationSaving = true;
    const store = useWorkflowStore.getState();
    resolveCanonical({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
      nodes: store.nodes,
      edges: store.edges,
      viewport: { x: 0, y: 0, zoom: 1 },
      features: store.features,
      envVariables: store.envVariables,
      runtimeVariables: store.runtimeVariables,
    });

    await waitFor(() => {
      expect(
        useWorkflowStore.getState().failTestExecution,
      ).toHaveBeenCalledWith(
        'Agent Builder 저장이 시작되어 테스트 실행을 중단했습니다. 저장 완료 후 다시 시도해주세요.',
      );
    });
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it('canonical 저장이 stale이면 workflow 실행을 시작하지 않는다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: { status: 409 },
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(useWorkflowStore.getState().failTestExecution).toHaveBeenCalled();
    });
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it('stale_graph 뒤 canonical graph가 editor snapshot과 같으면 재저장 없이 실행한다', async () => {
    const store = useWorkflowStore.getState();
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      })
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-14T00:00:01Z',
        nodes: store.nodes,
        edges: store.edges,
        viewport: { x: 0, y: 0, zoom: 1 },
        features: store.features,
        envVariables: store.envVariables,
        runtimeVariables: store.runtimeVariables,
      });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: { status: 409, data: { detail: 'stale_graph' } },
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(workflowApi.executeWorkflowStream).toHaveBeenCalledTimes(1);
    });
    expect(workflowApi.getDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(store.failTestExecution).not.toHaveBeenCalled();
  });

  it('does not ingest canonical metadata when stale_graph recovery finds a graph mismatch', async () => {
    const store = useWorkflowStore.getState();
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      })
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-14T00:00:01Z',
        nodes: [
          {
            id: 'server-only',
            type: 'startNode',
            position: { x: 0, y: 0 },
            data: {
              title: 'Server start',
              triggerType: 'manual',
              variables: [],
            },
          },
        ],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: { status: 409, data: { detail: 'stale_graph' } },
    });

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(store.failTestExecution).toHaveBeenCalled();
    });
    expect(store.ingestCanonicalDraftMetadata).not.toHaveBeenCalled();
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
  });

  it.each([
    [401, undefined, '로그인이 만료되었습니다. 다시 로그인한 뒤 시도해주세요.'],
    [403, undefined, '이 Workflow를 저장하거나 테스트할 권한이 없습니다.'],
    [
      409,
      'stale_graph',
      '다른 변경사항이 먼저 저장되었습니다. 서버 상태를 확인한 뒤 다시 시도해주세요.',
    ],
    [
      500,
      undefined,
      'Workflow 저장 중 오류가 발생했습니다. 서버 상태를 확인한 뒤 다시 시도해주세요.',
    ],
  ])(
    '테스트 전 저장 오류 %s/%s를 안전한 원인별 메시지로 구분한다',
    async (status, detail, expectedMessage) => {
      vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      });
      vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
        response: { status, data: detail ? { detail } : undefined },
      });

      render(<TestSidebar />);
      fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

      await waitFor(() => {
        expect(
          useWorkflowStore.getState().failTestExecution,
        ).toHaveBeenCalledWith(expectedMessage);
      });
      expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
    },
  );

  it('continues execution only when operation recovery confirms an acknowledged applied graph', async () => {
    const store = useWorkflowStore.getState();
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      })
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-14T00:00:01Z',
        nodes: store.nodes,
        edges: store.edges,
        viewport: { x: 0, y: 0, zoom: 1 },
        features: store.features,
        envVariables: store.envVariables,
        runtimeVariables: store.runtimeVariables,
      });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'operation envelope not found' },
      },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      status: 'completed',
      messages: [],
      active_graph_mutation: {
        status: 'acknowledged',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-14T00:00:01Z',
      },
    } as Awaited<ReturnType<typeof agentBuilderApi.getSession>>);

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(workflowApi.executeWorkflowStream).toHaveBeenCalledTimes(1);
    });
    expect(store.failTestExecution).not.toHaveBeenCalled();
    expect(store.ingestCanonicalDraftMetadata).toHaveBeenCalledTimes(1);
  });

  it('blocks execution when acknowledged graph hash matches but saved timestamp differs', async () => {
    const store = useWorkflowStore.getState();
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      })
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-14T00:00:02Z',
        nodes: store.nodes,
        edges: store.edges,
        viewport: { x: 0, y: 0, zoom: 1 },
        features: store.features,
        envVariables: store.envVariables,
        runtimeVariables: store.runtimeVariables,
      });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'operation envelope not found' },
      },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      status: 'completed',
      messages: [],
      active_graph_mutation: {
        status: 'acknowledged',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-14T00:00:01Z',
      },
    } as Awaited<ReturnType<typeof agentBuilderApi.getSession>>);

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(store.failTestExecution).toHaveBeenCalledWith(
        'Agent Builder 저장 상태를 확인했습니다. 최신 Workflow 상태에서 테스트를 다시 실행해주세요.',
      );
    });
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
    expect(store.ingestCanonicalDraftMetadata).not.toHaveBeenCalled();
  });

  it('operation envelope not found는 Agent Builder session과 canonical draft로 확인하고 자동 실행하지 않는다', async () => {
    const store = useWorkflowStore.getState();
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      })
      .mockResolvedValueOnce({
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-14T00:00:01Z',
        nodes: store.nodes,
        edges: store.edges,
        viewport: { x: 0, y: 0, zoom: 1 },
        features: store.features,
        envVariables: store.envVariables,
        runtimeVariables: store.runtimeVariables,
      });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'operation envelope not found' },
      },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      status: 'completed',
    } as Awaited<ReturnType<typeof agentBuilderApi.getSession>>);

    render(<TestSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /테스트 실행하기/ }));

    await waitFor(() => {
      expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
    });
    expect(workflowApi.getDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(workflowApi.executeWorkflowStream).not.toHaveBeenCalled();
    expect(store.failTestExecution).toHaveBeenCalledWith(
      'Agent Builder 저장 상태를 확인했습니다. 최신 Workflow 상태에서 테스트를 다시 실행해주세요.',
    );
  });

  it('테스트 입력 필드는 다크 모드에서도 입력값과 placeholder 색상을 명시한다', () => {
    expect(TEST_INPUT_CLASS_NAME).toContain('text-gray-900');
    expect(TEST_INPUT_CLASS_NAME).toContain('placeholder:text-gray-400');
    expect(TEST_INPUT_CLASS_NAME).toContain('dark:text-gray-100');
    expect(TEST_INPUT_CLASS_NAME).toContain('dark:placeholder:text-gray-500');
  });

  it('테스트 실행 패널 textarea에 다크 모드 입력 class를 실제 적용한다', () => {
    render(<TestSidebar />);

    const textarea = screen.getByPlaceholderText('질문을 입력하세요');

    expect(textarea).toHaveClass('min-h-[180px]');
    expect(textarea).toHaveClass('text-gray-900');
    expect(textarea).toHaveClass('dark:text-gray-100');
    expect(textarea).toHaveClass('placeholder:text-gray-400');
    expect(textarea).toHaveClass('dark:placeholder:text-gray-500');
    expect(
      screen.getByRole('button', { name: /테스트 실행하기/ }),
    ).toBeVisible();
  });

  it('최종 사용자가 받는 text 응답을 별도 카드로 표시한다', () => {
    render(
      <FinalResponseCard
        preview={{
          kind: 'text',
          text: '개발팀 커밋 컨벤션은 feat: 설명 형식입니다.',
          isEmpty: false,
          sourceLabel: '정책 답변 LLM',
        }}
      />,
    );

    expect(screen.getByRole('heading', { name: '최종 응답' })).toBeVisible();
    expect(screen.getByText('정책 답변 LLM')).toBeVisible();
    expect(
      screen.getByText('개발팀 커밋 컨벤션은 feat: 설명 형식입니다.'),
    ).toBeVisible();
  });

  it('전체 펼침 옵션에서는 응답 영역에 내부 스크롤을 만들지 않는다', () => {
    render(
      <FinalResponseCard
        expandContent
        preview={{
          kind: 'text',
          text: '길이가 긴 최종 응답 전체 내용',
          isEmpty: false,
          sourceLabel: '답변',
        }}
      />,
    );

    const responseContainer =
      screen.getByText('길이가 긴 최종 응답 전체 내용').parentElement;
    expect(responseContainer).not.toHaveClass('max-h-56', 'overflow-y-auto');
  });

  it('JSON 응답은 raw dump 대신 필드 preview로 표시한다', () => {
    const preview: FinalResponsePreview = {
      kind: 'json',
      items: [
        { label: 'summary', value: '온보딩 절차 안내' },
        { label: 'next_steps', value: '2개 항목' },
      ],
      text: 'summary: 온보딩 절차 안내\nnext_steps: 2개 항목',
      isEmpty: false,
      sourceLabel: '워크플로우 최종 출력',
    };

    render(<FinalResponseCard preview={preview} />);

    expect(screen.getByText('summary')).toBeVisible();
    expect(screen.getByText('온보딩 절차 안내')).toBeVisible();
    expect(screen.getByText('next_steps')).toBeVisible();
    expect(screen.getByText('2개 항목')).toBeVisible();
    expect(screen.queryByText(/"summary"/)).not.toBeInTheDocument();
  });

  it('uses the default card text size for Markdown responses', () => {
    render(
      <FinalResponseCard
        renderMarkdown
        preview={{
          kind: 'text',
          text: 'Response body\n\n| Column | Value |\n| --- | --- |\n| Team | Sales |',
          isEmpty: false,
          sourceLabel: 'Answer',
        }}
      />,
    );

    const responseContainer = screen.getByText('Response body').parentElement;
    expect(responseContainer).toHaveClass('text-sm', 'leading-6');
    expect(screen.getByRole('table')).not.toHaveClass('text-base');
  });

  it('does not render Markdown images', () => {
    const { container } = render(
      <FinalResponseCard
        renderMarkdown
        preview={{
          kind: 'text',
          text: '![tracking pixel](https://tracker.example/pixel.png)',
          isEmpty: false,
          sourceLabel: 'Answer',
        }}
      />,
    );

    expect(container.querySelector('img')).not.toBeInTheDocument();
  });

  it('빈 최종 응답은 empty state를 표시한다', () => {
    render(
      <FinalResponseCard
        preview={{
          kind: 'text',
          text: '',
          isEmpty: true,
          sourceLabel: '실행 결과',
        }}
      />,
    );

    expect(
      screen.getByText('최종 사용자에게 표시할 응답이 비어 있습니다.'),
    ).toBeVisible();
  });
});
