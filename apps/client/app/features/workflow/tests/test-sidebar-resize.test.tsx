import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TestSidebar } from '../components/editor/TestSidebar';

const testStore = vi.hoisted(() => ({
  state: null as { isTestPanelOpen: boolean } | null,
}));

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setCenter: vi.fn(),
    getViewport: vi.fn(() => ({ zoom: 1 })),
  }),
}));

vi.mock('../store/useWorkflowStore', () => {
  const state = {
    isTestPanelOpen: true,
    toggleTestPanel: vi.fn(),
    nodes: [],
    activeWorkflowId: 'workflow-1',
    setNodes: vi.fn(),
    updateNodeData: vi.fn(),
    workflowAccess: { can_execute: true },
    edges: [],
    features: {},
    envVariables: [],
    runtimeVariables: [],
    testExecutionStatus: 'idle',
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
    addTestNodeResult: vi.fn(),
    finishTestExecution: vi.fn(),
    failTestExecution: vi.fn(),
    resetTestExecution: vi.fn(),
  };
  const useWorkflowStore = Object.assign(
    vi.fn(() => state),
    { getState: vi.fn(() => state) },
  );
  testStore.state = state;
  return { useWorkflowStore };
});

afterEach(() => {
  if (testStore.state) {
    testStore.state.isTestPanelOpen = true;
  }
  cleanup();
});

describe('TestSidebar resize', () => {
  it('undoStack이 없는 이전 store shape도 빈 history로 처리한다', () => {
    expect(() => render(<TestSidebar />)).not.toThrow();
  });

  it('기본 너비를 560px로 표시한다', () => {
    render(<TestSidebar />);

    expect(screen.getByTestId('test-execution-sidebar')).toHaveStyle({
      width: '560px',
    });
  });

  it('패널 안의 글자 크기를 기존보다 한 단계 키운다', () => {
    render(<TestSidebar />);

    expect(screen.getByTestId('test-execution-sidebar')).toHaveClass(
      'text-lg',
      '[&_.text-xs]:text-sm',
      '[&_.text-sm]:text-base',
      '[&_.text-base]:text-lg',
      '[&_.text-lg]:text-xl',
      '[&_.text-xl]:text-2xl',
      '[&_.text-2xl]:text-3xl',
    );
  });

  it('캔버스 상단에 맞춰 패널을 배치한다', () => {
    render(<TestSidebar />);

    expect(screen.getByTestId('test-execution-sidebar')).toHaveClass('top-2');
    expect(screen.getByTestId('test-execution-sidebar')).not.toHaveClass(
      'top-18',
    );
  });

  it('왼쪽 handle을 드래그해 넓히되 최대 720px를 넘지 않는다', () => {
    render(<TestSidebar />);

    const handle = screen.getByRole('separator', {
      name: '테스트 실행 패널 너비 조절',
    });
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 560 });
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(screen.getByTestId('test-execution-sidebar')).toHaveStyle({
      width: '720px',
    });
  });

  it('키보드 조작으로도 최소 너비 아래로 줄지 않는다', () => {
    render(<TestSidebar />);

    const handle = screen.getByRole('separator', {
      name: '테스트 실행 패널 너비 조절',
    });
    fireEvent.keyDown(handle, { key: 'Home' });

    expect(screen.getByTestId('test-execution-sidebar')).toHaveStyle({
      width: '440px',
    });
  });

  it('같은 편집 세션에서 닫았다 다시 열어도 조정한 너비를 유지한다', () => {
    const { rerender } = render(<TestSidebar />);
    const handle = screen.getByRole('separator', {
      name: '테스트 실행 패널 너비 조절',
    });
    fireEvent.keyDown(handle, { key: 'End' });

    testStore.state!.isTestPanelOpen = false;
    rerender(<TestSidebar />);
    expect(screen.queryByTestId('test-execution-sidebar')).toBeNull();

    testStore.state!.isTestPanelOpen = true;
    rerender(<TestSidebar />);
    expect(screen.getByTestId('test-execution-sidebar')).toHaveStyle({
      width: '720px',
    });
  });
});
