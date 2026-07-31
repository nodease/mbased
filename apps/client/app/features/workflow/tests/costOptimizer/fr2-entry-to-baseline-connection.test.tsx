import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NodeInlinePanel } from '../../components/nodes/NodeInlinePanel';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { AppNode, LLMNodeData } from '../../types/Nodes';

const workflowApiMock = vi.hoisted(() => ({
  getCostOptimizerAvailability: vi.fn(),
  getCostOptimizerParameterRecommendations: vi.fn(),
  getCostOptimizerLatestBaseline: vi.fn(),
  listCostOptimizerBaselines: vi.fn(),
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

const createLlmNode = (): AppNode =>
  ({
    id: 'llm-1',
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
    } satisfies LLMNodeData,
  }) as AppNode;

const setWorkflowPermission = () => {
  useWorkflowStore.setState({
    activeWorkflowId: 'workflow-1',
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
  });
};

describe('FR-002 Cost Optimizer 진입-playground 연결', () => {
  beforeEach(() => {
    resetStore();
    setWorkflowPermission();
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
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('비교 분석 테스트는 최적화 버튼 없이 해당 workflow/node 전용 비교 화면으로 이동한다', async () => {
    render(<NodeInlinePanel node={createLlmNode()} />);

    fireEvent.click(
      await screen.findByRole('button', { name: '비교 분석 테스트' }),
    );

    expect(routerMock.push).toHaveBeenCalledWith(
      '/modules/workflow-1/cost-optimizer/llm-1',
    );
    expect(screen.queryByRole('button', { name: /^최적화$/i })).not.toBeInTheDocument();
  });
});
