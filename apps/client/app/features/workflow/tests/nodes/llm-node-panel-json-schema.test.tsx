import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LLMNodePanel } from '../../components/nodes/llm/components/LLMNodePanel';
import type { LLMNodeData } from '../../types/Nodes';

const updateNodeDataMock = vi.hoisted(() => vi.fn());

vi.mock('@/app/features/workflow/store/useWorkflowStore', () => ({
  useWorkflowStore: () => ({
    updateNodeData: updateNodeDataMock,
    nodes: [],
    edges: [],
    activeWorkflowId: 'workflow-1',
    workflowAccess: { can_write: true },
    hasUnsavedChanges: false,
  }),
}));

vi.mock('../../components/modals/PromptWizardModal', () => ({
  PromptWizardModal: () => null,
}));

vi.mock(
  '../../components/nodes/llm/components/ModelSelectDropdown',
  () => ({
    ModelSelectDropdown: () => <div data-testid="model-select" />,
  }),
);

vi.mock(
  '../../components/nodes/llm/components/LLMParameterSidePanel',
  () => ({
    LLMParameterSidePanel: () => <div data-testid="parameter-panel" />,
  }),
);

vi.mock('../../components/costOptimizer/CostOptimizerEntryAction', () => ({
  CostOptimizerEntryAction: ({ label }: { label: string }) => (
    <button type="button">{label}</button>
  ),
}));

vi.mock(
  '../../components/costOptimizer/OptimizationRecommendationModal',
  () => ({
    OptimizationRecommendationModal: () => null,
  }),
);

vi.mock('../../components/nodes/ui/VariableTokenEditor', () => ({
  VariableTokenEditor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (value: string) => void;
  }) => (
    <textarea
      aria-label="prompt editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

vi.mock('../../components/nodes/ui/PropertyVisibilityToggle', () => ({
  PropertyVisibilityToggle: () => null,
}));

vi.mock('@/app/features/workflow/utils/llmKnowledgeBaseSelection', () => ({
  MAX_CONFIGURED_KNOWLEDGE_REFERENCES: 20,
  fetchEligibleKnowledgeBases: vi.fn(async () => []),
  fetchEligibleKnowledgeCollections: vi.fn(async () => []),
  sanitizeSelectedKnowledgeBases: vi.fn((selected) => selected || []),
  sanitizeSelectedKnowledgeCollections: vi.fn((selected) => selected || []),
  isSameKnowledgeSelection: vi.fn(() => true),
  isSameKnowledgeCollectionSelection: vi.fn(() => true),
}));

const baseData = (): LLMNodeData => ({
  title: 'LLM',
  provider: 'openai',
  model_id: 'gpt-4.1-mini',
  system_prompt: 'sys',
  user_prompt: 'user',
  assistant_prompt: '',
  referenced_variables: [],
  parameters: {},
  output_format: { type: 'text' },
});

const jsonData = (): LLMNodeData => ({
  ...baseData(),
  output_format: {
    type: 'json',
    schema: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
});

describe('LLMNodePanel JSON schema editor', () => {
  beforeEach(() => {
    updateNodeDataMock.mockClear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => [],
      })),
    );
  });

  it('스키마 필드 추가는 빈 필드 행을 화면에 유지하고 저장 schema에는 빈 key를 넣지 않는다', () => {
    render(<LLMNodePanel nodeId="llm-1" data={jsonData()} />);

    fireEvent.click(screen.getByRole('button', { name: '스키마 필드 추가' }));

    expect(screen.getByPlaceholderText('예: summary')).toBeInTheDocument();
    expect(updateNodeDataMock).toHaveBeenLastCalledWith('llm-1', {
      output_format: {
        type: 'json',
        schema: {
          type: 'object',
          properties: {},
          required: [],
        },
      },
    });
  });

  it('스키마 필드명을 입력해도 행이 재마운트되지 않아 포커스가 유지된다', () => {
    render(<LLMNodePanel nodeId="llm-1" data={jsonData()} />);

    fireEvent.click(screen.getByRole('button', { name: '스키마 필드 추가' }));
    const fieldInput = screen.getByPlaceholderText('예: summary');

    fieldInput.focus();
    fireEvent.change(fieldInput, { target: { value: '긴' } });

    expect(document.activeElement).toBe(screen.getByDisplayValue('긴'));

    fireEvent.change(screen.getByDisplayValue('긴'), {
      target: { value: '긴급도' },
    });

    expect(document.activeElement).toBe(screen.getByDisplayValue('긴급도'));
  });
});
