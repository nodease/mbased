import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { workflowApi } from '../../api/workflowApi';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { Node } from '../../types/Workflow';
import { agentBuilderApi } from '../../api/agentBuilderApi';
import { applyAndSaveAgentBuilderMutation } from './useAgentBuilderEditor';
import {
  clearWorkflowDraftSaveCoordinatorForTests,
  tryAcquireWorkflowDraftSave,
} from '../../utils/workflowDraftSaveCoordinator';

vi.mock('../../api/workflowApi', () => ({
  workflowApi: {
    syncDraftWorkflow: vi.fn(),
    getDraftWorkflow: vi.fn(),
  },
}));
vi.mock('../../api/agentBuilderApi', () => ({
  agentBuilderApi: {
    acknowledgeMutation: vi.fn(),
    getSession: vi.fn(),
  },
}));

const startNode: Node = {
  id: 'start',
  type: 'startNode',
  position: { x: 0, y: 0 },
  data: { title: 'Start', triggerType: 'manual', variables: [] },
};
const answerNode: Node = {
  id: 'answer',
  type: 'answerNode',
  position: { x: 400, y: 0 },
  data: { title: 'Answer', outputs: [] },
};
const llmNode: Node = {
  id: 'llm',
  type: 'llmNode',
  position: { x: 200, y: 0 },
  data: {
    title: 'LLM',
    provider: 'configured',
    model_id: 'gpt-5.5',
    configuration_state: 'resolved',
    task_type: 'answer',
    system_prompt: '',
    user_prompt: '{{input}}',
    referenced_variables: [],
    parameters: {},
    output_format: { type: 'text' },
    knowledgeBases: [],
    scoreThreshold: 0.5,
    topK: 3,
  },
};

const expectEditorNodesWithDisplayNumbers = (nodes: Node[]) => {
  expect(useWorkflowStore.getState().nodes).toEqual(
    nodes.map((node, index) =>
      expect.objectContaining({
        ...node,
        data: expect.objectContaining({
          ...node.data,
          displayNumber: index + 1,
        }),
      }),
    ),
  );
};

const saveResponse = () => ({
  status: 'success' as const,
  workflow_id: 'workflow-1',
  graph_hash: 'b'.repeat(64),
  updated_at: '2026-07-13T00:00:00Z',
});

