import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

const workflowApiMock = vi.hoisted(() => ({
  getDraftWorkflow: vi.fn(),
  getCostOptimizerAvailability: vi.fn(),
  compareCostOptimizerCandidate: vi.fn(),
  applyCostOptimizerCandidate: vi.fn(),
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
          node_options: {
            model_id: 'gpt-4.1',
            provider: 'openai',
            system_prompt: 'baseline system',
            user_prompt: 'baseline user',
            assistant_prompt: '',
            parameters: {
              max_tokens: 800,
              temperature: 0.2,
              top_p: 0.8,
              presence_penalty: 0.1,
              frequency_penalty: -0.1,
              stop: ['END'],
            },
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

describe('FR-008 Cost Optimizer apply flow', () => {
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
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-14T00:00:00Z',
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
        state: 'compatible',
        label: '검증 가능',
        message: 'downstream 호환 가능',
      },
    });
    workflowApiMock.applyCostOptimizerCandidate.mockResolvedValue({
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      applied: true,
      downstream_compatibility: { state: 'compatible', label: '검증 가능' },
        graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('성공한 B 후보를 현재 노드에 적용한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));
    await screen.findByText('비교 리포트');

    fireEvent.click(screen.getByRole('button', { name: '현재 노드에 적용' }));

    expect(workflowApiMock.applyCostOptimizerCandidate).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog', {
      name: '후보 설정 적용 확인',
    });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText('변경되는 모델')).toBeInTheDocument();
    expect(within(dialog).getByText('변경되는 prompt')).toBeInTheDocument();
    expect(within(dialog).getByText('변경되는 parameter')).toBeInTheDocument();
    expect(within(dialog).getByText(/top_p: 0.8 → 0.8/)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/presence_penalty: 0.1 → 0.1/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/frequency_penalty: -0.1 → -0.1/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/stop: END → END/)).toBeInTheDocument();
    expect(within(dialog).getByText('변경되는 출력 형식')).toBeInTheDocument();
    expect(within(dialog).getByText('변경되는 JSON schema')).toBeInTheDocument();
    expect(
      within(dialog).getByText('변경되는 Knowledge/RAG 설정'),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('downstream 호환성')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '적용 확인' }));

    await waitFor(() => {
      expect(workflowApiMock.applyCostOptimizerCandidate).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
        expect.objectContaining({
          comparison_id: 'comparison-1',
          acknowledge_downstream_warning: false,
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
          candidate_settings: expect.objectContaining({
            model_id: 'gpt-4.1',
          }),
        }),
      );
    });
    expect(
      await screen.findByText('현재 노드에 후보 설정을 적용했습니다.'),
    ).toBeInTheDocument();
  });

  it('downstream warning 후보는 사용자가 확인해야 적용한다', async () => {
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
        state: 'warning',
        label: '확인 필요',
        message: 'downstream 계약 확인이 필요합니다.',
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
    await screen.findByText('비교 리포트');

    fireEvent.click(screen.getByRole('button', { name: '현재 노드에 적용' }));
    expect(screen.getByRole('dialog', { name: '후보 설정 적용 확인' }))
      .toBeInTheDocument();
    expect(screen.getByText(/현재 workflow 테스트 실행으로/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '취소' }));
    expect(workflowApiMock.applyCostOptimizerCandidate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '현재 노드에 적용' }));
    fireEvent.click(screen.getByRole('button', { name: '적용 확인' }));

    await waitFor(() => {
      expect(workflowApiMock.applyCostOptimizerCandidate).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
        expect.objectContaining({
          acknowledge_downstream_warning: true,
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        }),
      );
    });
  });
});
