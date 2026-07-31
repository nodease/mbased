import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';
import { ParameterInputRenderer } from './ParameterInputRenderer';
import {
  deriveParameterControlHydration,
  parameterControlDisplayValue,
} from './parameterControlHydration';

const branchTask: AgentBuilderParameterTask = {
  task_id: 'task-condition-default',
  group_id: 'group-1',
  step_id: 'step-condition',
  node_id: 'condition',
  node_type: 'conditionNode',
  parameter_key: 'condition_branch:default',
  label: '기본 분기 연결',
  input_type: 'select',
  required: true,
  defer_policy: 'forbidden',
  status: 'active',
  task_version: 1,
  stable_order: 0,
  resolution_source: null,
  reason: '조건 분기가 실행될 다음 노드를 확인합니다.',
  input_guidance: '기존 노드를 선택하거나 연결 안 함을 명시하세요.',
  node_label: '조건',
  node_purpose: '조건 결과에 따라 다음 단계를 선택합니다.',
  configuration_state: 'unresolved',
  validation: {
    options: ['answer-node', '__agent_builder_no_connection__'],
    option_labels: {
      'answer-node': '응답 노드',
      __agent_builder_no_connection__: '연결 안 함',
    },
  },
  suggestions: [],
  candidates: [],
};

describe('ParameterInputRenderer condition branch target', () => {
  it('clears a supported node secret immediately after secure storage succeeds', async () => {
    const onSubmit = vi.fn();
    const onSecretSubmit = vi.fn().mockResolvedValue(true);
    const secretTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-slack-token',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'bot_token',
      label: 'Bot Token',
      input_type: 'secret',
      required: false,
      sensitivity: 'secret_forbidden',
    };

    render(
      <ParameterInputRenderer
        task={secretTask}
        hydration={{ state: 'unavailable' }}
        onSubmit={onSubmit}
        onSecretSubmit={onSecretSubmit}
      />,
    );

    const input = screen.getByLabelText('Bot Token');
    expect(input).toHaveAttribute('type', 'password');
    expect(
      screen.queryByRole('button', { name: '노드 설정 열기' }),
    ).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'test-only-secret' } });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));
    expect(onSecretSubmit).toHaveBeenCalledWith(
      secretTask,
      'test-only-secret',
    );
    await waitFor(() => expect(input).toHaveValue(''));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('keeps a supported node secret available for retry when secure storage fails', async () => {
    const onSecretSubmit = vi.fn().mockResolvedValue(false);
    const secretTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-slack-token-retry',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'bot_token',
      label: 'Bot Token',
      input_type: 'secret',
      required: false,
      sensitivity: 'secret_forbidden',
    };

    render(
      <ParameterInputRenderer
        task={secretTask}
        hydration={{ state: 'unavailable' }}
        onSubmit={vi.fn()}
        onSecretSubmit={onSecretSubmit}
      />,
    );

    const input = screen.getByLabelText('Bot Token');
    fireEvent.change(input, { target: { value: 'test-only-secret' } });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    await waitFor(() => expect(onSecretSubmit).toHaveBeenCalledOnce());
    expect(input).toHaveValue('test-only-secret');
  });

  it('detects an existing nested Slack token without hydrating its raw value', () => {
    const secretTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-existing-slack-token',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'bot_token',
      label: 'Bot Token',
      input_type: 'secret',
      required: false,
      sensitivity: 'secret_forbidden',
    };
    const hydration = deriveParameterControlHydration(secretTask, {
      authConfig: { token: '<redacted>' },
    });

    expect(hydration).toEqual({ state: 'unavailable' });
    render(
      <ParameterInputRenderer
        task={secretTask}
        hydration={hydration}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Bot Token')).toHaveValue('');
  });

  it('parses a stored Slack JSON string before reapplying the typed array', () => {
    const onSubmit = vi.fn();
    const blocksTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-existing-slack-blocks',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'blocks',
      label: 'Blocks',
      input_type: 'json',
      required: false,
    };
    const hydration = deriveParameterControlHydration(blocksTask, {
      blocks: '[{"type":"section","text":{"type":"plain_text","text":"hello"}}]',
    });

    expect(hydration).toEqual({
      state: 'available',
      value: [
        {
          type: 'section',
          text: { type: 'plain_text', text: 'hello' },
        },
      ],
    });
    render(
      <ParameterInputRenderer
        task={blocksTask}
        hydration={hydration}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '적용' }));
    expect(onSubmit).toHaveBeenCalledWith([
      {
        type: 'section',
        text: { type: 'plain_text', text: 'hello' },
      },
    ]);
  });

  it('does not hydrate invalid or non-array Slack JSON strings', () => {
    const blocksTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-invalid-slack-blocks',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'blocks',
      label: 'Blocks',
      input_type: 'json',
      required: false,
    };

    expect(
      deriveParameterControlHydration(blocksTask, { blocks: '{invalid' }),
    ).toEqual({ state: 'unavailable' });
    expect(
      deriveParameterControlHydration(blocksTask, { blocks: '{"type":"section"}' }),
    ).toEqual({ state: 'unavailable' });
  });

  it('clears all existing optional selector-list values', () => {
    const onSubmit = vi.fn();
    const onClear = vi.fn();
    const selectorTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-llm-references',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'referenced_variables',
      label: '이전 node 출력 연결',
      input_type: 'variable_selector_list',
      required: false,
      suggestions: [
        {
          suggestion_id: 'selector-start-query',
          kind: 'variable_selector',
          label: '입력 query',
          description: '이전 node output',
          source_node_id: 'start',
          output_key: 'query',
          value_type: 'text',
          value_selector: ['start', 'query'],
          json_path: '$.query',
        },
      ],
    };

    render(
      <ParameterInputRenderer
        task={selectorTask}
        hydration={{ state: 'available', value: ['selector-start-query'] }}
        onSubmit={onSubmit}
        onClear={onClear}
      />,
    );

    fireEvent.click(screen.getByRole('checkbox', { name: /입력 query/ }));
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onClear).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('skips a new empty optional selector list without issuing a clear', () => {
    const onSubmit = vi.fn();
    const onSkip = vi.fn();
    const onClear = vi.fn();
    const selectorTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-new-optional-references',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'referenced_variables',
      label: '이전 node 출력 연결',
      input_type: 'variable_selector_list',
      required: false,
      suggestions: [],
    };

    render(
      <ParameterInputRenderer
        task={selectorTask}
        hydration={{ state: 'empty' }}
        onSubmit={onSubmit}
        onSkip={onSkip}
        onClear={onClear}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onClear).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('treats an empty optional JSON apply as an explicit skip', () => {
    const onSubmit = vi.fn();
    const onSkip = vi.fn();
    const blocksTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-slack-blocks',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'blocks',
      label: 'Blocks',
      input_type: 'json',
      required: false,
    };

    render(
      <ParameterInputRenderer
        task={blocksTask}
        onSubmit={onSubmit}
        onSkip={onSkip}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('clears an existing optional value instead of marking it skipped', () => {
    const onSubmit = vi.fn();
    const onSkip = vi.fn();
    const onClear = vi.fn();
    const messageTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-slack-message',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'message',
      label: 'Message',
      input_type: 'textarea',
      required: false,
      status: 'completed',
    };

    render(
      <ParameterInputRenderer
        task={messageTask}
        hydration={{ state: 'available', value: 'existing message' }}
        onSubmit={onSubmit}
        onSkip={onSkip}
        onClear={onClear}
      />,
    );
    fireEvent.change(screen.getByLabelText('Message'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button'));

    expect(onClear).toHaveBeenCalledTimes(1);
    expect(onSkip).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('keeps an invalid LLM JSON schema and accepts only JSON objects', () => {
    const onSubmit = vi.fn();
    const schemaTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-llm-output-schema',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'output_json_schema',
      label: 'JSON Schema',
      input_type: 'json',
      required: false,
    };

    render(<ParameterInputRenderer task={schemaTask} onSubmit={onSubmit} />);
    const input = screen.getByLabelText('JSON Schema');
    fireEvent.change(input, { target: { value: '["not-an-object"]' } });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'JSON 객체를 입력하세요.',
    );
    expect(input).toHaveValue('["not-an-object"]');

    fireEvent.change(input, {
      target: { value: '{"type":"object"}' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onSubmit).toHaveBeenCalledWith({ type: 'object' });
  });

  it('shows safe option labels while submitting the canonical target id', () => {
    const onSubmit = vi.fn();
    render(<ParameterInputRenderer task={branchTask} onSubmit={onSubmit} />);

    expect(screen.getByRole('option', { name: '응답 노드' })).toHaveValue(
      'answer-node',
    );
    expect(screen.getByRole('option', { name: '연결 안 함' })).toHaveValue(
      '__agent_builder_no_connection__',
    );

    fireEvent.change(screen.getByLabelText('기본 분기 연결'), {
      target: { value: 'answer-node' },
    });
    fireEvent.click(screen.getByRole('button'));

    expect(onSubmit).toHaveBeenCalledWith('answer-node');
  });

  it('hydrates an existing target and explicit no-connection from node data', () => {
    const targetHydration = deriveParameterControlHydration(branchTask, {
      _agent_builder_condition_branch_targets: { default: 'answer-node' },
    });
    const emptyHydration = deriveParameterControlHydration(branchTask, {
      _agent_builder_condition_branch_targets: { default: null },
    });

    expect(targetHydration).toEqual({
      state: 'available',
      value: 'answer-node',
    });
    expect(parameterControlDisplayValue(branchTask, targetHydration)).toBe(
      '응답 노드',
    );
    expect(emptyHydration).toEqual({
      state: 'available',
      value: '__agent_builder_no_connection__',
    });
    expect(parameterControlDisplayValue(branchTask, emptyHydration)).toBe(
      '연결 안 함',
    );
  });

  it('hydrates Mail select and false boolean recommendations for confirmation', () => {
    const onProcessingSubmit = vi.fn();
    const processingTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-mail-processing-mode',
      node_id: 'mail',
      node_type: 'mailNode',
      parameter_key: 'processing_mode',
      label: '처리 모드',
      required: false,
      resolution_source: 'catalog_default',
      validation: {
        options: ['search_only', 'durable'],
        option_labels: {
          search_only: '검색만',
          durable: '후속 처리 추적',
        },
      },
    };
    const { unmount } = render(
      <ParameterInputRenderer
        task={processingTask}
        hydration={{ state: 'available', value: 'durable' }}
        onSubmit={onProcessingSubmit}
      />,
    );

    expect(screen.getByLabelText('처리 모드')).toHaveValue('durable');
    fireEvent.click(screen.getByRole('button'));
    expect(onProcessingSubmit).toHaveBeenCalledWith('durable');
    unmount();

    const onBooleanSubmit = vi.fn();
    const unreadTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-mail-unread-only',
      node_id: 'mail',
      node_type: 'mailNode',
      parameter_key: 'unread_only',
      label: '읽지 않은 메일만',
      input_type: 'boolean',
      required: false,
      resolution_source: 'catalog_default',
      validation: {},
    };
    render(
      <ParameterInputRenderer
        task={unreadTask}
        hydration={{ state: 'available', value: false }}
        onSubmit={onBooleanSubmit}
      />,
    );

    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button'));
    expect(onBooleanSubmit).toHaveBeenCalledWith(false);
  });

  it('hydrates and submits multiple upstream selectors as one typed task value', () => {
    const onSubmit = vi.fn();
    const selectorListTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-mail-effects',
      node_id: 'mail-ack',
      node_type: 'mailAcknowledgeNode',
      parameter_key: 'required_effect_ref_selectors',
      label: '필수 처리 결과',
      input_type: 'variable_selector_list',
      suggestions: [
        {
          suggestion_id: 'sel-draft',
          kind: 'variable_selector',
          label: 'Gmail 답장 초안',
          description: '초안 생성 결과',
          source_node_id: 'draft',
          output_key: 'draft_ref',
          value_type: 'text',
          value_selector: ['draft', 'draft_ref'],
          json_path: '$.draft_ref',
        },
        {
          suggestion_id: 'sel-slack',
          kind: 'variable_selector',
          label: 'Slack 전송',
          description: 'Slack 전송 결과',
          source_node_id: 'slack',
          output_key: 'message_ref',
          value_type: 'text',
          value_selector: ['slack', 'message_ref'],
          json_path: '$.message_ref',
        },
      ],
    };
    const hydration = deriveParameterControlHydration(selectorListTask, {
      required_effect_ref_selectors: [
        ['draft', 'draft_ref'],
        ['slack', 'message_ref'],
      ],
    });

    render(
      <ParameterInputRenderer
        task={selectorListTask}
        hydration={hydration}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked();
    expect(screen.getAllByRole('checkbox')[1]).toBeChecked();
    fireEvent.click(screen.getByRole('button'));
    expect(onSubmit).toHaveBeenCalledWith(selectorListTask.suggestions);
  });

  it('hydrates a runtime GitHub PR number string as a number task', () => {
    const githubTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-github-pr-number',
      node_id: 'github',
      node_type: 'githubNode',
      parameter_key: 'pr_number',
      label: 'PR 번호',
      input_type: 'number',
      validation: { min: 1, integer: true },
    };

    expect(
      deriveParameterControlHydration(githubTask, { pr_number: '15' }),
    ).toEqual({ state: 'available', value: 15 });
  });

  it('hydrates a file extraction runtime reference object as a selector task', () => {
    const selectorTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-file-selector',
      node_id: 'file-extraction',
      node_type: 'fileExtractionNode',
      parameter_key: 'referenced_variables',
      label: '입력 파일',
      input_type: 'variable_selector',
      suggestions: [
        {
          suggestion_id: 'sel-file',
          kind: 'variable_selector',
          label: 'Webhook file',
          description: 'Webhook file output',
          source_node_id: 'webhook',
          output_key: 'file',
          value_type: 'unknown',
          value_selector: ['webhook', 'file'],
          json_path: '$.file',
        },
      ],
    };

    expect(
      deriveParameterControlHydration(selectorTask, {
        referenced_variables: [
          { name: 'file', value_selector: ['webhook', 'file'] },
        ],
      }),
    ).toEqual({ state: 'available', value: 'sel-file' });
  });

  it('hydrates model routing nested policy values into number controls', () => {
    const routingTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-routing-refresh',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'model_routing_refresh_every_runs',
      label: 'Routing 갱신 주기',
      input_type: 'number',
      validation: { min: 5, max: 100, integer: true },
    };

    expect(
      deriveParameterControlHydration(routingTask, {
        model_routing_policy: {
          refresh: { refresh_every_runs: 25 },
        },
      }),
    ).toEqual({ state: 'available', value: 25 });
  });

  it('enforces Catalog number bounds before submitting a Routing parameter', () => {
    const onSubmit = vi.fn();
    const routingTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-routing-refresh-bounds',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'model_routing_refresh_every_runs',
      label: 'Routing refresh interval',
      input_type: 'number',
      validation: { min: 5, max: 100, integer: true },
    };

    render(<ParameterInputRenderer task={routingTask} onSubmit={onSubmit} />);

    const input = screen.getByLabelText('Routing refresh interval');
    expect(input).toHaveAttribute('min', '5');
    expect(input).toHaveAttribute('max', '100');
    expect(input).toHaveAttribute('step', '1');

    fireEvent.change(input, { target: { value: '101' } });
    fireEvent.click(screen.getByRole('button'));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      '5 이상 100 이하의 값을 입력하세요.',
    );
  });

  it('hydrates the routing fallback model by its safe candidate reference', () => {
    const fallbackTask: AgentBuilderParameterTask = {
      ...branchTask,
      task_id: 'task-routing-fallback',
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'fallback_model_id',
      label: 'Fallback model',
      input_type: 'resource_ref',
      required: false,
      candidates: [
        {
          candidate_id: 'candidate-fallback',
          kind: 'resource_ref',
          label: 'Fallback model',
          description: 'openai',
          reference_value: 'provider-fallback-model',
        },
      ],
    };

    expect(
      deriveParameterControlHydration(fallbackTask, {
        fallback_model_id: 'provider-fallback-model',
      }),
    ).toEqual({ state: 'available', value: 'candidate-fallback' });
  });

  it('hydrates LLM output format and referenced variables from runtime graph shape', () => {
    const formatTask: AgentBuilderParameterTask = {
      ...branchTask,
      node_id: 'llm',
      node_type: 'llmNode',
      parameter_key: 'output_format_type',
      label: '출력 형식',
      input_type: 'select',
      required: false,
      validation: { options: ['text', 'json'] },
    };
    const selectorTask: AgentBuilderParameterTask = {
      ...formatTask,
      task_id: 'task-llm-selector',
      parameter_key: 'referenced_variables',
      label: '이전 node 출력 연결',
      input_type: 'variable_selector_list',
      suggestions: [
        {
          suggestion_id: 'selector-start-query',
          kind: 'variable_selector',
          label: '입력 query',
          description: '이전 node output',
          source_node_id: 'start',
          output_key: 'query',
          value_type: 'text',
          value_selector: ['start', 'query'],
          json_path: '$.query',
        },
      ],
    };

    expect(
      deriveParameterControlHydration(formatTask, {
        output_format: { type: 'json', schema: {} },
      }),
    ).toEqual({ state: 'available', value: 'json' });
    expect(
      deriveParameterControlHydration(selectorTask, {
        referenced_variables: [
          { name: 'query', value_selector: ['start', 'query'] },
        ],
      }),
    ).toEqual({ state: 'available', value: ['selector-start-query'] });
  });
});
