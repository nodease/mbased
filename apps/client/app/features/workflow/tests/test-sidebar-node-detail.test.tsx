import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TestSidebar } from '../components/editor/TestSidebar';

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
    nodes: [
      {
        id: 'start',
        type: 'startNode',
        data: { title: '입력', observability: { status: 'success' } },
      },
      {
        id: 'llm-triage',
        type: 'llmNode',
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
    testExecutionStartedAt: 1_000,
    testExecutionFinishedAt: 2_500,
    testExecutionResult: { 'llm-triage': { text: '분류 완료' } },
    testNodeResults: [
      {
        nodeId: 'llm-triage',
        nodeType: 'llmNode',
        output: {
          text: '분류 완료',
          model: 'gpt-5.6-terra',
          cost: 0.001,
          usage: { total_tokens: 42 },
          metadata: { fallback_used: true },
        },
        traceMetadata: {
          llm: {
            selected_model: 'gpt-5.6-luna',
            fallback_model: 'gpt-5.6-terra',
            fallback_used: true,
            fallback_from_model: 'gpt-5.6-luna',
            fallback_reason_code: 'provider_call_failed',
            decision_source: 'stored_model',
            execution_mode: 'test',
            strategy_id: 'judge_bootstrap_incremental_v1',
            reason_code: 'judge_bootstrap_required',
            judge_called: false,
            policy_source: 'active_deployment',
            included_in_routing_learning: false,
            input_length_bucket: 'medium',
            output_format: 'json',
            schema_required: true,
            knowledge_enabled: false,
          },
        },
      },
    ],
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
  return { useWorkflowStore };
});

afterEach(() => cleanup());

describe('TestSidebar node execution details', () => {
  it('LLM 노드가 아닌 실행 결과 카드와 상세에는 비용·토큰을 표시하지 않는다', () => {
    render(<TestSidebar />);

    const startCard = screen.getByText('입력').closest('div.overflow-hidden');
    expect(startCard).not.toBeNull();
    expect(within(startCard as HTMLElement).getByText('시간')).toBeVisible();
    expect(
      within(startCard as HTMLElement).queryByText('비용'),
    ).not.toBeInTheDocument();
    expect(
      within(startCard as HTMLElement).queryByText('토큰'),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '입력 상세 보기' }));
    expect(screen.getByRole('heading', { name: '입력 실행 상세' })).toBeVisible();
    expect(screen.getByText('실행 시간')).toBeVisible();
    expect(screen.queryByText('비용')).not.toBeInTheDocument();
  });

  it('실행 전 정책 미리보기 없이 실행 결과의 LLM 노드를 같은 사이드바에서 상세 보기로 전환한다', () => {
    render(<TestSidebar />);

    expect(screen.queryByText('라우팅 판단')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '라우팅 미리보기' }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '문의 분류 상세 보기' }),
    );

    expect(
      screen.getByRole('heading', { name: '문의 분류 실행 상세' }),
    ).toBeVisible();
    expect(
      screen.queryByText(
        '이 테스트 실행은 자동 라우팅 정책의 학습 및 갱신 횟수에 포함되지 않습니다.',
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        '이 테스트 실행은 배포 정책을 미리 적용한 결과이며, 정책 학습에는 포함되지 않습니다.',
      ),
    ).toBeVisible();
    expect(screen.getAllByText(/학습.*포함되지/)).toHaveLength(1);
    expect(screen.getByText('기본 모델로 실행')).toBeVisible();
    expect(screen.getByText('보통 입력')).toBeVisible();
    expect(screen.getByText('Judge 호출 안 함')).toBeVisible();
    expect(
      screen.getByText(
        (_, element) => element?.textContent === '최초 선택 모델gpt-5.6-luna',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        (_, element) => element?.textContent === '실제 대체 모델gpt-5.6-terra',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        (_, element) => element?.textContent === '대체 사유Provider 호출 실패',
      ),
    ).toBeVisible();

    const outputHeading = screen.getByText('출력 데이터');
    const routingHeading = screen.getByText('모델 선택 결과');
    expect(
      outputHeading.compareDocumentPosition(routingHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 결과로 돌아가기' }),
    );
    expect(screen.getByText('노드별 실행 결과')).toBeVisible();
  });
});
