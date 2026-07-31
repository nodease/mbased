import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

const workflowApiMock = vi.hoisted(() => ({
  getDraftWorkflow: vi.fn(),
  getCostOptimizerAvailability: vi.fn(),
  compareCostOptimizerCandidate: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'workflow-1', nodeId: 'llm-1' }),
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: workflowApiMock,
}));

vi.mock('../../components/costOptimizer/CostOptimizerBaselineSelection', () => ({
  CostOptimizerBaselineSelection: ({
    onBaselineSelected,
  }: {
    onBaselineSelected: (baseline: Record<string, unknown>) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onBaselineSelected({
          baseline_id: 'baseline-1',
          model: 'gpt-4.1',
          cost: 0.0012,
          total_tokens: 249,
          latency_ms: 1600,
          input_preview: '{"message":"baseline input"}',
          output_preview: '{"text":"baseline output"}',
          has_trace: true,
          downstream_compatibility: {
            state: 'unknown',
            label: '판정 전',
            message: 'baseline 선택 전 판정 전입니다.',
          },
          node_options: {
            model_id: 'gpt-4.1',
            provider: 'openai',
            system_prompt: 'baseline system',
            user_prompt: 'baseline user',
            assistant_prompt: '',
            parameters: { max_tokens: 800, temperature: 0.2 },
            knowledgeBases: [],
          },
        })
      }
    >
      테스트 baseline 선택
    </button>
  ),
}));

vi.mock('../../components/costOptimizer/NodeSettingsComparisonPanel', () => ({
  NodeSettingsComparisonPanel: ({ title }: { title: string }) => (
    <section aria-label={title}>{title}</section>
  ),
}));

const loadPlaygroundPage = async () => {
  const module = await import(
    '@/app/modules/[id]/cost-optimizer/[nodeId]/page'
  );
  return module.default;
};

const expectVisibleNonOptionLabel = async (label: string) => {
  const matches = await screen.findAllByText(label);
  expect(matches.some((element) => element.tagName !== 'OPTION')).toBe(true);
};

describe('FR-007 Cost Optimizer downstream compatibility UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.ResizeObserver = class ResizeObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    };
    workflowApiMock.getCostOptimizerAvailability.mockResolvedValue({
      available: true,
      reason: null,
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      node_type: 'llmNode',
      permission: {
        can_compare: true,
        can_apply: true,
        required_auth_state: 'builder',
      },
    });
    workflowApiMock.getDraftWorkflow.mockResolvedValue({
      nodes: [
        {
          id: 'llm-1',
          type: 'llmNode',
          position: { x: 0, y: 0 },
          data: {
            title: '티켓 처리 판단',
            provider: 'openai',
            model_id: 'gpt-4.1',
            system_prompt: 'system',
            user_prompt: 'user',
            assistant_prompt: '',
            referenced_variables: [],
            parameters: { max_tokens: 800, temperature: 0.2 },
            knowledgeBases: [],
          },
        },
      ],
    });
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValue({
      comparison_id: 'comparison-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      baseline: { baseline_id: 'baseline-1' },
      candidate: {
        label: 'B',
        status: 'success',
        output: { text: 'candidate output' },
        usage: { total_tokens: 128, cost: 0.00042 },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      downstream_compatibility: {
        state: 'warning',
        label: '주의 필요',
        message:
          'downstream 구조가 일부 달라졌지만 첫 소비 노드는 동일합니다. 적용 전 현재 workflow 테스트 실행으로 확인해야 합니다.',
        contract_check: {
          status: 'warning',
          checked_node_ids: ['extract-result'],
          warnings: ['downstream structure changed after first consumer'],
        },
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('compare 응답의 downstream 3상태 라벨과 설명을 결과 분석 화면에 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await expectVisibleNonOptionLabel('주의 필요');
    expect(
      screen.getByText(/적용 전 현재 workflow 테스트 실행으로 확인해야 합니다/),
    ).toBeInTheDocument();
    expect(screen.getByText('extract-result')).toBeInTheDocument();
    expect(
      screen.getByText(/외부 전송이나 쓰기 작업이 있는 downstream 노드는 자동 실행하지 않습니다/),
    ).toBeInTheDocument();
  });

  it('검증 불가 상태는 workflow 전체 테스트 실행 안내를 fallback으로 표시한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      baseline: { baseline_id: 'baseline-1' },
      candidate: {
        label: 'B',
        status: 'success',
        output: { text: 'candidate output' },
        usage: { total_tokens: 128, cost: 0.00042 },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      downstream_compatibility: {
        state: 'incompatible',
        contract_check: {
          status: 'incompatible',
          checked_node_ids: [],
          warnings: ['first consumer changed or missing'],
        },
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await expectVisibleNonOptionLabel('검증 불가');
    expect(
      screen.getByText(/workflow 전체 테스트 실행으로 downstream 성공 여부를 별도 확인하세요/),
    ).toBeInTheDocument();
  });
});