describe('Agent Builder editor adapter', () => {
  afterEach(() => {
    clearWorkflowDraftSaveCoordinatorForTests();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [],
      edges: [],
      undoStack: [],
      redoStack: [],
      features: {},
      envVariables: [],
      runtimeVariables: [],
      isAgentBuilderMutationSaving: false,
    });
  });

  it('atomic apply 뒤 CAS save와 acknowledgement를 한 번씩 수행한다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [
          { op: 'add_node', node: startNode },
          { op: 'add_node', node: answerNode },
          {
            op: 'add_edge',
            edge: { id: 'e1', source: 'start', target: 'answer' },
          },
        ],
      },
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({
        operationId: 'operation-1',
        workflowId: 'workflow-1',
      }),
    );
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderHistory,
    ).toEqual(expect.objectContaining({ acknowledged: true }));
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it('accepts equivalent UTC timestamp encodings from mutation and canonical draft responses', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00+00:00',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-equivalent-timestamp',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-equivalent-timestamp',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(1);
  });

  it('테스트 preflight 저장이 끝날 때까지 Agent Builder 저장을 시작하지 않는다', async () => {
    const releaseTestSave = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    const saving = applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_result_graph_hash: 'b'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    await Promise.resolve();
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(true);
    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();

    releaseTestSave?.();
    await saving;

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it.each(['workflow-2', 'default'])(
    'does not apply a waiting mutation after the active workflow changes to %s',
    async (nextWorkflowId) => {
    const releaseTestSave = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    const saving = applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_result_graph_hash: 'b'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    await Promise.resolve();
    useWorkflowStore.setState({
      activeWorkflowId: nextWorkflowId,
      nodes: [{ id: 'next-workflow-node', data: {} } as Node],
      edges: [],
      undoStack: [],
      redoStack: [],
      hasUnsavedChanges: false,
    });
    releaseTestSave?.();

    await expect(saving).rejects.toMatchObject({
      code: 'workflow_context_changed',
    });
    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toEqual([
      { id: 'next-workflow-node', data: {} },
    ]);
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
    },
  );

  it('canonical graph 조회 중 workflow가 바뀌면 mutation 적용 전에 중단한다', async () => {
    let resolveCanonical!: (
      value: Awaited<ReturnType<typeof workflowApi.getDraftWorkflow>>,
    ) => void;
    vi.mocked(workflowApi.getDraftWorkflow).mockReturnValue(
      new Promise((resolve) => {
        resolveCanonical = resolve;
      }),
    );
    const saving = applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_result_graph_hash: 'b'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    await Promise.resolve();
    expect(workflowApi.getDraftWorkflow).toHaveBeenCalledWith('workflow-1');
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-2',
      nodes: [{ id: 'workflow-2-node', data: {} } as Node],
      edges: [],
      undoStack: [],
      redoStack: [],
      hasUnsavedChanges: false,
    });
    resolveCanonical({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });

    await expect(saving).rejects.toMatchObject({
      code: 'workflow_context_changed',
    });
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toEqual([
      { id: 'workflow-2-node', data: {} },
    ]);
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
  });

  it('uses the canonical server graph instead of stale canvas nodes when applying a mutation', async () => {
    useWorkflowStore.setState({
      nodes: [startNode],
      edges: [],
      hasUnsavedChanges: false,
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'workflow-1',
      expect.objectContaining({
        nodes: [startNode],
        expected_graph_hash: 'a'.repeat(64),
      }),
    );
    expectEditorNodesWithDisplayNumbers([startNode]);
  });

  it('rejects a mutation before applying it when the canonical revision is newer than its issued base', async () => {
    useWorkflowStore.getState().setCanonicalDraftMetadata({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-12T00:00:00Z',
    });
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: { noteNodes: [] },
      envVariables: [
        {
          id: 'env-server-only',
          key: 'SERVER_ONLY',
          value: 'preserve',
          type: 'string',
        },
      ],
      runtimeVariables: [],
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:01Z',
    });

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-stale-before-apply',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('stale_graph');

    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toEqual([]);
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-12T00:00:00Z',
    });
  });

  it('does not persist editor-only display numbers when editing a canonical graph', async () => {
    const canonicalStart = structuredClone(startNode);
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [canonicalStart],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });
    useWorkflowStore.setState({
      nodes: [
        {
          ...structuredClone(startNode),
          data: { ...startNode.data, displayNumber: 7 },
        },
      ],
      edges: [],
      hasUnsavedChanges: false,
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-edit',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-edit',
        kind: 'graph_edit',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [
          { op: 'add_node', node: answerNode },
          {
            op: 'add_edge',
            edge: { id: 'e1', source: 'start', target: 'answer' },
          },
        ],
      },
    });

    const request = vi.mocked(workflowApi.syncDraftWorkflow).mock.calls[0]?.[1];
    expect(request?.nodes).toEqual([canonicalStart, answerNode]);
    expect(request?.nodes[0]?.data).not.toHaveProperty('displayNumber');
  });

  it('persists the typed LLM mutation result instead of React Flow runtime measurements', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-llm',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    const store = useWorkflowStore.getState();
    const applyMutation = store.applyAgentBuilderGraphMutation;
    vi.spyOn(store, 'applyAgentBuilderGraphMutation').mockImplementation(
      (...args) => {
        applyMutation(...args);
        useWorkflowStore.setState((state) => ({
          nodes: state.nodes.map((node) =>
            node.id === llmNode.id
              ? ({
                  ...node,
                  width: 320,
                  height: 180,
                  measured: { width: 320, height: 180 },
                } as Node)
              : node,
          ),
        }));
      },
    );

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-llm',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: llmNode }],
      },
    });

    const request = vi.mocked(workflowApi.syncDraftWorkflow).mock.calls[0]?.[1];
    const expectedPersistedNode = {
      ...llmNode,
      data: { ...llmNode.data },
    };
    delete (expectedPersistedNode.data as Record<string, unknown>)
      .configuration_state;
    expect(request?.nodes).toEqual([expectedPersistedNode]);
    expect(request?.nodes[0].data).not.toHaveProperty('configuration_state');
    expect(request?.nodes[0]).not.toHaveProperty('width');
    expect(request?.nodes[0]).not.toHaveProperty('height');
    expect(request?.nodes[0]).not.toHaveProperty('measured');
    expect(useWorkflowStore.getState().nodes[0]).toEqual(
      expect.objectContaining({ width: 320, height: 180 }),
    );
  });

  it('updates the shared canonical metadata from Agent Builder draft GET and save', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'b'.repeat(64),
      updatedAt: '2026-07-13T00:00:00Z',
    });
  });

  it('CAS save가 실패하면 acknowledgement 없이 local mutation을 복구한다', async () => {
    const staleGraphError = Object.assign(new Error('stale_graph'), {
      isAxiosError: true,
      response: { status: 409, data: { detail: 'stale_graph' } },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      staleGraphError,
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'operation_payload_unavailable',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-1',
        status: 'blocked',
        blocked_reason: 'operation_payload_unavailable',
      },
    });
    const exposedAutosyncWindow: Array<{
      saving: boolean;
      unsaved: boolean;
    }> = [];
    const unsubscribe = useWorkflowStore.subscribe((state) => {
      exposedAutosyncWindow.push({
        saving: state.isAgentBuilderMutationSaving,
        unsaved: state.hasUnsavedChanges,
      });
    });

    try {
      await expect(
        applyAndSaveAgentBuilderMutation({
          sessionId: 'session-1',
          workflowId: 'workflow-1',
          viewport: { x: 0, y: 0, zoom: 1 },
          mutation: {
            operation_id: 'operation-1',
            kind: 'initial_graph',
            base_graph_hash: 'a'.repeat(64),
            expected_workflow_updated_at: '2026-07-12T00:00:00Z',
            operations: [{ op: 'add_node', node: startNode }],
          },
        }),
      ).rejects.toThrow('stale_graph');
    } finally {
      unsubscribe();
    }

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    expect(useWorkflowStore.getState().nodes).toEqual([]);
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
    expect(
      exposedAutosyncWindow.some(({ saving, unsaved }) => !saving && unsaved),
    ).toBe(false);
  });

  it('첫 CAS save 응답이 유실되면 동일 operation으로 재시도하고 저장을 이어간다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(1);
    expectEditorNodesWithDisplayNumbers([startNode]);
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('CAS save 재시도 응답도 유실되면 pending_ack envelope로 acknowledgement를 이어간다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [startNode],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'graph_mutation_ready',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-1',
        status: 'pending_ack',
        expected_result_graph_hash: 'b'.repeat(64),
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-13T00:00:00Z',
      },
    });
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({
        operationId: 'operation-1',
        graphHash: 'b'.repeat(64),
      }),
    );
    expectEditorNodesWithDisplayNumbers([startNode]);
  });

  it('CAS save 결과와 canonical 상태가 모두 불명확하면 local graph와 history boundary를 보존한다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockRejectedValueOnce(new Error('canonical unavailable'));

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-ambiguous',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('response lost');

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expectEditorNodesWithDisplayNumbers([startNode]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore.getState().undoStack[0]?.agentBuilderHistory,
    ).toEqual(
      expect.objectContaining({
        latestOperationId: 'operation-ambiguous',
        acknowledged: false,
      }),
    );
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-12T00:00:00Z',
    });
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it('does not trust pending_ack when the canonical workflow is still the base graph', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'graph_mutation_ready',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-base-conflict',
        status: 'pending_ack',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-13T00:00:00Z',
      },
    });
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      });

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-base-conflict',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('response lost');

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toEqual([]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(0);
  });

  it('does not trust an acknowledged envelope when canonical is a third graph', async () => {
    const thirdNode: Node = {
      id: 'third-from-server',
      type: 'answerNode',
      position: { x: 0, y: 0 },
      data: { title: 'Third', outputs: [] },
    };
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-third-conflict',
        status: 'acknowledged',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-13T00:00:00Z',
      },
    });
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [thirdNode],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'c'.repeat(64),
        updated_at: '2026-07-13T00:00:03Z',
      });

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-third-conflict',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('response lost');

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expectEditorNodesWithDisplayNumbers([startNode]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-12T00:00:00Z',
    });
  });

  it('preserves pending history when ambiguous canonical graph is neither base nor expected result', async () => {
    const thirdNode: Node = {
      id: 'third',
      type: 'answerNode',
      position: { x: 0, y: 0 },
      data: { title: 'Third', outputs: [] },
    };
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [thirdNode],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'c'.repeat(64),
        updated_at: '2026-07-13T00:00:03Z',
      });

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-third-ambiguous',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('response lost');

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expectEditorNodesWithDisplayNumbers([startNode]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore.getState().undoStack[0]?.agentBuilderHistory,
    ).toEqual(
      expect.objectContaining({
        latestOperationId: 'operation-third-ambiguous',
        acknowledged: false,
      }),
    );
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-12T00:00:00Z',
    });
    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it('CAS save 결과 유실 뒤 canonical graph가 base이면 local mutation을 복구한다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      });

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-unapplied',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('response lost');

    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().nodes).toEqual([]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(0);
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('CAS save 뒤 acknowledgement가 실패하면 persisted graph를 local Undo하지 않는다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockRejectedValue(
      new Error('acknowledgement unavailable'),
    );

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-1',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toThrow('acknowledgement unavailable');

    expectEditorNodesWithDisplayNumbers([startNode]);
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
    expect(
      useWorkflowStore.getState().undoStack[0]?.agentBuilderOperation,
    ).toEqual({
      operationId: 'operation-1',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:00Z',
      sessionId: 'session-1',
      revertGraph: { nodes: [], edges: [] },
    });
    expect(
      useWorkflowStore.getState().undoStack[0]?.agentBuilderHistory,
    ).toEqual(expect.objectContaining({ acknowledged: false }));
    expect(
      useWorkflowStore
        .getState()
        .getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'b'.repeat(64),
      updatedAt: '2026-07-13T00:00:00Z',
    });
  });

  it('acknowledgement 응답이 한 번 유실되면 동일 payload로 재시도한다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation)
      .mockRejectedValueOnce(new Error('ack response lost'))
      .mockResolvedValueOnce({
        operation_id: 'operation-1',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      });

    const result = await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(2);
    expect(result.acknowledgement.operation_status).toBe('acknowledged');
    expectEditorNodesWithDisplayNumbers([startNode]);
  });

  it('does not retry acknowledgement after an explicit 4xx rejection', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    const conflict = Object.assign(new Error('task_conflict'), {
      response: { status: 409, data: { detail: 'task_conflict' } },
    });
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockRejectedValue(conflict);

    await expect(
      applyAndSaveAgentBuilderMutation({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        viewport: { x: 0, y: 0, zoom: 1 },
        mutation: {
          operation_id: 'operation-1',
          kind: 'initial_graph',
          base_graph_hash: 'a'.repeat(64),
          expected_workflow_updated_at: '2026-07-12T00:00:00Z',
          expected_result_graph_hash: 'b'.repeat(64),
          operations: [{ op: 'add_node', node: startNode }],
        },
      }),
    ).rejects.toBe(conflict);

    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(1);
    expect(agentBuilderApi.getSession).not.toHaveBeenCalled();
  });

  it('acknowledgement 재시도도 유실되면 matching canonical/session acknowledged 상태로 boundary를 reconcile한다', async () => {
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockRejectedValue(
      new Error('ack response lost'),
    );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      } as any)
      .mockResolvedValueOnce({
        nodes: [startNode],
        edges: [],
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      } as any);
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-1',
        status: 'acknowledged',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-13T00:00:00Z',
        completion_context: {
          parameter_task_id: 'task-completed-after-response-loss',
        },
      },
      parameter_group: null,
    });

    const result = await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(2);
    expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
    expect(result.acknowledgement).toEqual(
      expect.objectContaining({
        operation_id: 'operation-1',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
        completed_task_id: 'task-completed-after-response-loss',
      }),
    );
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(expect.objectContaining({ operationId: 'operation-1' }));
    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderHistory,
    ).toEqual(expect.objectContaining({ acknowledged: true }));
  });

  it('keeps the canonical server base graph for a persisted Undo', async () => {
    const canonicalLegacyNode: Node = {
      id: 'legacy',
      type: 'startNode',
      position: { x: 0, y: 0 },
      data: { title: 'Legacy', triggerType: 'manual', variables: [] },
    };
    const editorLegacyNode: Node = {
      ...canonicalLegacyNode,
      data: { ...canonicalLegacyNode.data, displayNumber: 1 },
    };
    useWorkflowStore.setState({ nodes: [editorLegacyNode], edges: [] });
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [canonicalLegacyNode],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(saveResponse());
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-replace',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      status: 'completed',
    } as any);

    await applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-replace',
        kind: 'replace_workflow',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [
          { op: 'remove_node', node_id: 'legacy' },
          { op: 'add_node', node: startNode },
        ],
      },
    });
    useWorkflowStore.getState().undo();

    expect(
      useWorkflowStore.getState().pendingAgentBuilderRevert?.revertGraph,
    ).toEqual({ nodes: [canonicalLegacyNode], edges: [] });
    expect(useWorkflowStore.getState().nodes).toEqual([editorLegacyNode]);
  });
});

