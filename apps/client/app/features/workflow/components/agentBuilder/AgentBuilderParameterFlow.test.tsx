import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { WorkflowResultGroup } from './WorkflowResultGroup';
import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { Node } from '../../types/Workflow';

const initialStoreState = useWorkflowStore.getState();
const scrollIntoView = vi.fn();

const tasks: AgentBuilderParameterTask[] = [
  {
    task_id: 'task-model',
    group_id: 'group-1',
    step_id: 'step_llm',
    node_id: 'llm',
    node_type: 'llmNode',
    parameter_key: 'model_id',
    label: 'LLM model',
    input_type: 'resource_ref',
    required: true,
    defer_policy: 'allow_unresolved',
    status: 'active',
    task_version: 1,
    stable_order: 0,
    resolution_source: null,
    reason: '응답을 생성할 모델이 필요합니다.',
    input_guidance: '사용 가능한 모델을 선택하세요.',
    node_label: '응답 생성',
    node_purpose: '직원 질문에 답변합니다.',
    configuration_state: 'unresolved',
    candidates: [
      {
        candidate_id: 'model-1',
        kind: 'resource_ref',
        label: 'GPT Mini',
        description: 'OpenAI',
      },
    ],
  },
  {
    task_id: 'task-kb',
    group_id: 'group-1',
    step_id: 'step_llm',
    node_id: 'llm',
    node_type: 'llmNode',
    parameter_key: 'knowledgeBases',
    label: 'Knowledge Bases',
    input_type: 'resource_ref',
    required: false,
    defer_policy: 'forbidden',
    status: 'pending',
    task_version: 1,
    stable_order: 1,
    resolution_source: null,
    reason: '참고할 지식을 연결할 수 있습니다.',
    input_guidance: '필요한 Knowledge Base를 선택하세요.',
  },
  {
    task_id: 'task-output',
    group_id: 'group-1',
    step_id: 'step_answer',
    node_id: 'answer',
    node_type: 'answerNode',
    parameter_key: 'outputs',
    label: '응답 출력',
    input_type: 'json',
    required: true,
    defer_policy: 'forbidden',
    status: 'completed',
    task_version: 2,
    stable_order: 2,
    resolution_source: 'upstream_selector',
    reason: '최종 응답 값을 정합니다.',
    input_guidance: '출력 변수를 확인하세요.',
  },
];

