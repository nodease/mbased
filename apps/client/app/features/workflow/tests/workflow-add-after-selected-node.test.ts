import type { Edge } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { describe, expect, it, beforeEach, vi } from 'vitest';
import {
  findAddAfterTarget,
  getNonOverlappingPosition,
} from '../hooks/useNodeCreation';
import { NodeLibraryContent } from '../components/editor/NodeLibraryContent';
import { useWorkflowStore } from '../store/useWorkflowStore';
import type { AppNode } from '../types/Nodes';
import type {
  AnswerNode,
  CodeNode,
  NoteNode,
  StartNode,
} from '../types/Workflow';

vi.mock('../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
    createWorkflow: vi.fn(),
    listWorkflowsByApp: vi.fn(),
  },
}));

const initialState = useWorkflowStore.getState();

const resetStore = () => {
  vi.clearAllMocks();
  useWorkflowStore.setState(initialState, true);
};

const createStartNode = (id: string, x = 0, y = 0): StartNode => ({
  id,
  type: 'startNode',
  position: { x, y },
  data: { title: id, triggerType: 'manual', variables: [] },
});

const createCodeNode = (
  id: string,
  x = 0,
  y = 0,
  selected = false,
): CodeNode => ({
  id,
  type: 'codeNode',
  selected,
  position: { x, y },
  data: {
    title: id,
    code: 'def main(inputs):\n    return {}',
    inputs: [],
    timeout: 10,
  },
});

const createAnswerNode = (id: string, x = 0, y = 0): AnswerNode => ({
  id,
  type: 'answerNode',
  position: { x, y },
  data: { title: id, outputs: [] },
});

const createNoteNode = (id: string, x = 0, y = 0): NoteNode => ({
  id,
  type: 'note',
  position: { x, y },
  data: { title: id, content: '' },
});

const edge = (id: string, source: string, target: string): Edge => ({
  id,
  source,
  target,
});

