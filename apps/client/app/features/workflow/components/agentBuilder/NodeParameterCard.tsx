'use client';

import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Pencil,
  SkipForward,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';
import { ParameterInputRenderer } from './ParameterInputRenderer';
import {
  deriveParameterControlHydration,
} from './parameterControlHydration';

export type ParameterDecisionInput = {
  taskId: string;
  action: 'confirm' | 'set' | 'clear' | 'defer' | 'skip' | 'previous';
  value?: unknown;
};

const TERMINAL_STATUSES = ['completed', 'skipped', 'deferred'] as const;
const STATUS_LABEL: Record<string, string> = {
  completed: '완료',
  skipped: '건너뜀',
  deferred: '나중에 설정',
  invalid: '입력 오류',
};

const EDITING_LABEL = '\uD3B8\uC9D1 \uC911';
const CLOSE_EDITING_LABEL = '\uD3B8\uC9D1 \uB2EB\uAE30';
const RECOMMENDATION_SOURCE_LABEL: Record<
  Exclude<AgentBuilderParameterTask['resolution_source'], null>,
  string
> = {
  user_request: '\uC0AC\uC6A9\uC790 \uC694\uCCAD\uC5D0\uC11C \uD655\uC778',
  existing_graph: '\uAE30\uC874 Workflow \uC124\uC815 \uC0AC\uC6A9',
  upstream_selector:
    '\uC774\uC804 \uB178\uB4DC \uCD9C\uB825\uC5D0\uC11C \uC5F0\uACB0',
  catalog_default: '\uAE30\uBCF8\uAC12 \uCD94\uCC9C',
};

const matchesRecommendedValue = (
  task: AgentBuilderParameterTask,
  currentValue: unknown,
  submittedValue: unknown,
) => {
  if (task.input_type === 'variable_selector') {
    return (
      typeof currentValue === 'string' &&
      typeof submittedValue === 'object' &&
      submittedValue !== null &&
      (submittedValue as { suggestion_id?: unknown }).suggestion_id ===
        currentValue
    );
  }
  if (task.input_type === 'variable_selector_list') {
    const currentIds = Array.isArray(currentValue) ? currentValue : [];
    const submittedIds = Array.isArray(submittedValue)
      ? submittedValue.flatMap((item) =>
          item &&
          typeof item === 'object' &&
          typeof (item as { suggestion_id?: unknown }).suggestion_id === 'string'
            ? [(item as { suggestion_id: string }).suggestion_id]
            : [],
        )
      : [];
    return (
      currentIds.length === submittedIds.length &&
      currentIds.every((id, index) => id === submittedIds[index])
    );
  }
  return JSON.stringify(currentValue) === JSON.stringify(submittedValue);
};

const parameterTaskIsVisible = (
  task: AgentBuilderParameterTask,
  nodeData?: Record<string, unknown> | null,
) => {
  const visibleWhen = task.validation?.visible_when;
  if (!visibleWhen && task.parameter_key === 'output_json_schema') {
    const outputFormat = nodeData?.output_format;
    return (
      outputFormat !== null &&
      typeof outputFormat === 'object' &&
      (outputFormat as Record<string, unknown>).type === 'json'
    );
  }
  if (
    !visibleWhen ||
    typeof visibleWhen !== 'object' ||
    Array.isArray(visibleWhen)
  ) {
    return true;
  }
  const condition = visibleWhen as Record<string, unknown>;
  const parameterKey = condition.parameter_key;
  if (typeof parameterKey !== 'string' || !parameterKey) return true;

  if (parameterKey === 'output_format_type') {
    const outputFormat = nodeData?.output_format;
    const currentValue =
      outputFormat && typeof outputFormat === 'object'
        ? (outputFormat as Record<string, unknown>).type
        : undefined;
    return currentValue === condition.equals;
  }
  return nodeData?.[parameterKey] === condition.equals;
};

