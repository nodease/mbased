import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useEffect, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NodeSettingsComparisonPanel } from '../../components/costOptimizer/NodeSettingsComparisonPanel';
import {
  applyCandidatePatchToDraft,
  candidateFromOptions,
  compareRequestCandidateFromDraft,
  downstreamOutputContractChipsFromNodes,
  inputContractChipsFromBaseline,
  llmDataFromCandidate,
  type CandidateDraft,
} from '../../components/costOptimizer/costOptimizerPlaygroundModel';
import type { CostOptimizerBaselineRow } from '../../types/Api';
import type { AppNode } from '../../types/Nodes';

vi.mock(
  '@/app/features/workflow/components/nodes/llm/components/ModelSelectDropdown',
  () => ({
    ModelSelectDropdown: ({
      value,
      onChange,
      disabled,
      placeholder,
      models,
    }: {
      value: string;
      onChange: (value: string) => void;
      disabled?: boolean;
      placeholder?: string;
      models: Array<{ model_id_for_api_call: string; name: string }>;
    }) => (
      <select
        aria-label={placeholder || '모델 선택'}
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">모델 없음</option>
        {models.map((model) => (
          <option
            key={model.model_id_for_api_call}
            value={model.model_id_for_api_call}
          >
            {model.name}
          </option>
        ))}
      </select>
    ),
  }),
);

vi.mock('../../components/modals/PromptWizardModal', () => ({
  PromptWizardModal: () => null,
}));

vi.mock('../../components/nodes/llm/components/LLMParameterSidePanel', () => ({
  LLMParameterSidePanel: () => null,
}));

vi.mock('../../components/nodes/llm/components/LLMReferenceSidePanel', () => ({
  LLMReferenceSidePanel: () => null,
}));

vi.mock(
  '@/app/features/workflow/components/nodes/ui/VariableTokenEditor',
  () => ({
    VariableTokenEditor: ({
      ariaLabel,
      value,
      onChange,
      onDropOutput,
      insertOutputRequest,
      tokenLabels,
    }: {
      ariaLabel?: string;
      value: string;
      onChange: (value: string) => void;
      onDropOutput?: (output: {
        key: string;
        label: string;
        dataType: 'string';
        sourceNodeId: string;
        sourceTitle: string;
      }) => string | void;
      insertOutputRequest?: {
        id: string;
        output: {
          key: string;
          label: string;
          dataType: 'string';
          sourceNodeId: string;
          sourceTitle: string;
        };
      } | null;
      tokenLabels?: Record<string, string>;
    }) => {
      useEffect(() => {
        if (!insertOutputRequest) return;
        const name =
          onDropOutput?.(insertOutputRequest.output) ||
          insertOutputRequest.output.key;
        onChange(`${value}{{${name}}}`);
      }, [insertOutputRequest, onChange, onDropOutput, value]);

      return (
        <div>
          <textarea
            aria-label={ariaLabel}
            data-testid={`variable-token-editor-${ariaLabel}`}
            data-token-labels={JSON.stringify(tokenLabels || {})}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
          {onDropOutput ? (
            <button
              type="button"
              onClick={() => {
                const name = onDropOutput({
                  key: 'message',
                  label: 'message',
                  dataType: 'string',
                  sourceNodeId: 'start-1',
                  sourceTitle: '고객 티켓 수신',
                });
                onChange(`${value}{{${name || 'message'}}}`);
              }}
            >
              {ariaLabel}에 변수 삽입
            </button>
          ) : null}
        </div>
      );
    },
  }),
);

const baseDraft: CandidateDraft = {
  model_id: '',
  fallback_model_id: '',
  auto_model_routing: false,
  model_routing_policy: undefined,
  task_type: 'generate',
  system_prompt: '시스템 프롬프트',
  user_prompt: '사용자 프롬프트',
  assistant_prompt: '',
  referenced_variables: [],
  max_tokens: 1024,
  temperature: 0.3,
  top_p: 1,
  presence_penalty: 0,
  frequency_penalty: 0,
  stop: [],
  output_format: 'text',
  json_schema_fields: [],
  knowledgeBases: [],
  topK: 3,
  scoreThreshold: 0.5,
  dedupeRetrievedContext: false,
  retrievedContextMaxChars: null,
  retrievedContextCompression: 'off',
  answerGroundingCheck: 'off',
};

