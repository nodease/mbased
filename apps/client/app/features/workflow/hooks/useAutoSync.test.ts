import { renderHook, act } from '@testing-library/react';
import { useAutoSync } from './useAutoSync';
import { useWorkflowStore } from '../store/useWorkflowStore';
import { workflowApi } from '../api/workflowApi';
import { agentBuilderApi } from '../api/agentBuilderApi';
import type { Node } from '../types/Workflow';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { toast } from 'sonner';
import {
  clearWorkflowDraftSaveCoordinatorForTests,
  tryAcquireWorkflowDraftSave,
} from '../utils/workflowDraftSaveCoordinator';

// 1. Next.js의 useParams 모킹 (workflowId 제공)
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'test-workflow-id' }),
}));

// 2. React Flow 모킹 (viewport 관련 함수 제공)
// 안정적인 함수 참조를 위해 모킹 함수를 미리 생성
const mockGetViewport = vi.fn(() => ({ x: 0, y: 0, zoom: 1 }));
const mockSetViewport = vi.fn();

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    getViewport: mockGetViewport,
    setViewport: mockSetViewport,
  }),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// 3. API 모킹 (실제 서버 요청 방지)
vi.mock('../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
  },
}));

vi.mock('../api/agentBuilderApi', () => ({
  agentBuilderApi: {
    getSession: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    warning: vi.fn(),
    error: vi.fn(),
  },
}));

// 4. Zustand 초기화 헬퍼 (테스트 간 상태 간섭 방지)
const initialStoreState = useWorkflowStore.getState();
const resetStore = () => useWorkflowStore.setState(initialStoreState, true);
const canonicalDraft = (overrides: Record<string, unknown> = {}) => ({
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  workflow_id: 'test-workflow-id',
  graph_hash: 'a'.repeat(64),
  updated_at: '2026-07-13T00:00:00Z',
  ...overrides,
});
const canonicalSave = (overrides: Record<string, unknown> = {}) => ({
  status: 'success' as const,
  workflow_id: 'test-workflow-id',
  graph_hash: 'b'.repeat(64),
  updated_at: '2026-07-13T00:00:01Z',
  ...overrides,
});

