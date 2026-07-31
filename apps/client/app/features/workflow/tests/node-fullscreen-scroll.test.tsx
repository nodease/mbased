import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { NodeFullscreenEditor } from '../components/editor/NodeFullscreenEditor';

const workflowState = vi.hoisted(() => ({
  fullscreenNodeId: 'llm-1',
  closeNodeFullscreen: vi.fn(),
  openNodeFullscreen: vi.fn(),
  updateNodeData: vi.fn(),
  nodes: [
    {
      id: 'llm-1',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: {
        title: 'LLM',
        provider: 'openai',
        model_id: 'model-1',
        referenced_variables: [],
        parameters: {},
      },
    },
  ],
}));

vi.mock('../store/useWorkflowStore', () => ({
  useWorkflowStore: (
    selector: (state: typeof workflowState) => unknown,
  ) => selector(workflowState),
}));

vi.mock('../hooks/useNodeIO', () => ({
  useNodeIO: () => ({
    node: workflowState.nodes[0],
    inputVariables: [],
    inputVariableGroups: [],
    outputVariables: [],
  }),
}));

vi.mock('../hooks/useNodeNavigation', () => ({
  useNodeNavigation: () => ({
    previousNodes: [],
    nextNodes: [],
    primaryPreviousNode: null,
  }),
}));

vi.mock('../hooks/useKeyboardShortcut', () => ({
  useKeyboardShortcut: () => undefined,
}));

vi.mock('../config/nodeRegistry', () => ({
  getNodeDefinitionByType: () => ({
    name: 'LLM',
    description: 'LLM node',
    color: '#3b82f6',
  }),
}));

vi.mock('../components/nodes/NodeInlinePanel', () => ({
  NodeInlinePanel: ({
    onOpenSidePanel,
  }: {
    onOpenSidePanel?: (panelId: 'knowledge') => void;
  }) => (
    <button type="button" onClick={() => onOpenSidePanel?.('knowledge')}>
      Knowledge 설정 열기
    </button>
  ),
}));

vi.mock(
  '../components/nodes/llm/components/LLMReferenceSidePanel',
  () => ({
    LLMReferenceSidePanel: () => <div>Knowledge panel body</div>,
  }),
);

vi.mock('../components/nodes/ui/VariableInsertionProvider', () => ({
  VariableInsertionProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}));

vi.mock('../components/nodes/ui/useVariableInsertion', () => ({
  useVariableInsertion: () => ({
    activeTarget: null,
    message: null,
    insertOutput: vi.fn(),
  }),
}));

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const renderEditor = () => {
  render(<NodeFullscreenEditor />);

  const openKnowledgeButton = screen.getByRole('button', {
    name: 'Knowledge 설정 열기',
  });
  const centerPanel = openKnowledgeButton.parentElement?.parentElement;
  const grid = centerPanel?.parentElement;
  const layoutShell = grid?.parentElement;
  const body = layoutShell?.parentElement;
  const rightPanel = centerPanel?.nextElementSibling?.nextElementSibling;

  expect(centerPanel).not.toBeNull();
  expect(grid).not.toBeNull();
  expect(layoutShell).not.toBeNull();
  expect(body).not.toBeNull();
  expect(rightPanel).not.toBeNull();

  return {
    openKnowledgeButton,
    centerPanel: centerPanel as HTMLElement,
    grid: grid as HTMLElement,
    layoutShell: layoutShell as HTMLElement,
    body: body as HTMLElement,
    rightPanel: rightPanel as HTMLElement,
  };
};

describe('NodeFullscreenEditor scroll boundaries', () => {
  beforeAll(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    vi.stubGlobal('requestAnimationFrame', () => 1);
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  afterAll(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the center and Knowledge panels inside one bounded-height layout', () => {
    const { openKnowledgeButton, centerPanel, grid, layoutShell, body, rightPanel } =
      renderEditor();

    fireEvent.click(openKnowledgeButton);

    expect(screen.getByText('Knowledge panel body')).toBeInTheDocument();
    expect(body).toHaveClass('h-full', 'min-h-0');
    expect(layoutShell).toHaveClass('h-full', 'min-h-0');
    expect(grid).toHaveClass('h-full', 'min-h-0');
    expect(centerPanel).toHaveClass('min-h-0', 'overflow-y-auto');
    expect(rightPanel).toHaveClass('min-h-0', 'overflow-hidden');
  });

  it('excludes fullscreen panel wheel events from canvas zoom capture', () => {
    renderEditor();

    const fullscreenRoot = screen
      .getByRole('button', { name: '워크플로우로 가기' })
      .closest('[data-canvas-shortcut-scope="blocked"]');

    expect(fullscreenRoot).toHaveClass('nowheel');
  });
});