describe('Agent Builder parameter cards', () => {
  beforeEach(() => {
    useWorkflowStore.setState(initialStoreState, true);
    scrollIntoView.mockClear();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
  });

  it('node별 카드와 진행률을 표시하고 active node에 focus한다', () => {
    const onFocusNode = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={tasks}
        onFocusNode={onFocusNode}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByText('1 / 3 완료')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '응답 생성' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'answer' })).toBeInTheDocument();
    expect(
      screen.getByText('응답을 생성할 모델이 필요합니다.'),
    ).toBeInTheDocument();
    expect(onFocusNode).toHaveBeenCalledWith('llm');
  });

  it('현재 node card 하나만 확장하고 나머지 node card는 접힌 상태로 표시한다', () => {
    render(
      <WorkflowResultGroup
        tasks={[
          tasks[0],
          { ...tasks[2], status: 'pending', resolution_source: null },
        ]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    const cards = screen.getAllByTestId('agent-builder-parameter-card');
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveAttribute('data-node-id', 'llm');
    expect(cards[0]).toHaveAttribute('aria-expanded', 'true');
    expect(cards[1]).toHaveAttribute('data-node-id', 'answer');
    expect(cards[1]).toHaveAttribute('aria-expanded', 'false');
  });

  it('모든 설정이 완료되면 node card는 요약 상태로 접고 완료 상태를 표시한다', () => {
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[0], status: 'completed', resolution_source: 'catalog_default' },
          tasks[2],
        ]}
        setupStatus="completed"
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Workflow 생성 완료');
    for (const card of screen.getAllByTestId('agent-builder-parameter-card')) {
      expect(card).toHaveAttribute('aria-expanded', 'false');
    }
  });

  it('required task는 skip을 숨기고 Catalog가 허용한 defer만 표시한다', () => {
    render(
      <WorkflowResultGroup
        tasks={tasks}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: '건너뛰기' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '나중에 설정' }),
    ).toBeInTheDocument();
  });

  it.each([
    ['Slack Bot Token', 'slackPostNode', 'bot_token'],
    ['Slack Webhook URL', 'slackPostNode', 'url'],
    ['GitHub API Token', 'githubNode', 'api_token'],
  ] as const)(
    '%s 필수 secret task는 나중에 설정으로 unresolved defer할 수 있다',
    (label, nodeType, parameterKey) => {
      const onDecision = vi.fn();
      render(
        <WorkflowResultGroup
          tasks={[
            {
              ...tasks[0],
              task_id: `task-${parameterKey}`,
              node_id: nodeType === 'githubNode' ? 'github' : 'slack',
              node_type: nodeType,
              parameter_key: parameterKey,
              label,
              input_type: 'secret',
              required: true,
              defer_policy: 'allow_unresolved',
            },
          ]}
          onFocusNode={vi.fn()}
          onDecision={onDecision}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: '나중에 설정' }));

      expect(onDecision).toHaveBeenCalledWith({
        taskId: `task-${parameterKey}`,
        action: 'defer',
      });
      expect(
        screen.queryByRole('button', { name: '건너뛰기' }),
      ).not.toBeInTheDocument();
    },
  );

  it('typed value를 제출한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[{ ...tasks[0], input_type: 'text' }]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.change(screen.getByLabelText('LLM model'), {
      target: { value: 'model-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        taskId: 'task-model',
        action: 'set',
        value: 'model-1',
      }),
    );
  });

  it('optional JSON을 빈 상태로 적용하면 invalid set 대신 skip을 제출한다', () => {
    const onDecision = vi.fn();
    const blocksTask: AgentBuilderParameterTask = {
      ...tasks[0],
      task_id: 'task-slack-blocks',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'blocks',
      label: 'Blocks',
      input_type: 'json',
      required: false,
      defer_policy: 'forbidden',
    };

    render(
      <WorkflowResultGroup
        tasks={[blocksTask]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-slack-blocks',
      action: 'skip',
    });
  });

  it('clears an existing optional graph value instead of only skipping the task', () => {
    const onDecision = vi.fn();
    const messageTask: AgentBuilderParameterTask = {
      ...tasks[0],
      task_id: 'task-slack-message',
      node_id: 'slack',
      node_type: 'slackPostNode',
      parameter_key: 'message',
      label: 'Slack message',
      input_type: 'textarea',
      required: false,
      defer_policy: 'forbidden',
      status: 'active',
      node_label: 'Slack',
    };

    render(
      <WorkflowResultGroup
        tasks={[messageTask]}
        nodes={[{ id: 'slack', data: { message: 'existing' } } as Node]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.change(screen.getByLabelText('Slack message'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-slack-message',
      action: 'clear',
    });
  });

  it('resource reference는 검색 가능한 권한 후보에서만 선택한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={tasks}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.change(screen.getByLabelText('LLM model 검색'), {
      target: { value: 'mini' },
    });
    fireEvent.change(screen.getByLabelText('LLM model'), {
      target: { value: 'model-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        taskId: 'task-model',
        action: 'set',
        value: 'model-1',
      }),
    );
  });

  it('select parameter는 Catalog options를 표시하고 선택값을 제출한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[
          {
            ...tasks[0],
            input_type: 'select',
            validation: { options: ['mini', 'standard'] },
          },
        ]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.change(screen.getByLabelText('LLM model'), {
      target: { value: 'mini' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    expect(onDecision).toHaveBeenCalledWith(
      expect.objectContaining({ value: 'mini' }),
    );
  });

  it('완료한 task를 다시 열어 수정할 수 있다', () => {
    render(
      <WorkflowResultGroup
        tasks={tasks}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'answer 설정 보기' }));
    fireEvent.click(screen.getByRole('button', { name: '응답 출력 수정' }));
    expect(screen.getByText('최종 응답 값을 정합니다.')).toBeInTheDocument();
  });

  it('이전 node card를 펼쳐도 현재 active node card를 열린 상태로 유지한다', () => {
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[0], status: 'completed' },
          { ...tasks[2], status: 'active', resolution_source: null },
        ]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    const cards = screen.getAllByTestId('agent-builder-parameter-card');
    expect(cards[0]).toHaveAttribute('aria-expanded', 'false');
    expect(cards[1]).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(within(cards[0]).getByRole('button'));

    expect(cards[0]).toHaveAttribute('aria-expanded', 'true');
    expect(cards[1]).toHaveAttribute('aria-expanded', 'true');
  });

  it('현재 active task에서 이전 완료 항목으로 이동한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[0], status: 'completed' },
          { ...tasks[1], status: 'active' },
        ]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '이전 항목' }));
    expect(onDecision).toHaveBeenCalledWith(
      expect.objectContaining({ taskId: 'task-kb', action: 'previous' }),
    );
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
  });

  it('Previous Item은 기존 workflow history를 변경하지 않는다', () => {
    const historyEntry = {
      nodes: useWorkflowStore.getState().nodes,
      edges: useWorkflowStore.getState().edges,
    };
    useWorkflowStore.setState({ undoStack: [historyEntry], redoStack: [] });
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[0], status: 'completed' },
          { ...tasks[1], status: 'active' },
        ]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '이전 항목' }));

    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-kb',
      action: 'previous',
    });
    expect(useWorkflowStore.getState().undoStack).toEqual([historyEntry]);
    expect(useWorkflowStore.getState().redoStack).toEqual([]);
  });

  it('previous presentation cursor는 canonical status/version을 유지하며 card heading으로 접근성 focus를 옮긴다', () => {
    const canonicalTasks = [
      { ...tasks[0], status: 'skipped' as const, task_version: 8 },
      { ...tasks[2], status: 'active' as const, task_version: 4 },
    ];
    render(
      <WorkflowResultGroup
        tasks={canonicalTasks}
        presentationTaskId="task-model"
        focusHeadingTaskId="task-model"
        onPresentationHeadingFocused={vi.fn()}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '응답 생성' })).toHaveFocus();
    expect(canonicalTasks[0]).toEqual(
      expect.objectContaining({ status: 'skipped', task_version: 8 }),
    );
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it('previous로 표시한 값 없는 optional task에서도 건너뛰기를 유지한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[1], status: 'skipped' },
          { ...tasks[2], status: 'active', resolution_source: null },
        ]}
        presentationTaskId="task-kb"
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '건너뛰기' }));
    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-kb',
      action: 'skip',
    });
  });

  it('previous로 표시한 기존 optional 값은 값 지우고 건너뛰기로 clear한다', () => {
    const onDecision = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[1], status: 'completed' },
          { ...tasks[2], status: 'active', resolution_source: null },
        ]}
        nodes={[
          {
            id: 'llm',
            type: 'llmNode',
            position: { x: 0, y: 0 },
            data: { title: 'LLM', knowledgeBases: ['kb-1'] },
          } as unknown as Node,
        ]}
        presentationTaskId="task-kb"
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: '값 지우고 건너뛰기' }),
    );
    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-kb',
      action: 'clear',
    });
  });

  it('canonical workflow node data에서 typed text/boolean/json 현재값을 hydrate한다', () => {
    const canonicalNode = {
      id: 'llm',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: {
        title: 'LLM',
        prompt: 'canonical prompt',
        enabled: true,
        config: { temperature: 0.2 },
      },
    } as unknown as Node;
    const promptTask = {
      ...tasks[0],
      parameter_key: 'prompt',
      label: 'Prompt',
      input_type: 'text' as const,
    };
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[promptTask]}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Prompt')).toHaveValue('canonical prompt');

    rerender(
      <WorkflowResultGroup
        tasks={[
          {
            ...promptTask,
            task_id: 'task-enabled',
            parameter_key: 'enabled',
            label: 'Enabled',
            input_type: 'boolean',
          },
        ]}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'Enabled' })).toBeChecked();

    rerender(
      <WorkflowResultGroup
        tasks={[
          {
            ...promptTask,
            task_id: 'task-config',
            parameter_key: 'config',
            label: 'Config',
            input_type: 'json',
          },
        ]}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Config')).toHaveValue(
      JSON.stringify({ temperature: 0.2 }, null, 2),
    );
  });

  it('canonical reference는 현재 권한 후보와 일치할 때만 safe label로 hydrate한다', () => {
    const canonicalNode = {
      id: 'llm',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: { title: 'LLM', model_id: 'model-1' },
    } as unknown as Node;
    render(
      <WorkflowResultGroup
        tasks={tasks}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('LLM model')).toHaveValue('model-1');
    expect(screen.getByText('GPT Mini')).toBeInTheDocument();
  });

  it('권한을 잃은 reference와 raw secret은 값이나 식별자를 노출하지 않고 unavailable 상태로 표시한다', () => {
    const canonicalNode = {
      id: 'llm',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: {
        title: 'LLM',
        model_id: 'deleted-model-id',
        api_key: 'raw-secret-must-not-render',
      },
    } as unknown as Node;
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={tasks}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(
      screen.getByText('사용할 수 없는 기존 설정입니다.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('LLM model')).toHaveValue('');
    expect(screen.queryByText('deleted-model-id')).not.toBeInTheDocument();

    rerender(
      <WorkflowResultGroup
        tasks={[
          {
            ...tasks[0],
            parameter_key: 'api_key',
            label: 'API key',
            input_type: 'text',
            sensitivity: 'secret_forbidden',
          },
        ]}
        nodes={[canonicalNode]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.queryByDisplayValue('raw-secret-must-not-render')).toBeNull();
    expect(screen.queryByText('raw-secret-must-not-render')).toBeNull();
    expect(
      screen.getByText('사용할 수 없는 기존 설정입니다.'),
    ).toBeInTheDocument();
  });

  it('입력 Ctrl+Z는 native 편집에 남고 Agent Builder card의 canvas shortcut 전파는 차단한다', () => {
    const canvasShortcut = vi.fn();
    window.addEventListener('keydown', canvasShortcut);
    try {
      render(
        <WorkflowResultGroup
          tasks={[{ ...tasks[0], input_type: 'text' }]}
          onFocusNode={vi.fn()}
          onDecision={vi.fn()}
        />,
      );
      const input = screen.getByLabelText('LLM model');
      const undoEvent = new KeyboardEvent('keydown', {
        key: 'z',
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      });

      input.dispatchEvent(undoEvent);

      expect(undoEvent.defaultPrevented).toBe(false);
      expect(canvasShortcut).not.toHaveBeenCalled();

      const button = screen.getByRole('button', { name: '나중에 설정' });
      const redoEvent = new KeyboardEvent('keydown', {
        key: 'z',
        ctrlKey: true,
        shiftKey: true,
        bubbles: true,
        cancelable: true,
      });
      button.dispatchEvent(redoEvent);

      expect(redoEvent.defaultPrevented).toBe(false);
      expect(canvasShortcut).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', canvasShortcut);
    }
  });

  it('진행 상태를 screen reader에 정중하게 알린다', () => {
    render(
      <WorkflowResultGroup
        tasks={tasks}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('1 / 3 완료');
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('active task가 바뀌면 이전 task의 입력 초안을 제거한다', () => {
    const firstTask = {
      ...tasks[0],
      input_type: 'text',
    } as AgentBuilderParameterTask;
    const secondTask = {
      ...firstTask,
      task_id: 'task-model-2',
      parameter_key: 'prompt',
      label: 'Prompt',
    };
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[firstTask]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('LLM model'), {
      target: { value: 'previous draft' },
    });

    rerender(
      <WorkflowResultGroup
        tasks={[secondTask]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Prompt')).toHaveValue('');
  });

  it('같은 active task의 candidate 갱신에는 focus를 반복하지 않는다', () => {
    const onFocusNode = vi.fn();
    const firstTask = {
      ...tasks[0],
      input_type: 'text',
    } as AgentBuilderParameterTask;
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[firstTask]}
        onFocusNode={onFocusNode}
        onDecision={vi.fn()}
      />,
    );

    rerender(
      <WorkflowResultGroup
        tasks={[{ ...firstTask, validation: { max_length: 100 } }]}
        onFocusNode={onFocusNode}
        onDecision={vi.fn()}
      />,
    );
    expect(onFocusNode).toHaveBeenCalledTimes(1);

    rerender(
      <WorkflowResultGroup
        tasks={[
          {
            ...firstTask,
            task_id: 'task-model-2',
            parameter_key: 'prompt',
          },
        ]}
        onFocusNode={onFocusNode}
        onDecision={vi.fn()}
      />,
    );
    expect(onFocusNode).toHaveBeenCalledTimes(2);
  });

  it('moves to the previous task across node cards', () => {
    const onDecision = vi.fn();
    const onFocusNode = vi.fn();
    const crossNodeTasks = [
      { ...tasks[0], status: 'completed' as const },
      { ...tasks[2], status: 'active' as const },
    ];
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={crossNodeTasks}
        onFocusNode={onFocusNode}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '이전 항목' }));
    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-output',
      action: 'previous',
    });

    rerender(
      <WorkflowResultGroup
        tasks={[
          { ...tasks[0], status: 'active' },
          { ...tasks[2], status: 'pending' },
        ]}
        onFocusNode={onFocusNode}
        onDecision={onDecision}
      />,
    );
    expect(onFocusNode).toHaveBeenLastCalledWith('llm');
  });

  it('Knowledge와 parameter를 하나의 setup container에서 순차 표시한다', () => {
    const onKnowledgeSubmit = vi.fn();
    render(
      <WorkflowResultGroup
        tasks={tasks}
        knowledgeStep={{
          status: 'active',
          timing: 'before_graph',
          question: '사용할 Knowledge Base를 선택하세요.',
          candidates: [
            {
              selection_id: 'resolution-1:candidate-1',
              candidate_id: 'candidate-1',
              label: '휴가 정책',
            },
          ],
        }}
        setupStatus="awaiting_confirmation"
        onKnowledgeSubmit={onKnowledgeSubmit}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    const setup = screen.getByTestId('workflow-result-group');
    expect(within(setup).getByText('Workflow 설정')).toBeInTheDocument();
    expect(
      within(setup).getByText('Graph 생성 전 Knowledge'),
    ).toBeInTheDocument();
    expect(
      within(setup).getByRole('checkbox', { name: '휴가 정책' }),
    ).toBeInTheDocument();
    expect(
      within(setup).queryByLabelText('LLM model'),
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(setup).getByRole('button', {
        name: 'Knowledge Base 없이 생성',
      }),
    );
    expect(onKnowledgeSubmit).toHaveBeenCalledWith([]);
  });

  it('자동 추천값은 완료로 접고 수정할 때 기존값과 다른 선택지를 보여준다', () => {
    const onDecision = vi.fn();
    const recommendedTask = {
      ...tasks[0],
      resolution_source: 'catalog_default' as const,
      status: 'completed' as const,
      candidates: tasks[0].candidates?.map((candidate) => ({
        ...candidate,
        reference_value: 'gpt-5.5-mini',
      })).concat({
        candidate_id: 'model-2',
        kind: 'resource_ref',
        label: 'GPT Standard',
        description: 'OpenAI',
        reference_value: 'gpt-5.5',
      }),
    };
    const canonicalNode = {
      id: 'llm',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: { title: 'LLM', model_id: 'gpt-5.5-mini' },
    } as unknown as Node;
    render(
      <WorkflowResultGroup
        tasks={[recommendedTask]}
        nodes={[canonicalNode]}
        setupStatus="completed"
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    expect(screen.getByText('Workflow 생성 완료')).toBeInTheDocument();
    expect(screen.queryByLabelText('LLM model')).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '응답 생성 설정 보기' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'LLM model 수정' }));
    expect(screen.getByLabelText('LLM model')).toHaveValue('model-1');
    expect(screen.getByRole('option', { name: 'GPT Standard' })).toHaveValue(
      'model-2',
    );

    fireEvent.change(screen.getByLabelText('LLM model'), {
      target: { value: 'model-2' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));
    expect(onDecision).toHaveBeenCalledWith({
      taskId: 'task-model',
      action: 'set',
      value: 'model-2',
    });
  });

  it('first-Undo reentry에서는 completed 자동 추천값을 다시 confirm하지 않고 set으로 수정한다', () => {
    const onDecision = vi.fn();
    const reenteredTask = {
      ...tasks[0],
      status: 'active' as const,
      resolution_source: 'catalog_default' as const,
      recommendation_fingerprint: 'safe-fingerprint',
    };
    render(
      <WorkflowResultGroup
        tasks={[reenteredTask]}
        nodes={[
          {
            id: 'llm',
            type: 'llmNode',
            position: { x: 0, y: 0 },
            data: { title: 'LLM', model_id: 'model-1' },
          } as Node,
        ]}
        presentationTaskId="task-model"
        isPresentationReentry
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    expect(
      screen.queryByRole('button', { name: '추천값 확인' }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('LLM model')).toHaveValue('model-1');
    fireEvent.click(screen.getByRole('button', { name: '적용' }));
    expect(onDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        taskId: 'task-model',
        action: 'set',
        value: 'model-1',
      }),
    );
  });

  it('planning, saving, failed, completed setup 상태를 서로 다른 접근성 문구로 표시한다', () => {
    const commonProps = {
      tasks: [],
      onFocusNode: vi.fn(),
      onDecision: vi.fn(),
    };
    const { rerender } = render(
      <WorkflowResultGroup {...commonProps} setupStatus="planning" />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Workflow 계획 중');

    rerender(<WorkflowResultGroup {...commonProps} setupStatus="saving" />);
    expect(screen.getByRole('status')).toHaveTextContent('Workflow 저장 중');

    rerender(<WorkflowResultGroup {...commonProps} setupStatus="failed" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Workflow 설정 실패');

    rerender(<WorkflowResultGroup {...commonProps} setupStatus="completed" />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Workflow 생성 완료',
    );
  });

  it('자동 추천 출처를 안전한 사용자 문구로 표시한다', () => {
    const nodeData = { outputs: ['answer.text'] };
    const sourceCases: Array<
      [AgentBuilderParameterTask['resolution_source'], string]
    > = [
      ['user_request', '사용자 요청에서 확인'],
      ['existing_graph', '기존 Workflow 설정 사용'],
      ['upstream_selector', '이전 노드 출력에서 연결'],
      ['catalog_default', '기본값 추천'],
    ];

    sourceCases.forEach(([source, label]) => {
      const { unmount } = render(
        <WorkflowResultGroup
          tasks={[
            {
              ...tasks[2],
              status: 'active',
              resolution_source: source,
            },
          ]}
          nodes={[{ id: 'answer', data: nodeData } as Node]}
          onFocusNode={vi.fn()}
          onDecision={vi.fn()}
        />,
      );

      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    });
  });

  it('완료 task 편집은 명시적 editing 상태와 닫기를 제공하고 실패 rerender 후 입력을 보존한다', () => {
    const onDecision = vi.fn();
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[{ ...tasks[2], input_type: 'text' }]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'answer 설정 보기' }));
    fireEvent.click(screen.getByRole('button', { name: '응답 출력 수정' }));
    expect(screen.getByText('편집 중')).toBeInTheDocument();
    expect(screen.getByText('설정 수정 중')).toBeInTheDocument();
    expect(screen.queryByText('Workflow 생성 완료')).not.toBeInTheDocument();
    expect(screen.queryByText('응답 출력 · 완료')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '편집 닫기' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('응답 출력'), {
      target: { value: 'draft answer' },
    });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));
    rerender(
      <WorkflowResultGroup
        tasks={[{ ...tasks[2], input_type: 'text' }]}
        onFocusNode={vi.fn()}
        onDecision={onDecision}
      />,
    );

    expect(screen.getByLabelText('응답 출력')).toHaveValue('draft answer');
  });

  it('완료 task 편집은 acknowledgement 뒤 완료 summary로 닫힌다', () => {
    const { rerender } = render(
      <WorkflowResultGroup
        tasks={[{ ...tasks[2], input_type: 'text', task_version: 2 }]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'answer 설정 보기' }));
    fireEvent.click(screen.getByRole('button', { name: '응답 출력 수정' }));
    expect(screen.getByText('편집 중')).toBeInTheDocument();
    expect(screen.getByText('설정 수정 중')).toBeInTheDocument();

    rerender(
      <WorkflowResultGroup
        tasks={[{ ...tasks[2], input_type: 'text', task_version: 3 }]}
        onFocusNode={vi.fn()}
        onDecision={vi.fn()}
      />,
    );

    expect(screen.queryByText('편집 중')).not.toBeInTheDocument();
    expect(screen.getByText('Workflow 생성 완료')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'answer 설정 보기' })).toBeInTheDocument();
  });
});
