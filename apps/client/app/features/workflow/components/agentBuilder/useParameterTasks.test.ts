import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  agentBuilderApi,
  type AgentBuilderParameterGroup,
  type AgentBuilderParameterTask,
} from '../../api/agentBuilderApi';
import {
  parameterDecisionErrorMessage,
  useParameterTasks,
  toParameterDecisionValue,
} from './useParameterTasks';
import { applyAndSaveAgentBuilderMutation } from './useAgentBuilderEditor';
import { useWorkflowStore } from '../../store/useWorkflowStore';

vi.mock('../../api/agentBuilderApi', () => ({
  agentBuilderApi: {
    decideParameterTask: vi.fn(),
    getSession: vi.fn(),
    cancelParameterGroup: vi.fn(),
  },
}));

vi.mock('./useAgentBuilderEditor', () => ({
  applyAndSaveAgentBuilderMutation: vi.fn(),
}));

const initialStoreState = useWorkflowStore.getState();

const task = (input_type: AgentBuilderParameterTask['input_type']) =>
  ({ input_type }) as AgentBuilderParameterTask;

describe('toParameterDecisionValue', () => {
  it('maps scalar and reference inputs to discriminated payloads', () => {
    expect(toParameterDecisionValue(task('text'), 'value')).toEqual({
      kind: 'text',
      value: 'value',
    });
    expect(toParameterDecisionValue(task('select'), 'option-a')).toEqual({
      kind: 'select',
      value: 'option-a',
    });
    expect(toParameterDecisionValue(task('secret'), 'secret-value')).toEqual({
      kind: 'secret',
      value: 'secret-value',
    });
    expect(
      toParameterDecisionValue(task('resource_ref'), 'resource-id'),
    ).toEqual({
      kind: 'resource_ref',
      resource_id: 'resource-id',
    });
    expect(
      toParameterDecisionValue(task('credential_ref'), 'credential-id'),
    ).toEqual({
      kind: 'credential_ref',
      credential_id: 'credential-id',
    });
  });

  it('maps multiple selector suggestions to one typed selector-list value', () => {
    expect(
      toParameterDecisionValue(task('variable_selector_list'), [
        {
          suggestion_id: 'sel-draft',
          value_selector: ['draft', 'draft_ref'],
        },
        {
          suggestion_id: 'sel-slack',
          value_selector: ['slack', 'message_ref'],
        },
      ]),
    ).toEqual({
      kind: 'variable_selector_list',
      selections: [
        {
          suggestion_id: 'sel-draft',
          value_selector: ['draft', 'draft_ref'],
        },
        {
          suggestion_id: 'sel-slack',
          value_selector: ['slack', 'message_ref'],
        },
      ],
    });
  });
});

describe('parameterDecisionErrorMessage', () => {
  it('maps a client-detected stale graph code to the same safe message', () => {
    expect(
      parameterDecisionErrorMessage(
        Object.assign(new Error('stale_graph'), { code: 'stale_graph' }),
      ),
    ).toBe(
      'Workflow가 서버에서 변경되어 설정을 저장하지 못했습니다. 최신 상태를 확인한 뒤 다시 시도해주세요.',
    );
  });

  it('maps a workflow switch without exposing the abandoned operation', () => {
    expect(
      parameterDecisionErrorMessage(
        Object.assign(new Error('workflow_context_changed'), {
          code: 'workflow_context_changed',
        }),
      ),
    ).toBe(
      'Workflow가 전환되어 이전 Agent Builder 설정을 적용하지 않았습니다.',
    );
  });

  it.each([
    [
      'stale_graph',
      'Workflow가 서버에서 변경되어 설정을 저장하지 못했습니다. 최신 상태를 확인한 뒤 다시 시도해주세요.',
    ],
    [
      'result_graph_hash_mismatch',
      '설정 결과가 서버 검증 결과와 일치하지 않아 저장하지 않았습니다.',
    ],
    [
      'invalid_decision',
      '입력한 값이 이 파라미터의 형식 또는 허용 범위와 맞지 않습니다.',
    ],
  ])('maps safe server code %s without exposing raw detail', (detail, expected) => {
    expect(
      parameterDecisionErrorMessage({
        isAxiosError: true,
        response: { status: 409, data: { detail } },
      }),
    ).toBe(expected);
  });

  it('does not expose arbitrary server detail', () => {
    expect(
      parameterDecisionErrorMessage({
        isAxiosError: true,
        response: {
          status: 500,
          data: { detail: 'raw secret-like diagnostic value' },
        },
      }),
    ).toBe('파라미터 설정을 저장하지 못했습니다. (HTTP 500)');
  });
});

