import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NodeInlinePanel } from '../../components/nodes/NodeInlinePanel';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { AppNode, LLMNodeData } from '../../types/Nodes';

const workflowApiMock = vi.hoisted(() => ({
  getDraftWorkflow: vi.fn(),
  syncDraftWorkflow: vi.fn(),
  createWorkflow: vi.fn(),
  listWorkflowsByApp: vi.fn(),
  getWorkflowPermission: vi.fn(),
  getCostOptimizerAvailability: vi.fn(),
  getCostOptimizerParameterRecommendations: vi.fn(),
}));

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: workflowApiMock,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
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

const initialState = useWorkflowStore.getState();

const resetStore = () => {
  vi.clearAllMocks();
  useWorkflowStore.setState(initialState, true);
};

const createLlmNode = (
  data: Partial<LLMNodeData> = {},
  id = 'llm-1',
): AppNode =>
  ({
    id,
    type: 'llmNode',
    position: { x: 0, y: 0 },
    data: {
      title: 'LLM 비용 비교 대상',
      provider: 'openai',
      model_id: 'gpt-4.1',
      system_prompt: '너는 고객 응대 담당자다.',
      user_prompt: '고객 문의를 분류해줘.',
      assistant_prompt: '',
      referenced_variables: [],
      parameters: { max_tokens: 800, temperature: 0.2 },
      knowledgeBases: [],
      ...data,
    },
  }) as AppNode;

const createNonLlmNode = (type: AppNode['type'] = 'templateNode'): AppNode =>
  ({
    id: 'non-llm-1',
    type,
    position: { x: 0, y: 0 },
    data: {
      title: 'LLM이 아닌 노드',
    },
  }) as AppNode;

const setWorkflowPermission = (canWrite: boolean) => {
  useWorkflowStore.setState({
    activeWorkflowId: 'workflow-1',
    workflowAccess: {
      workflow_id: 'workflow-1',
      organization_id: 'org-1',
      auth_state: canWrite ? 'builder' : 'operator',
      can_read: true,
      can_write: canWrite,
      can_execute: true,
      can_deploy: canWrite,
      can_manage: canWrite,
      sources: [],
    },
  });
};

const renderPanel = (node: AppNode) => render(<NodeInlinePanel node={node} />);

describe('FR-001 Cost Optimizer 비교 분석 진입 액션', () => {
  beforeEach(() => {
    resetStore();
    setWorkflowPermission(true);
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
    workflowApiMock.getCostOptimizerParameterRecommendations.mockResolvedValue({
      analysis_stage: 'recommendations_available',
      policy_version: 'test',
      recommendations: [],
      warnings: [],
      profile: {},
    });
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    })) as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('LLM 노드 상세 패널에는 비교 분석 테스트 진입 액션이 표시된다', () => {
    renderPanel(createLlmNode());

    expect(
      screen.getByRole('button', { name: /^비교 분석 테스트$/i }),
    ).toBeInTheDocument();
  });

  it('LLM 노드가 아닌 노드 상세 패널에는 비교 분석 테스트 진입 액션이 표시되지 않는다', () => {
    renderPanel(createNonLlmNode());

    expect(
      screen.queryByRole('button', { name: /^비교 분석 테스트$/i }),
    ).not.toBeInTheDocument();
  });

  it('builder 이상 권한이 없으면 비교 분석 테스트 진입 액션은 비활성화된다', () => {
    setWorkflowPermission(false);

    renderPanel(createLlmNode());

    expect(
      screen.getByRole('button', { name: /^비교 분석 테스트$/i }),
    ).toBeDisabled();
  });

  it('비교 분석 테스트 클릭 시 Cost Optimizer 화면으로 이동한다', async () => {
    renderPanel(createLlmNode());
    const button = screen.getByRole('button', {
      name: /^비교 분석 테스트$/i,
    });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(
      button,
    );

    expect(routerMock.push).toHaveBeenCalledWith(
      '/modules/workflow-1/cost-optimizer/llm-1',
    );
  });

  it('저장되지 않은 draft가 있으면 저장 후 비교를 시작해야 한다는 안내를 표시한다', async () => {
    useWorkflowStore.setState({
      hasUnsavedChanges: true,
    });

    renderPanel(createLlmNode());
    fireEvent.click(
      screen.getByRole('button', { name: /^비교 분석 테스트$/i }),
    );

    expect(routerMock.push).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        '현재 노드 설정을 저장한 뒤 비교를 시작할 수 있습니다.',
      ),
    ).toBeInTheDocument();
  });

  it('availability API가 unavailable을 반환하면 모델 라우팅 최적화 진입 액션은 비활성화된다', async () => {
    workflowApiMock.getCostOptimizerAvailability.mockResolvedValue({
      available: false,
      reason: 'cost_optimizer.not_llm_node',
      workflow_id: 'workflow-1',
      node_id: 'llm-1',
      node_type: 'templateNode',
      permission: {
        can_compare: false,
        can_apply: false,
        required_auth_state: 'builder',
      },
    });

    renderPanel(createLlmNode());

    await waitFor(() => {
      expect(workflowApiMock.getCostOptimizerAvailability).toHaveBeenCalledWith(
        'workflow-1',
        'llm-1',
      );
    });
    expect(
      screen.getByRole('button', { name: /^비교 분석 테스트$/i }),
    ).toBeDisabled();
  });
});
