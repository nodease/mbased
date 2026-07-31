import { renderHook, cleanup } from '@testing-library/react';
import type { Edge } from '@xyflow/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCanvasKeyboardShortcuts } from '../hooks/useCanvasKeyboardShortcuts';
import { useWorkflowStore } from '../store/useWorkflowStore';
import type { CodeNode, Node, StartNode, AnswerNode } from '../types/Workflow';

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

const createStartNode = (id: string): StartNode => ({
  id,
  type: 'startNode',
  position: { x: 0, y: 0 },
  data: { title: id, triggerType: 'manual', variables: [] },
});

const createCodeNode = (id: string, selected = false): CodeNode => ({
  id,
  type: 'codeNode',
  selected,
  position: { x: 0, y: 0 },
  data: {
    title: id,
    code: 'def main(inputs):\n    return {}',
    inputs: [],
    timeout: 10,
  },
});

const createAnswerNode = (id: string): AnswerNode => ({
  id,
  type: 'answerNode',
  position: { x: 0, y: 0 },
  data: { title: id, outputs: [] },
});

const edge = (id: string, source: string, target: string): Edge => ({
  id,
  source,
  target,
});

const installShortcutActions = (hasSelection: boolean) => {
  const actions = {
    copySelectedNodes: vi.fn(),
    pasteCopiedNodes: vi.fn(),
    duplicateSelectedNodes: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    deleteSelectedElements: vi.fn(),
    clearSelection: vi.fn(),
    clearInnerNodeSelection: vi.fn(),
    hasSelectedElements: vi.fn(() => hasSelection),
  };
  useWorkflowStore.setState(actions);
  return actions;
};

const dispatchKeyDown = (
  key: string,
  target: HTMLElement | Window = window,
  init: KeyboardEventInit = {},
) => {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  const preventDefault = vi.spyOn(event, 'preventDefault');
  target.dispatchEvent(event);
  return preventDefault;
};

const shortcutOptions = (
  overrides: Partial<Parameters<typeof useCanvasKeyboardShortcuts>[0]> = {},
) => ({
  isEnabled: true,
  closeMenus: vi.fn(() => false),
  closePanels: vi.fn(() => false),
  toggleNodeLibrary: vi.fn(),
  ...overrides,
});

describe('workflow test cases: 워크플로우 조작 편의성', () => {
  beforeEach(() => {
    resetStore();
    document.body.innerHTML = '';
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('단일 중간 노드를 삭제하면 incoming source와 outgoing target 사이에 새 edge가 생성된다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createStartNode('a'),
        createCodeNode('b', true),
        createAnswerNode('c'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([edge('a-b', 'a', 'b'), edge('b-c', 'b', 'c')]);

    useWorkflowStore.getState().deleteSelectedElements();

    const state = useWorkflowStore.getState();
    expect(state.nodes.map((node) => node.id)).toEqual(['a', 'c']);
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0]).toMatchObject({ source: 'a', target: 'c' });
  });

  it('자동 재연결은 기존 연결 검증 규칙을 통과하는 경우에만 edge를 생성한다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createCodeNode('a'),
        createCodeNode('b', true),
        createStartNode('c'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([edge('a-b', 'a', 'b'), edge('b-c', 'b', 'c')]);

    useWorkflowStore.getState().deleteSelectedElements();

    expect(useWorkflowStore.getState().edges).toEqual([]);
  });

  it('여러 incoming/outgoing edge가 있는 노드를 삭제하면 가능한 유효 조합만 생성하고 중복 edge는 만들지 않는다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createStartNode('a'),
        createCodeNode('x'),
        createCodeNode('b', true),
        createAnswerNode('c'),
        createAnswerNode('d'),
      ] as Node[]);
    useWorkflowStore
      .getState()
      .setEdges([
        edge('a-b', 'a', 'b'),
        edge('x-b', 'x', 'b'),
        edge('b-c', 'b', 'c'),
        edge('b-d', 'b', 'd'),
        edge('a-c', 'a', 'c'),
      ]);

    useWorkflowStore.getState().deleteSelectedElements();

    const edgeKeys = useWorkflowStore
      .getState()
      .edges.map((item) => `${item.source}->${item.target}`)
      .sort();
    expect(edgeKeys).toEqual(['a->c', 'a->d', 'x->c', 'x->d']);
  });

  it('Backspace와 Delete는 캔버스 선택 요소 삭제를 호출한다', () => {
    const actions = installShortcutActions(true);
    renderHook(() => useCanvasKeyboardShortcuts(shortcutOptions()));

    dispatchKeyDown('Backspace');
    dispatchKeyDown('Delete');

    expect(actions.deleteSelectedElements).toHaveBeenCalledTimes(2);
  });

  it('입력 필드에 focus가 있을 때 Backspace/Delete를 눌러도 노드 삭제 함수가 호출되지 않는다', () => {
    const actions = installShortcutActions(true);
    const input = document.createElement('textarea');
    document.body.appendChild(input);
    renderHook(() => useCanvasKeyboardShortcuts(shortcutOptions()));

    const backspacePreventDefault = dispatchKeyDown('Backspace', input);
    const deletePreventDefault = dispatchKeyDown('Delete', input);

    expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
    expect(backspacePreventDefault).not.toHaveBeenCalled();
    expect(deletePreventDefault).not.toHaveBeenCalled();
  });

  it('A -> B -> C 구조에서 B 삭제 후 undo를 실행하면 B와 기존 edge가 복구된다', () => {
    useWorkflowStore
      .getState()
      .setNodes([
        createStartNode('a'),
        createCodeNode('b', true),
        createAnswerNode('c'),
      ]);
    useWorkflowStore
      .getState()
      .setEdges([edge('a-b', 'a', 'b'), edge('b-c', 'b', 'c')]);

    useWorkflowStore.getState().deleteSelectedElements();
    useWorkflowStore.getState().undo();

    expect(useWorkflowStore.getState().nodes.map((node) => node.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
    expect(useWorkflowStore.getState().edges.map((item) => item.id)).toEqual([
      'a-b',
      'b-c',
    ]);
  });

  it.todo(
    'read-only 사용자는 Backspace/Delete로 노드 삭제 또는 자동 재연결을 수행할 수 없다',
  );
  it.todo(
    '삭제 대상 노드에 incoming edge만 있거나 outgoing edge만 있으면 재연결 없이 해당 노드와 연결 edge만 제거한다',
  );
  it.todo(
    '삭제 대상이 시작 트리거 노드 또는 삭제 제한 노드라면 기존 삭제 제한 정책을 따른다',
  );
  it.todo(
    '여러 노드를 동시에 삭제할 때 삭제되는 노드끼리의 edge는 재연결 후보에서 제외한다',
  );
});