describe('useAutoSync Hook', () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
    vi.useFakeTimers(); // 시간 제어를 위해 타이머 모킹
    useWorkflowStore.setState({ activeWorkflowId: 'test-workflow-id' });
  });

  afterEach(() => {
    clearWorkflowDraftSaveCoordinatorForTests();
    vi.useRealTimers();
  });

  it('데이터 로딩 전에는 노드가 변경되어도 저장 API를 호출하지 않아야 한다 (데이터 보호)', async () => {
    // API가 아직 응답하지 않음 (로딩 중 상태 유지: isLoadedRef = false)
    (workflowApi.getDraftWorkflow as any).mockReturnValue(
      new Promise(() => {}),
    );

    renderHook(() => useAutoSync());

    // 노드 변경 시도
    act(() => {
      useWorkflowStore.setState({ nodes: [{ id: 'new', data: {} } as any] });
    });

    // 5초가 지나도
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    // 저장 API는 절대 호출되면 안 됨!
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
  });

  it('데이터 로딩 후, 노드가 변경되고 1초가 지나면 저장 API가 호출되어야 한다 (최종 저장)', async () => {
    // API가 즉시 응답함 (로딩 완료 상태: isLoadedRef = true)
    (workflowApi.getDraftWorkflow as any).mockResolvedValue(canonicalDraft());

    renderHook(() => useAutoSync());

    // 로딩 완료를 기다림 (Promises 처리)
    await act(async () => {
      await Promise.resolve();
    });

    // 노드 변경 발생
    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'updated', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });

    // 1초(1000ms) 흐른 뒤 (디바운스 시간)
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    // 저장 API가 정확히 1번 호출되었는지 확인
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('uses the canonical projection for ordinary autosync payloads', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(canonicalSave());
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      useWorkflowStore.setState({
        nodes: [
          {
            id: 'loop-1',
            type: 'loopNode',
            position: { x: 0, y: 0 },
            data: {
              displayNumber: 1,
              status: 'success',
              observability: { latency_ms: 4 },
              subGraph: {
                nodes: [
                  {
                    id: 'nested-1',
                    type: 'codeNode',
                    position: { x: 0, y: 0 },
                    data: {
                      code: 'return inputs',
                      displayNumber: 2,
                      status: 'running',
                      observability: { latency_ms: 2 },
                    },
                  },
                ],
                edges: [],
              },
            },
          } as any,
        ],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const request = vi.mocked(workflowApi.syncDraftWorkflow).mock.calls[0][1];
    const loopData = request.nodes[0].data as Record<string, any>;
    const nestedData = loopData.subGraph.nodes[0].data;
    expect(loopData).not.toHaveProperty('displayNumber');
    expect(loopData).not.toHaveProperty('status');
    expect(loopData).not.toHaveProperty('observability');
    expect(nestedData).toEqual({ code: 'return inputs' });
  });

  it.each(['workflow-b', 'default'])(
    'does not apply a late save response to the newly active %s workflow',
    async (nextWorkflowId) => {
    let resolveSave!: (value: ReturnType<typeof canonicalSave>) => void;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });

    const workflowANode = {
      id: 'shared-1',
      type: 'slackPostNode',
      position: { x: 0, y: 0 },
      data: { _deferred_parameters: ['channel'] },
    } as unknown as Node;
    const workflowBNode = {
      id: 'shared-1',
      type: 'githubNode',
      position: { x: 0, y: 0 },
      data: { _deferred_parameters: ['repo_name'] },
    } as unknown as Node;

    act(() => {
      useWorkflowStore.setState({
        activeWorkflowId: 'test-workflow-id',
        nodes: [workflowANode],
        workflows: [
          {
            id: 'test-workflow-id',
            appId: 'app-1',
            nodes: [workflowANode],
            edges: [],
            features: {},
            viewport: { x: 0, y: 0, zoom: 1 },
          },
          {
            id: nextWorkflowId,
            appId: 'app-1',
            nodes: [workflowBNode],
            edges: [],
            features: {},
            viewport: { x: 0, y: 0, zoom: 1 },
          },
        ],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);

    act(() => {
      useWorkflowStore.setState({
        activeWorkflowId: nextWorkflowId,
        nodes: [workflowBNode],
        hasUnsavedChanges: true,
      });
    });
    await act(async () => {
      resolveSave(
        canonicalSave({
          canonical_deferred_parameters: [
            { node_path: ['shared-1'], parameter_keys: [] },
          ],
        }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe(nextWorkflowId);
    expect(state.nodes[0].data._deferred_parameters).toEqual(['repo_name']);
    expect(state.hasUnsavedChanges).toBe(true);
    },
  );

  it('keeps newer same-workflow edits when an older autosync response arrives', async () => {
    let resolveSave!: (value: ReturnType<typeof canonicalSave>) => void;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      useWorkflowStore.setState({
        nodes: [
          {
            id: 'slack-1',
            type: 'slackPostNode',
            position: { x: 0, y: 0 },
            data: { _deferred_parameters: ['channel'] },
          } as unknown as Node,
        ],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);

    act(() => {
      useWorkflowStore.setState({
        nodes: [
          {
            id: 'slack-1',
            type: 'slackPostNode',
            position: { x: 0, y: 0 },
            data: { _deferred_parameters: ['channel', 'message'] },
          } as unknown as Node,
        ],
        hasUnsavedChanges: true,
      });
    });

    await act(async () => {
      resolveSave(
        canonicalSave({
          graph_hash: 'c'.repeat(64),
          updated_at: '2026-07-13T00:00:02Z',
          canonical_deferred_parameters: [
            { node_path: ['slack-1'], parameter_keys: [] },
          ],
        }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].data._deferred_parameters).toEqual([
      'channel',
      'message',
    ]);
    expect(state.hasUnsavedChanges).toBe(true);
    expect(state.getCanonicalDraftMetadata('test-workflow-id')).toEqual({
      workflowId: 'test-workflow-id',
      graphHash: 'c'.repeat(64),
      updatedAt: '2026-07-13T00:00:02Z',
    });
  });

  it('Agent Builder 저장 완료 플래그만 해제되면 같은 graph를 다시 저장하지 않는다', async () => {
    (workflowApi.getDraftWorkflow as any).mockResolvedValue({
      nodes: [{ id: 'persisted', data: {} } as any],
      edges: [],
    });
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        isAgentBuilderMutationSaving: true,
        hasUnsavedChanges: false,
      });
    });
    act(() => {
      useWorkflowStore.setState({ isAgentBuilderMutationSaving: false });
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
  });

  it('같은 workflow의 test preflight 저장 중에는 autosync를 시작하지 않는다', async () => {
    (workflowApi.getDraftWorkflow as any).mockResolvedValue(canonicalDraft());
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();
    const releaseTestSave = tryAcquireWorkflowDraftSave(
      'test-workflow-id',
      'test_preflight',
    );

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'updated', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    releaseTestSave?.();
  });

  it('test preflight 잠금이 해제되면 최신 dirty graph를 자동으로 저장한다', async () => {
    (workflowApi.getDraftWorkflow as any).mockResolvedValue(canonicalDraft());
    (workflowApi.syncDraftWorkflow as any).mockResolvedValue(canonicalSave());
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();
    const releaseTestSave = tryAcquireWorkflowDraftSave(
      'test-workflow-id',
      'test_preflight',
    );

    act(() => {
      useWorkflowStore.setState({
        activeWorkflowId: 'test-workflow-id',
        nodes: [{ id: 'latest', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().activeWorkflowId).toBe(
      'test-workflow-id',
    );
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('test-workflow-id'),
    ).not.toBeNull();

    await act(async () => {
      releaseTestSave?.();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        nodes: [expect.objectContaining({ id: 'latest' })],
      }),
    );
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('persisted Undo 저장 후 서버 task 상태를 복구하고 revert 잠금을 해제한다', async () => {
    (workflowApi.getDraftWorkflow as any).mockResolvedValue({
      nodes: [{ id: 'persisted', data: {} } as any],
      edges: [],
    });
    (workflowApi.syncDraftWorkflow as any).mockResolvedValue({
      status: 'success',
    });
    (agentBuilderApi.getSession as any).mockResolvedValue({
      session_id: 'session-1',
      status: 'active',
      messages: [],
      parameter_group: {
        group_id: 'group-1',
        status: 'active',
        tasks: [],
      },
    });
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'base', data: {} } as any],
        hasUnsavedChanges: true,
        pendingAgentBuilderRevert: {
          operationId: 'operation-1',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:00Z',
          sessionId: 'session-1',
        },
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({
      sessionId: 'session-1',
      parameterGroup: expect.objectContaining({ group_id: 'group-1' }),
    });
  });

  it('persisted Undo 저장 성공 뒤 session 복구 실패는 저장을 되돌리지 않는다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSave({
        parameter_group: {
          group_id: 'group-from-save',
          status: 'active',
          tasks: [],
        },
      }),
    );
    (agentBuilderApi.getSession as any).mockRejectedValue(
      new Error('session unavailable'),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'base', data: {} } as any],
        hasUnsavedChanges: true,
        pendingAgentBuilderRevert: {
          operationId: 'operation-1',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:00Z',
          sessionId: 'session-1',
        },
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({
      sessionId: 'session-1',
      parameterGroup: expect.objectContaining({ group_id: 'group-from-save' }),
    });
  });

  it('uses the canonical revert graph when persisting an Agent Builder Undo', async () => {
    const canonicalNode = {
      id: 'legacy',
      type: 'startNode',
      position: { x: 0, y: 0 },
      data: { title: 'Legacy', triggerType: 'manual', variables: [] },
    } as any;
    const editorNode = {
      ...canonicalNode,
      data: { ...canonicalNode.data, displayNumber: 1 },
    };
    (workflowApi.getDraftWorkflow as any).mockResolvedValue({
      nodes: [canonicalNode],
      edges: [],
    });
    (workflowApi.syncDraftWorkflow as any).mockResolvedValue({
      status: 'success',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:03Z',
      parameter_group: null,
    });
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [editorNode],
        edges: [],
        hasUnsavedChanges: true,
        pendingAgentBuilderRevert: {
          operationId: 'operation-replace',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:02Z',
          sessionId: 'session-1',
          revertGraph: { nodes: [canonicalNode], edges: [] },
        } as any,
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        nodes: [canonicalNode],
        edges: [],
      }),
    );
    expect(useWorkflowStore.getState().nodes).toEqual([editorNode]);
  });

  it('revert transport 응답이 유실되면 동일 요청을 정확히 한 번 재시도한다', async () => {
    const baseNode = {
      id: 'base',
      type: 'startNode',
      position: { x: 0, y: 0 },
      data: { title: 'Base' },
    } as Node;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(
        canonicalSave({
          graph_hash: 'a'.repeat(64),
          updated_at: '2026-07-13T00:00:03Z',
          parameter_group: null,
        }),
      );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [baseNode],
        edges: [],
        hasUnsavedChanges: true,
        pendingAgentBuilderRevert: {
          operationId: 'operation-retry',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:02Z',
          sessionId: 'session-1',
          revertGraph: { nodes: [baseNode], edges: [] },
        },
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(vi.mocked(workflowApi.syncDraftWorkflow).mock.calls[0]).toEqual(
      vi.mocked(workflowApi.syncDraftWorkflow).mock.calls[1],
    );
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
  });

  it('revert save의 명시적인 409는 두 번째 POST 없이 실패한다', async () => {
    const baseNode = {
      id: 'base-stale',
      type: 'startNode',
      position: { x: 0, y: 0 },
      data: { title: 'Base stale' },
    } as Node;
    const pending = {
      operationId: 'operation-stale',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:02Z',
      sessionId: 'session-1',
      revertGraph: { nodes: [baseNode], edges: [] },
    };
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      Object.assign(new Error('stale_graph'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: 'stale_graph' } },
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [baseNode],
        edges: [],
        hasUnsavedChanges: true,
        pendingAgentBuilderRevert: pending,
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toEqual(
      pending,
    );
  });

  it('revert 재시도 뒤 canonical/session 분류도 불가능하면 pending history를 보존한다', async () => {
    const pending = {
      operationId: 'operation-uncertain',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:02Z',
      sessionId: 'session-1',
      revertGraph: { nodes: [], edges: [] },
    };
    const undoStack = [
      {
        nodes: [],
        edges: [],
        agentBuilderOperation: pending,
      },
    ];
    const redoStack = [
      {
        nodes: [{ id: 'final', data: {} } as Node],
        edges: [],
        agentBuilderOperation: pending,
      },
    ];
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce(canonicalDraft())
      .mockRejectedValueOnce(new Error('canonical unavailable'));
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [],
        edges: [],
        hasUnsavedChanges: true,
        undoStack,
        redoStack,
        pendingAgentBuilderRevert: pending,
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toEqual(
      pending,
    );
    expect(useWorkflowStore.getState().undoStack).toEqual(undoStack);
    expect(useWorkflowStore.getState().redoStack).toEqual(redoStack);
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it('refreshes the next persisted Undo boundary from the canonical revert save', async () => {
    const firstResultGraphHash = 'a'.repeat(64);
    (workflowApi.getDraftWorkflow as any).mockResolvedValue({
      nodes: [],
      edges: [],
    });
    (workflowApi.syncDraftWorkflow as any).mockResolvedValue({
      status: 'success',
      graph_hash: firstResultGraphHash,
      updated_at: '2026-07-13T00:00:03Z',
      parameter_group: null,
    });
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'first-result', data: {} } as any],
        hasUnsavedChanges: true,
        undoStack: [
          {
            nodes: [],
            edges: [],
            agentBuilderOperation: {
              operationId: 'operation-first',
              resultGraphHash: firstResultGraphHash,
              workflowUpdatedAt: '2026-07-13T00:00:01Z',
              sessionId: 'session-1',
            },
          },
        ],
        pendingAgentBuilderRevert: {
          operationId: 'operation-second',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:02Z',
          sessionId: 'session-1',
        },
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(
      expect.objectContaining({
        operationId: 'operation-first',
        workflowUpdatedAt: '2026-07-13T00:00:03Z',
      }),
    );
  });

  it('manual edit Undo 저장이 Agent Builder 최종 graph로 돌아오면 아래 경계의 CAS 시점을 갱신한다', async () => {
    const agentBuilderFinalHash = 'c'.repeat(64);
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSave({
        graph_hash: agentBuilderFinalHash,
        updated_at: '2026-07-13T00:00:05Z',
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [
          {
            id: 'agent-builder-final',
            type: 'startNode',
            position: { x: 0, y: 0 },
            data: { title: 'Agent Builder final' },
          } as Node,
        ],
        edges: [],
        hasUnsavedChanges: true,
        undoStack: [
          {
            nodes: [],
            edges: [],
            agentBuilderOperation: {
              operationId: 'operation-agent-builder',
              resultGraphHash: agentBuilderFinalHash,
              workflowUpdatedAt: '2026-07-13T00:00:01Z',
              sessionId: 'session-1',
            },
          },
        ],
        pendingAgentBuilderRevert: null,
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(
      expect.objectContaining({
        operationId: 'operation-agent-builder',
        workflowUpdatedAt: '2026-07-13T00:00:05Z',
      }),
    );
    expect(agentBuilderApi.getSession).not.toHaveBeenCalled();
  });

  it('전체 revert 뒤 memory Redo는 action=redo CAS로 final graph만 저장한다', async () => {
    const baseGraphHash = 'a'.repeat(64);
    const finalGraphHash = 'b'.repeat(64);
    const finalNode = {
      id: 'agent-builder-final',
      type: 'startNode',
      position: { x: 0, y: 0 },
      data: { title: 'Agent Builder final' },
    } as Node;
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockResolvedValueOnce(
        canonicalSave({
          graph_hash: baseGraphHash,
          updated_at: '2026-07-13T00:00:03Z',
          parameter_group: null,
        }),
      )
      .mockResolvedValueOnce(
        canonicalSave({
          graph_hash: finalGraphHash,
          updated_at: '2026-07-13T00:00:04Z',
        }),
      );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [],
        edges: [],
        hasUnsavedChanges: true,
        undoStack: [],
        redoStack: [
          {
            nodes: [finalNode],
            edges: [],
            agentBuilderOperation: {
              operationId: 'root-operation',
              resultGraphHash: finalGraphHash,
              workflowUpdatedAt: '2026-07-13T00:00:02Z',
              sessionId: 'session-1',
              revertGraph: { nodes: [], edges: [] },
            },
          },
        ],
        pendingAgentBuilderRevert: {
          operationId: 'root-operation',
          resultGraphHash: finalGraphHash,
          workflowUpdatedAt: '2026-07-13T00:00:02Z',
          sessionId: 'session-1',
          revertGraph: { nodes: [], edges: [] },
        },
      });
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    act(() => {
      useWorkflowStore.getState().redo();
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(workflowApi.syncDraftWorkflow).toHaveBeenLastCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        nodes: [finalNode],
        edges: [],
        mutation_context: {
          operation_id: 'root-operation',
          action: 'redo',
          expected_base_graph_hash: baseGraphHash,
          expected_workflow_updated_at: '2026-07-13T00:00:03Z',
          catalog_version: 3,
        },
      }),
    );
    expect(agentBuilderApi.getSession).not.toHaveBeenCalled();
  });

  it('non-Agent autosync는 마지막 canonical GET/POST metadata를 expected fields로 전달한다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [{ id: 'loaded', data: {} } as any],
      edges: [],
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    } as any);
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockResolvedValueOnce({
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:01Z',
      } as any)
      .mockResolvedValueOnce({
        graph_hash: 'c'.repeat(64),
        updated_at: '2026-07-13T00:00:02Z',
      } as any);
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'first-save', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenLastCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        expected_graph_hash: 'a'.repeat(64),
        expected_updated_at: '2026-07-13T00:00:00Z',
      }),
    );

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'second-save', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenLastCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        expected_graph_hash: 'b'.repeat(64),
        expected_updated_at: '2026-07-13T00:00:01Z',
      }),
    );
  });

  it('non-Agent autosync reads the shared canonical metadata when another save path advances it', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(
      canonicalDraft({
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      }),
    );
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSave({
        graph_hash: 'd'.repeat(64),
        updated_at: '2026-07-13T00:00:04Z',
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.getState().setCanonicalDraftMetadata({
        workflowId: 'test-workflow-id',
        graphHash: 'c'.repeat(64),
        updatedAt: '2026-07-13T00:00:03Z',
      });
      useWorkflowStore.setState({
        nodes: [{ id: 'manual-after-version-restore', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'test-workflow-id',
      expect.objectContaining({
        expected_graph_hash: 'c'.repeat(64),
        expected_updated_at: '2026-07-13T00:00:03Z',
      }),
    );
    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('test-workflow-id'),
    ).toEqual({
      workflowId: 'test-workflow-id',
      graphHash: 'd'.repeat(64),
      updatedAt: '2026-07-13T00:00:04Z',
    });
  });

  it('ordinary stale_graph autosync failures remain visible and retryable', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue(canonicalDraft());
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      Object.assign(new Error('stale_graph'), {
        response: {
          status: 409,
          data: { detail: 'stale_graph' },
        },
      }),
    );
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    vi.clearAllMocks();

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'stale-save', data: {} } as any],
        hasUnsavedChanges: true,
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('stale'),
      expect.any(Object),
    );
  });

  it('ambiguous Agent Builder recovery의 third canonical graph로 editor와 history를 재동기화한다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [{ id: 'loaded', data: {} } as any],
        edges: [],
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      } as any)
      .mockResolvedValueOnce({
        nodes: [
          {
            id: 'canonical-other',
            type: 'startNode',
            position: { x: 0, y: 0 },
            data: { title: 'Canonical other' },
          } as any,
        ],
        edges: [],
        graph_hash: 'z'.repeat(64),
        updated_at: '2026-07-13T00:00:09Z',
      } as any);
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('network ambiguous'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      status: 'active',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-other',
        status: 'pending_ack',
      },
    } as any);
    renderHook(() => useAutoSync());
    await act(async () => {
      await Promise.resolve();
    });
    const undoSnapshot = {
      nodes: [{ id: 'before-agent-builder', data: {} } as any],
      edges: [],
      agentBuilderOperation: {
        operationId: 'operation-1',
        resultGraphHash: 'b'.repeat(64),
        workflowUpdatedAt: '2026-07-13T00:00:01Z',
        sessionId: 'session-1',
      },
    };
    useWorkflowStore.getState().setCanonicalDraftMetadata({
      workflowId: 'test-workflow-id',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-13T00:00:00Z',
    });

    act(() => {
      useWorkflowStore.setState({
        nodes: [{ id: 'desired-revert', data: {} } as any],
        edges: [],
        hasUnsavedChanges: true,
        undoStack: [undoSnapshot],
        redoStack: [
          { nodes: [{ id: 'redo-final', data: {} } as any], edges: [] },
        ],
        pendingAgentBuilderRevert: {
          operationId: 'operation-1',
          resultGraphHash: 'b'.repeat(64),
          workflowUpdatedAt: '2026-07-13T00:00:01Z',
          sessionId: 'session-1',
          revertGraph: {
            nodes: [{ id: 'desired-revert', data: {} } as any],
            edges: [],
          },
        },
      });
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useWorkflowStore.getState().nodes).toEqual([
      expect.objectContaining({ id: 'canonical-other' }),
    ]);
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
    expect(useWorkflowStore.getState().redoStack).toEqual([]);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('test-workflow-id'),
    ).toEqual({
      workflowId: 'test-workflow-id',
      graphHash: 'z'.repeat(64),
      updatedAt: '2026-07-13T00:00:09Z',
    });
  });
});
