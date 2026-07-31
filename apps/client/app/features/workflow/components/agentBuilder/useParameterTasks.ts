'use client';

import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  agentBuilderApi,
  type AgentBuilderGraphMutation,
  type AgentBuilderParameterGroup,
  type AgentBuilderParameterTask,
} from '../../api/agentBuilderApi';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { applyAndSaveAgentBuilderMutation } from './useAgentBuilderEditor';

const SAFE_PARAMETER_ERROR_CODE = /^[a-z][a-z0-9_]{0,79}$/;

const parameterDecisionErrorCode = (error: unknown): string | null => {
  if (!error || typeof error !== 'object') return null;
  const clientCode = (error as { code?: unknown }).code;
  if (
    typeof clientCode === 'string' &&
    SAFE_PARAMETER_ERROR_CODE.test(clientCode)
  ) {
    return clientCode;
  }
  const data = (error as { response?: { data?: unknown } }).response?.data;
  if (!data || typeof data !== 'object') return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string') {
    return SAFE_PARAMETER_ERROR_CODE.test(detail) ? detail : null;
  }
  if (!detail || typeof detail !== 'object') return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' && SAFE_PARAMETER_ERROR_CODE.test(code)
    ? code
    : null;
};

export const parameterDecisionErrorMessage = (error: unknown): string => {
  switch (parameterDecisionErrorCode(error)) {
    case 'stale_graph':
    case 'stale_workflow_updated_at':
      return 'Workflow가 서버에서 변경되어 설정을 저장하지 못했습니다. 최신 상태를 확인한 뒤 다시 시도해주세요.';
    case 'workflow_context_changed':
      return 'Workflow가 전환되어 이전 Agent Builder 설정을 적용하지 않았습니다.';
    case 'result_graph_hash_mismatch':
      return '설정 결과가 서버 검증 결과와 일치하지 않아 저장하지 않았습니다.';
    case 'invalid_decision':
      return '입력한 값이 이 파라미터의 형식 또는 허용 범위와 맞지 않습니다.';
    case 'task_conflict':
      return '다른 설정 변경이 먼저 반영되었습니다. 최신 설정을 확인한 뒤 다시 시도해주세요.';
    case 'permission_denied':
      return '이 파라미터를 변경할 권한이 없습니다.';
  }
  const status = (error as { response?: { status?: unknown } } | null)?.response
    ?.status;
  return typeof status === 'number'
    ? `파라미터 설정을 저장하지 못했습니다. (HTTP ${status})`
    : '파라미터 설정을 저장하지 못했습니다.';
};

export const toParameterDecisionValue = (
  task: AgentBuilderParameterTask,
  value: unknown,
): Record<string, unknown> => {
  if (task.input_type === 'boolean') return { kind: 'boolean', value };
  if (task.input_type === 'number') return { kind: 'number', value };
  if (task.input_type === 'json') return { kind: 'json', value };
  if (task.input_type === 'secret') return { kind: 'secret', value };
  if (task.input_type === 'variable_selector') {
    const suggestion = value as {
      suggestion_id: string;
      value_selector: string[];
    };
    return {
      kind: 'variable_selector',
      suggestion_id: suggestion.suggestion_id,
      value_selector: suggestion.value_selector,
    };
  }
  if (task.input_type === 'variable_selector_list') {
    const selections = value as Array<{
      suggestion_id: string;
      value_selector: string[];
    }>;
    return {
      kind: 'variable_selector_list',
      selections: selections.map((selection) => ({
        suggestion_id: selection.suggestion_id,
        value_selector: selection.value_selector,
      })),
    };
  }
  if (task.input_type === 'resource_ref') {
    return { kind: 'resource_ref', resource_id: String(value) };
  }
  if (task.input_type === 'credential_ref') {
    return { kind: 'credential_ref', credential_id: String(value) };
  }
  return {
    kind: ['textarea', 'code', 'select'].includes(task.input_type)
      ? task.input_type
      : 'text',
    value: String(value ?? ''),
  };
};

const requireBaseHash = (
  mutation: AgentBuilderGraphMutation,
): AgentBuilderGraphMutation & { base_graph_hash: string } => {
  if (!mutation.base_graph_hash) {
    throw new Error('Agent Builder mutation base hash is missing');
  }
  return mutation as AgentBuilderGraphMutation & { base_graph_hash: string };
};

