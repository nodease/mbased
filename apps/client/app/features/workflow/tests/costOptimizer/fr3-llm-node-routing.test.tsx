import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NodeInlinePanel } from '../../components/nodes/NodeInlinePanel';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { AppNode, LLMNodeData } from '../../types/Nodes';

const workflowApiMock = vi.hoisted(() => ({
  getCostOptimizerAvailability: vi.fn(),
  getModelRoutingPolicy: vi.fn(),
  patchModelRoutingPolicy: vi.fn(),
  refreshModelRoutingPolicy: vi.fn(),
  getModelRoutingBootstrapPreview: vi.fn(),
  createModelRoutingBootstrap: vi.fn(),
}));

vi.mock('../../components/nodes/llm/components/ModelSelectDropdown', () => ({
  ModelSelectDropdown: ({
    value,
    onChange,
    disabled,
    placeholder,
  }: {
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder || '모델 선택'}
      disabled={disabled}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">모델 없음</option>
      <option value="gpt-4.1">GPT-4.1</option>
      <option value="gpt-4.1-mini">GPT-4.1 mini</option>
    </select>
  ),
}));

vi.mock('../../components/modals/PromptWizardModal', () => ({
  PromptWizardModal: () => null,
}));

vi.mock('../../components/nodes/ui/VariableTokenEditor', () => ({
  VariableTokenEditor: ({
    ariaLabel,
    value,
    onChange,
  }: {
    ariaLabel: string;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <textarea
      aria-label={ariaLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

vi.mock('../../components/nodes/ui/PropertyVisibilityToggle', () => ({
  PropertyVisibilityToggle: () => null,
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: workflowApiMock,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const initialState = useWorkflowStore.getState();

const createLlmNode = (data: Partial<LLMNodeData> = {}): AppNode =>
  ({
    id: 'llm-1',
    type: 'llmNode',
    position: { x: 0, y: 0 },
    data: {
      title: '비용 비교 대상',
      provider: 'openai',
      model_id: 'gpt-4.1',
      fallback_model_id: '',
      task_type: 'generate',
      system_prompt: '너는 고객 응대 담당자다.',
      user_prompt: '고객 문의를 처리해줘.',
      assistant_prompt: '',
      referenced_variables: [],
      parameters: { max_tokens: 800, temperature: 0.2 },
      knowledgeBases: [],
      ...data,
    },
  }) as AppNode;

describe('FR-003 LLM node model routing optimization entry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    workflowApiMock.getModelRoutingPolicy.mockResolvedValue({
      enabled: true,
      status: 'active',
      policy_id: 'policy-persisted',
      policy_version: 'router-policy-v5',
      active_policy: {
        strategy_id: 'judge_bootstrap_incremental_v1',
        default_model_id: 'gpt-4.1-mini',
        fallback_model_id: 'gpt-4.1',
        rules: [],
        judge_provider: 'openai',
        judge_model_id: 'gpt-4.1-mini',
        minimum_local_samples: 24,
      },
      learner: {
        id: 'learner-1',
        status: 'ready',
        mode: 'local_first',
        task_fingerprint: 'fingerprint-1',
        judged_request_count: 57,
        pending_count: 2,
        accepted_count: 57,
        rejected_count: 4,
        active_version: 3,
        recent_evaluation: {
          judge_match_rate: 0.85,
          contract_pass_rate: 0.97,
        },
      },
      pending_policy: null,
      refresh: {
        refresh_every_runs: 20,
        eligible_runs_since_last_refresh: 2,
        next_refresh_after_runs: 18,
        last_refresh_result: 'applied',
        last_refresh_at: null,
      },
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
      last_decision: {
        selected_model_id: 'gpt-4.1-mini',
        fallback_model_id: 'gpt-4.1',
        fallback_used: false,
        decision_source: 'runtime_judge',
        reason_code: 'multi_constraint',
        reason_label: '여러 조건 종합',
        created_at: '2026-07-10T00:00:00+00:00',
      },
    });
    workflowApiMock.patchModelRoutingPolicy.mockResolvedValue({
      enabled: true,
      status: 'collecting',
      policy_id: null,
      policy_version: null,
      active_policy: null,
      learner: null,
      pending_policy: null,
      refresh: {
        refresh_every_runs: 20,
        eligible_runs_since_last_refresh: 0,
        next_refresh_after_runs: 20,
        last_refresh_result: null,
        last_refresh_at: null,
      },
      last_decision: null,
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
    });
    workflowApiMock.refreshModelRoutingPolicy.mockResolvedValue({
      policy_id: 'policy-persisted',
      status: 'refreshing',
      trigger: 'manual_refresh',
      scheduled: true,
    });
    workflowApiMock.getModelRoutingBootstrapPreview.mockResolvedValue({
      task_fingerprint: 'fingerprint-1',
      history_mode: 'judge_first',
      available_history_count: 0,
      excluded_history_count: 0,
      excluded_reason_summary: {},
      bootstrap: null,
    });
    workflowApiMock.createModelRoutingBootstrap.mockResolvedValue({
      id: 'bootstrap-1',
      status: 'ready',
      source: 'judge_first',
      task_fingerprint: 'fingerprint-1',
      task_description: '고객 문의를 JSON으로 분류합니다.',
      default_model_id: 'gpt-4.1',
      fallback_model_id: 'gpt-4.1-mini',
      generation_summary: {
        history_sample_count: 0,
        candidate_model_count: 2,
      },
    });
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => [
        {
          id: 'model-1',
          model_id_for_api_call: 'gpt-4.1',
          name: 'GPT-4.1',
          type: 'chat',
          provider_name: 'openai',
          is_active: true,
        },
        {
          id: 'model-2',
          model_id_for_api_call: 'gpt-4.1-mini',
          name: 'GPT-4.1 mini',
          type: 'chat',
          provider_name: 'openai',
          is_active: true,
        },
      ],
    })) as unknown as typeof fetch;
    const node = createLlmNode();
    useWorkflowStore.setState(
      {
        ...initialState,
        nodes: [node],
        activeWorkflowId: 'workflow-1',
        canonicalDraftMetadata: {
          'workflow-1': {
            workflowId: 'workflow-1',
            graphHash: 'a'.repeat(64),
            updatedAt: '2026-07-14T00:00:00Z',
          },
        },
        workflowAccess: {
          workflow_id: 'workflow-1',
          organization_id: 'org-1',
          auth_state: 'builder',
          can_read: true,
          can_write: true,
          can_execute: true,
          can_deploy: true,
          can_manage: true,
          sources: [],
        },
      },
      true,
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('자동 모델 라우팅이 꺼져 있으면 직접 모델 선택을 보여주고 작업 유형 입력은 숨긴다', async () => {
    const node = useWorkflowStore.getState().nodes[0] as AppNode;

    render(<NodeInlinePanel node={node} />);

    expect(
      await screen.findByRole('checkbox', { name: /자동 모델 라우팅/ }),
    ).not.toBeChecked();
    expect(await screen.findByText('기본 모델')).toBeInTheDocument();
    expect(screen.getByText('대체 모델')).toBeInTheDocument();
    expect(screen.queryByLabelText('작업 유형')).not.toBeInTheDocument();
  });

  it('자동 모델 라우팅이 켜져 있으면 필요한 정책 설정만 보여준다', async () => {
    const node = createLlmNode({ auto_model_routing: true });

    render(<NodeInlinePanel node={node} />);

    expect(
      await screen.findByRole('checkbox', { name: /자동 모델 라우팅/ }),
    ).toBeChecked();
    expect(screen.getByText('자동 라우팅 사용 중')).toBeInTheDocument();
    expect(screen.getByText('기본 모델 (규칙 미일치 시)')).toBeInTheDocument();
    expect(screen.getByText('실행 실패 대체 모델')).toBeInTheDocument();
    expect(screen.getByText('최근 실행 선택')).toBeInTheDocument();
    expect(screen.getByText('여러 조건 종합')).toBeInTheDocument();
    expect(screen.getByText('자동 라우팅 학습')).toBeInTheDocument();
    expect(screen.getByText('57건')).toBeInTheDocument();
    expect(screen.getByText('85%')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.queryByText('Judge-first')).not.toBeInTheDocument();
    expect(screen.queryByText('Judge 선택 학습 중')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '자동 선택 기준 만들기' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('자동 라우팅 작업 설명')).not.toBeInTheDocument();
    expect(screen.queryByText('router-policy-v5')).not.toBeInTheDocument();
    expect(screen.queryByTestId('routing-judge-first-policy')).not.toBeInTheDocument();
    expect(screen.queryByText('입력군 관리')).not.toBeInTheDocument();
    expect(screen.queryByText('월간 모델 검증 한도')).not.toBeInTheDocument();

    fireEvent.change(
      screen.getByRole('combobox', { name: '기본 모델을 선택하세요' }),
      { target: { value: 'gpt-4.1-mini' } },
    );
    await waitFor(() => {
      expect(workflowApiMock.patchModelRoutingPolicy).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
        expect.objectContaining({
          enabled: true,
          default_model_id: 'gpt-4.1-mini',
          fallback_model_id: null,
          refresh_every_runs: 20,
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        }),
      );
    });
    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'b'.repeat(64),
      updatedAt: '2026-07-14T00:00:01Z',
    });
  });

  it('자동 모델 라우팅 토글은 수동 bootstrap 생성을 요구하지 않는다', async () => {
    const node = useWorkflowStore.getState().nodes[0] as AppNode;

    render(<NodeInlinePanel node={node} />);

    fireEvent.click(
      await screen.findByRole('checkbox', { name: /자동 모델 라우팅/ }),
    );

    expect(
      (useWorkflowStore.getState().nodes[0].data as LLMNodeData)
        .auto_model_routing,
    ).toBe(true);
    expect(workflowApiMock.patchModelRoutingPolicy).not.toHaveBeenCalled();
  });

  it('비교 분석 테스트만 상단 액션으로 보여준다', async () => {
    const node = useWorkflowStore.getState().nodes[0] as AppNode;

    render(<NodeInlinePanel node={node} />);

    expect(
      await screen.findByRole('button', { name: '비교 분석 테스트' }),
    ).toHaveAttribute(
      'title',
      '실행 로그를 기준으로 A/B 비교 분석 테스트 화면을 엽니다.',
    );
    expect(screen.queryByRole('button', { name: /^최적화$/ })).not.toBeInTheDocument();
    expect(
      await screen.findByRole('checkbox', { name: /자동 모델 라우팅/ }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('작업 유형')).not.toBeInTheDocument();
  });
});