export const NodeParameterCard = ({
  nodeId,
  tasks,
  nodeData,
  presentationTaskId = null,
  isPresentationReentry = false,
  isCurrent = false,
  focusHeading = false,
  onHeadingFocused,
  onEditingChange,
  onSecretSubmit,
  onSecretClear,
  onDecision,
  hasPrevious = false,
  expanded = true,
  onToggle,
  disabled = false,
}: {
  nodeId: string;
  tasks: AgentBuilderParameterTask[];
  nodeData?: Record<string, unknown> | null;
  presentationTaskId?: string | null;
  isPresentationReentry?: boolean;
  isCurrent?: boolean;
  focusHeading?: boolean;
  onHeadingFocused?: (taskId: string) => void;
  onEditingChange?: (taskId: string | null) => void;
  onSecretSubmit?: (
    task: AgentBuilderParameterTask,
    value: string,
  ) => boolean | Promise<boolean>;
  onSecretClear?: (task: AgentBuilderParameterTask) => void;
  onDecision: (decision: ParameterDecisionInput) => void;
  hasPrevious?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  disabled?: boolean;
}) => {
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const editingStartedVersionRef = useRef<number | null>(null);
  const cardRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const visibleTasks = tasks.filter((task) =>
    parameterTaskIsVisible(task, nodeData),
  );
  const activeTask = visibleTasks.find((task) =>
    ['active', 'invalid'].includes(task.status),
  );
  const presentationTask = visibleTasks.find(
    (task) => task.task_id === presentationTaskId,
  );
  const editingTask = visibleTasks.find((task) => task.task_id === editingTaskId);
  const currentTask = editingTask ?? presentationTask ?? activeTask;
  const hydration = useMemo(
    () =>
      currentTask
        ? deriveParameterControlHydration(currentTask, nodeData)
        : { state: 'empty' as const },
    [currentTask, nodeData],
  );
  const isRecommendationReview = Boolean(
    !editingTask &&
      !isPresentationReentry &&
      currentTask?.status === 'active' &&
      currentTask.resolution_source,
  );
  const terminalTasks = visibleTasks.filter((task) =>
    TERMINAL_STATUSES.includes(
      task.status as (typeof TERMINAL_STATUSES)[number],
    ),
  );
  const nodeLabel = visibleTasks[0]?.node_label || nodeId;
  const nodePurpose = visibleTasks[0]?.node_purpose;
  const configurationState = visibleTasks[0]?.configuration_state ?? 'unresolved';
  const hasInProgressTask = visibleTasks.some((task) =>
    ['pending', 'active', 'invalid'].includes(task.status),
  );
  const configurationLabel = hasInProgressTask
    ? '설정 진행 중'
    : configurationState === 'resolved'
      ? '설정 완료'
      : '설정 필요';
  const recommendationSourceLabel = currentTask?.resolution_source
    ? RECOMMENDATION_SOURCE_LABEL[currentTask.resolution_source]
    : null;
  const canSkipOrClearCurrentTask = Boolean(
    !editingTask &&
      currentTask &&
      !currentTask.required &&
      !currentTask.confirmation_required,
  );
  const skipOrClearAction =
    hydration.state !== 'empty'
      ? ('clear' as const)
      : ('skip' as const);
  const skipOrClearLabel =
    skipOrClearAction === 'clear' ? '값 지우고 건너뛰기' : '건너뛰기';
  const closeEditing = useCallback(() => {
    setEditingTaskId(null);
    editingStartedVersionRef.current = null;
    onEditingChange?.(null);
  }, [onEditingChange]);
  const beginEditingTask = (task: AgentBuilderParameterTask) => {
    editingStartedVersionRef.current = task.task_version;
    setEditingTaskId(task.task_id);
    onEditingChange?.(task.task_id);
  };

  useEffect(() => {
    if (!isCurrent) return;
    cardRef.current?.scrollIntoView({ block: 'nearest' });
  }, [isCurrent, currentTask?.task_id]);

  useEffect(() => {
    if (!focusHeading || !currentTask) return;
    headingRef.current?.focus({ preventScroll: true });
    onHeadingFocused?.(currentTask.task_id);
  }, [currentTask, focusHeading, onHeadingFocused]);

  useEffect(() => {
    if (!editingTaskId) return;
    if (!editingTask) {
      closeEditing();
      return;
    }
    if (
      editingStartedVersionRef.current !== null &&
      editingTask.task_version !== editingStartedVersionRef.current &&
      TERMINAL_STATUSES.includes(
        editingTask.status as (typeof TERMINAL_STATUSES)[number],
      )
    ) {
      closeEditing();
    }
  }, [closeEditing, editingTask, editingTaskId]);

  return (
    <section
      ref={cardRef}
      data-testid="agent-builder-parameter-card"
      data-node-id={nodeId}
      aria-expanded={expanded}
      onKeyDown={(event) => event.stopPropagation()}
      className="rounded-md border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3
            ref={headingRef}
            tabIndex={-1}
            className="truncate text-sm font-semibold text-neutral-900 outline-none dark:text-neutral-100"
          >
            {nodeLabel}
          </h3>
          {nodePurpose ? (
            <p className="mt-1 text-xs text-neutral-500">{nodePurpose}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-start gap-2 text-right text-xs text-neutral-500">
          <div>
            <div>
              {terminalTasks.length}/{visibleTasks.length}
            </div>
            <div
              className={
                !hasInProgressTask && configurationState === 'resolved'
                  ? 'text-emerald-600'
                  : 'text-amber-600'
              }
            >
              {configurationLabel}
            </div>
          </div>
          {onToggle ? (
            <button
              type="button"
              onClick={onToggle}
              disabled={disabled}
              aria-label={`${nodeLabel} 설정 ${expanded ? '접기' : '보기'}`}
              className="rounded p-0.5 text-neutral-500 hover:bg-neutral-100 disabled:opacity-60 dark:hover:bg-neutral-800"
            >
              {expanded ? (
                <ChevronDown className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          ) : null}
        </div>
      </div>

      {expanded && terminalTasks.length > 0 ? (
        <ul className="mt-3 divide-y divide-neutral-100 border-y border-neutral-100 dark:divide-neutral-800 dark:border-neutral-800">
          {terminalTasks.map((task) => (
            <li
              key={task.task_id}
              className="flex items-center justify-between gap-2 py-2 text-xs"
            >
              <span className="min-w-0 text-neutral-600 dark:text-neutral-300">
                <span className="block truncate">
                  {task.label}
                  {editingTaskId === task.task_id
                    ? ''
                    : ` · ${STATUS_LABEL[task.status]}`}
                </span>
                {editingTaskId === task.task_id ? (
                  <span className="mt-1 block font-medium text-blue-700 dark:text-blue-300">
                    {EDITING_LABEL}
                  </span>
                ) : null}
              </span>
              <button
                type="button"
                aria-label={`${task.label} 수정`}
                disabled={disabled}
                onClick={() => beginEditingTask(task)}
                className="inline-flex shrink-0 items-center gap-1 text-blue-600 disabled:opacity-60"
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                수정
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {expanded && currentTask ? (
        <div className="mt-3 space-y-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium">
              <Clock3 className="h-4 w-4 text-blue-600" aria-hidden="true" />
              {currentTask.label}
            </div>
            <p className="mt-1 text-sm text-neutral-700 dark:text-neutral-300">
              {currentTask.reason}
            </p>
            <p className="mt-1 text-xs text-neutral-500">
              {currentTask.input_guidance}
            </p>
            {editingTask ? (
              <button
                type="button"
                disabled={disabled}
                onClick={closeEditing}
                className="mt-2 rounded-md border border-neutral-300 px-2 py-1 text-xs disabled:opacity-60 dark:border-neutral-700"
              >
                {CLOSE_EDITING_LABEL}
              </button>
            ) : null}
          </div>
          {isRecommendationReview ? (
            <div className="space-y-2 rounded-md border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950/30">
              <p className="text-xs font-medium text-blue-800 dark:text-blue-200">
                자동 추천 · 확인 필요
              </p>
              {recommendationSourceLabel ? (
                <p className="text-xs text-blue-700 dark:text-blue-300">
                  {recommendationSourceLabel}
                </p>
              ) : null}
            </div>
          ) : null}
          <ParameterInputRenderer
            key={currentTask.task_id}
            task={currentTask}
            hydration={hydration}
            disabled={disabled}
            onSecretSubmit={onSecretSubmit}
            onSubmit={(value) => {
              const action =
                isRecommendationReview &&
                hydration.state === 'available' &&
                matchesRecommendedValue(currentTask, hydration.value, value)
                  ? 'confirm'
                  : 'set';
              onDecision({
                taskId: currentTask.task_id,
                action,
                ...(action === 'set' ? { value } : {}),
              });
            }}
            onSkip={
              !currentTask.required && !currentTask.confirmation_required
                ? () =>
                    onDecision({
                      taskId: currentTask.task_id,
                      action: 'skip',
                    })
                : undefined
            }
            onClear={
              !currentTask.required && !currentTask.confirmation_required
                ? () => {
                    if (currentTask.input_type === 'secret' && onSecretClear) {
                      onSecretClear(currentTask);
                      return;
                    }
                    onDecision({
                      taskId: currentTask.task_id,
                      action: 'clear',
                    });
                  }
                : undefined
            }
          />
          <div className="flex flex-wrap gap-2">
            {editingTask && activeTask ? (
              <button
                type="button"
                disabled={disabled}
                onClick={closeEditing}
                className="rounded-md border border-neutral-300 px-2 py-1.5 text-xs disabled:opacity-60 dark:border-neutral-700"
              >
                현재 항목으로 돌아가기
              </button>
            ) : null}
            {!editingTask && hasPrevious && currentTask ? (
              <button
                type="button"
                aria-label="이전 항목"
                disabled={disabled}
                onClick={() =>
                  onDecision({
                    taskId: currentTask.task_id,
                    action: 'previous',
                  })
                }
                className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1.5 text-xs disabled:opacity-60 dark:border-neutral-700"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                이전 항목
              </button>
            ) : null}
            {canSkipOrClearCurrentTask && currentTask ? (
              <button
                type="button"
                aria-label={skipOrClearLabel}
                disabled={disabled}
                onClick={() => {
                  if (
                    skipOrClearAction === 'clear' &&
                    currentTask.input_type === 'secret' &&
                    onSecretClear
                  ) {
                    onSecretClear(currentTask);
                    return;
                  }
                  onDecision({
                    taskId: currentTask.task_id,
                    action: skipOrClearAction,
                  });
                }}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1.5 text-xs disabled:opacity-60 dark:border-neutral-700"
              >
                <SkipForward className="h-3.5 w-3.5" aria-hidden="true" />
                {skipOrClearLabel}
              </button>
            ) : null}
            {!editingTask &&
            activeTask?.task_id === currentTask.task_id &&
            currentTask.defer_policy === 'allow_unresolved' ? (
              <button
                type="button"
                aria-label="나중에 설정"
                disabled={disabled}
                onClick={() =>
                  onDecision({ taskId: currentTask.task_id, action: 'defer' })
                }
                className="rounded-md border border-neutral-300 px-2 py-1.5 text-xs disabled:opacity-60 dark:border-neutral-700"
              >
                나중에 설정
              </button>
            ) : null}
          </div>
        </div>
      ) : expanded ? (
        <div className="mt-2 flex items-center gap-1 text-xs text-neutral-500">
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
          {visibleTasks.some((task) => task.status === 'pending')
            ? '설정 대기'
            : '설정 항목 확인 완료'}
        </div>
      ) : null}
    </section>
  );
};
