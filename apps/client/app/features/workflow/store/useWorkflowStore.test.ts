/**
 * useWorkflowStore 테스트
 *
 * Zustand 스토어의 상태 관리 및 워크플로우 에디터 핵심 기능을 테스트합니다.
 * - 노드 추가/삭제
 * - Edge 생성/삭제
 * - 스토어 상태 관리
 *
 * 실행 방법:
 *   cd apps/client
 *   npm test -- --run
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useWorkflowStore } from './useWorkflowStore';
import { workflowApi } from '../api/workflowApi';
import type { DeploymentResponse } from '../types/Deployment';
import type { AnswerNode, CodeNode, Node, StartNode } from '../types/Workflow';
import type { Edge, Connection } from '@xyflow/react';
import { DEFAULT_NODES } from '../constants';
import type { AgentBuilderParameterGroup } from '../api/agentBuilderApi';
import {
  clearWorkflowDraftSaveCoordinatorForTests,
  tryAcquireWorkflowDraftSave,
} from '../utils/workflowDraftSaveCoordinator';

// API 모킹
vi.mock('../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
    createWorkflow: vi.fn(),
    listWorkflowsByApp: vi.fn(),
  },
}));

// 테스트용 초기 상태 저장 및 리셋 헬퍼
const initialState = useWorkflowStore.getState();
const resetStore = () => {
  vi.clearAllMocks();
  clearWorkflowDraftSaveCoordinatorForTests();
  useWorkflowStore.setState(initialState, true);
};

// ============================================================================
// 테스트용 Fixture 데이터
// ============================================================================

const createStartNode = (
  id: string,
  data: Partial<StartNode['data']> & Record<string, unknown> = {},
  position = { x: 0, y: 0 },
): StartNode => ({
  id,
  type: 'startNode',
  position,
  data: {
    title: `Node ${id}`,
    triggerType: 'manual',
    variables: [],
    ...data,
  },
});

const createAnswerNode = (
  id: string,
  data: Partial<AnswerNode['data']> & Record<string, unknown> = {},
  position = { x: 0, y: 0 },
): AnswerNode => ({
  id,
  type: 'answerNode',
  position,
  data: {
    title: `Node ${id}`,
    outputs: [],
    ...data,
  },
});

const createCodeNode = (
  id: string,
  data: Partial<CodeNode['data']> & Record<string, unknown> = {},
  position = { x: 0, y: 0 },
): CodeNode => ({
  id,
  type: 'codeNode',
  position,
  data: {
    title: `Node ${id}`,
    code: 'def main(inputs):\n    return {}',
    inputs: [],
    timeout: 10,
    ...data,
  },
});

const createMockNode = (
  id: string,
  type: NonNullable<Node['type']> = 'startNode',
  position = { x: 0, y: 0 },
): Node => {
  if (type === 'answerNode') return createAnswerNode(id, {}, position);
  if (type === 'codeNode') return createCodeNode(id, {}, position);
  if (type === 'startNode') return createStartNode(id, {}, position);
  return {
    id,
    type,
    position,
    data: { title: `Node ${id}` } as Node['data'],
  } as Node;
};

const createMockEdge = (
  id: string,
  source: string,
  target: string,
  handles: Pick<Edge, 'sourceHandle' | 'targetHandle'> = {},
): Edge => ({
  id,
  source,
  target,
  ...handles,
});

const completedParameterGroup = (): AgentBuilderParameterGroup => ({
  group_id: 'group-1',
  status: 'completed',
  tasks: [
    {
      task_id: 'task-manual',
      group_id: 'group-1',
      step_id: 'step-answer',
      node_id: 'answer',
      node_type: 'answerNode',
      parameter_key: 'outputs',
      label: '응답 출력',
      input_type: 'text',
      required: true,
      defer_policy: 'forbidden',
      status: 'completed',
      task_version: 2,
      stable_order: 0,
      resolution_source: null,
      reason: '최종 응답 값을 정합니다.',
      input_guidance: '응답 값을 입력하세요.',
      configuration_state: 'resolved',
    },
  ],
});

// ============================================================================
// 1. 노드 추가/삭제 테스트
// ============================================================================

describe('노드 추가/삭제 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('setNodes로 노드를 설정할 수 있다', () => {
    const nodes: Node[] = [
      createMockNode('node-1', 'startNode'),
      createMockNode('node-2', 'answerNode'),
    ];

    useWorkflowStore.getState().setNodes(nodes);

    const state = useWorkflowStore.getState();
    expect(state.nodes).toHaveLength(2);
    expect(state.nodes[0].id).toBe('node-1');
    expect(state.nodes[1].id).toBe('node-2');
  });

  it('onNodesChange로 노드를 추가할 수 있다', () => {
    // 초기 노드 설정
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    // 노드 추가 변경 적용
    const newNode = createMockNode('node-2', 'answerNode');
    useWorkflowStore.getState().onNodesChange([{ type: 'add', item: newNode }]);

    const state = useWorkflowStore.getState();
    expect(state.nodes).toHaveLength(2);
  });

  it('onNodesChange로 노드를 삭제할 수 있다', () => {
    // 초기에 2개 노드 설정
    useWorkflowStore
      .getState()
      .setNodes([createMockNode('node-1'), createMockNode('node-2')]);

    // node-1 삭제
    useWorkflowStore
      .getState()
      .onNodesChange([{ type: 'remove', id: 'node-1' }]);

    const state = useWorkflowStore.getState();
    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0].id).toBe('node-2');
  });

  it('선택한 중간 노드를 삭제하면 앞뒤 노드를 자동 재연결한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-a', 'startNode'),
        { ...createMockNode('node-b', 'codeNode'), selected: true },
        createMockNode('node-c', 'answerNode'),
      ]);
    useWorkflowStore.getState().setEdges([
      createMockEdge('edge-a-b', 'node-a', 'node-b', {
        sourceHandle: 'result',
      }),
      createMockEdge('edge-b-c', 'node-b', 'node-c', {
        targetHandle: 'input',
      }),
    ]);

    useWorkflowStore.getState().deleteSelectedElements();

    const state = useWorkflowStore.getState();
    expect(state.nodes.map((node) => node.id)).toEqual(['node-a', 'node-c']);
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0]).toMatchObject({
      source: 'node-a',
      sourceHandle: 'result',
      target: 'node-c',
      targetHandle: 'input',
    });
  });

  it('자동 재연결은 이미 같은 연결이 있으면 중복 edge를 만들지 않는다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-a', 'startNode'),
        { ...createMockNode('node-b', 'codeNode'), selected: true },
        createMockNode('node-c', 'answerNode'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([
        createMockEdge('edge-a-b', 'node-a', 'node-b'),
        createMockEdge('edge-b-c', 'node-b', 'node-c'),
        createMockEdge('edge-a-c', 'node-a', 'node-c'),
      ]);

    useWorkflowStore.getState().deleteSelectedElements();

    const state = useWorkflowStore.getState();
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].id).toBe('edge-a-c');
  });

  it('자동 재연결이 그래프 검증을 통과하지 못하면 삭제만 수행한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-a', 'codeNode'),
        { ...createMockNode('node-b', 'codeNode'), selected: true },
        createMockNode('node-c', 'startNode'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([
        createMockEdge('edge-a-b', 'node-a', 'node-b'),
        createMockEdge('edge-b-c', 'node-b', 'node-c'),
      ]);

    useWorkflowStore.getState().deleteSelectedElements();

    const state = useWorkflowStore.getState();
    expect(state.nodes.map((node) => node.id)).toEqual(['node-a', 'node-c']);
    expect(state.edges).toEqual([]);
  });

  it('선택 노드 삭제와 자동 재연결은 undo 한 번으로 복구된다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-a', 'startNode'),
        { ...createMockNode('node-b', 'codeNode'), selected: true },
        createMockNode('node-c', 'answerNode'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([
        createMockEdge('edge-a-b', 'node-a', 'node-b'),
        createMockEdge('edge-b-c', 'node-b', 'node-c'),
      ]);

    useWorkflowStore.getState().deleteSelectedElements();
    useWorkflowStore.getState().undo();

    const state = useWorkflowStore.getState();
    expect(state.nodes.map((node) => node.id)).toEqual([
      'node-a',
      'node-b',
      'node-c',
    ]);
    expect(state.edges.map((edge) => edge.id)).toEqual([
      'edge-a-b',
      'edge-b-c',
    ]);
  });

  it('onNodesChange로 노드 위치를 변경할 수 있다', () => {
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 100, y: 200 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 100, y: 200 });
  });

  it('React Flow dimensions 측정은 저장 또는 Undo 변경으로 기록하지 않는다', () => {
    const node = createMockNode('node-1');
    useWorkflowStore.getState().setNodes([node]);
    useWorkflowStore.setState({
      hasUnsavedChanges: false,
      undoStack: [],
      redoStack: [],
    });

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'dimensions',
        id: 'node-1',
        dimensions: { width: 420, height: 200 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].measured).toEqual({ width: 420, height: 200 });
    expect(state.hasUnsavedChanges).toBe(false);
    expect(state.undoStack).toEqual([]);
  });

  it('기본 10px grid snap으로 노드 위치를 보정한다', () => {
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 104, y: 207 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 100, y: 210 });
  });

  it.each([
    {
      gridSize: 5 as const,
      position: { x: 103, y: 207 },
      expected: { x: 105, y: 205 },
    },
    {
      gridSize: 20 as const,
      position: { x: 111, y: 231 },
      expected: { x: 120, y: 240 },
    },
  ])(
    '$gridSize px grid snap으로 노드 위치를 보정한다',
    ({ gridSize, position, expected }) => {
      useWorkflowStore.getState().setSnapGridSize(gridSize);
      useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

      useWorkflowStore.getState().onNodesChange([
        {
          type: 'position',
          id: 'node-1',
          position,
        },
      ]);

      const state = useWorkflowStore.getState();
      expect(state.nodes[0].position).toEqual(expected);
    },
  );

  it('snap 설정이 off면 노드 위치를 보정하지 않는다', () => {
    useWorkflowStore.getState().setSnapGridSize('off');
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 104, y: 207 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 104, y: 207 });
  });

  it('Alt로 snap이 임시 해제되면 노드 위치를 보정하지 않는다', () => {
    useWorkflowStore.getState().setSnapTemporarilyDisabled(true);
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 104, y: 207 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 104, y: 207 });
  });

  it('여러 노드 이동 시 그룹 origin의 snap delta로 상대 위치를 유지한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-1', 'startNode', { x: 3, y: 7 }),
        createMockNode('node-2', 'answerNode', { x: 18, y: 32 }),
      ]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 16, y: 23 },
      },
      {
        type: 'position',
        id: 'node-2',
        position: { x: 31, y: 48 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 20, y: 20 });
    expect(state.nodes[1].position).toEqual({ x: 35, y: 45 });
  });

  it('positionChanges 순서가 노드 순서와 달라도 그룹 상대 위치를 유지한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-1', 'startNode', { x: 3, y: 7 }),
        createMockNode('node-2', 'answerNode', { x: 18, y: 32 }),
        createMockNode('node-3', 'codeNode', { x: 41, y: 11 }),
      ]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-3',
        position: { x: 54, y: 27 },
      },
      {
        type: 'position',
        id: 'node-2',
        position: { x: 31, y: 48 },
      },
      {
        type: 'position',
        id: 'node-1',
        position: { x: 16, y: 23 },
      },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].position).toEqual({ x: 20, y: 20 });
    expect(state.nodes[1].position).toEqual({ x: 35, y: 45 });
    expect(state.nodes[2].position).toEqual({ x: 58, y: 24 });
  });
});

describe('Agent Builder GraphMutation transaction', () => {
  beforeEach(() => {
    resetStore();
    useWorkflowStore.setState({
      nodes: [createStartNode('start'), createAnswerNode('answer')],
      edges: [createMockEdge('old-edge', 'start', 'answer')],
      undoStack: [],
      redoStack: [],
    });
  });

  it('keeps the global save guard active until every overlapping save finishes', () => {
    const store = useWorkflowStore.getState();

    store.setAgentBuilderMutationSaving(true);
    store.setAgentBuilderMutationSaving(true);
    store.setAgentBuilderMutationSaving(false);

    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      true,
    );

    store.setAgentBuilderMutationSaving(false);

    expect(useWorkflowStore.getState().isAgentBuilderMutationSaving).toBe(
      false,
    );
  });

  it('assigns display numbers to Agent Builder nodes so edge handles remain visible', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-display-numbers',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-12T00:00:00Z',
      operations: [
        { op: 'remove_edge', edge_id: 'old-edge' },
        {
          op: 'add_node',
          node: createMockNode('llm', 'llmNode', { x: 400, y: 0 }),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('start-llm', 'start', 'llm'),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('llm-answer', 'llm', 'answer'),
        },
      ],
    });

    expect(
      useWorkflowStore.getState().nodes.map((node) => node.data.displayNumber),
    ).toEqual([1, 2, 3]);
  });

  it('mutation 전체를 한 번의 Undo 단위로 적용한다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-1',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-12T00:00:00Z',
      operations: [
        { op: 'remove_edge', edge_id: 'old-edge' },
        {
          op: 'add_node',
          node: createMockNode('llm', 'llmNode', { x: 400, y: 0 }),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('start-llm', 'start', 'llm'),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('llm-answer', 'llm', 'answer'),
        },
      ],
    });

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'answer',
      'llm',
    ]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'answer',
    ]);
    expect(useWorkflowStore.getState().edges).toEqual([
      createMockEdge('old-edge', 'start', 'answer'),
    ]);
  });

  it('전체 workflow 교체를 한 번의 Undo로 기존 graph까지 복구한다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-replace',
      kind: 'replace_workflow',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'remove_edge', edge_id: 'old-edge' },
        { op: 'remove_node', node_id: 'start' },
        { op: 'remove_node', node_id: 'answer' },
        {
          op: 'add_node',
          node: createMockNode('webhook', 'webhookTrigger', { x: 0, y: 0 }),
        },
        {
          op: 'add_node',
          node: createMockNode('new-answer', 'answerNode', { x: 400, y: 0 }),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('new-edge', 'webhook', 'new-answer'),
        },
      ],
    });

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'webhook',
      'new-answer',
    ]);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'answer',
    ]);
    expect(useWorkflowStore.getState().edges).toEqual([
      createMockEdge('old-edge', 'start', 'answer'),
    ]);
  });

  it('operation 하나라도 실패하면 부분 적용하지 않는다', () => {
    const before = {
      nodes: structuredClone(useWorkflowStore.getState().nodes),
      edges: structuredClone(useWorkflowStore.getState().edges),
    };

    expect(() =>
      useWorkflowStore.getState().applyAgentBuilderGraphMutation({
        operation_id: 'operation-invalid',
        kind: 'parameter_update',
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        operations: [
          {
            op: 'replace_node_data',
            node_id: 'answer',
            data: { title: 'Changed', outputs: [] },
          },
          { op: 'remove_node', node_id: 'missing' },
        ],
      }),
    ).toThrow('missing');

    expect(useWorkflowStore.getState().nodes).toEqual(before.nodes);
    expect(useWorkflowStore.getState().edges).toEqual(before.edges);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(0);
  });

  it('acknowledged mutation Undo를 persisted revert로 표시한다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-persisted',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-12T00:00:00Z',
      operations: [
        { op: 'remove_edge', edge_id: 'old-edge' },
        {
          op: 'add_node',
          node: createMockNode('llm', 'llmNode', { x: 400, y: 0 }),
        },
        {
          op: 'add_edge',
          edge: createMockEdge('start-llm', 'start', 'llm'),
        },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-persisted',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:00Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-persisted', true);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toEqual({
      operationId: 'operation-persisted',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:00Z',
      sessionId: 'session-1',
    });

    const graphAfterFirstUndo = structuredClone(
      useWorkflowStore.getState().nodes,
    );
    useWorkflowStore.getState().undo();
    expect(useWorkflowStore.getState().nodes).toEqual(graphAfterFirstUndo);
    useWorkflowStore.getState().redo();
    expect(useWorkflowStore.getState().nodes).toEqual(graphAfterFirstUndo);
  });

  it('Agent Builder 전체 실행을 pre-run에서 최종 graph까지 하나의 history 경계로 합친다', () => {
    const preRunGraph = {
      nodes: structuredClone(useWorkflowStore.getState().nodes),
      edges: structuredClone(useWorkflowStore.getState().edges),
    };
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-first',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        {
          op: 'add_node',
          node: createMockNode('first', 'llmNode', { x: 400, y: 0 }),
        },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-first',
      resultGraphHash: 'a'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
      revertGraph: preRunGraph,
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-first');

    useWorkflowStore.getState().applyAgentBuilderGraphMutation(
      {
        operation_id: 'operation-second',
        kind: 'parameter_update',
        expected_workflow_updated_at: '2026-07-13T00:00:01Z',
        operations: [
          {
            op: 'replace_node_data',
            node_id: 'answer',
            data: { title: 'Final answer', outputs: [] },
          },
        ],
      },
      'session-1',
    );
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-second',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:02Z',
      sessionId: 'session-1',
      revertGraph: {
        nodes: [createMockNode('wrong-intermediate')],
        edges: [],
      },
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-second', true);

    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(
      expect.objectContaining({
        operationId: 'operation-first',
        revertGraph: preRunGraph,
      }),
    );
    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderHistory,
    ).toEqual(
      expect.objectContaining({
        latestOperationId: 'operation-second',
        acknowledged: true,
      }),
    );

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(preRunGraph.nodes);
    expect(useWorkflowStore.getState().edges).toEqual(preRunGraph.edges);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toEqual(
      expect.objectContaining({
        operationId: 'operation-first',
        revertGraph: preRunGraph,
      }),
    );
  });

  it('완료 후 첫 Undo는 수동 설정 이력보다 stable_order가 가장 큰 재편집 task를 다시 연다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-completed',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        {
          op: 'replace_node_data',
          node_id: 'answer',
          data: { title: 'Configured answer', outputs: ['value'] },
        },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-completed',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-completed');
    const parameterGroup = completedParameterGroup();
    parameterGroup.tasks.push(
      {
        ...parameterGroup.tasks[0],
        task_id: 'task-auto',
        parameter_key: 'automatic',
        label: '자동 확정 값',
        status: 'completed',
        stable_order: 10,
        resolution_source: 'catalog_default',
      },
      {
        ...parameterGroup.tasks[0],
        task_id: 'task-canceled',
        parameter_key: 'canceled',
        label: '취소된 값',
        status: 'canceled',
        stable_order: 20,
      },
    );
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory(
        'session-1',
        parameterGroup,
        'task-manual',
        true,
      );
    const completedGraph = structuredClone(useWorkflowStore.getState().nodes);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(completedGraph);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(1);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({
      sessionId: 'session-1',
      parameterGroup: expect.objectContaining({
        status: 'active',
        tasks: expect.arrayContaining([
          expect.objectContaining({ task_id: 'task-auto', status: 'active' }),
          expect.objectContaining({
            task_id: 'task-manual',
            status: 'completed',
          }),
          expect.objectContaining({
            task_id: 'task-canceled',
            status: 'canceled',
          }),
        ]),
      }),
    });

    useWorkflowStore.getState().redo();

    expect(useWorkflowStore.getState().nodes).toEqual(completedGraph);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({
      sessionId: 'session-1',
      parameterGroup: expect.objectContaining({ status: 'completed' }),
    });
    expect(useWorkflowStore.getState().redoStack).toHaveLength(0);
  });

  it('재열린 parameter UI에서 전체 Redo 뒤 다음 Undo는 task 재진입 없이 같은 boundary를 직접 복구한다', () => {
    const preRunNodes = structuredClone(useWorkflowStore.getState().nodes);
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-two-stage',
      kind: 'replace_workflow',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'remove_edge', edge_id: 'old-edge' },
        { op: 'remove_node', node_id: 'start' },
        { op: 'remove_node', node_id: 'answer' },
        {
          op: 'add_node',
          node: createMockNode('replacement', 'startNode'),
        },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-two-stage',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
      revertGraph: {
        nodes: preRunNodes,
        edges: [createMockEdge('old-edge', 'start', 'answer')],
      },
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-two-stage');
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory(
        'session-1',
        completedParameterGroup(),
        'task-manual',
        true,
      );
    const finalNodes = structuredClone(useWorkflowStore.getState().nodes);

    useWorkflowStore.getState().undo();
    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(preRunNodes);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({ sessionId: 'session-1', parameterGroup: null });
    expect(
      useWorkflowStore.getState().pendingAgentBuilderRevert,
    ).not.toBeNull();

    useWorkflowStore.getState().clearPendingAgentBuilderRevert();
    useWorkflowStore.getState().redo();

    expect(useWorkflowStore.getState().nodes).toEqual(finalNodes);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({ sessionId: 'session-1', parameterGroup: null });
    expect(
      useWorkflowStore.getState().undoStack.at(-1)?.agentBuilderOperation,
    ).toEqual(expect.objectContaining({ operationId: 'operation-two-stage' }));

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(preRunNodes);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toEqual(
      expect.objectContaining({ operationId: 'operation-two-stage' }),
    );
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({ sessionId: 'session-1', parameterGroup: null });
  });

  it('완료 뒤 수동 editor 변경은 Agent Builder 경계보다 먼저 Undo된다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-before-manual',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        {
          op: 'add_node',
          node: createMockNode('generated', 'llmNode'),
        },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-before-manual',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-before-manual');
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory(
        'session-1',
        completedParameterGroup(),
        'task-manual',
        true,
      );
    const agentBuilderGraph = structuredClone(
      useWorkflowStore.getState().nodes,
    );
    useWorkflowStore
      .getState()
      .setNodes([...agentBuilderGraph, createMockNode('manual')]);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(agentBuilderGraph);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toBeNull();

    useWorkflowStore.getState().undo();
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual(
      expect.objectContaining({
        parameterGroup: expect.objectContaining({ status: 'active' }),
      }),
    );
  });

  it('parameter task가 없으면 첫 Undo가 즉시 pre-run graph를 복구한다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-no-task',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'add_node', node: createMockNode('generated', 'llmNode') },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-no-task',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-no-task');
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory('session-1', null, null, true);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes.map((item) => item.id)).toEqual([
      'start',
      'answer',
    ]);
    expect(
      useWorkflowStore.getState().pendingAgentBuilderRevert,
    ).not.toBeNull();
  });

  it.each(['completed', 'skipped', 'deferred'] as const)(
    '자동 확정 또는 미설정 %s task도 stable_order 기준 첫 Undo 재진입 대상이 된다',
    (terminalStatus) => {
      useWorkflowStore.getState().applyAgentBuilderGraphMutation({
        operation_id: 'operation-auto-task',
        kind: 'graph_edit',
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        operations: [
          { op: 'add_node', node: createMockNode('generated', 'llmNode') },
        ],
      });
      useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
        operationId: 'operation-auto-task',
        resultGraphHash: 'b'.repeat(64),
        workflowUpdatedAt: '2026-07-13T00:00:01Z',
        sessionId: 'session-1',
      });
      useWorkflowStore
        .getState()
        .markLatestAgentBuilderMutationAcknowledged('operation-auto-task');
      const parameterGroup = completedParameterGroup();
      parameterGroup.tasks.push({
        ...parameterGroup.tasks[0],
        task_id: 'task-terminal-last',
        parameter_key: 'last',
        status: terminalStatus,
        stable_order: 99,
        resolution_source:
          terminalStatus === 'completed' ? 'catalog_default' : null,
      });
      useWorkflowStore
        .getState()
        .setAgentBuilderParameterHistory(
          'session-1',
          parameterGroup,
          null,
          true,
        );
      const finalGraph = structuredClone(useWorkflowStore.getState().nodes);

      useWorkflowStore.getState().undo();

      expect(useWorkflowStore.getState().nodes).toEqual(finalGraph);
      expect(
        useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
      ).toEqual({
        sessionId: 'session-1',
        parameterGroup: expect.objectContaining({
          status: 'active',
          tasks: expect.arrayContaining([
            expect.objectContaining({
              task_id: 'task-terminal-last',
              status: 'active',
            }),
          ]),
        }),
      });
      expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    },
  );

  it('첫 Undo 재진입은 secret, credential, Slack/GitHub task를 제외하지 않고 stable_order만 따른다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-sensitive-task',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'add_node', node: createMockNode('generated', 'slackPostNode') },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-sensitive-task',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-sensitive-task');
    const parameterGroup = completedParameterGroup();
    parameterGroup.tasks.push(
      {
        ...parameterGroup.tasks[0],
        task_id: 'task-secret',
        parameter_key: 'api_key',
        input_type: 'text',
        status: 'completed',
        stable_order: 98,
        sensitivity: 'secret_forbidden',
      },
      {
        ...parameterGroup.tasks[0],
        task_id: 'task-slack-credential',
        parameter_key: 'credential_id',
        input_type: 'credential_ref',
        node_type: 'slackPostNode',
        status: 'deferred',
        stable_order: 99,
        sensitivity: 'reference_only',
      },
    );
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory('session-1', parameterGroup, null, true);

    useWorkflowStore.getState().undo();

    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toEqual({
      sessionId: 'session-1',
      parameterGroup: expect.objectContaining({
        status: 'active',
        tasks: expect.arrayContaining([
          expect.objectContaining({
            task_id: 'task-slack-credential',
            status: 'active',
          }),
          expect.objectContaining({
            task_id: 'task-secret',
            status: 'completed',
          }),
        ]),
      }),
    });
  });

  it('parameter UI가 active이면 Ctrl+Z가 completed boundary를 소비하지 않는다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-active-task',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'add_node', node: createMockNode('generated', 'llmNode') },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-active-task',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged('operation-active-task');
    const activeGroup = completedParameterGroup();
    activeGroup.status = 'active';
    activeGroup.tasks[0].status = 'active';
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory('session-1', activeGroup, null, false);

    const activeGraph = structuredClone(useWorkflowStore.getState().nodes);
    const undoCount = useWorkflowStore.getState().undoStack.length;

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(activeGraph);
    expect(useWorkflowStore.getState().undoStack).toHaveLength(undoCount);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toBeNull();
    expect(useWorkflowStore.getState().agentBuilderHistoryNotice).toBeTruthy();
  });

  it('save/ack 진행 중에는 완료 Undo 단계를 시작하지 않는다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-saving',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'add_node', node: createMockNode('generated', 'llmNode') },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-saving',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory(
        'session-1',
        completedParameterGroup(),
        'task-manual',
      );
    const savingGraph = structuredClone(useWorkflowStore.getState().nodes);
    useWorkflowStore.getState().setAgentBuilderMutationSaving(true);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(savingGraph);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toBeNull();
  });

  it('저장은 끝났지만 acknowledgement가 끝나지 않은 경계는 Undo하지 않는다', () => {
    useWorkflowStore.getState().applyAgentBuilderGraphMutation({
      operation_id: 'operation-pending-ack',
      kind: 'graph_edit',
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      operations: [
        { op: 'add_node', node: createMockNode('generated', 'llmNode') },
      ],
    });
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: 'operation-pending-ack',
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:01Z',
      sessionId: 'session-1',
    });
    useWorkflowStore
      .getState()
      .setAgentBuilderParameterHistory(
        'session-1',
        completedParameterGroup(),
        'task-manual',
      );
    const persistedGraph = structuredClone(useWorkflowStore.getState().nodes);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes).toEqual(persistedGraph);
    expect(useWorkflowStore.getState().pendingAgentBuilderRevert).toBeNull();
    expect(useWorkflowStore.getState().agentBuilderHistoryNotice).toContain(
      '확인',
    );
  });

  it('reload 시 memory-only Redo와 parameter presentation을 복구하지 않는다', () => {
    useWorkflowStore.setState({
      redoStack: [{ nodes: [createMockNode('redo')], edges: [] }],
      recoveredAgentBuilderParameterGroup: {
        sessionId: 'session-1',
        parameterGroup: completedParameterGroup(),
      },
    });

    useWorkflowStore.getState().setWorkflowData(
      {
        nodes: [createMockNode('loaded')],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
      useWorkflowStore.getState().activeWorkflowId,
    );

    expect(useWorkflowStore.getState().redoStack).toEqual([]);
    expect(
      useWorkflowStore.getState().recoveredAgentBuilderParameterGroup,
    ).toBeNull();
  });

  it('refreshes the next persisted Undo CAS boundary after a nested revert', () => {
    const firstResultGraphHash = 'a'.repeat(64);
    useWorkflowStore.setState({
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
    });

    useWorkflowStore.getState().refreshNextAgentBuilderRevertBoundary({
      resultGraphHash: firstResultGraphHash,
      workflowUpdatedAt: '2026-07-13T00:00:03Z',
    });

    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(
      expect.objectContaining({ workflowUpdatedAt: '2026-07-13T00:00:03Z' }),
    );
  });

  it('does not refresh the next persisted Undo boundary for another graph', () => {
    useWorkflowStore.setState({
      undoStack: [
        {
          nodes: [],
          edges: [],
          agentBuilderOperation: {
            operationId: 'operation-first',
            resultGraphHash: 'a'.repeat(64),
            workflowUpdatedAt: '2026-07-13T00:00:01Z',
            sessionId: 'session-1',
          },
        },
      ],
    });

    useWorkflowStore.getState().refreshNextAgentBuilderRevertBoundary({
      resultGraphHash: 'b'.repeat(64),
      workflowUpdatedAt: '2026-07-13T00:00:03Z',
    });

    expect(
      useWorkflowStore.getState().undoStack[0].agentBuilderOperation,
    ).toEqual(
      expect.objectContaining({
        workflowUpdatedAt: '2026-07-13T00:00:01Z',
      }),
    );
  });
});

describe('canonical draft metadata', () => {
  beforeEach(() => {
    resetStore();
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
    });
  });

  it('stores and clears canonical graph hash metadata per workflow', () => {
    useWorkflowStore.getState().setCanonicalDraftMetadata({
      workflowId: 'wf-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-13T00:00:00Z',
    });

    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('wf-1'),
    ).toEqual({
      workflowId: 'wf-1',
      graphHash: 'a'.repeat(64),
      updatedAt: '2026-07-13T00:00:00Z',
    });

    useWorkflowStore.getState().clearCanonicalDraftMetadata('wf-1');

    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('wf-1'),
    ).toBeNull();
  });

  it('updates canonical metadata from successful version restore save', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });

    await useWorkflowStore.getState().restoreVersion({
      id: 'deployment-1',
      app_id: 'app-1',
      version: 1,
      created_by: 'user-1',
      created_at: '2026-07-01T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored', 'startNode')],
        edges: [],
        features: { nextNodeDisplayNumber: 1 },
      },
    } as DeploymentResponse);

    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('wf-1'),
    ).toEqual({
      workflowId: 'wf-1',
      graphHash: 'b'.repeat(64),
      updatedAt: '2026-07-13T00:00:01Z',
    });
  });

  it('keeps the local workflow viewport equal to the saved version restore payload', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 320, y: -180, zoom: 1.75 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    useWorkflowStore.setState((state) => ({
      workflows: state.workflows.map((workflow) =>
        workflow.id === 'wf-1'
          ? {
              ...workflow,
              viewport: { x: 320, y: -180, zoom: 1.75 },
            }
          : workflow,
      ),
      hasUnsavedChanges: true,
    }));

    await useWorkflowStore.getState().restoreVersion({
      id: 'deployment-viewport',
      app_id: 'app-1',
      version: 1,
      created_by: 'user-1',
      created_at: '2026-07-01T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored', 'startNode')],
        edges: [],
        features: { nextNodeDisplayNumber: 1 },
      },
    } as DeploymentResponse);

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({
        viewport: { x: 0, y: 0, zoom: 1 },
      }),
    );
    expect(
      useWorkflowStore
        .getState()
        .workflows.find((workflow) => workflow.id === 'wf-1')?.viewport,
    ).toEqual({ x: 0, y: 0, zoom: 1 });
    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(false);
  });
});

// ============================================================================
// 1-1. 캔버스 히스토리/클립보드 테스트
// ============================================================================

describe('캔버스 히스토리/클립보드 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('undo/redo로 그래프 변경을 되돌리고 다시 적용할 수 있다', () => {
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);
    useWorkflowStore
      .getState()
      .setNodes([createMockNode('node-1'), createMockNode('node-2')]);

    useWorkflowStore.getState().undo();
    expect(useWorkflowStore.getState().nodes).toHaveLength(1);
    expect(useWorkflowStore.getState().nodes[0].id).toBe('node-1');

    useWorkflowStore.getState().redo();
    expect(useWorkflowStore.getState().nodes).toHaveLength(2);
    expect(useWorkflowStore.getState().nodes[1].id).toBe('node-2');
  });

  it('선택 변경은 undo 스택에 기록하지 않는다', () => {
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);
    useWorkflowStore
      .getState()
      .onNodesChange([{ type: 'select', id: 'node-1', selected: true }]);

    useWorkflowStore.getState().undo();
    expect(useWorkflowStore.getState().nodes).toHaveLength(
      DEFAULT_NODES.length,
    );
  });

  it('선택 노드를 복사하고 붙여넣을 때 새 ID와 오프셋 위치를 부여한다', () => {
    const selectedNode = {
      ...createMockNode('node-1'),
      selected: true,
      position: { x: 10, y: 20 },
    };
    useWorkflowStore.getState().setNodes([selectedNode]);

    useWorkflowStore.getState().copySelectedNodes();
    useWorkflowStore.getState().pasteCopiedNodes();

    const pastedNode = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id !== 'node-1');

    expect(useWorkflowStore.getState().nodes).toHaveLength(2);
    expect(pastedNode?.id).toContain('node-1-copy-');
    expect(pastedNode?.selected).toBe(true);
    expect(pastedNode?.position).toEqual({ x: 50, y: 60 });
  });

  it('붙여넣은 노드 데이터의 내부 노드 참조를 새 ID로 재매핑한다', () => {
    useWorkflowStore.getState().setNodes([
      {
        ...createMockNode('source-node'),
        selected: true,
        position: { x: 0, y: 0 },
      },
      {
        ...createAnswerNode('target-node', {
          title: 'Target',
          value_selector: ['source-node', 'output'],
        }),
        selected: true,
        position: { x: 100, y: 0 },
      },
    ]);

    useWorkflowStore.getState().copySelectedNodes();
    useWorkflowStore.getState().pasteCopiedNodes();

    const pastedSource = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('source-node-copy-'));
    const pastedTarget = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('target-node-copy-'));

    expect(pastedTarget?.data.value_selector).toEqual([
      pastedSource?.id,
      'output',
    ]);
  });

  it('붙여넣기 재매핑은 참조 필드만 바꾸고 새 displayNumber를 배정한다', () => {
    useWorkflowStore.getState().setNodes([
      {
        ...createStartNode('source-node', {
          title: 'source-node',
          description: 'source-node',
          content: 'source-node',
          displayNumber: 12,
        }),
        selected: true,
      },
      {
        ...createAnswerNode('target-node', {
          title: 'Target',
          value_selector: ['source-node', 'output'],
        }),
        selected: true,
      },
    ]);

    useWorkflowStore.getState().copySelectedNodes();
    useWorkflowStore.getState().pasteCopiedNodes();

    const pastedSource = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('source-node-copy-'));
    const pastedTarget = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('target-node-copy-'));

    expect(pastedSource?.data.title).toBe('source-node');
    expect(pastedSource?.data.description).toBe('source-node');
    expect(pastedSource?.data.content).toBe('source-node');
    expect(pastedSource?.data.displayNumber).toBe(1);
    expect(pastedTarget?.data.displayNumber).toBe(2);
    expect(pastedSource?.data.displayNumber).not.toBe(12);
    expect(useWorkflowStore.getState().features.nextNodeDisplayNumber).toBe(3);
    expect(pastedTarget?.data.value_selector).toEqual([
      pastedSource?.id,
      'output',
    ]);
  });

  it('노드 복제는 Slack과 GitHub secret reference만 제거하고 일반 설정은 보존한다', () => {
    const slackReference =
      'workflow-node-secret://00000000-0000-4000-8000-000000000001';
    const webhookReference =
      'workflow-node-secret://00000000-0000-4000-8000-000000000002';
    const githubReference =
      'workflow-node-secret://00000000-0000-4000-8000-000000000003';
    useWorkflowStore.getState().setNodes([
      {
        ...createMockNode('slack-api'),
        type: 'slackPostNode',
        selected: true,
        data: {
          title: 'Slack API',
          slackMode: 'api',
          channel: 'C123',
          message: 'hello',
          authConfig: { token: slackReference },
        },
      } as Node,
      {
        ...createMockNode('slack-webhook'),
        type: 'slackPostNode',
        selected: true,
        data: {
          title: 'Slack Webhook',
          slackMode: 'webhook',
          url: webhookReference,
          message: 'webhook message',
        },
      } as Node,
      {
        ...createMockNode('github'),
        type: 'githubNode',
        selected: true,
        data: {
          title: 'GitHub',
          action: 'get_pr',
          repo_owner: 'octo',
          repo_name: 'repo',
          pr_number: '15',
          api_token: githubReference,
        },
      } as Node,
    ]);

    useWorkflowStore.getState().duplicateSelectedNodes();

    const duplicates = useWorkflowStore
      .getState()
      .nodes.filter((node) => node.id.includes('-copy-'));
    const slackApi = duplicates.find((node) =>
      node.id.startsWith('slack-api-copy-'),
    );
    const slackWebhook = duplicates.find((node) =>
      node.id.startsWith('slack-webhook-copy-'),
    );
    const github = duplicates.find((node) =>
      node.id.startsWith('github-copy-'),
    );

    expect(slackApi?.data.authConfig).toEqual({});
    expect(slackApi?.data.channel).toBe('C123');
    expect(slackApi?.data.message).toBe('hello');
    expect(slackWebhook?.data.url).toBeUndefined();
    expect(slackWebhook?.data.message).toBe('webhook message');
    expect(github?.data.api_token).toBeUndefined();
    expect(github?.data.repo_owner).toBe('octo');
    expect(github?.data.pr_number).toBe('15');
  });

  it('selector 배열은 첫 번째 슬롯만 새 ID로 재매핑하고 이후 key 값은 보존한다', () => {
    useWorkflowStore.getState().setNodes([
      {
        ...createMockNode('source-node'),
        selected: true,
      },
      {
        ...createMockNode('target-node'),
        selected: true,
      },
      {
        ...createAnswerNode('consumer-node', {
          title: 'Consumer',
          value_selector: ['source-node', 'target-node'],
        }),
        selected: true,
      },
    ]);

    useWorkflowStore.getState().duplicateSelectedNodes();

    const duplicatedSource = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('source-node-copy-'));
    const duplicatedConsumer = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('consumer-node-copy-'));

    expect(duplicatedConsumer?.data.value_selector).toEqual([
      duplicatedSource?.id,
      'target-node',
    ]);
  });

  it('선택 노드를 즉시 복제하고 클립보드 상태는 덮어쓰지 않는다', () => {
    useWorkflowStore
      .getState()
      .setNodes([{ ...createMockNode('clipboard-node'), selected: true }]);
    useWorkflowStore.getState().copySelectedNodes();

    useWorkflowStore.getState().setNodes([
      {
        ...createStartNode('source-node', {
          title: 'source-node',
          description: 'source-node',
          content: 'source-node',
          name: 'source-node',
          displayNumber: 12,
        }),
        selected: true,
        position: { x: 10, y: 20 },
      },
      {
        ...createAnswerNode('target-node', {
          title: 'Target',
          value_selector: ['source-node', 'output'],
        }),
        selected: true,
        position: { x: 100, y: 20 },
      },
    ]);
    useWorkflowStore
      .getState()
      .setEdges([
        createMockEdge('edge-source-target', 'source-node', 'target-node'),
      ]);

    useWorkflowStore.getState().duplicateSelectedNodes();

    const state = useWorkflowStore.getState();
    const duplicatedSource = state.nodes.find((node) =>
      node.id.startsWith('source-node-copy-'),
    );
    const duplicatedTarget = state.nodes.find((node) =>
      node.id.startsWith('target-node-copy-'),
    );
    const duplicatedEdge = state.edges.find((edge) =>
      edge.id.startsWith('edge-source-target-copy-'),
    );

    expect(state.nodes).toHaveLength(4);
    expect(duplicatedSource?.selected).toBe(true);
    expect(duplicatedTarget?.selected).toBe(true);
    expect(duplicatedSource?.position).toEqual({ x: 50, y: 60 });
    expect(duplicatedTarget?.position).toEqual({ x: 140, y: 60 });
    expect(duplicatedEdge?.source).toBe(duplicatedSource?.id);
    expect(duplicatedEdge?.target).toBe(duplicatedTarget?.id);
    expect(duplicatedTarget?.data.value_selector).toEqual([
      duplicatedSource?.id,
      'output',
    ]);
    expect(duplicatedSource?.data.title).toBe('source-node');
    expect(duplicatedSource?.data.description).toBe('source-node');
    expect(duplicatedSource?.data.content).toBe('source-node');
    expect(duplicatedSource?.data.name).toBe('source-node');
    expect(duplicatedSource?.data.displayNumber).toBe(1);
    expect(duplicatedTarget?.data.displayNumber).toBe(2);
    expect(duplicatedSource?.data.displayNumber).not.toBe(12);
    expect(state.features.nextNodeDisplayNumber).toBe(3);
    expect(state.copiedNodes.map((node) => node.id)).toEqual([
      'clipboard-node',
    ]);
  });

  it('code node와 upstream node를 함께 복제하면 inputs[].source의 node id만 새 ID로 재매핑한다', () => {
    useWorkflowStore.getState().setNodes([
      {
        ...createStartNode('source-node'),
        selected: true,
      },
      {
        ...createCodeNode('code-node', {
          inputs: [
            { name: 'result', source: 'source-node.output' },
            { name: 'external', source: 'external-node.output' },
            { name: 'literal', source: 'plain-user-text' },
          ],
        }),
        selected: true,
      },
    ]);

    useWorkflowStore.getState().duplicateSelectedNodes();

    const duplicatedSource = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('source-node-copy-'));
    const duplicatedCode = useWorkflowStore
      .getState()
      .nodes.find(
        (node): node is CodeNode =>
          node.type === 'codeNode' && node.id.startsWith('code-node-copy-'),
      );

    expect(duplicatedCode?.data.inputs).toEqual([
      { name: 'result', source: `${duplicatedSource?.id}.output` },
      { name: 'external', source: 'external-node.output' },
      { name: 'literal', source: 'plain-user-text' },
    ]);
  });

  it('code node와 upstream node를 함께 붙여넣으면 inputs[].source의 node id만 새 ID로 재매핑한다', () => {
    useWorkflowStore.getState().setNodes([
      {
        ...createStartNode('source-node'),
        selected: true,
      },
      {
        ...createCodeNode('code-node', {
          inputs: [{ name: 'result', source: 'source-node.output' }],
        }),
        selected: true,
      },
    ]);

    useWorkflowStore.getState().copySelectedNodes();
    useWorkflowStore.getState().pasteCopiedNodes();

    const pastedSource = useWorkflowStore
      .getState()
      .nodes.find((node) => node.id.startsWith('source-node-copy-'));
    const pastedCode = useWorkflowStore
      .getState()
      .nodes.find(
        (node): node is CodeNode =>
          node.type === 'codeNode' && node.id.startsWith('code-node-copy-'),
      );

    expect(pastedCode?.data.inputs).toEqual([
      { name: 'result', source: `${pastedSource?.id}.output` },
    ]);
  });

  it('드래그 중 position 변경은 드래그 시작 지점 하나만 undo 스냅샷으로 기록한다', () => {
    useWorkflowStore.getState().setNodes([createMockNode('node-1')]);

    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 50, y: 50 },
        dragging: true,
      },
    ]);
    useWorkflowStore.getState().onNodesChange([
      {
        type: 'position',
        id: 'node-1',
        position: { x: 100, y: 100 },
        dragging: false,
      },
    ]);

    expect(useWorkflowStore.getState().nodes[0].position).toEqual({
      x: 100,
      y: 100,
    });

    useWorkflowStore.getState().undo();
    expect(useWorkflowStore.getState().nodes[0].position).toEqual({
      x: 0,
      y: 0,
    });
  });

  it('선택 노드를 삭제할 때 연결된 엣지도 함께 제거한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        { ...createMockNode('node-1'), selected: true },
        createMockNode('node-2'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([createMockEdge('edge-1', 'node-1', 'node-2')]);

    useWorkflowStore.getState().deleteSelectedElements();

    const state = useWorkflowStore.getState();
    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0].id).toBe('node-2');
    expect(state.edges).toHaveLength(0);
  });
});

// ============================================================================
// 2. Edge 생성/삭제 테스트
// ============================================================================

describe('Edge 생성/삭제 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('setEdges로 엣지를 설정할 수 있다', () => {
    const edges: Edge[] = [
      createMockEdge('edge-1', 'node-1', 'node-2'),
      createMockEdge('edge-2', 'node-2', 'node-3'),
    ];

    useWorkflowStore.getState().setEdges(edges);

    const state = useWorkflowStore.getState();
    expect(state.edges).toHaveLength(2);
    expect(state.edges[0].source).toBe('node-1');
    expect(state.edges[0].target).toBe('node-2');
  });

  it('onConnect로 새 엣지를 생성할 수 있다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createMockNode('node-1', 'startNode'),
        createMockNode('node-2', 'answerNode'),
      ]);
    // 초기 엣지 없음
    useWorkflowStore.getState().setEdges([]);

    // 연결 생성
    const connection: Connection = {
      source: 'node-1',
      target: 'node-2',
      sourceHandle: null,
      targetHandle: null,
    };
    useWorkflowStore.getState().onConnect(connection);

    const state = useWorkflowStore.getState();
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].source).toBe('node-1');
    expect(state.edges[0].target).toBe('node-2');
  });

  it('onEdgesChange로 엣지를 삭제할 수 있다', () => {
    // 초기에 2개 엣지 설정
    useWorkflowStore
      .getState()
      .setEdges([
        createMockEdge('edge-1', 'node-1', 'node-2'),
        createMockEdge('edge-2', 'node-2', 'node-3'),
      ]);

    // edge-1 삭제
    useWorkflowStore
      .getState()
      .onEdgesChange([{ type: 'remove', id: 'edge-1' }]);

    const state = useWorkflowStore.getState();
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].id).toBe('edge-2');
  });
});

// ============================================================================
// 3. Zustand 스토어 상태 관리 테스트
// ============================================================================

describe('Zustand 스토어 상태 관리 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('updateNodeData로 특정 노드의 데이터를 업데이트할 수 있다', () => {
    const node = createMockNode('node-1');
    useWorkflowStore.getState().setNodes([node]);

    useWorkflowStore.getState().updateNodeData('node-1', {
      title: '업데이트된 제목',
      newField: 'newValue',
    });

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].data.title).toBe('업데이트된 제목');
    expect(state.nodes[0].data.newField).toBe('newValue');
  });

  it('clears only a server-validated deferred parameter after node detail save', () => {
    const node = createMockNode('slack-1', 'slackPostNode');
    node.data = {
      ...node.data,
      channel: 'old-channel',
      message: 'hello',
      _deferred_parameters: ['channel', 'message'],
    } as Node['data'];
    useWorkflowStore.setState({ activeWorkflowId: 'workflow-1', nodes: [node] });

    useWorkflowStore.getState().updateNodeData('slack-1', {
      channel: 'new-channel',
    });

    expect(
      useWorkflowStore.getState().nodes[0].data._deferred_parameters,
    ).toEqual(['channel', 'message']);

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['slack-1'], parameter_keys: ['message'] },
      ],
    });

    expect(useWorkflowStore.getState().nodes[0].data).toMatchObject({
      channel: 'new-channel',
      _deferred_parameters: ['message'],
    });
  });

  it('keeps deferred markers for unrelated or empty node detail edits', () => {
    const node = createMockNode('slack-1', 'slackPostNode');
    node.data = {
      ...node.data,
      channel: 'old-channel',
      _deferred_parameters: ['channel'],
    } as Node['data'];
    useWorkflowStore.getState().setNodes([node]);

    useWorkflowStore.getState().updateNodeData('slack-1', {
      title: 'Slack updated',
    });
    useWorkflowStore.getState().updateNodeData('slack-1', { channel: '   ' });

    expect(
      useWorkflowStore.getState().nodes[0].data._deferred_parameters,
    ).toEqual(['channel']);
  });

  it('keeps a deferred marker when a non-empty node detail value violates catalog validation', () => {
    const node = createMockNode('github-1', 'githubNode');
    node.data = {
      ...node.data,
      pr_number: '',
      _deferred_parameters: ['pr_number'],
    } as Node['data'];
    useWorkflowStore.getState().setNodes([node]);

    useWorkflowStore.getState().updateNodeData('github-1', {
      pr_number: 0,
    });

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['github-1'], parameter_keys: ['pr_number'] },
      ],
    });

    expect(
      useWorkflowStore.getState().nodes[0].data._deferred_parameters,
    ).toEqual(['pr_number']);
  });

  it('reconciles a server-validated deferred parameter inside a nested subgraph', () => {
    const nestedNode = createMockNode('mail-1', 'mailNode');
    nestedNode.data = {
      ...nestedNode.data,
      credential_id: null,
      _deferred_parameters: ['credential_id', 'query'],
    } as Node['data'];
    const parentNode = createMockNode('loop-1', 'loopNode');
    parentNode.data = {
      ...parentNode.data,
      subGraph: { nodes: [nestedNode], edges: [] },
    } as Node['data'];
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [parentNode],
    });

    useWorkflowStore
      .getState()
      .updateInnerNodeData('loop-1', 'mail-1', {
        credential_id: 'credential-1',
      });

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-1',
      graph_hash: 'c'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['loop-1'], parameter_keys: [] },
        {
          node_path: ['loop-1', 'mail-1'],
          parameter_keys: ['query'],
        },
      ],
    });

    const nestedData = (useWorkflowStore.getState().nodes[0].data.subGraph as {
      nodes: Node[];
    }).nodes[0].data;
    expect(nestedData).toMatchObject({
      credential_id: 'credential-1',
      _deferred_parameters: ['query'],
    });
  });

  it('reconciles duplicate node ids by their nested graph path', () => {
    const topLevel = createMockNode('shared-1', 'githubNode');
    topLevel.data = {
      ...topLevel.data,
      _deferred_parameters: ['repo_name'],
    } as Node['data'];
    const nested = createMockNode('shared-1', 'mailNode');
    nested.data = {
      ...nested.data,
      _deferred_parameters: ['query'],
    } as Node['data'];
    const loop = createMockNode('loop-1', 'loopNode');
    loop.data = {
      ...loop.data,
      subGraph: { nodes: [nested], edges: [] },
    } as Node['data'];
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [topLevel, loop],
    });

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-1',
      graph_hash: 'd'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['shared-1'], parameter_keys: ['repo_owner'] },
        { node_path: ['loop-1'], parameter_keys: [] },
        {
          node_path: ['loop-1', 'shared-1'],
          parameter_keys: ['credential_id'],
        },
      ],
    });

    const state = useWorkflowStore.getState();
    expect(state.nodes[0].data._deferred_parameters).toEqual(['repo_owner']);
    const nestedData = (state.nodes[1].data.subGraph as { nodes: Node[] })
      .nodes[0].data;
    expect(nestedData._deferred_parameters).toEqual(['credential_id']);
  });

  it('keeps an inactive workflow projection out of the live editor', () => {
    const workflowANode = createMockNode('shared-1', 'slackPostNode');
    workflowANode.data = {
      ...workflowANode.data,
      _deferred_parameters: ['channel'],
    } as Node['data'];
    const workflowBNode = createMockNode('shared-1', 'githubNode');
    workflowBNode.data = {
      ...workflowBNode.data,
      _deferred_parameters: ['repo_name'],
    } as Node['data'];
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-b',
      nodes: [workflowBNode],
      workflows: [
        {
          id: 'workflow-a',
          appId: 'app-1',
          nodes: [workflowANode],
          edges: [],
          features: {},
          viewport: { x: 0, y: 0, zoom: 1 },
        },
        {
          id: 'workflow-b',
          appId: 'app-1',
          nodes: [workflowBNode],
          edges: [],
          features: {},
          viewport: { x: 0, y: 0, zoom: 1 },
        },
      ],
      hasUnsavedChanges: true,
    });

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-a',
      graph_hash: 'e'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['shared-1'], parameter_keys: [] },
      ],
    });

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('workflow-b');
    expect(state.nodes[0].data._deferred_parameters).toEqual(['repo_name']);
    expect(state.hasUnsavedChanges).toBe(true);
    expect(
      state.workflows.find((workflow) => workflow.id === 'workflow-a')?.nodes[0]
        .data._deferred_parameters,
    ).toBeUndefined();
  });

  it('treats the default editor as inactive for another workflow projection', () => {
    const workflowANode = createMockNode('shared-1', 'slackPostNode');
    workflowANode.data = {
      ...workflowANode.data,
      _deferred_parameters: ['channel'],
    } as Node['data'];
    const defaultNode = createMockNode('shared-1', 'githubNode');
    defaultNode.data = {
      ...defaultNode.data,
      _deferred_parameters: ['repo_name'],
    } as Node['data'];
    useWorkflowStore.setState({
      activeWorkflowId: 'default',
      nodes: [defaultNode],
      workflows: [
        {
          id: 'workflow-a',
          appId: 'app-1',
          nodes: [workflowANode],
          edges: [],
          features: {},
          viewport: { x: 0, y: 0, zoom: 1 },
        },
        {
          id: 'default',
          appId: 'app-1',
          nodes: [defaultNode],
          edges: [],
          features: {},
          viewport: { x: 0, y: 0, zoom: 1 },
        },
      ],
      hasUnsavedChanges: true,
    });

    useWorkflowStore.getState().ingestCanonicalDraftMetadata({
      workflow_id: 'workflow-a',
      graph_hash: 'f'.repeat(64),
      updated_at: '2026-07-19T00:00:00Z',
      canonical_deferred_parameters: [
        { node_path: ['shared-1'], parameter_keys: [] },
      ],
    });

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('default');
    expect(state.nodes[0].data._deferred_parameters).toEqual(['repo_name']);
    expect(state.hasUnsavedChanges).toBe(true);
    expect(
      state.workflows.find((workflow) => workflow.id === 'workflow-a')?.nodes[0]
        .data._deferred_parameters,
    ).toBeUndefined();
    expect(
      state.workflows.find((workflow) => workflow.id === 'default')?.nodes[0]
        .data._deferred_parameters,
    ).toEqual(['repo_name']);
  });

  it('노드 데이터를 수정하면 저장되지 않은 변경 상태로 표시한다', () => {
    const node = createMockNode('node-1');
    useWorkflowStore.getState().setNodes([node]);
    useWorkflowStore.getState().setHasUnsavedChanges(false);

    useWorkflowStore.getState().updateNodeData('node-1', {
      title: '저장 전 제목',
    });

    expect(useWorkflowStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('테스트 실행 presentation은 설정값을 유지하고 dirty/history를 변경하지 않는다', () => {
    const node = createCodeNode('code-1', {
      title: '코드 실행',
      code: 'def main(inputs):\n    return {"ok": True}',
      timeout: 30,
    });
    useWorkflowStore.getState().setNodes([node]);
    useWorkflowStore.getState().setHasUnsavedChanges(false);
    const undoCount = useWorkflowStore.getState().undoStack.length;

    useWorkflowStore.getState().updateNodeExecutionData('code-1', {
      status: 'success',
      observability: {
        status: 'success',
        latency_ms: 3400,
        total_tokens: 361,
        total_cost: 0.001964,
      },
    });

    const state = useWorkflowStore.getState();
    const updated = state.nodes[0];
    expect(updated.data).toMatchObject({
      title: '코드 실행',
      code: 'def main(inputs):\n    return {"ok": True}',
      timeout: 30,
      status: 'success',
      observability: {
        status: 'success',
        latency_ms: 3400,
        total_tokens: 361,
        total_cost: 0.001964,
      },
    });
    expect(state.hasUnsavedChanges).toBe(false);
    expect(state.undoStack).toHaveLength(undoCount);

    useWorkflowStore.getState().resetNodeExecutionData();
    expect(useWorkflowStore.getState().nodes[0].data).not.toHaveProperty(
      'status',
    );
    expect(useWorkflowStore.getState().nodes[0].data).not.toHaveProperty(
      'observability',
    );
  });

  it('setWorkflowData로 전체 워크플로우 데이터를 설정할 수 있다', () => {
    const nodes: Node[] = [createMockNode('node-1'), createMockNode('node-2')];
    const edges: Edge[] = [createMockEdge('edge-1', 'node-1', 'node-2')];

    useWorkflowStore.getState().setHasUnsavedChanges(true);

    useWorkflowStore.getState().setWorkflowData({
      nodes,
      edges,
      viewport: { x: 100, y: 200, zoom: 1.5 },
      features: { key: 'value' },
      envVariables: [
        { id: 'env-1', key: 'API_KEY', value: 'secret', type: 'string' },
      ],
    });

    const state = useWorkflowStore.getState();
    expect(state.nodes).toHaveLength(2);
    expect(state.edges).toHaveLength(1);
    expect(state.features).toMatchObject({ key: 'value' });
    expect(state.envVariables).toHaveLength(1);
    expect(state.hasUnsavedChanges).toBe(false);
  });

  it('setFeatures로 기능 설정을 업데이트할 수 있다', () => {
    useWorkflowStore.getState().setFeatures({ debug: true, logging: false });

    const state = useWorkflowStore.getState();
    expect(state.features).toEqual({ debug: true, logging: false });
    expect(state.workflows[0].features).toEqual({
      debug: true,
      logging: false,
    });
  });

  it('setEnvVariables로 환경 변수를 설정할 수 있다', () => {
    useWorkflowStore.getState().setEnvVariables([
      { id: 'env-1', key: 'API_KEY', value: 'key123', type: 'string' },
      { id: 'env-2', key: 'DEBUG', value: 'true', type: 'string' },
    ]);

    const state = useWorkflowStore.getState();
    expect(state.envVariables).toHaveLength(2);
    expect(state.envVariables[0].key).toBe('API_KEY');
  });
});

// ============================================================================
// 4. 워크플로우 관리 테스트
// ============================================================================

describe('워크플로우 관리 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('setActiveWorkflow로 활성 워크플로우를 변경할 수 있다', () => {
    // 여러 워크플로우 설정
    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('n1')],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
        {
          id: 'wf-2',
          appId: 'app-1',
          nodes: [createMockNode('n2')],
          edges: [],
          features: { nextNodeDisplayNumber: 20 },
        },
      ],
      activeWorkflowId: 'wf-1',
      isTestPanelOpen: true,
      testExecutionStatus: 'success',
      testExecutionStartedAt: 1000,
      testExecutionFinishedAt: 2000,
      testExecutionResult: { answer: 'previous workflow result' },
      testNodeResults: [
        { nodeId: 'n1', nodeType: 'llmNode', output: { text: 'old' } },
      ],
      testExecutionError: null,
      currentExecutingNodeId: 'n1',
      isTestUploading: true,
    });

    // wf-2로 변경
    useWorkflowStore.getState().setActiveWorkflow('wf-2');

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('wf-2');
    expect(state.nodes[0].id).toBe('n2');
    expect(state.features.nextNodeDisplayNumber).toBe(20);
    expect(state.isTestPanelOpen).toBe(false);
    expect(state.testExecutionStatus).toBe('idle');
    expect(state.testExecutionStartedAt).toBeNull();
    expect(state.testExecutionFinishedAt).toBeNull();
    expect(state.testExecutionResult).toBeNull();
    expect(state.testNodeResults).toEqual([]);
    expect(state.testExecutionError).toBeNull();
    expect(state.currentExecutingNodeId).toBeNull();
    expect(state.isTestUploading).toBe(false);
  });

  it('setActiveWorkflowIdSafe는 로드된 대상 워크플로우의 nodes/edges/features를 반영한다', () => {
    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('n1')],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
        {
          id: 'wf-2',
          appId: 'app-1',
          nodes: [createMockNode('n2')],
          edges: [],
          features: { nextNodeDisplayNumber: 20 },
        },
      ],
      activeWorkflowId: 'wf-1',
      nodes: [createMockNode('draft-node')],
      edges: [createMockEdge('edge-1', 'draft-node', 'n1')],
      features: { nextNodeDisplayNumber: 10 },
      isTestPanelOpen: true,
      testExecutionStatus: 'failure',
      testExecutionStartedAt: 1000,
      testExecutionFinishedAt: 2000,
      testExecutionResult: { answer: 'previous workflow result' },
      testNodeResults: [
        { nodeId: 'draft-node', nodeType: 'llmNode', output: { text: 'old' } },
      ],
      testExecutionError: 'previous workflow error',
      currentExecutingNodeId: 'draft-node',
      isTestUploading: true,
    });

    useWorkflowStore.getState().setActiveWorkflowIdSafe('wf-2');

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('wf-2');
    expect(state.nodes[0].id).toBe('n2');
    expect(state.edges).toEqual([]);
    expect(state.features.nextNodeDisplayNumber).toBe(20);
    expect(state.isTestPanelOpen).toBe(false);
    expect(state.testExecutionStatus).toBe('idle');
    expect(state.testExecutionStartedAt).toBeNull();
    expect(state.testExecutionFinishedAt).toBeNull();
    expect(state.testExecutionResult).toBeNull();
    expect(state.testNodeResults).toEqual([]);
    expect(state.testExecutionError).toBeNull();
    expect(state.currentExecutingNodeId).toBeNull();
    expect(state.isTestUploading).toBe(false);
  });

  it('setActiveWorkflowIdSafe는 같은 workflow를 다시 열어도 최신 테스트 실행 결과를 초기화하지 않는다', () => {
    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('n1')],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
      ],
      activeWorkflowId: 'wf-1',
      nodes: [createMockNode('n1')],
      edges: [],
      features: { nextNodeDisplayNumber: 10 },
      isTestPanelOpen: true,
      testExecutionStatus: 'success',
      testExecutionStartedAt: 1_000,
      testExecutionFinishedAt: 2_000,
      testExecutionResult: { answer: 'previous test result' },
      testNodeResults: [
        { nodeId: 'n1', nodeType: 'llmNode', output: { text: 'done' } },
      ],
      testExecutionError: null,
      currentExecutingNodeId: null,
      isTestUploading: false,
    });

    useWorkflowStore.getState().setActiveWorkflowIdSafe('wf-1');

    const state = useWorkflowStore.getState();
    expect(state.isTestPanelOpen).toBe(true);
    expect(state.testExecutionStatus).toBe('success');
    expect(state.testExecutionResult).toEqual({
      answer: 'previous test result',
    });
    expect(state.testNodeResults).toEqual([
      { nodeId: 'n1', nodeType: 'llmNode', output: { text: 'done' } },
    ]);
  });

  it('setActiveWorkflowIdSafe는 대상 workflow가 없으면 id만 변경한다', () => {
    const currentNodes = [createMockNode('draft-node')];
    const currentEdges = [createMockEdge('edge-1', 'draft-node', 'n1')];

    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('n1')],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
      ],
      activeWorkflowId: 'wf-1',
      nodes: currentNodes,
      edges: currentEdges,
      features: { nextNodeDisplayNumber: 10 },
      isTestPanelOpen: true,
      testExecutionStatus: 'success',
      testExecutionStartedAt: 1000,
      testExecutionFinishedAt: 2000,
      testExecutionResult: { answer: 'previous workflow result' },
      testNodeResults: [
        { nodeId: 'draft-node', nodeType: 'llmNode', output: { text: 'old' } },
      ],
      testExecutionError: null,
      currentExecutingNodeId: 'draft-node',
      isTestUploading: true,
    });

    useWorkflowStore.getState().setActiveWorkflowIdSafe('wf-missing');

    const state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('wf-missing');
    expect(state.nodes).toBe(currentNodes);
    expect(state.edges).toBe(currentEdges);
    expect(state.features.nextNodeDisplayNumber).toBe(10);
    expect(state.isTestPanelOpen).toBe(false);
    expect(state.testExecutionStatus).toBe('idle');
    expect(state.testExecutionStartedAt).toBeNull();
    expect(state.testExecutionFinishedAt).toBeNull();
    expect(state.testExecutionResult).toBeNull();
    expect(state.testNodeResults).toEqual([]);
    expect(state.testExecutionError).toBeNull();
    expect(state.currentExecutingNodeId).toBeNull();
    expect(state.isTestUploading).toBe(false);
  });

  it('inactive workflow 데이터가 먼저 로드된 뒤 safe active 전환 시 화면 store에 반영한다', () => {
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('wf-1-node')],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
        {
          id: 'wf-2',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
      nodes: [createMockNode('wf-1-node')],
      edges: [],
      features: { nextNodeDisplayNumber: 10 },
    });

    const wf2Nodes = [createMockNode('wf-2-node', 'answerNode')];
    const wf2Edges = [createMockEdge('wf-2-edge', 'wf-2-node', 'wf-2-node')];

    useWorkflowStore.getState().setWorkflowData(
      {
        nodes: wf2Nodes,
        edges: wf2Edges,
        viewport: { x: 0, y: 0, zoom: 1 },
        features: { nextNodeDisplayNumber: 42 },
      },
      'wf-2',
    );

    let state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('wf-1');
    expect(state.nodes[0].id).toBe('wf-1-node');
    expect(state.features.nextNodeDisplayNumber).toBe(10);
    expect(
      state.workflows.find((workflow) => workflow.id === 'wf-2')?.features
        .nextNodeDisplayNumber,
    ).toBe(2);

    useWorkflowStore.getState().setActiveWorkflowIdSafe('wf-2');

    state = useWorkflowStore.getState();
    expect(state.activeWorkflowId).toBe('wf-2');
    expect(state.nodes[0].id).toBe('wf-2-node');
    expect(state.edges[0].id).toBe('wf-2-edge');
    expect(state.features.nextNodeDisplayNumber).toBe(2);
    expect(
      state.workflows.find((workflow) => workflow.id === 'wf-1')?.features
        .nextNodeDisplayNumber,
    ).toBe(10);
  });

  it('active workflow가 아닌 응답은 현재 편집 중인 nodes/edges를 덮어쓰지 않는다', () => {
    const activeNodes = [createMockNode('active-node')];
    const activeEdges = [
      createMockEdge('active-edge', 'active-node', 'active-node'),
    ];

    useWorkflowStore.setState({
      activeWorkflowId: 'wf-active',
      nodes: activeNodes,
      edges: activeEdges,
      workflows: [
        {
          id: 'wf-active',
          appId: 'app-1',
          nodes: activeNodes,
          edges: activeEdges,
          features: { nextNodeDisplayNumber: 10 },
          viewport: { x: 0, y: 0, zoom: 1 },
        },
      ],
    });

    useWorkflowStore.getState().setWorkflowData(
      {
        nodes: [createMockNode('stale-node')],
        edges: [createMockEdge('stale-edge', 'stale-node', 'stale-node')],
        viewport: { x: 0, y: 0, zoom: 1 },
        features: { nextNodeDisplayNumber: 2 },
      },
      'wf-stale',
    );

    const state = useWorkflowStore.getState();
    expect(state.nodes).toBe(activeNodes);
    expect(state.edges).toBe(activeEdges);
    expect(
      state.workflows.find((workflow) => workflow.id === 'wf-stale'),
    ).toMatchObject({
      id: 'wf-stale',
      nodes: [expect.objectContaining({ id: 'stale-node' })],
      edges: [expect.objectContaining({ id: 'stale-edge' })],
    });
  });

  it('테스트 실행 실패 상태는 오류 메시지와 종료 시각을 남기고 실행 중 노드를 해제한다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-04T00:00:00.000Z'));
    useWorkflowStore.getState().beginTestExecution();
    useWorkflowStore.getState().setCurrentExecutingNode('llm-1');

    vi.setSystemTime(new Date('2026-07-04T00:00:08.600Z'));
    useWorkflowStore.getState().failTestExecution('모듈 실행 실패: node error');

    const state = useWorkflowStore.getState();
    expect(state.testExecutionStatus).toBe('failure');
    expect(state.testExecutionError).toBe('모듈 실행 실패: node error');
    expect(state.currentExecutingNodeId).toBeNull();
    expect(state.testExecutionStartedAt).toBe(1783123200000);
    expect(state.testExecutionFinishedAt).toBe(1783123208600);
    vi.useRealTimers();
  });

  it('deleteWorkflow로 워크플로우를 삭제할 수 있다', () => {
    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 10 },
        },
        {
          id: 'wf-2',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 20 },
        },
      ],
      activeWorkflowId: 'wf-1',
    });

    useWorkflowStore.getState().deleteWorkflow('wf-1');

    const state = useWorkflowStore.getState();
    expect(state.workflows).toHaveLength(1);
    expect(state.workflows[0].id).toBe('wf-2');
    // 삭제된 워크플로우가 활성이었으면 다른 워크플로우로 전환
    expect(state.activeWorkflowId).toBe('wf-2');
    expect(state.features.nextNodeDisplayNumber).toBe(20);
  });

  it('updateWorkflowViewport로 뷰포트를 업데이트할 수 있다', () => {
    useWorkflowStore.setState({
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
          viewport: { x: 0, y: 0, zoom: 1 },
        },
      ],
    });

    useWorkflowStore
      .getState()
      .updateWorkflowViewport('wf-1', { x: 50, y: 100, zoom: 2 });

    const state = useWorkflowStore.getState();
    expect(state.workflows[0].viewport).toEqual({ x: 50, y: 100, zoom: 2 });
  });

  it('addNode는 현재 nodes/features를 읽어 번호와 workflow features를 함께 갱신한다', () => {
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [createMockNode('n1')],
          edges: [],
          features: { nextNodeDisplayNumber: 3 },
        },
      ],
      nodes: [
        {
          id: 'n1',
          type: 'startNode',
          position: { x: 0, y: 0 },
          data: {
            title: 'Node n1',
            triggerType: 'manual',
            variables: [],
            displayNumber: 1,
          },
        } as Node,
      ],
      features: { nextNodeDisplayNumber: 3 },
    });

    const added = useWorkflowStore
      .getState()
      .addNode(createMockNode('n2', 'answerNode'));

    const state = useWorkflowStore.getState();
    expect(added.data.displayNumber).toBe(2);
    expect(state.nodes[1].data.displayNumber).toBe(2);
    expect(state.features.nextNodeDisplayNumber).toBe(3);
    expect(state.workflows[0].features.nextNodeDisplayNumber).toBe(3);
  });

  it('restoreVersion은 snapshot 번호를 보정한 뒤 draft와 store에 반영한다', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
    });

    const version = {
      id: 'deployment-1',
      app_id: 'app-1',
      version: 1,
      created_by: 'user-1',
      created_at: '2026-06-25T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [
          createMockNode('n1', 'startNode'),
          createMockNode('n2', 'answerNode'),
          createMockNode('note-1', 'note'),
        ],
        edges: [],
        features: { nextNodeDisplayNumber: 1 },
      },
    } as DeploymentResponse;

    await useWorkflowStore.getState().restoreVersion(version);

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({
            id: 'n1',
            data: expect.not.objectContaining({
              displayNumber: expect.any(Number),
            }),
          }),
          expect.objectContaining({
            id: 'n2',
            data: expect.not.objectContaining({
              displayNumber: expect.any(Number),
            }),
          }),
        ]),
        features: expect.objectContaining({
          nextNodeDisplayNumber: 3,
          noteNodes: expect.arrayContaining([
            expect.objectContaining({
              id: 'note-1',
              data: expect.not.objectContaining({
                displayNumber: expect.any(Number),
              }),
            }),
          ]),
        }),
      }),
    );

    const state = useWorkflowStore.getState();
    expect(state.nodes.map((node) => node.data.displayNumber)).toEqual([
      1,
      2,
      undefined,
    ]);
    expect(state.features.nextNodeDisplayNumber).toBe(3);
  });

  it('restoreVersion restores canonical feature notes into the saved draft and editor', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
      nodes: [],
      features: { nextNodeDisplayNumber: 1 },
    });
    const canonicalNote = createMockNode('feature-note', 'note');

    await useWorkflowStore.getState().restoreVersion({
      id: 'deployment-feature-note',
      app_id: 'app-1',
      version: 2,
      created_by: 'user-1',
      created_at: '2026-07-01T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored', 'startNode')],
        edges: [],
        features: {
          nextNodeDisplayNumber: 1,
          noteNodes: [canonicalNote],
        },
      },
    } as DeploymentResponse);

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({
        features: expect.objectContaining({
          noteNodes: [expect.objectContaining({ id: 'feature-note' })],
        }),
      }),
    );
    expect(useWorkflowStore.getState().nodes).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: 'feature-note' })]),
    );
  });

  it('restoreVersion preserves current notes when a legacy snapshot has no note representation', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    const currentNote = createMockNode('current-note', 'note');
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [currentNote],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
      nodes: [currentNote],
      features: { nextNodeDisplayNumber: 1 },
    });

    await useWorkflowStore.getState().restoreVersion({
      id: 'deployment-legacy-without-notes',
      app_id: 'app-1',
      version: 3,
      created_by: 'user-1',
      created_at: '2026-07-01T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored', 'startNode')],
        edges: [],
        features: null,
      },
    } as DeploymentResponse);

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({
        features: expect.objectContaining({
          noteNodes: [expect.objectContaining({ id: 'current-note' })],
        }),
      }),
    );
    expect(useWorkflowStore.getState().nodes).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: 'current-note' })]),
    );
  });

  it('restoreVersion treats an explicit empty feature note list as authoritative', async () => {
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    const currentNote = createMockNode('current-note', 'note');
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [currentNote],
          edges: [],
          features: { nextNodeDisplayNumber: 1 },
        },
      ],
      nodes: [currentNote],
      features: { nextNodeDisplayNumber: 1 },
    });

    await useWorkflowStore.getState().restoreVersion({
      id: 'deployment-explicit-no-notes',
      app_id: 'app-1',
      version: 4,
      created_by: 'user-1',
      created_at: '2026-07-01T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored', 'startNode')],
        edges: [],
        features: { nextNodeDisplayNumber: 1, noteNodes: [] },
      },
    } as DeploymentResponse);

    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({
        features: expect.objectContaining({ noteNodes: [] }),
      }),
    );
    expect(
      useWorkflowStore.getState().nodes.filter((node) => node.type === 'note'),
    ).toEqual([]);
  });

  it('restoreVersion waits for an in-flight Agent Builder workflow save', async () => {
    const releaseAgentBuilderSave = tryAcquireWorkflowDraftSave(
      'wf-1',
      'agent_builder',
    );
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'wf-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue({
      status: 'success',
      workflow_id: 'wf-1',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: {},
        },
      ],
    });

    const version = {
      id: 'deployment-restore-wait',
      app_id: 'app-1',
      version: 1,
      created_by: 'user-1',
      created_at: '2026-06-25T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('n1', 'startNode')],
        edges: [],
        features: {},
      },
    } as DeploymentResponse;

    const restoring = useWorkflowStore.getState().restoreVersion(version);
    await Promise.resolve();
    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();

    releaseAgentBuilderSave?.();
    await restoring;

    expect(workflowApi.getDraftWorkflow).toHaveBeenCalledWith('wf-1');
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(1);
  });

  it('restoreVersion aborts without mutation when the active workflow changes while waiting for the save lock', async () => {
    const releaseAgentBuilderSave = tryAcquireWorkflowDraftSave(
      'wf-1',
      'agent_builder',
    );
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-1',
      workflows: [
        {
          id: 'wf-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: {},
        },
        {
          id: 'wf-2',
          appId: 'app-1',
          nodes: [createMockNode('wf-2-node', 'startNode')],
          edges: [],
          features: {},
        },
      ],
      nodes: [],
      edges: [],
      undoStack: [],
      redoStack: [],
      hasUnsavedChanges: false,
    });
    const version = {
      id: 'deployment-aborted-after-switch',
      app_id: 'app-1',
      version: 1,
      created_by: 'user-1',
      created_at: '2026-06-25T00:00:00Z',
      type: 'api',
      is_active: false,
      graph_snapshot: {
        nodes: [createMockNode('restored-wf-1', 'answerNode')],
        edges: [],
        features: {},
      },
    } as DeploymentResponse;

    const restoring = useWorkflowStore.getState().restoreVersion(version);
    await Promise.resolve();
    useWorkflowStore.setState({
      activeWorkflowId: 'wf-2',
      nodes: [createMockNode('wf-2-node', 'startNode')],
      edges: [],
    });
    releaseAgentBuilderSave?.();
    await restoring;

    expect(workflowApi.getDraftWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.syncDraftWorkflow).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().activeWorkflowId).toBe('wf-2');
    expect(useWorkflowStore.getState().nodes).toEqual([
      expect.objectContaining({ id: 'wf-2-node' }),
    ]);
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
    expect(useWorkflowStore.getState().redoStack).toEqual([]);
  });
});

// ============================================================================
// 5. UI 상태 테스트
// ============================================================================

describe('UI 상태 테스트', () => {
  beforeEach(() => {
    resetStore();
  });

  it('toggleFullscreen으로 전체 화면 상태를 토글 할 수 있다', () => {
    expect(useWorkflowStore.getState().isFullscreen).toBe(false);

    useWorkflowStore.getState().toggleFullscreen();
    expect(useWorkflowStore.getState().isFullscreen).toBe(true);

    useWorkflowStore.getState().toggleFullscreen();
    expect(useWorkflowStore.getState().isFullscreen).toBe(false);
  });

  it('setInteractiveMode로 입력 모드를 변경할 수 있다', () => {
    expect(useWorkflowStore.getState().interactiveMode).toBe('mouse');

    useWorkflowStore.getState().setInteractiveMode('touchpad');
    expect(useWorkflowStore.getState().interactiveMode).toBe('touchpad');
  });

  it('toggleVersionHistory로 버전 기록 패널을 토글할 수 있다', () => {
    expect(useWorkflowStore.getState().isVersionHistoryOpen).toBe(false);

    useWorkflowStore.getState().toggleVersionHistory();
    expect(useWorkflowStore.getState().isVersionHistoryOpen).toBe(true);
  });

  it('Agent Builder Routing 이동은 대상 노드의 전체화면 Routing section을 열고 닫을 때 초기화한다', () => {
    useWorkflowStore.getState().openNodeFullscreen('llm-routing', 'routing');

    expect(useWorkflowStore.getState()).toMatchObject({
      fullscreenNodeId: 'llm-routing',
      fullscreenNodeSettingsSection: 'routing',
    });

    useWorkflowStore.getState().closeNodeFullscreen();

    expect(useWorkflowStore.getState()).toMatchObject({
      fullscreenNodeId: null,
      fullscreenNodeSettingsSection: null,
    });
  });

  it('setProjectInfo로 프로젝트 정보를 설정할 수 있다', () => {
    useWorkflowStore.getState().setProjectInfo('새 프로젝트', {
      type: 'emoji',
      content: '🚀',
      background_color: '#E0F7FA',
    });

    const state = useWorkflowStore.getState();
    expect(state.projectName).toBe('새 프로젝트');
    expect(state.projectIcon.content).toBe('🚀');
  });

  it('triggerWorkflowRun으로 실행 트리거를 증가시킬 수 있다', () => {
    const initialTrigger = useWorkflowStore.getState().runTrigger;

    useWorkflowStore.getState().triggerWorkflowRun();
    expect(useWorkflowStore.getState().runTrigger).toBe(initialTrigger + 1);

    useWorkflowStore.getState().triggerWorkflowRun();
    expect(useWorkflowStore.getState().runTrigger).toBe(initialTrigger + 2);
  });
});