describe('FR-003 Cost Optimizer candidate editor', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('B 후보 모델과 대체 모델은 원본 LLM 노드와 같은 선택 UI로 편집한다', async () => {
    const onChange = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [
          {
            id: 'model-1',
            model_id_for_api_call: 'gpt-4.1-mini',
            name: 'GPT-4.1 mini',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: true,
          },
          {
            id: 'model-2',
            model_id_for_api_call: 'gpt-4.1',
            name: 'GPT-4.1',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: true,
          },
        ],
      }),
    );

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={onChange}
      />,
    );

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/v1/llm/my-models', {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        method: 'GET',
      }),
    );

    fireEvent.change(screen.getByLabelText('모델을 선택하세요'), {
      target: { value: 'gpt-4.1-mini' },
    });

    expect(onChange).toHaveBeenCalledWith('model_id', 'gpt-4.1-mini');
    expect(screen.getByLabelText('먼저 모델을 선택하세요')).toBeDisabled();
  });

  it('B 후보 자동 라우팅을 켜면 모델 선택 UI를 숨기고 candidate 요청에 라우팅 정책을 포함한다', () => {
    const onChange = vi.fn();
    const routingDraft: CandidateDraft = {
      ...baseDraft,
      auto_model_routing: true,
      model_id: '',
      fallback_model_id: '',
      model_routing_policy: {
        status: 'active',
        policy_version: 'policy-v1',
        active_policy: {
          default_model_id: 'gpt-5-mini',
          fallback_model_id: 'gpt-4.1',
        },
      },
    };

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={routingDraft}
        onChange={onChange}
      />,
    );

    expect(
      screen.getByRole('checkbox', { name: /자동 모델 라우팅/i }),
    ).toBeChecked();
    expect(screen.getByText('자동 라우팅 사용 중')).toBeInTheDocument();
    expect(screen.getByText('gpt-5-mini')).toBeInTheDocument();
    expect(screen.getByText('gpt-4.1')).toBeInTheDocument();
    expect(
      screen.queryByLabelText('모델을 선택하세요'),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: /자동 모델 라우팅/i }));

    expect(onChange).toHaveBeenCalledWith('auto_model_routing', false);

    const request = compareRequestCandidateFromDraft(routingDraft);
    expect(request).toEqual(
      expect.objectContaining({
        model_id: '',
        fallback_model_id: null,
        auto_model_routing: true,
        model_routing_policy: routingDraft.model_routing_policy,
      }),
    );
  });

  it('모델 후보 목록에서는 비활성 모델, 날짜 버전, workflow LLM 외 용도 모델을 제외한다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [
          {
            id: 'model-1',
            model_id_for_api_call: 'gpt-4.1-mini',
            name: 'GPT-4.1 mini',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: true,
          },
          {
            id: 'model-2',
            model_id_for_api_call: 'o3-pro',
            name: 'o3-pro',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: true,
          },
          {
            id: 'model-3',
            model_id_for_api_call: 'text-embedding-3-small',
            name: 'Text Embedding 3 Small',
            type: 'embedding',
            provider_name: 'OpenAI',
            is_active: true,
          },
          {
            id: 'model-4',
            model_id_for_api_call: 'legacy-chat',
            name: 'Legacy Chat',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: false,
          },
          {
            id: 'model-5',
            model_id_for_api_call: 'gpt-5.2-pro-2025-12-11',
            name: 'gpt-5.2-pro-2025-12-11',
            type: 'chat',
            provider_name: 'OpenAI',
            is_active: true,
          },
          {
            id: 'model-6',
            model_id_for_api_call: 'gpt-realtime',
            name: 'gpt-realtime',
            type: 'realtime',
            provider_name: 'OpenAI',
            is_active: true,
          },
        ],
      }),
    );

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={vi.fn()}
      />,
    );

    expect(
      await screen.findAllByRole('option', { name: 'GPT-4.1 mini' }),
    ).toHaveLength(2);
    expect(screen.getAllByRole('option', { name: 'o3-pro' })).toHaveLength(2);

    expect(
      screen.queryByRole('option', { name: 'Text Embedding 3 Small' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'Legacy Chat' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'gpt-5.2-pro-2025-12-11' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'gpt-realtime' }),
    ).not.toBeInTheDocument();
  });

  it('B 후보 기본 설정은 기준 입력과 후속 노드 출력 계약을 읽기 전용 칩으로 표시한다', () => {
    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={vi.fn()}
        ioContract={{
          inputs: [
            {
              key: 'message',
              source: 'webhook-ticket',
              value: '정산 파일을 다시 생성하는 방법을 안내해 주세요.',
            },
            {
              key: 'customerTier',
              source: 'webhook-ticket',
              value: 'enterprise',
            },
          ],
          outputs: [
            {
              key: 'approvalRequired',
              source: '처리 결과 추출',
              detail: 'JSON path: 긴급도',
            },
            {
              key: 'mailDraft',
              source: '처리 결과 추출',
              detail: 'JSON path: 답변 초안',
            },
          ],
        }}
      />,
    );

    expect(screen.getByText('기준 입력')).toBeInTheDocument();
    expect(screen.getByText('후속 노드가 사용하는 출력')).toBeInTheDocument();
    expect(screen.getAllByText('message').length).toBeGreaterThan(0);
    expect(screen.getAllByText('customerTier').length).toBeGreaterThan(0);
    expect(screen.getByText('approvalRequired')).toBeInTheDocument();
    expect(screen.getByText('mailDraft')).toBeInTheDocument();
  });

  it('기준 입력과 variable extraction downstream 출력 계약을 데이터에서 계산한다', () => {
    const baseline = {
      input: {
        'webhook-ticket': {
          message: '정산 파일을 다시 생성하는 방법을 안내해 주세요.',
          customerTier: 'enterprise',
        },
      },
      input_preview: '',
      output_preview: '',
    } as CostOptimizerBaselineRow;
    const nodes = [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '티켓 처리 판단' },
      },
      {
        id: 'extract-ticket',
        type: 'variableExtractionNode',
        position: { x: 0, y: 0 },
        data: {
          title: '처리 결과 추출',
          source_selector: ['llm-triage', 'text'],
          mappings: [
            { name: 'approvalRequired', json_path: '긴급도' },
            { name: 'mailDraft', json_path: '답변 초안' },
          ],
        },
      },
    ] as unknown as AppNode[];

    expect(
      inputContractChipsFromBaseline(baseline).map((chip) => chip.key),
    ).toEqual(['message', 'customerTier']);
    expect(
      downstreamOutputContractChipsFromNodes(nodes, 'llm-triage').map(
        (chip) => chip.key,
      ),
    ).toEqual(['approvalRequired', 'mailDraft']);
  });

  it('JSON 출력 형식에서는 flat key-type schema 행을 추가하고 required 여부를 편집한다', () => {
    const onChange = vi.fn();
    const jsonDraft = {
      ...baseDraft,
      output_format: 'json' as const,
      json_schema_fields: [],
    };

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={jsonDraft}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '스키마 필드 추가' }));

    expect(onChange).toHaveBeenCalledWith('json_schema_fields', [
      { key: '', type: 'string', required: false },
    ]);
  });

  it('JSON schema 필드명을 입력해도 후보 편집 행 포커스가 유지된다', () => {
    const CandidateSchemaEditor = () => {
      const [draft, setDraft] = useState<CandidateDraft>({
        ...baseDraft,
        output_format: 'json',
        json_schema_fields: [{ key: '', type: 'string', required: false }],
      });

      return (
        <NodeSettingsComparisonPanel
          title="B Candidate"
          nodeId="llm-1"
          tab="basic"
          onTabChange={vi.fn()}
          draft={draft}
          onChange={(field, value) =>
            setDraft((current) => ({ ...current, [field]: value }))
          }
        />
      );
    };

    render(<CandidateSchemaEditor />);

    const fieldInput = screen.getByPlaceholderText('예: summary');
    fieldInput.focus();
    fireEvent.change(fieldInput, { target: { value: '긴' } });

    expect(document.activeElement).toBe(screen.getByDisplayValue('긴'));

    fireEvent.change(screen.getByDisplayValue('긴'), {
      target: { value: '긴급도' },
    });

    expect(document.activeElement).toBe(screen.getByDisplayValue('긴급도'));
  });

  it('text 출력 형식에서는 JSON schema 편집 UI를 표시하지 않는다', () => {
    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={{
          ...baseDraft,
          output_format: 'text',
          json_schema_fields: [
            { key: 'answer', type: 'string', required: true },
          ],
        }}
        onChange={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: '스키마 필드 추가' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('JSON schema')).not.toBeInTheDocument();
  });

  it('flat schema 행은 compare request의 JSON schema로 변환된다', () => {
    const request = compareRequestCandidateFromDraft({
      ...baseDraft,
      model_id: 'gpt-4.1-mini',
      output_format: 'json',
      json_schema_fields: [
        { key: 'answer', type: 'string', required: true },
        { key: 'confidence', type: 'number', required: false },
      ],
    });

    expect(request.output_format).toEqual({
      type: 'json',
      schema: {
        type: 'object',
        properties: {
          answer: { type: 'string' },
          confidence: { type: 'number' },
        },
        required: ['answer'],
      },
    });
  });

  it('고급 파라미터는 compare request의 candidate parameters로 변환된다', () => {
    const request = compareRequestCandidateFromDraft({
      ...baseDraft,
      model_id: 'gpt-4.1-mini',
      max_tokens: 512,
      temperature: 0.4,
      top_p: 0.8,
      presence_penalty: 0.2,
      frequency_penalty: -0.1,
      stop: ['END', 'STOP'],
    });

    expect(request.parameters).toEqual({
      max_tokens: 512,
      temperature: 0.4,
      top_p: 0.8,
      presence_penalty: 0.2,
      frequency_penalty: -0.1,
      stop: ['END', 'STOP'],
    });
  });

  it('null 파라미터 추천 패치는 compare request에서 해당 파라미터를 제거한다', () => {
    const patchedDraft = applyCandidatePatchToDraft(
      {
        ...baseDraft,
        top_p: 0.8,
      },
      { parameters: { top_p: null } },
    );
    const request = compareRequestCandidateFromDraft(patchedDraft);
    const nodeData = llmDataFromCandidate(patchedDraft);

    expect(patchedDraft.removed_parameter_keys).toContain('top_p');
    expect(request.parameters).not.toHaveProperty('top_p');
    expect(nodeData.parameters).not.toHaveProperty('top_p');
  });

  it('redacted 지식 베이스 id는 baseline 복사와 compare request에서 제외한다', () => {
    const draft = candidateFromOptions({
      model_id: 'gpt-4.1',
      parameters: { max_tokens: 800, temperature: 0.2 },
      knowledgeBases: [
        { id: '[REDACTED]-[REDACTED]', name: 'redacted KB' },
        { id: 'kb-1', name: '제품 정책' },
      ],
    });
    const request = compareRequestCandidateFromDraft({
      ...draft,
      knowledgeBases: [
        ...draft.knowledgeBases,
        { id: '[REDACTED]', name: 'redacted KB 2' },
      ],
    });

    expect(draft.knowledgeBases).toEqual([{ id: 'kb-1', name: '제품 정책' }]);
    expect(request.knowledge?.knowledge_base_ids).toEqual(['kb-1']);
  });

  it('task type은 원본 LLM node data 변환에서도 보존된다', () => {
    const nodeData = llmDataFromCandidate({
      ...baseDraft,
      task_type: 'classify',
    });

    expect(nodeData.task_type).toBe('classify');
  });

  it('출력 형식과 JSON schema는 원본 LLM node data 변환에서도 보존된다', () => {
    const nodeData = llmDataFromCandidate({
      ...baseDraft,
      output_format: 'json',
      json_schema_fields: [
        { key: 'answer', type: 'string', required: true },
        { key: 'score', type: 'number', required: false },
      ],
    });

    expect(nodeData.output_format).toEqual({
      type: 'json',
      schema: {
        type: 'object',
        properties: {
          answer: { type: 'string' },
          score: { type: 'number' },
        },
        required: ['answer'],
      },
    });
  });

  it('B 후보 prompt 입력은 기존 variable token editor를 재사용한다', () => {
    const onChange = vi.fn();

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={onChange}
      />,
    );

    fireEvent.change(
      screen.getByTestId('variable-token-editor-사용자 프롬프트'),
      {
        target: { value: '티켓 내용: {{message}}' },
      },
    );

    expect(onChange).toHaveBeenCalledWith(
      'user_prompt',
      '티켓 내용: {{message}}',
    );
  });

  it('prompt 변수 삽입은 candidate referenced_variables를 함께 갱신한다', () => {
    const onChange = vi.fn();

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={onChange}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: '사용자 프롬프트에 변수 삽입' }),
    );

    expect(onChange).toHaveBeenCalledWith('referenced_variables', [
      { name: 'message', value_selector: ['start-1', 'message'] },
    ]);
    expect(onChange).toHaveBeenCalledWith(
      'user_prompt',
      '사용자 프롬프트{{message}}',
    );
  });

  it('입력 변수 칩을 클릭하면 해당 프롬프트에 변수 토큰과 참조 정보를 추가한다', () => {
    const onChange = vi.fn();

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="basic"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={onChange}
        ioContract={{
          inputs: [{ key: 'message', source: 'webhook-ticket' }],
          outputs: [],
        }}
      />,
    );

    expect(
      screen
        .getByTestId('variable-token-editor-사용자 프롬프트')
        .getAttribute('data-token-labels'),
    ).toContain('"message":"message"');

    fireEvent.click(screen.getByRole('button', { name: 'message' }));

    expect(onChange).toHaveBeenCalledWith('referenced_variables', [
      { name: 'message', value_selector: ['webhook-ticket', 'message'] },
    ]);
    expect(onChange).toHaveBeenCalledWith(
      'user_prompt',
      '사용자 프롬프트{{message}}',
    );
  });

  it('compare request는 prompt referenced_variables를 포함한다', () => {
    const request = compareRequestCandidateFromDraft({
      ...baseDraft,
      model_id: 'gpt-4.1-mini',
      user_prompt: '티켓 내용: {{message}}',
      referenced_variables: [
        { name: 'message', value_selector: ['start-1', 'message'] },
      ],
    });

    expect(request.referenced_variables).toEqual([
      { name: 'message', value_selector: ['start-1', 'message'] },
    ]);
  });
});