export const useParameterTasks = ({
  sessionId,
  workflowId,
  getViewport,
  onRequestStatusChange,
}: {
  sessionId: string | null;
  workflowId: string;
  getViewport: () => { x: number; y: number; zoom: number };
  onRequestStatusChange?: (status: string) => void;
}) => {
  const [parameterGroup, setParameterGroupState] =
    useState<AgentBuilderParameterGroup | null>(null);
  const [presentationTaskId, setPresentationTaskId] = useState<string | null>(
    null,
  );
  const [isPresentationReentry, setIsPresentationReentry] = useState(false);
  const [focusHeadingTaskId, setFocusHeadingTaskId] = useState<string | null>(
    null,
  );
  const [isApplying, setIsApplying] = useState(false);
  const parameterGroupIdRef = useRef<string | null>(null);
  const lastManuallyConfiguredTaskIdRef = useRef<string | null>(null);
  const pendingDecisionRef = useRef<{
    fingerprint: string;
    operationId: string;
  } | null>(null);
  const pendingCancellationRef = useRef<{
    fingerprint: string;
    operationId: string;
  } | null>(null);

  const resetParameterTasks = useCallback(() => {
    setIsApplying(false);
    setParameterGroupState(null);
    setPresentationTaskId(null);
    setIsPresentationReentry(false);
    setFocusHeadingTaskId(null);
    parameterGroupIdRef.current = null;
    lastManuallyConfiguredTaskIdRef.current = null;
    pendingDecisionRef.current = null;
    pendingCancellationRef.current = null;
  }, []);

  const syncParameterGroup = useCallback(
    (
      nextParameterGroup: AgentBuilderParameterGroup | null,
      manuallyConfiguredTaskId?: string,
      completionEligible = false,
    ) => {
      if (nextParameterGroup?.group_id !== parameterGroupIdRef.current) {
        parameterGroupIdRef.current = nextParameterGroup?.group_id ?? null;
        lastManuallyConfiguredTaskIdRef.current = null;
      }
      if (manuallyConfiguredTaskId) {
        lastManuallyConfiguredTaskIdRef.current = manuallyConfiguredTaskId;
      }
      if (!nextParameterGroup) {
        lastManuallyConfiguredTaskIdRef.current = null;
      }
      setParameterGroupState(nextParameterGroup);
      setPresentationTaskId(
        nextParameterGroup?.tasks.find((task) =>
          ['active', 'invalid'].includes(task.status),
        )?.task_id ?? null,
      );
      setIsPresentationReentry(false);
      setFocusHeadingTaskId(null);
      if (sessionId) {
        useWorkflowStore
          .getState()
          .setAgentBuilderParameterHistory(
            sessionId,
            nextParameterGroup,
            lastManuallyConfiguredTaskIdRef.current,
            completionEligible,
          );
      }
    },
    [sessionId],
  );

  const setParameterGroup = useCallback(
    (
      nextParameterGroup: AgentBuilderParameterGroup | null,
      completionEligible = false,
    ) => {
      syncParameterGroup(nextParameterGroup, undefined, completionEligible);
    },
    [syncParameterGroup],
  );

  const presentParameterGroup = useCallback(
    (nextParameterGroup: AgentBuilderParameterGroup | null) => {
      setParameterGroupState(nextParameterGroup);
      setPresentationTaskId(
        nextParameterGroup?.tasks.find((task) =>
          ['active', 'invalid'].includes(task.status),
        )?.task_id ?? null,
      );
      setIsPresentationReentry(Boolean(nextParameterGroup));
      setFocusHeadingTaskId(null);
    },
    [],
  );

  const acknowledgePresentationFocus = useCallback((taskId: string) => {
    setFocusHeadingTaskId((current) => (current === taskId ? null : current));
  }, []);

  const decideParameter = useCallback(
    async (input: {
      taskId: string;
      action: 'confirm' | 'set' | 'clear' | 'defer' | 'skip' | 'previous';
      value?: unknown;
    }) => {
      if (!sessionId || !parameterGroup || isApplying) return;
      const task = parameterGroup.tasks.find(
        (item) => item.task_id === input.taskId,
      );
      if (!task) return;
      const decisionValue =
        input.action === 'set'
          ? toParameterDecisionValue(task, input.value)
          : undefined;
      const fingerprint = JSON.stringify({
        taskId: task.task_id,
        taskVersion: task.task_version,
        action: input.action,
        value: decisionValue,
      });
      const operationId =
        pendingDecisionRef.current?.fingerprint === fingerprint
          ? pendingDecisionRef.current.operationId
          : crypto.randomUUID();
      pendingDecisionRef.current = { fingerprint, operationId };
      setIsApplying(true);
      try {
        const decision = await agentBuilderApi.decideParameterTask(
          sessionId,
          task.task_id,
          {
            operationId,
            expectedTaskVersion: task.task_version,
            action: input.action,
            value: decisionValue,
          },
        );
        if (input.action === 'previous') {
          const previousTaskId = decision.next_task_id ?? null;
          if (
            previousTaskId &&
            parameterGroup.tasks.some((item) => item.task_id === previousTaskId)
          ) {
            setPresentationTaskId(previousTaskId);
            setFocusHeadingTaskId(previousTaskId);
          }
          pendingDecisionRef.current = null;
          return;
        }
        if (decision.graph_mutation) {
          const applied = await applyAndSaveAgentBuilderMutation({
            sessionId,
            workflowId,
            viewport: getViewport(),
            mutation: requireBaseHash(decision.graph_mutation),
          });
          syncParameterGroup(
            applied.acknowledgement.parameter_group ?? null,
            input.action === 'set' ? task.task_id : undefined,
            applied.session?.status === 'completed',
          );
          if (applied.session?.status) {
            onRequestStatusChange?.(applied.session.status);
          } else {
            const acknowledgedGroup =
              applied.acknowledgement.parameter_group ?? null;
            const hasInteractiveTask =
              acknowledgedGroup?.status === 'active' &&
              acknowledgedGroup.tasks.some((candidate) =>
                ['active', 'invalid'].includes(candidate.status),
              );
            onRequestStatusChange?.(
              hasInteractiveTask
                ? 'parameter_configuration'
                : 'completion_confirming',
            );
          }
        } else {
          const restored = await agentBuilderApi.getSession(sessionId);
          onRequestStatusChange?.(restored.status);
          syncParameterGroup(
            restored.parameter_group ?? null,
            input.action === 'set' ? task.task_id : undefined,
            restored.status === 'completed',
          );
        }
        pendingDecisionRef.current = null;
      } catch (error) {
        if (input.action === 'previous') {
          toast.error('이전 설정 항목을 확인하지 못했습니다.');
          return;
        }
        try {
          const restored = await agentBuilderApi.getSession(sessionId);
          onRequestStatusChange?.(restored.status);
          const restoredTask = restored.parameter_group?.tasks.find(
            (item) => item.task_id === task.task_id,
          );
          syncParameterGroup(
            restored.parameter_group ?? null,
            input.action === 'set' &&
              restored.parameter_group?.status !== 'pending_ack' &&
              restoredTask?.status === 'completed'
              ? task.task_id
              : undefined,
            restored.status === 'completed',
          );
          if (restored.parameter_group?.status !== 'pending_ack') {
            pendingDecisionRef.current = null;
          }
        } catch {
          // Keep the operation id while the server outcome remains unknown.
        }
        toast.error(parameterDecisionErrorMessage(error));
      } finally {
        setIsApplying(false);
      }
    },
    [
      getViewport,
      isApplying,
      parameterGroup,
      onRequestStatusChange,
      sessionId,
      syncParameterGroup,
      workflowId,
    ],
  );

  const cancelParameterFlow = useCallback(async () => {
    if (!sessionId || !parameterGroup || isApplying) return;
    const activeTask = parameterGroup.tasks.find(
      (task) => task.status === 'active',
    );
    if (!activeTask) return;
    const fingerprint = JSON.stringify({
      groupId: parameterGroup.group_id,
      taskId: activeTask.task_id,
      taskVersion: activeTask.task_version,
    });
    const operationId =
      pendingCancellationRef.current?.fingerprint === fingerprint
        ? pendingCancellationRef.current.operationId
        : crypto.randomUUID();
    pendingCancellationRef.current = { fingerprint, operationId };
    setIsApplying(true);
    try {
      const response = await agentBuilderApi.cancelParameterGroup(
        sessionId,
        parameterGroup.group_id,
        {
          operationId,
          expectedTaskId: activeTask.task_id,
          expectedTaskVersion: activeTask.task_version,
        },
      );
      syncParameterGroup(response.parameter_group);
      pendingCancellationRef.current = null;
    } catch {
      try {
        const restored = await agentBuilderApi.getSession(sessionId);
        onRequestStatusChange?.(restored.status);
        syncParameterGroup(
          restored.parameter_group ?? null,
          undefined,
          restored.status === 'completed',
        );
        const restoredTask = restored.parameter_group?.tasks.find(
          (task) => task.task_id === activeTask.task_id,
        );
        if (
          restored.parameter_group?.status === 'canceled' ||
          restoredTask?.status === 'canceled'
        ) {
          pendingCancellationRef.current = null;
        }
      } catch {
        // Keep the operation id while the cancellation outcome is unknown.
      }
      toast.error('노드 설정 종료에 실패했습니다.');
    } finally {
      setIsApplying(false);
    }
  }, [
    isApplying,
    onRequestStatusChange,
    parameterGroup,
    sessionId,
    syncParameterGroup,
  ]);

  return {
    parameterGroup,
    presentationTaskId,
    isPresentationReentry,
    focusHeadingTaskId,
    setParameterGroup,
    presentParameterGroup,
    acknowledgePresentationFocus,
    isApplying,
    resetParameterTasks,
    decideParameter,
    cancelParameterFlow,
  };
};
