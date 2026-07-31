import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { WorkflowResultGroup } from './WorkflowResultGroup';
import type { Node } from '../../types/Workflow';
import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';

const nodes: Node[] = [
  {
    id: 'generated-llm',
    type: 'llmNode',
    position: { x: 0, y: 0 },
    data: { title: '요약 LLM', auto_model_routing: false },
  } as Node,
  {
    id: 'routing-enabled-llm',
    type: 'llmNode',
    position: { x: 320, y: 0 },
    data: { title: '라우팅 완료 LLM', auto_model_routing: true },
  } as Node,
  {
    id: 'existing-llm',
    type: 'llmNode',
    position: { x: 640, y: 0 },
    data: { title: '기존 LLM', auto_model_routing: false },
  } as Node,
  {
    id: 'generated-slack',
    type: 'slackPostNode',
    position: { x: 960, y: 0 },
    data: { title: '알림 Slack' },
  } as Node,
  {
    id: 'generated-github',
    type: 'githubNode',
    position: { x: 1280, y: 0 },
    data: { title: '리뷰 GitHub' },
  } as Node,
];

describe('WorkflowResultGroup model routing guidance', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  const routingTask: AgentBuilderParameterTask = {
    task_id: 'routing-task',
    group_id: 'group',
    step_id: 'step-llm',
    node_id: 'generated-llm',
    node_type: 'llmNode',
    parameter_key: 'auto_model_routing',
    label: '자동 모델 라우팅',
    input_type: 'boolean',
    required: false,
    confirmation_required: true,
    defer_policy: 'forbidden',
    status: 'active',
    task_version: 1,
    stable_order: 0,
    resolution_source: 'catalog_default',
    reason: '입력에 따라 사용할 모델을 자동으로 선택합니다.',
    input_guidance: '사용 여부를 확인하세요.',
    node_label: 'LLM',
    node_purpose: 'LLM 호출',
    configuration_state: 'resolved',
  };
  const completedRoutingTasks: AgentBuilderParameterTask[] = [
    'auto_model_routing',
  ].map((parameterKey, stableOrder) => ({
    ...routingTask,
    task_id: `routing-task-${parameterKey}`,
    parameter_key: parameterKey,
    label: parameterKey,
    input_type:
      parameterKey === 'auto_model_routing'
        ? 'boolean'
        : parameterKey.includes('model_id')
          ? 'resource_ref'
          : 'number',
    task_group: 'model_routing',
    status: 'completed',
    stable_order: stableOrder,
  }));

  it('keeps a false routing recommendation active until the user confirms it', () => {
    const onDecision = vi.fn();

    render(
      <WorkflowResultGroup
        tasks={[routingTask]}
        nodes={[nodes[0]]}
        routingNodeIds={['generated-llm']}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('추천값 확인 필요');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(
      screen.queryByText('Workflow 생성 완료'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '건너뛰기' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('설정 진행 중')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith({
      taskId: routingTask.task_id,
      action: 'confirm',
    });
  });

  it('shows optional JSON Schema only when the LLM output format is JSON', () => {
    const schemaTask: AgentBuilderParameterTask = {
      ...routingTask,
      task_id: 'schema-task',
      parameter_key: 'output_json_schema',
      label: 'JSON Schema',
      input_type: 'json',
      confirmation_required: false,
      resolution_source: null,
      status: 'active',
    };
    const formatTask: AgentBuilderParameterTask = {
      ...routingTask,
      task_id: 'format-task',
      parameter_key: 'output_format_type',
      label: '출력 형식',
      input_type: 'select',
      confirmation_required: false,
      resolution_source: 'catalog_default',
      status: 'completed',
      validation: { options: ['text', 'json'] },
    };
    const textNode = {
      ...nodes[0],
      data: { ...nodes[0].data, output_format: { type: 'text' } },
    } as Node;
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[formatTask, schemaTask]}
        nodes={[textNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.queryByText('JSON Schema')).not.toBeInTheDocument();

    rerender(
      <WorkflowResultGroup
        tasks={[formatTask, schemaTask]}
        nodes={[
          {
            ...textNode,
            data: { ...textNode.data, output_format: { type: 'json' } },
          } as Node,
        ]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByText('JSON Schema')).toBeInTheDocument();
  });

  it('shows only Slack parameters that apply to the selected delivery mode', () => {
    const baseSlackTask: AgentBuilderParameterTask = {
      ...routingTask,
      group_id: 'slack-group',
      step_id: 'step-slack',
      node_id: 'generated-slack',
      node_type: 'slackPostNode',
      confirmation_required: false,
      resolution_source: null,
      status: 'pending',
    };
    const tokenTask: AgentBuilderParameterTask = {
      ...baseSlackTask,
      task_id: 'slack-token',
      parameter_key: 'bot_token',
      label: 'Bot Token',
      input_type: 'secret',
      status: 'active',
      validation: {
        visible_when: { parameter_key: 'slackMode', equals: 'api' },
      },
    };
    const urlTask: AgentBuilderParameterTask = {
      ...baseSlackTask,
      task_id: 'slack-url',
      parameter_key: 'url',
      label: 'Webhook URL',
      input_type: 'secret',
      status: 'pending',
      validation: {
        visible_when: { parameter_key: 'slackMode', equals: 'webhook' },
      },
    };
    const apiNode = {
      ...nodes[3],
      data: { ...nodes[3].data, slackMode: 'api' },
    } as Node;
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[tokenTask, urlTask]}
        nodes={[apiNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByText('Bot Token')).toBeInTheDocument();
    expect(screen.queryByText('Webhook URL')).not.toBeInTheDocument();

    rerender(
      <WorkflowResultGroup
        tasks={[
          { ...tokenTask, status: 'skipped' },
          { ...urlTask, status: 'active' },
        ]}
        nodes={[
          {
            ...apiNode,
            data: { ...apiNode.data, slackMode: 'webhook' },
          } as Node,
        ]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.queryByText('Bot Token')).not.toBeInTheDocument();
    expect(screen.getByText('Webhook URL')).toBeInTheDocument();
  });
  it('이번 결과의 라우팅 미설정 LLM만 하나의 안내로 보여주고 Routing control 열기를 요청한다', () => {
    const onFocusNode = vi.fn();
    const onOpenNodeSettings = vi.fn();

    render(
      <WorkflowResultGroup
        tasks={[]}
        nodes={nodes}
        routingNodeIds={['generated-llm', 'routing-enabled-llm']}
        onFocusNode={onFocusNode}
        onOpenNodeSettings={onOpenNodeSettings}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByTestId('agent-builder-routing-guidance')).toBeInTheDocument();
    expect(
      screen.getByText('모델 자동 라우팅은 LLM 노드를 선택한 뒤 Routing 설정할 수 있습니다.'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '요약 LLM Routing 설정으로 이동' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '라우팅 완료 LLM Routing 설정으로 이동' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '기존 LLM Routing 설정으로 이동' }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '요약 LLM Routing 설정으로 이동' }),
    );

    expect(onOpenNodeSettings).toHaveBeenCalledTimes(1);
    expect(onOpenNodeSettings).toHaveBeenCalledWith('generated-llm', 'routing');
  });

  it('이번 결과에 라우팅 미설정 LLM이 없으면 안내를 숨긴다', () => {
    render(
      <WorkflowResultGroup
        tasks={[]}
        nodes={nodes}
        routingNodeIds={['routing-enabled-llm']}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('agent-builder-routing-guidance')).not.toBeInTheDocument();
  });

  it('Slack과 GitHub에 Agent Builder 전용 credential 안내를 만들지 않는다', () => {
    const onOpenNodeSettings = vi.fn();

    render(
      <WorkflowResultGroup
        tasks={[]}
        nodes={nodes}
        onFocusNode={vi.fn()}
        onOpenNodeSettings={onOpenNodeSettings}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.queryByTestId('agent-builder-connection-guidance'),
    ).not.toBeInTheDocument();
    expect(onOpenNodeSettings).not.toHaveBeenCalled();
  });

  it('offers the existing advanced Routing settings only after every graph task is complete', () => {
    const onOpenNodeSettings = vi.fn();
    const routingNode: Node = {
      id: 'generated-llm',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: { title: 'Routing LLM', auto_model_routing: true },
    } as Node;

    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[
          { ...completedRoutingTasks[0], status: 'active' },
          ...completedRoutingTasks.slice(1).map((task) => ({
            ...task,
            status: 'pending' as const,
          })),
        ]}
        nodes={[routingNode]}
        routingNodeIds={['generated-llm']}
        setupStatus="configuring"
        onFocusNode={vi.fn()}
        onOpenNodeSettings={onOpenNodeSettings}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: 'Routing LLM 고급 Routing 설정' }),
    ).not.toBeInTheDocument();

    rerender(
      <WorkflowResultGroup
        tasks={completedRoutingTasks}
        nodes={[routingNode]}
        routingNodeIds={['generated-llm']}
        setupStatus="completed"
        onFocusNode={vi.fn()}
        onOpenNodeSettings={onOpenNodeSettings}
        onDecision={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Routing LLM 고급 Routing 설정' }),
    );

    expect(onOpenNodeSettings).toHaveBeenCalledWith('generated-llm', 'routing');
  });

  it('configure-and-generate routing task suppresses duplicate routing guidance', () => {
    render(
      <WorkflowResultGroup
        tasks={[routingTask]}
        nodes={nodes}
        routingNodeIds={['generated-llm']}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.queryByTestId('agent-builder-routing-guidance'),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText('자동 모델 라우팅').length).toBeGreaterThan(0);
  });

  it('summarizes completed Collection and Knowledge Base selections separately', () => {
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[]}
        nodes={[]}
        knowledgeStep={{
          status: 'completed',
          timing: 'after_graph',
          candidates: [],
          selectedCollectionHandles: ['collection-a'],
          selectedKbHandles: [],
        }}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByText('Collection 1개 선택')).toBeInTheDocument();
    expect(screen.queryByText('Knowledge Base 없이 진행')).toBeNull();

    rerender(
      <WorkflowResultGroup
        tasks={[]}
        nodes={[]}
        knowledgeStep={{
          status: 'completed',
          timing: 'after_graph',
          candidates: [],
          selectedCollectionHandles: ['collection-a'],
          selectedKbHandles: ['kb-a', 'kb-b'],
        }}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.getByText('Collection 1개 · Knowledge Base 2개 선택'),
    ).toBeInTheDocument();
  });
});