describe('workflow test cases: 뒤에 추가', () => {
  beforeEach(() => {
    resetStore();
  });

  it('선택 노드가 있으면 terminal node가 여러 개여도 선택 노드를 기준으로 추가한다', () => {
    const selected = createCodeNode('selected', 200, 0, true);
    const farTerminal = createCodeNode('far-terminal', 900, 0);
    const nodes = [
      createStartNode('start'),
      selected,
      createCodeNode('branch-terminal', 400, 200),
      farTerminal,
    ] as AppNode[];
    const edges = [edge('start-selected', 'start', 'selected')];

    expect(findAddAfterTarget(nodes, edges)?.id).toBe('selected');
  });

  it('선택 노드가 없고 terminal node가 여러 개면 임의 target을 고르지 않는다', () => {
    const nodes = [
      createStartNode('start'),
      createCodeNode('left-terminal', 300, 0),
      createCodeNode('right-terminal', 700, 0),
      createCodeNode('lower-terminal', 700, 200),
    ] as AppNode[];
    const edges = [edge('start-left', 'start', 'left-terminal')];

    expect(findAddAfterTarget(nodes, edges)).toBeNull();
  });

  it('선택 노드가 없고 terminal node가 하나뿐이면 그 노드를 기준으로 추가한다', () => {
    const nodes = [
      createStartNode('start'),
      createCodeNode('middle', 300, 0),
      createCodeNode('terminal', 600, 0),
    ] as AppNode[];
    const edges = [
      edge('start-middle', 'start', 'middle'),
      edge('middle-terminal', 'middle', 'terminal'),
    ];

    expect(findAddAfterTarget(nodes, edges)?.id).toBe('terminal');
  });

  it('입력 노드 하나만 있으면 입력 노드를 기준으로 추가한다', () => {
    const nodes = [createStartNode('start')] as AppNode[];

    expect(findAddAfterTarget(nodes, [])?.id).toBe('start');
  });

  it('note는 terminal node 계산에서 제외한다', () => {
    const nodes = [
      createStartNode('start'),
      createCodeNode('terminal', 300, 0),
      createNoteNode('note', 1000, 0),
    ] as AppNode[];
    const edges = [edge('start-terminal', 'start', 'terminal')];

    expect(findAddAfterTarget(nodes, edges)?.id).toBe('terminal');
  });

  it('answer를 포함해 terminal node가 여러 개면 임의 target을 고르지 않는다', () => {
    const nodes = [
      createStartNode('start'),
      createCodeNode('terminal', 300, 0),
      createAnswerNode('answer', 600, 0),
    ] as AppNode[];
    const edges = [edge('start-terminal', 'start', 'terminal')];

    expect(findAddAfterTarget(nodes, edges)).toBeNull();
  });

  it('선택 노드가 여러 개면 임의 target을 고르지 않는다', () => {
    const nodes = [
      createCodeNode('first', 0, 0, true),
      createCodeNode('second', 300, 0, true),
    ] as AppNode[];

    expect(findAddAfterTarget(nodes, [])).toBeNull();
  });

  it('선택 노드가 note이면 뒤에 추가 기준으로 사용하지 않는다', () => {
    const note = { ...createNoteNode('note'), selected: true };
    const nodes = [createStartNode('start'), note] as AppNode[];

    expect(findAddAfterTarget(nodes, [])).toBeNull();
  });

  it('노드와 edge를 하나의 undo 단위로 추가한다', () => {
    const start = createStartNode('start');
    const target = createCodeNode('target');
    useWorkflowStore.getState().setNodes([start, target]);
    useWorkflowStore.getState().setEdges([]);

    const added = useWorkflowStore.getState().addNodeWithEdge(
      createCodeNode('new-node'),
      {
        id: 'edge-target-new',
        source: 'target',
        type: 'puzzle',
      },
      (nodes, numberedNode) => [...nodes, numberedNode],
    );

    expect(added?.id).toBe('new-node');
    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'target',
      'new-node',
    ]);
    expect(useWorkflowStore.getState().edges).toMatchObject([
      { id: 'edge-target-new', source: 'target', target: 'new-node' },
    ]);

    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'target',
    ]);
    expect(useWorkflowStore.getState().edges).toEqual([]);
  });

  it('기존 connection validation을 통과하지 못하면 추가하지 않는다', () => {
    useWorkflowStore
      .getState()
      .setNodes([createStartNode('start'), createAnswerNode('answer')]);
    useWorkflowStore.getState().setEdges([]);

    const added = useWorkflowStore.getState().addNodeWithEdge(
      createCodeNode('new-node'),
      {
        id: 'edge-answer-new',
        source: 'answer',
        type: 'puzzle',
      },
    );

    expect(added).toBeNull();
    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'start',
      'answer',
    ]);
    expect(useWorkflowStore.getState().edges).toEqual([]);
  });

  it('뒤에 추가 handler가 없는 selector 화면에서는 뒤에 추가 버튼을 렌더링하지 않는다', () => {
    render(createElement(NodeLibraryContent, { onSelect: () => undefined }));

    expect(screen.queryByRole('button', { name: /기준 노드 뒤에 추가/ })).toBeNull();
  });

  it('뒤에 추가 handler가 있으면 단일 입력 노드 상태에서 뒤에 추가 버튼을 활성화한다', () => {
    useWorkflowStore.getState().setNodes([createStartNode('start')]);
    useWorkflowStore.getState().setEdges([]);

    render(
      createElement(NodeLibraryContent, {
        onSelect: () => undefined,
        onAddAfterSelected: () => undefined,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: '노드' }));

    expect(screen.getByRole('button', { name: 'LLM 기준 노드 뒤에 추가' })).toBeEnabled();
  });

  it('선택 노드가 note이면 뒤에 추가 버튼을 비활성화한다', () => {
    const note = { ...createNoteNode('note'), selected: true };
    useWorkflowStore.getState().setNodes([createStartNode('start'), note]);
    useWorkflowStore.getState().setEdges([]);

    render(
      createElement(NodeLibraryContent, {
        onSelect: () => undefined,
        onAddAfterSelected: () => undefined,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: '노드' }));

    expect(
      screen.getByRole('button', { name: 'LLM 기준 노드 뒤에 추가' }),
    ).toBeDisabled();
  });

  it('뒤에 추가 위치가 기존 노드 bounding box와 겹치면 다음 세로 위치로 피한다', () => {
    const start = createStartNode('start', 0, 0);
    const existing = createCodeNode('existing', 700, 0);

    expect(
      getNonOverlappingPosition([start, existing] as AppNode[], {
        x: 520,
        y: 0,
      }),
    ).toEqual({ x: 520, y: 240 });
  });
});