describe('Agent Builder editor dirty preservation', () => {
  afterEach(() => {
    clearWorkflowDraftSaveCoordinatorForTests();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [],
      edges: [],
      undoStack: [],
      redoStack: [],
      features: {},
      envVariables: [],
      runtimeVariables: [],
      hasUnsavedChanges: false,
      isAgentBuilderMutationSaving: false,
    });
  });

  it('preserves dirty state when a manual editor change happens after Agent Builder save payload is captured', async () => {
    let resolveSave!: (value: ReturnType<typeof saveResponse>) => void;
    vi.mocked(workflowApi.syncDraftWorkflow).mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }) as ReturnType<typeof workflowApi.syncDraftWorkflow>,
    );
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-1',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });

    const saving = applyAndSaveAgentBuilderMutation({
      sessionId: 'session-1',
      workflowId: 'workflow-1',
      viewport: { x: 0, y: 0, zoom: 1 },
      mutation: {
        operation_id: 'operation-1',
        kind: 'initial_graph',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        operations: [{ op: 'add_node', node: startNode }],
      },
    });

    for (let attempt = 0; attempt < 10; attempt += 1) {
      if (vi.mocked(workflowApi.syncDraftWorkflow).mock.calls.length > 0) break;
      await Promise.resolve();
    }
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'start',
        position: { x: 120, y: 40 },
      },
    ]);

    resolveSave(saveResponse());
    await saving;

    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
    expect(useWorkflowStore.getState().nodes[0]?.position).toEqual({
      x: 120,
      y: 40,
    });
  });
});
