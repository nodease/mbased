import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

const workflowApiMock = vi.hoisted(() => ({
  getDraftWorkflow: vi.fn(),
  getCostOptimizerAvailability: vi.fn(),
  compareCostOptimizerCandidate: vi.fn(),
  listCostOptimizerExperiments: vi.fn(),
  getCostOptimizerExperimentCandidate: vi.fn(),
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
          total_tokens: 120,
          latency_ms: 1600,
          input_preview: '{"message":"baseline input"}',
          output_preview: '{"text":"baseline output"}',
          has_trace: true,
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

describe('FR-009 Cost Optimizer usage display', () => {
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
      baseline: {
        baseline_id: 'baseline-1',
        usage: {
          prompt_tokens: 90,
          completion_tokens: 30,
          total_tokens: 120,
          cost: 0.0012,
          latency_ms: 1600,
        },
      },
      candidate: {
        label: 'B',
        status: 'success',
        output: { text: 'candidate output' },
        usage: {
          prompt_tokens: 50,
          completion_tokens: 20,
          total_tokens: 70,
          cost: 0.00042,
          latency_ms: 940,
        },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      downstream_compatibility: { state: 'compatible', label: '검증 가능' },
    });
    workflowApiMock.listCostOptimizerExperiments.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
          experiment_id: 'comparison-previous',
          workflow_id: 'workflow-1',
          node_id: 'llm-1',
          baseline_node_run_id: 'baseline-1',
          status: 'completed',
          created_at: '2026-07-05T01:30:00+00:00',
          usage_summary: { total_tokens: 88, cost: 0.00031 },
          candidates: [
            {
              candidate_id: 'candidate-previous',
              name: '저비용 후보',
              status: 'success',
              model_id: 'gpt-4.1-mini',
              total_cost: 0.00031,
              total_tokens: 88,
              latency_ms: 720,
              schema_status: 'pass',
              downstream_state: 'compatible',
              is_applied: false,
              created_at: '2026-07-05T01:30:00+00:00',
            },
          ],
        },
      ],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('A와 B의 prompt/completion/total token, 비용, latency를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    expect(await screen.findByText('A 프롬프트 토큰')).toBeInTheDocument();
    expect(screen.getByText('A 응답 토큰')).toBeInTheDocument();
    expect(screen.getByText('B 프롬프트 토큰')).toBeInTheDocument();
    expect(screen.getByText('B 응답 토큰')).toBeInTheDocument();
    expect(screen.getAllByText('90').length).toBeGreaterThan(0);
    expect(screen.getAllByText('30').length).toBeGreaterThan(0);
    expect(screen.getAllByText('50').length).toBeGreaterThan(0);
    expect(screen.getAllByText('20').length).toBeGreaterThan(0);
    expect(screen.getAllByText('$0.00042').length).toBeGreaterThan(0);
    expect(screen.getAllByText('940ms').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/비교 실행에서 발생한 LLM 비용도 usage 기록에 포함됩니다/),
    ).toBeInTheDocument();
  });

  it('B 후보 모델 가격 정보가 없으면 비용 계산 불가 상태를 표시한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      baseline: {
        baseline_id: 'baseline-1',
        usage: {
          prompt_tokens: 90,
          completion_tokens: 30,
          total_tokens: 120,
          cost: 0.0012,
          latency_ms: 1600,
        },
      },
      candidate: {
        label: 'B',
        status: 'success',
        output: { text: 'candidate output' },
        usage: {
          prompt_tokens: 50,
          completion_tokens: 20,
          total_tokens: 70,
          cost: null,
          total_cost: null,
          latency_ms: 940,
        },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      downstream_compatibility: { state: 'compatible', label: '검증 가능' },
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

    expect(await screen.findAllByText('비용 계산 불가')).not.toHaveLength(0);
  });

  it('결과 분석 화면은 같은 baseline의 이전 experiment 이력과 필터를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerExperiments).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
        {
          baseline_id: 'baseline-1',
          candidate_status: 'success',
          model: undefined,
          schema_status: undefined,
          downstream_state: undefined,
          limit: 20,
          offset: 0,
        },
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '펼치기' }));
    expect(screen.getByText('이전 실험 이력')).toBeInTheDocument();
    expect(screen.getByLabelText('후보 상태')).toBeInTheDocument();
    expect(screen.getByLabelText('모델 필터')).toBeInTheDocument();
    expect(screen.getByLabelText('Schema 상태')).toBeInTheDocument();
    expect(screen.getByLabelText('Downstream 상태')).toBeInTheDocument();
    expect(screen.getByText('저비용 후보')).toBeInTheDocument();
    expect(screen.getByText('gpt-4.1-mini')).toBeInTheDocument();
    expect(screen.getByText('$0.00031')).toBeInTheDocument();
    expect(screen.getByText('88')).toBeInTheDocument();
    expect(screen.getByText('720ms')).toBeInTheDocument();
  });

  it('결과 분석 화면은 기간, 실행자, 적용 여부 필터를 experiment history API에 전달한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerExperiments).toHaveBeenCalled();
    });
    const initialRequestCount =
      workflowApiMock.listCostOptimizerExperiments.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: '펼치기' }));
    fireEvent.change(screen.getByLabelText('시작일'), {
      target: { value: '2026-07-01' },
    });
    fireEvent.change(screen.getByLabelText('종료일'), {
      target: { value: '2026-07-05' },
    });
    fireEvent.change(screen.getByLabelText('실행자'), {
      target: { value: 'user-1' },
    });
    fireEvent.change(screen.getByLabelText('적용 여부'), {
      target: { value: 'false' },
    });

    expect(workflowApiMock.listCostOptimizerExperiments).toHaveBeenCalledTimes(
      initialRequestCount,
    );
    fireEvent.click(screen.getByRole('button', { name: '필터 적용' }));

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerExperiments).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-1',
        expect.objectContaining({
          baseline_id: 'baseline-1',
          date_from: '2026-07-01T00:00:00',
          date_to: '2026-07-05T23:59:59',
          created_by: 'user-1',
          is_applied: false,
        }),
      );
    });
  });
});
