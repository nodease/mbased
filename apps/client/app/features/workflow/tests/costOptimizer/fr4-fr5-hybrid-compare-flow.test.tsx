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
const baselineNodeOptionsMock = vi.hoisted(() => ({
  current: {
    model_id: 'gpt-4.1',
    provider: 'openai',
    system_prompt: 'baseline system',
    user_prompt: 'baseline user',
    assistant_prompt: '',
    parameters: { max_tokens: 800, temperature: 0.2, stop: ['END'] },
    knowledgeBases: [],
    retrievedContextMaxChars: 6000,
  } as Record<string, unknown>,
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
          node_options: baselineNodeOptionsMock.current,
        })
      }
    >
      테스트 baseline 선택
    </button>
  ),
}));

vi.mock('../../components/costOptimizer/NodeSettingsComparisonPanel', () => ({
  NodeSettingsComparisonPanel: ({
    title,
    readOnly,
    onNodeDataChange,
  }: {
    title: string;
    readOnly?: boolean;
    onNodeDataChange?: (updates: {
      parameters?: Record<string, unknown>;
      retrievedContextMaxChars?: number;
    }) => void;
  }) => (
    <section aria-label={title}>
      {title}
      {!readOnly ? (
        <>
          <button
            type="button"
            onClick={() =>
              onNodeDataChange?.({ parameters: { stop: undefined } })
            }
          >
            stop 모두 삭제
          </button>
          <button
            type="button"
            onClick={() =>
              onNodeDataChange?.({ retrievedContextMaxChars: undefined })
            }
          >
            참조 길이 제한 해제
          </button>
        </>
      ) : null}
    </section>
  ),
}));

const loadPlaygroundPage = async () => {
  const module = await import(
    '@/app/modules/[id]/cost-optimizer/[nodeId]/page'
  );
  return module.default;
};

describe('FR-004/FR-005 Cost Optimizer hybrid compare flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    baselineNodeOptionsMock.current = {
      model_id: 'gpt-4.1',
      provider: 'openai',
      system_prompt: 'baseline system',
      user_prompt: 'baseline user',
      assistant_prompt: '',
      parameters: { max_tokens: 800, temperature: 0.2, stop: ['END'] },
      knowledgeBases: [],
      retrievedContextMaxChars: 6000,
    };
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
        usage: { total_tokens: 249, total_cost: 0.0012, latency_ms: 1600 },
      },
      candidate: {
        label: 'B',
        status: 'success',
        output: { text: 'candidate output' },
        usage: { total_tokens: 128, total_cost: 0.00042 },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      downstream_compatibility: {
        state: 'unknown',
        label: '판정 전',
        message: 'downstream compatibility is not evaluated yet',
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('B 실행은 baseline_id와 candidate만 보내고 A baseline 영역은 재실행 상태로 바꾸지 않는다', async () => {
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
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
        expect.objectContaining({
          baseline_id: 'baseline-1',
          candidate: expect.objectContaining({
            label: 'B',
            model_id: 'gpt-4.1',
            system_prompt: 'baseline system',
            user_prompt: 'baseline user',
            parameters: expect.objectContaining({
              max_tokens: 800,
              temperature: 0.2,
            }),
          }),
        }),
      );
    });

    const requestBody =
      workflowApiMock.compareCostOptimizerCandidate.mock.calls[0][2];
    expect(requestBody).not.toHaveProperty('inputs');
    expect(screen.getByText('A baseline 출력')).toBeInTheDocument();
    expect(screen.getByText(/baseline output/)).toBeInTheDocument();
    expect(screen.queryByText('A baseline 실행 중')).not.toBeInTheDocument();
    expect(await screen.findByText('candidate output')).toBeInTheDocument();
    expect(screen.getAllByText('128').length).toBeGreaterThan(0);
    expect(screen.getAllByText('$0.00042').length).toBeGreaterThan(0);
  });

  it('B candidate 자동 라우팅은 모델 id가 비어 있어도 compare API로 실행된다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      baseline: {
        baseline_id: 'baseline-1',
        usage: { total_tokens: 249, total_cost: 0.0012, latency_ms: 1600 },
      },
      candidate: {
        label: 'B',
        status: 'success',
        output: {
          text: 'candidate output',
          model: 'gpt-5-mini',
          metadata: {
            model_routing: {
              decision_source: 'active_policy',
              selected_model: 'gpt-5-mini',
              fallback_model: 'gpt-4.1',
              reason_code: 'policy_default',
            },
          },
        },
        usage: { total_tokens: 128, total_cost: 0.00042 },
        latency_ms: 940,
        trace: {
          model_routing: {
            decision_source: 'active_policy',
            selected_model: 'gpt-5-mini',
            fallback_model: 'gpt-4.1',
            reason_code: 'policy_default',
          },
        },
        error_message: null,
      },
      diff: {},
      downstream_compatibility: {
        state: 'unknown',
        label: '판정 전',
        message: 'downstream compatibility is not evaluated yet',
      },
    });
    baselineNodeOptionsMock.current = {
      model_id: '',
      provider: 'openai',
      auto_model_routing: true,
      model_routing_policy: {
        status: 'active',
        policy_version: 'policy-v1',
        active_policy: {
          default_model_id: 'gpt-5-mini',
          fallback_model_id: 'gpt-4.1',
        },
      },
      system_prompt: 'baseline system',
      user_prompt: 'baseline user',
      assistant_prompt: '',
      parameters: { max_tokens: 800, temperature: 0.2 },
      knowledgeBases: [],
    };
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
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    const requestBody =
      workflowApiMock.compareCostOptimizerCandidate.mock.calls[0][2];
    expect(requestBody.candidate).toEqual(
      expect.objectContaining({
        model_id: '',
        auto_model_routing: true,
        model_routing_policy: baselineNodeOptionsMock.current
          .model_routing_policy,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: '근거/Trace' }));

    expect(await screen.findByText('모델 선택 결과')).toBeInTheDocument();
    expect(screen.getByText('gpt-5-mini')).toBeInTheDocument();
    expect(screen.getByText('저장된 정책으로 모델 선택')).toBeInTheDocument();
    expect(screen.getByText('기본 라우팅 규칙 일치')).toBeInTheDocument();
  });

  it('고급 설정에서 stop sequence를 모두 삭제하면 B 실행 request에도 빈 stop 배열을 보낸다', async () => {
    workflowApiMock.getDraftWorkflow.mockResolvedValueOnce({
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
            parameters: { max_tokens: 800, temperature: 0.2, stop: ['END'] },
            knowledgeBases: [],
          },
        },
      ],
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: 'stop 모두 삭제' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    const requestBody =
      workflowApiMock.compareCostOptimizerCandidate.mock.calls[0][2];
    expect(requestBody.candidate.parameters.stop).toEqual([]);
  });

  it('지식 베이스 설정에서 참조 문서 길이 제한을 비우면 B 실행 request에도 제한 없음으로 보낸다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '테스트 baseline 선택' }));
    fireEvent.click(screen.getByRole('button', { name: '참조 길이 제한 해제' }));
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    const requestBody =
      workflowApiMock.compareCostOptimizerCandidate.mock.calls[0][2];
    expect(
      requestBody.candidate.knowledge.retrieved_context_max_chars,
    ).toBeNull();
  });
});
