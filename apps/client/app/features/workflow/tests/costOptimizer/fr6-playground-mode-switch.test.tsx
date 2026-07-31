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

const searchParamsMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

const paramsMock = vi.hoisted(() => ({
  current: { id: 'workflow-1', nodeId: 'llm-1' },
}));

const workflowApiMock = vi.hoisted(() => ({
  getDraftWorkflow: vi.fn(),
  getCostOptimizerAvailability: vi.fn(),
  getCostOptimizerLatestBaseline: vi.fn(),
  compareCostOptimizerCandidate: vi.fn(),
  listCostOptimizerExperiments: vi.fn(),
  getCostOptimizerExperimentCandidate: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: () => paramsMock.current,
  useRouter: () => routerMock,
  useSearchParams: () => searchParamsMock,
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: workflowApiMock,
}));

vi.mock(
  '../../components/costOptimizer/CostOptimizerBaselineSelection',
  () => ({
    CostOptimizerBaselineSelection: ({
      onBaselineSelected,
      onClose,
    }: {
      onBaselineSelected: (baseline: Record<string, unknown>) => void;
      onClose: () => void;
    }) => (
      <div>
        <button
          type="button"
          onClick={() =>
            onBaselineSelected({
              baseline_id: 'baseline-1',
              run_started_at: '2026-07-05T01:30:00Z',
              model: 'gpt-4.1',
              cost: 0.0012,
              total_tokens: 249,
              latency_ms: 1600,
              input_preview: JSON.stringify({
                message:
                  'baseline input '.repeat(20) + '끝까지 보여야 하는 입력 문장',
                customerTier: 'enterprise',
                product: 'workflow',
                severity: 'high',
                region: 'ap-northeast-2',
                owner: 'support',
                escalationReason: '일곱 번째 입력 필드도 보여야 함',
                requestedAction: '여덟 번째 입력 필드도 보여야 함',
              }),
              output_preview: JSON.stringify({
                answer:
                  'baseline output '.repeat(20) +
                  '끝까지 보여야 하는 출력 문장',
                approvalRequired: false,
                urgency: 'normal',
                routedTeam: 'support',
                category: 'billing',
                confidence: 0.91,
                followUp: '일곱 번째 출력 필드도 보여야 함',
                auditNote: '여덟 번째 출력 필드도 보여야 함',
              }),
              output: {
                cost: 0.0012,
                text: JSON.stringify({
                  answer:
                    'baseline output '.repeat(20) +
                    '끝까지 보여야 하는 출력 문장',
                  approvalRequired: false,
                  urgency: 'normal',
                  routedTeam: 'support',
                  category: 'billing',
                  confidence: 0.91,
                  followUp: '일곱 번째 출력 필드도 보여야 함',
                  auditNote: '여덟 번째 출력 필드도 보여야 함',
                }),
              },
              has_trace: true,
              downstream_compatibility: {
                state: 'compatible',
                label: '검증 가능',
                message: 'downstream compatible',
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
        <button type="button" onClick={onClose}>
          닫기
        </button>
      </div>
    ),
  }),
);

vi.mock('../../components/costOptimizer/NodeSettingsComparisonPanel', () => ({
  NodeSettingsComparisonPanel: ({
    title,
    hideTitle,
    readOnly,
    onChange,
    onNodeDataChange,
    draft,
  }: {
    title: string;
    hideTitle?: boolean;
    readOnly?: boolean;
    onChange?: (key: string, value: string) => void;
    onNodeDataChange?: (updates: Record<string, unknown>) => void;
    draft?: { model_id?: string; max_tokens?: number };
  }) => (
    <section aria-label={title}>
      {hideTitle
        ? '설정 패널'
        : `${title}:${draft?.model_id || '-'}:${draft?.max_tokens || '-'}`}
      <span data-testid={`draft-${title}`}>
        {`${draft?.model_id || '-'}:${draft?.max_tokens || '-'}`}
      </span>
      {!readOnly && onChange ? (
        <button
          type="button"
          onClick={() => onChange('model_id', 'gpt-4.1-mini')}
        >
          후보 모델 변경
        </button>
      ) : null}
      {!readOnly && onNodeDataChange ? (
        <button
          type="button"
          onClick={() =>
            onNodeDataChange({
              dedupeRetrievedContext: true,
              retrievedContextMaxChars: 6000,
              retrievedContextCompression: 'light',
              answerGroundingCheck: 'basic',
            })
          }
        >
          RAG 비용 옵션 변경
        </button>
      ) : null}
    </section>
  ),
}));

const loadPlaygroundPage = async () => {
  const module =
    await import('@/app/modules/[id]/cost-optimizer/[nodeId]/page');
  return module.default;
};

describe('FR-006 Cost Optimizer playground mode switch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    paramsMock.current = { id: 'workflow-1', nodeId: 'llm-1' };
    searchParamsMock.get.mockReturnValue(null);
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
      name: '고객 티켓 처리 워크플로우',
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
    workflowApiMock.getCostOptimizerLatestBaseline.mockResolvedValue({
      baseline: {
        baseline_id: 'latest-baseline-1',
        model: 'gpt-4.1',
        cost: 0.0012,
        total_tokens: 249,
        latency_ms: 1600,
        input_preview: '{"message":"latest input"}',
        output_preview: '{"text":"latest output"}',
        node_options: {
          model_id: 'gpt-4.1',
          provider: 'openai',
          system_prompt: 'latest system',
          user_prompt: 'latest user',
          assistant_prompt: '',
          parameters: { max_tokens: 800, temperature: 0.2 },
          knowledgeBases: [],
        },
      },
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
        usage: { total_tokens: 128, total_cost: 0.00042, latency_ms: 940 },
        latency_ms: 940,
        error_message: null,
      },
      diff: {},
      quality_evaluation: {
        status: 'completed',
        baseline: { score: 86 },
        candidate: { score: 82 },
        delta: -4,
        dimensions: {
          clarity_consistency: { baseline: 86, candidate: 82, delta: -4 },
        },
        confidence: 'medium',
        safe_summary:
          '후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.',
        judge_cost: 0.00008,
      },
      downstream_compatibility: {
        state: 'compatible',
        label: '검증 가능',
        message: 'downstream compatible',
      },
    });
    workflowApiMock.listCostOptimizerExperiments.mockResolvedValue({
      items: [
        {
          experiment_id: 'experiment-1',
          workflow_id: 'workflow-1',
          node_id: 'llm-1',
          created_at: '2026-07-05T02:00:00Z',
          candidates: [
            {
              candidate_id: 'candidate-history-1',
              name: 'mini 비용 절감',
              status: 'success',
              model_id: 'gpt-4.1-mini',
              total_cost: 0.00042,
              total_tokens: 128,
              latency_ms: 940,
              schema_status: 'pass',
              downstream_state: 'compatible',
              quality_evaluation: {
                status: 'completed',
                baseline: { score: 74 },
                candidate: { score: 79 },
                delta: 5,
                dimensions: {},
                confidence: 'high',
                safe_summary:
                  '후보 출력의 품질 점수가 기준 출력보다 높게 평가되었습니다.',
                judge_cost: 0.00008,
              },
              is_applied: false,
              created_at: '2026-07-05T02:00:00Z',
            },
          ],
        },
      ],
    });
  });

  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it('builder 권한이 없으면 playground 직접 진입에서도 workflow draft를 불러오지 않는다', async () => {
    workflowApiMock.getCostOptimizerAvailability.mockResolvedValue({
      available: false,
      reason: 'permission.denied',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      node_type: 'llmNode',
      permission: {
        can_compare: false,
        can_apply: false,
        required_auth_state: 'builder',
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getCostOptimizerAvailability).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
      );
    });
    expect(screen.getByText(/비용 비교 권한이 없습니다/i)).toBeInTheDocument();
    expect(workflowApiMock.getDraftWorkflow).not.toHaveBeenCalled();
  });

  it('baseline 선택 전에는 B candidate와 Inspector를 열지 않는다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    expect(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('B candidate')).not.toBeInTheDocument();
    expect(screen.queryByText('Inspector')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '결과 분석' }),
    ).not.toBeInTheDocument();
  });

  it('legacy baseline=latest 진입도 기준 로그를 자동 선택하지 않는다', async () => {
    searchParamsMock.get.mockImplementation((key: string) =>
      key === 'baseline' ? 'latest' : null,
    );
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    expect(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    ).toBeInTheDocument();
    expect(workflowApiMock.getCostOptimizerLatestBaseline).not.toHaveBeenCalled();
    expect(screen.queryByText('B candidate')).not.toBeInTheDocument();
  });

  it('상세 분석 deep link는 저장된 experiment 후보를 선택한 결과 분석 화면을 연다', async () => {
    searchParamsMock.get.mockImplementation((key: string) => {
      if (key === 'comparisonId') return 'comparison-inline-1';
      if (key === 'candidateId') return 'candidate-inline-1';
      return null;
    });
    workflowApiMock.getCostOptimizerExperimentCandidate.mockResolvedValue({
      experiment_id: 'comparison-inline-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      created_at: '2026-07-11T10:10:00Z',
      baseline_summary: {
        baseline_id: 'baseline-inline-1',
        workflow_run_id: 'run-inline-1',
        model: 'gpt-4.1',
        cost: 0.02,
        total_tokens: 1800,
        latency_ms: 4200,
      },
      candidate: {
        candidate_id: 'candidate-inline-1',
        name: '추천 설정 검증',
        status: 'schema_failed',
        model_id: 'gpt-4.1-mini',
        total_cost: 0.004,
        total_tokens: 700,
        latency_ms: 1700,
        schema_status: 'failed',
        downstream_state: 'incompatible',
        created_at: '2026-07-11T10:11:00Z',
      },
    });
    workflowApiMock.listCostOptimizerExperiments.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
          experiment_id: 'different-success-experiment',
          workflow_id: 'workflow-1',
          node_id: 'llm-1',
          candidates: [
            {
              candidate_id: 'different-success-candidate',
              status: 'success',
            },
          ],
        },
      ],
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    expect(
      await screen.findByText('선택한 이전 실험'),
    ).toBeInTheDocument();
    expect(
      workflowApiMock.getCostOptimizerExperimentCandidate,
    ).toHaveBeenCalledWith(
      'workflow-1',
      'llm-1',
      'comparison-inline-1',
      'candidate-inline-1',
    );
    expect(screen.getAllByText('추천 설정 검증').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '결과 분석' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      screen.queryByRole('button', { name: '테스트 baseline 선택' }),
    ).not.toBeInTheDocument();
  });

  it('route의 workflow와 node가 바뀌면 이전 baseline과 비교 결과를 초기화한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();
    const { rerender } = render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });
    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));
    expect(
      await screen.findByRole('table', { name: '핵심 지표 비교' }),
    ).toBeInTheDocument();

    paramsMock.current = { id: 'workflow-2', nodeId: 'llm-2' };
    workflowApiMock.getDraftWorkflow.mockResolvedValueOnce({
      name: '두 번째 워크플로우',
      nodes: [
        {
          id: 'llm-2',
          type: 'llmNode',
          position: { x: 0, y: 0 },
          data: { model_id: 'gpt-4.1-mini', parameters: {} },
        },
      ],
    });
    rerender(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-2',
      );
    });
    expect(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('table', { name: '핵심 지표 비교' }),
    ).not.toBeInTheDocument();
  });

  it('추천 테스트 진입은 baseline 선택 후에만 추천 설정을 B 후보에 반영한다', async () => {
    const presetKey = 'cost-optimizer-recommendations:workflow-1:llm-1:1';
    window.sessionStorage.setItem(
      presetKey,
      JSON.stringify([{ parameters: { max_tokens: 600 } }]),
    );
    searchParamsMock.get.mockImplementation((key: string) =>
      key === 'recommendationPresetKey' ? presetKey : null,
    );
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await screen.findByRole('button', { name: '테스트 baseline 선택' });
    expect(screen.queryByText('B candidate')).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );

    expect(screen.getByTestId('draft-후보 옵션')).toHaveTextContent(
      'gpt-4.1:600',
    );
  });

  it('닫기와 워크플로우로 가기는 대상 노드 상세 화면으로 이동한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '닫기' }));
    expect(routerMock.push).toHaveBeenCalledWith(
      '/modules/workflow-1?node=llm-1',
    );

    fireEvent.click(
      screen.getByRole('button', { name: '워크플로우로 돌아가기' }),
    );
    expect(routerMock.push).toHaveBeenCalledWith(
      '/modules/workflow-1?node=llm-1',
    );
  });

  it('baseline 선택 후 실험 설정이 열리고 결과 분석 모드로 전환할 수 있다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );

    expect(screen.getByRole('button', { name: '실험 설정' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByText('B candidate')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '기준 실행 정보' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '결과 분석' }));

    expect(screen.getByRole('button', { name: '결과 분석' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByText('비교 리포트')).toBeInTheDocument();
    expect(
      screen.getByText(/B 실행 후 결과 분석이 표시됩니다/),
    ).toBeInTheDocument();
  });

  it('상단 context bar는 workflow, target node, baseline 실행 시각, 입력 기준, downstream 상태를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );

    expect(screen.getByText('고객 티켓 처리 워크플로우')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '티켓 처리 판단' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/기준 실행/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2026/).length).toBeGreaterThan(0);
    expect(screen.getByText('같은 입력 기준')).toBeInTheDocument();
    expect(screen.getByText('검증 가능')).toBeInTheDocument();
  });

  it('B 실행 전 결과 분석 mode는 empty state를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '결과 분석' }));

    expect(
      screen.getByText(/B 실행 후 결과 분석이 표시됩니다/),
    ).toBeInTheDocument();
  });

  it('B candidate는 후보 옵션 제목 대신 테스트명을 입력한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );

    const testNameInput = screen.getByLabelText('테스트명');
    expect(testNameInput).toBeInTheDocument();
    expect(screen.queryByText('후보 옵션')).not.toBeInTheDocument();

    fireEvent.change(testNameInput, {
      target: { value: 'gpt-4.1-mini 비용 절감 테스트' },
    });

    expect(testNameInput).toHaveValue('gpt-4.1-mini 비용 절감 테스트');
  });

  it('실험 설정에서는 왼쪽에 실행 시점 옵션을, 오른쪽에 기준 실행 정보를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );

    expect(screen.getByLabelText('실행 시점 옵션')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '기준 실행 정보' }),
    ).toBeInTheDocument();
    expect(screen.getByText('선택된 기준 실행')).toBeInTheDocument();
    expect(screen.getByText(/baseline input/)).toBeInTheDocument();
    expect(screen.getByText(/baseline output/)).toBeInTheDocument();
    expect(
      screen.getByText(/끝까지 보여야 하는 입력 문장/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/끝까지 보여야 하는 출력 문장/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('일곱 번째 입력 필드도 보여야 함'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('여덟 번째 입력 필드도 보여야 함'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('일곱 번째 출력 필드도 보여야 함'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('여덟 번째 출력 필드도 보여야 함'),
    ).toBeInTheDocument();
  });

  it('B 실행 후 후보 설정이 바뀌면 결과 분석에서도 stale 안내를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });
    expect(screen.getByRole('button', { name: '결과 분석' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    fireEvent.click(
      screen.getByRole('button', { name: '실험 설정으로 돌아가기' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '후보 모델 변경' }));
    fireEvent.click(screen.getByRole('button', { name: '결과 분석' }));

    expect(
      screen.getByText(/후보 설정이 마지막 B 실행 이후 변경되었습니다/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '현재 노드에 적용' }),
    ).toBeDisabled();
  });

  it('B 실행 후 후보 설정이 바뀌어도 실험 설정 패널에는 stale 안내를 표시하지 않는다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(
      screen.getByRole('button', { name: '실험 설정으로 돌아가기' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '후보 모델 변경' }));

    expect(
      screen.queryByText(/후보 설정이 마지막 B 실행 이후 변경되었습니다/),
    ).not.toBeInTheDocument();
  });

  it('B 실행 후 RAG 비용 최적화 옵션이 바뀌면 결과 분석에서도 stale 안내를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(
      screen.getByRole('button', { name: '실험 설정으로 돌아가기' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'RAG 비용 옵션 변경' }));
    fireEvent.click(screen.getByRole('button', { name: '결과 분석' }));

    expect(
      screen.getByText(/후보 설정이 마지막 B 실행 이후 변경되었습니다/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '현재 노드에 적용' }),
    ).toBeDisabled();
  });

  it('B 후보 실행 중에는 spinner와 텍스트를 함께 표시한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    expect(
      await screen.findByRole('button', { name: 'B 실행 중' }),
    ).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('B 실행 중');
    expect(
      screen.getByTestId('cost-optimizer-running-spinner'),
    ).toBeInTheDocument();
  });

  it('B 후보 실행 API가 지식 베이스 권한 오류를 반환하면 원인 메시지를 표시한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockRejectedValueOnce({
      response: {
        data: { detail: 'cost_optimizer.knowledge_unavailable' },
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    expect(
      await screen.findByText(/선택한 지식 베이스를 사용할 수 없습니다/),
    ).toBeInTheDocument();
  });

  it('결과 분석 mode는 이전 실험 이력, 판단 요약, 핵심 지표 비교, 출력 품질 비교를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    expect(
      screen.getByRole('heading', { name: '이전 실험 이력' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '펼치기' })).toBeInTheDocument();
    expect(
      screen.queryByRole('columnheader', { name: '실행 시각' }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText(/선택: 방금 실행/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '펼치기' }));

    expect(
      screen.getByRole('columnheader', { name: '실행 시각' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('columnheader', { name: '테스트명' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('방금 실행').length).toBeGreaterThan(0);
    expect(
      screen.getByRole('heading', { name: '주의 필요' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '핵심 지표 비교' })).toBeInTheDocument();
    expect(screen.getByText('입력 토큰')).toBeInTheDocument();
    expect(screen.getByText('출력 토큰')).toBeInTheDocument();
    expect(screen.getByText('전체 토큰')).toBeInTheDocument();
    expect(screen.getByText('실행 시간')).toBeInTheDocument();
    expect(screen.getByText('출력 품질 점수')).toBeInTheDocument();
    expect(screen.getByText('86점')).toBeInTheDocument();
    expect(screen.getByText('82점')).toBeInTheDocument();
    expect(
      screen.getByText('4점 하락 · 신뢰도 보통'),
    ).toBeInTheDocument();
    expect(
      screen.getByTitle(/품질 평가 비용 \$0\.00008/),
    ).toBeInTheDocument();
    expect(screen.getByText('출력 스키마')).toBeInTheDocument();
    expect(screen.getAllByText('후속 노드 영향').length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: '출력 품질 비교' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'A baseline 출력' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'B candidate 출력' })).toBeInTheDocument();
    expect(screen.getAllByText('approvalRequired').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/baseline output/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/candidate output/).length).toBeGreaterThan(0);
  });

  it('품질 평가가 unavailable이면 핵심 지표 행과 실제 발생한 judge 비용을 유지한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-unavailable',
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
        usage: { total_tokens: 128, total_cost: 0.00042, latency_ms: 940 },
        latency_ms: 940,
      },
      diff: {},
      quality_evaluation: {
        status: 'unavailable',
        baseline: { score: null },
        candidate: { score: null },
        delta: null,
        dimensions: {},
        confidence: 'unavailable',
        safe_summary: '품질 평가 응답을 해석하지 못했습니다.',
        judge_cost: 0.00008,
      },
      downstream_compatibility: {
        state: 'compatible',
        label: '검증 가능',
        message: 'downstream compatible',
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });
    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    const metricsTable = await screen.findByRole('table', {
      name: '핵심 지표 비교',
    });
    expect(
      within(metricsTable).getByText('출력 품질 점수'),
    ).toBeInTheDocument();
    expect(within(metricsTable).getAllByText('평가 불가')).toHaveLength(3);
    expect(
      within(metricsTable).getByTitle(
        /품질 평가 응답을 해석하지 못했습니다.*품질 평가 비용 \$0\.00008/,
      ),
    ).toBeInTheDocument();
  });

  it('후보 품질 점수가 높아도 confidence가 낮으면 경고하되 적용은 차단하지 않는다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-low-confidence',
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
        usage: { total_tokens: 128, total_cost: 0.00042, latency_ms: 940 },
        latency_ms: 940,
      },
      diff: {},
      quality_evaluation: {
        status: 'completed',
        baseline: { score: 82 },
        candidate: { score: 86 },
        delta: 4,
        dimensions: {},
        confidence: 'low',
        safe_summary:
          '후보 출력의 품질 점수가 기준 출력보다 높게 평가되었습니다.',
        judge_cost: 0.00008,
      },
      downstream_compatibility: {
        state: 'compatible',
        label: '검증 가능',
        message: 'downstream compatible',
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });
    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    expect(
      await screen.findByRole('heading', { name: '주의 필요' }),
    ).toBeInTheDocument();
    expect(screen.getByText('4점 상승 · 신뢰도 낮음')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '이 설정으로 노드 적용하기' }),
    ).toBeEnabled();
  });

  it('이전 실험 이력은 접고 펼칠 수 있으며 선택한 이력 summary를 Inspector에서 보여준다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    expect(screen.getByRole('button', { name: '펼치기' })).toBeInTheDocument();
    expect(
      screen.queryByRole('columnheader', { name: '실행 시각' }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText(/선택: 방금 실행/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '펼치기' }));
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /mini 비용 절감 선택/ }),
      ).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole('button', { name: /mini 비용 절감 선택/ }),
    );

    expect(screen.getByText('선택한 이전 실험')).toBeInTheDocument();
    expect(screen.getAllByText('mini 비용 절감').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gpt-4.1-mini').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/이전 실험 이력 API가 제공하는 summary 기준입니다/),
    ).toBeInTheDocument();
    const metricsTable = screen.getByRole('table', {
      name: '핵심 지표 비교',
    });
    expect(within(metricsTable).getByText('74점')).toBeInTheDocument();
    expect(within(metricsTable).getByText('79점')).toBeInTheDocument();
    expect(
      within(metricsTable).getByText('5점 상승 · 신뢰도 높음'),
    ).toBeInTheDocument();
  });

  it('이전 실험 이력 선택 시 schema와 downstream 판정을 선택 이력 기준으로 표시한다', async () => {
    workflowApiMock.listCostOptimizerExperiments.mockResolvedValueOnce({
      items: [
        {
          experiment_id: 'experiment-risky',
          workflow_id: 'workflow-1',
          node_id: 'llm-1',
          created_at: '2026-07-05T03:00:00Z',
          candidates: [
            {
              candidate_id: 'candidate-risky-1',
              name: 'schema 누락 후보',
              status: 'schema_failed',
              model_id: 'gpt-4.1-mini',
              total_cost: 0.00031,
              total_tokens: 96,
              latency_ms: 700,
              schema_status: 'failed',
              downstream_state: 'incompatible',
              is_applied: false,
              created_at: '2026-07-05T03:00:00Z',
            },
          ],
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

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: '펼치기' }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /schema 누락 후보 선택/ }),
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole('button', { name: /schema 누락 후보 선택/ }),
    );

    expect(screen.getByText('적용 비추천')).toBeInTheDocument();
    expect(screen.getByText('Schema: 실패')).toBeInTheDocument();
    expect(screen.getByText('Downstream: 검증 불가')).toBeInTheDocument();
    expect(screen.getByText('Schema 판정')).toBeInTheDocument();
    expect(screen.getByText('후속 노드 판정')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '후속 노드 영향' }));

    expect(screen.getAllByText('검증 불가').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/저장된 이전 실험 summary 기준의 downstream 판정입니다/),
    ).toBeInTheDocument();
  });

  it('결과 분석 mode의 Inspector는 설정 차이, 근거/Trace, 후속 노드 영향 탭을 제공한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    expect(
      screen.getByRole('button', { name: '설정 차이' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '근거/Trace' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '후속 노드 영향' }),
    ).toBeInTheDocument();
  });

  it('설정 차이 탭은 모델, prompt, parameter, 출력 형식 차이를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: '설정 차이' }));

    expect(screen.getByText('모델 차이')).toBeInTheDocument();
    expect(screen.getByText('prompt 차이')).toBeInTheDocument();
    expect(screen.getByText('parameter 차이')).toBeInTheDocument();
    expect(screen.getByText('출력 형식 차이')).toBeInTheDocument();
  });

  it('근거/Trace 탭은 schema 검증 결과와 A/B retrieval summary를 구분해 표시한다', async () => {
    workflowApiMock.compareCostOptimizerCandidate.mockResolvedValueOnce({
      comparison_id: 'comparison-1',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      baseline: {
        baseline_id: 'baseline-1',
        usage: { total_tokens: 249, total_cost: 0.0012, latency_ms: 1600 },
        trace: {
          rag_summary: {
            knowledge_bases: ['HR Policy'],
            retrieved_chunks: 2,
          },
        },
      },
      candidate: {
        label: 'B',
        status: 'schema_failed',
        output: { text: '{"answer":"candidate output"}' },
        usage: {
          total_tokens: 128,
          total_cost: 0.00042,
          latency_ms: 940,
          status: 'schema_failed',
        },
        latency_ms: 940,
        schema_validation: {
          status: 'schema_failed',
          errors: ['필수 필드 누락: approvalRequired'],
        },
        trace: {
          rag_summary: {
            knowledge_bases: ['Billing Guide'],
            retrieved_chunks: 3,
          },
        },
        error_message: null,
      },
      diff: {},
      downstream_compatibility: {
        state: 'warning',
        label: '주의 필요',
      },
    });
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: '근거/Trace' }));

    expect(screen.getByText('Schema 검증')).toBeInTheDocument();
    expect(screen.getAllByText('실패').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/필수 필드 누락: approvalRequired/),
    ).toBeInTheDocument();
    expect(screen.getByText('A retrieval summary')).toBeInTheDocument();
    expect(screen.getByText('B retrieval summary')).toBeInTheDocument();
    expect(screen.getByText(/HR Policy/)).toBeInTheDocument();
    expect(screen.getByText(/Billing Guide/)).toBeInTheDocument();
    expect(screen.getByText('A usage trace')).toBeInTheDocument();
    expect(screen.getByText('B 후보 실행 정보')).toBeInTheDocument();
  });

  it('후속 노드 영향 탭은 downstream 상태와 side-effect 제외 안내를 표시한다', async () => {
    const CostOptimizerPlaygroundPage = await loadPlaygroundPage();

    render(<CostOptimizerPlaygroundPage />);

    await waitFor(() => {
      expect(workflowApiMock.getDraftWorkflow).toHaveBeenCalledWith(
        'workflow-1',
      );
    });

    fireEvent.click(
      screen.getByRole('button', { name: '테스트 baseline 선택' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'B 후보 실행' }));

    await waitFor(() => {
      expect(workflowApiMock.compareCostOptimizerCandidate).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: '후속 노드 영향' }));

    expect(screen.getAllByText('Downstream 상태').length).toBeGreaterThan(0);
    expect(screen.getAllByText('검증 가능').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/외부 전송이나 쓰기 작업이 있는 downstream 노드는 자동 실행하지 않습니다/),
    ).toBeInTheDocument();
  });
});