describe('useParameterTasks response-loss recovery', () => {
  const activeTask = {
    task_id: 'task-1',
    group_id: 'group-1',
    input_type: 'text',
    status: 'active',
    task_version: 1,
  } as AgentBuilderParameterTask;
  const activeGroup = {
    group_id: 'group-1',
    status: 'active',
    tasks: [activeTask],
  } as AgentBuilderParameterGroup;

  beforeEach(() => {
    vi.clearAllMocks();
    useWorkflowStore.setState(initialStoreState, true);
  });

  it('acknowledged set이 완료되면 마지막 수동 task를 workflow history 경계에 연결한다', async () => {
    const completedGroup = {
      ...activeGroup,
      status: 'completed',
      tasks: [{ ...activeTask, status: 'completed', task_version: 2 }],
    } as AgentBuilderParameterGroup;
    const setParameterHistory = vi.fn();
    useWorkflowStore.setState({
      setAgentBuilderParameterHistory: setParameterHistory,
    });
    vi.mocked(agentBuilderApi.decideParameterTask).mockResolvedValue({
      task: completedGroup.tasks[0],
      graph_mutation: {
        operation_id: 'operation-set',
        kind: 'parameter_update',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        operations: [],
      },
      next_task_id: null,
      group_status: 'completed',
      awaiting_persistence_ack: true,
    });
    vi.mocked(applyAndSaveAgentBuilderMutation).mockResolvedValue({
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
      acknowledgement: {
        operation_id: 'operation-set',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:01Z',
        parameter_group: completedGroup,
      },
      session: { status: 'completed' } as any,
    });
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-1',
        action: 'set',
        value: 'manual value',
      });
    });

    expect(setParameterHistory).toHaveBeenLastCalledWith(
      'session-1',
      completedGroup,
      'task-1',
      true,
    );
  });

  it('ack success with session read failure enters completion_confirming for reconciliation', async () => {
    const completedGroup = {
      ...activeGroup,
      status: 'completed',
      tasks: [{ ...activeTask, status: 'completed', task_version: 2 }],
    } as AgentBuilderParameterGroup;
    const onRequestStatusChange = vi.fn();
    vi.mocked(agentBuilderApi.decideParameterTask).mockResolvedValue({
      task: completedGroup.tasks[0],
      graph_mutation: {
        operation_id: 'operation-set-confirming',
        kind: 'parameter_update',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        operations: [],
      },
      next_task_id: null,
      group_status: 'completed',
      awaiting_persistence_ack: true,
    });
    vi.mocked(applyAndSaveAgentBuilderMutation).mockResolvedValue({
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
      acknowledgement: {
        operation_id: 'operation-set-confirming',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:01Z',
        parameter_group: completedGroup,
      },
      session: null,
    });
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
        onRequestStatusChange,
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-1',
        action: 'set',
        value: 'manual value',
      });
    });

    expect(onRequestStatusChange).toHaveBeenCalledWith(
      'completion_confirming',
    );
  });

  it('keeps parameter configuration interactive when acknowledgement activates the next task', async () => {
    const nextTask = {
      ...activeTask,
      task_id: 'task-2',
      status: 'active',
      task_version: 1,
    } as AgentBuilderParameterTask;
    const acknowledgedGroup = {
      ...activeGroup,
      tasks: [
        { ...activeTask, status: 'completed', task_version: 2 },
        nextTask,
      ],
    } as AgentBuilderParameterGroup;
    const onRequestStatusChange = vi.fn();
    vi.mocked(agentBuilderApi.decideParameterTask).mockResolvedValue({
      task: acknowledgedGroup.tasks[0],
      graph_mutation: {
        operation_id: 'operation-next-task',
        kind: 'parameter_update',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-18T00:00:00Z',
        operations: [],
      },
      next_task_id: nextTask.task_id,
      group_status: 'active',
      awaiting_persistence_ack: true,
    });
    vi.mocked(applyAndSaveAgentBuilderMutation).mockResolvedValue({
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-18T00:00:01Z',
      acknowledgement: {
        operation_id: 'operation-next-task',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-18T00:00:01Z',
        parameter_group: acknowledgedGroup,
      },
      session: null,
    });
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
        onRequestStatusChange,
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.decideParameter({
        taskId: activeTask.task_id,
        action: 'set',
        value: 'manual value',
      });
    });

    expect(result.current.parameterGroup).toEqual(acknowledgedGroup);
    expect(onRequestStatusChange).toHaveBeenLastCalledWith(
      'parameter_configuration',
    );
  });

  it('세션 recovery도 실패하면 동일 decision 재시도에 operation id를 재사용한다', async () => {
    vi.mocked(agentBuilderApi.decideParameterTask).mockRejectedValue(
      new Error('network unavailable'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-1',
        action: 'set',
        value: 'first',
      });
    });
    await waitFor(() => expect(result.current.isApplying).toBe(false));
    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-1',
        action: 'set',
        value: 'first',
      });
    });

    const firstOperationId = vi.mocked(agentBuilderApi.decideParameterTask).mock
      .calls[0][2].operationId;
    const secondOperationId = vi.mocked(agentBuilderApi.decideParameterTask)
      .mock.calls[1][2].operationId;
    expect(secondOperationId).toBe(firstOperationId);
  });

  it('세션 recovery가 task를 다시 열면 복구된 group을 표시한다', async () => {
    vi.mocked(agentBuilderApi.decideParameterTask).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-1',
      protocol_version: 'direct_edit_v1',
      status: 'operation_payload_unavailable',
      messages: [],
      parameter_group: activeGroup,
    });
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-1',
        action: 'set',
        value: 'first',
      });
    });

    expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
    expect(result.current.parameterGroup).toEqual(activeGroup);
  });

  it('previous 응답의 next_task_id만 presentation cursor로 사용하고 canonical task와 history를 바꾸지 않는다', async () => {
    const previousTask = {
      ...activeTask,
      task_id: 'task-previous',
      status: 'skipped',
      task_version: 7,
      stable_order: 0,
    } as AgentBuilderParameterTask;
    const currentTask = {
      ...activeTask,
      task_id: 'task-current',
      stable_order: 1,
    } as AgentBuilderParameterTask;
    const group = {
      ...activeGroup,
      tasks: [previousTask, currentTask],
    } as AgentBuilderParameterGroup;
    const setParameterHistory = vi.fn();
    useWorkflowStore.setState({
      setAgentBuilderParameterHistory: setParameterHistory,
    });
    vi.mocked(agentBuilderApi.decideParameterTask).mockResolvedValue({
      task: { ...currentTask, status: 'completed', task_version: 99 },
      graph_mutation: null,
      next_task_id: 'task-previous',
      group_status: 'active',
      awaiting_persistence_ack: false,
    });
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      }),
    );
    act(() => result.current.setParameterGroup(group));
    setParameterHistory.mockClear();

    await act(async () => {
      await result.current.decideParameter({
        taskId: 'task-current',
        action: 'previous',
      });
    });

    expect(result.current.presentationTaskId).toBe('task-previous');
    expect(result.current.focusHeadingTaskId).toBe('task-previous');
    expect(result.current.parameterGroup).toEqual(group);
    expect(agentBuilderApi.getSession).not.toHaveBeenCalled();
    expect(applyAndSaveAgentBuilderMutation).not.toHaveBeenCalled();
    expect(setParameterHistory).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().undoStack).toEqual([]);
  });

  it('lost cancellation response retry reuses the same operation id for the same group task version', async () => {
    vi.mocked(agentBuilderApi.cancelParameterGroup).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockRejectedValue(
      new Error('session unavailable'),
    );
    const { result } = renderHook(() =>
      useParameterTasks({
        sessionId: 'session-1',
        workflowId: 'workflow-1',
        getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      }),
    );
    act(() => result.current.setParameterGroup(activeGroup));

    await act(async () => {
      await result.current.cancelParameterFlow();
    });
    await waitFor(() => expect(result.current.isApplying).toBe(false));
    await act(async () => {
      await result.current.cancelParameterFlow();
    });

    const firstOperationId = vi.mocked(agentBuilderApi.cancelParameterGroup).mock
      .calls[0][2].operationId;
    const secondOperationId = vi.mocked(agentBuilderApi.cancelParameterGroup)
      .mock.calls[1][2].operationId;
    expect(secondOperationId).toBe(firstOperationId);
    expect(agentBuilderApi.getSession).toHaveBeenCalledWith('session-1');
  });
});
