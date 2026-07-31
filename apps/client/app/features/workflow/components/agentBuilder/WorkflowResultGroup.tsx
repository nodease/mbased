'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';

import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';
import type { Node } from '../../types/Workflow';
import {
  KnowledgeSelectionControl,
  type KnowledgeSelectionCandidate,
  type KnowledgeSelectionCollection,
  type KnowledgeSelectionChild,
  type KnowledgeHierarchySubmission,
} from './KnowledgeSelectionControl';
import {
  NodeParameterCard,
  type ParameterDecisionInput,
} from './NodeParameterCard';

export type WorkflowSetupStatus =
  | 'planning'
  | 'recovery_required'
  | 'saving'
  | 'awaiting_confirmation'
  | 'configuring'
  | 'editing'
  | 'confirming'
  | 'failed'
  | 'completed';

export type WorkflowKnowledgeStep = {
  status: 'active' | 'confirming' | 'completed';
  timing: 'before_graph' | 'after_graph';
  question?: string | null;
  candidates: KnowledgeSelectionCandidate[];
  collections?: KnowledgeSelectionCollection[];
  ungroupedKbs?: KnowledgeSelectionChild[];
  selectedCandidateIds?: string[];
  selectedCollectionHandles?: string[];
  selectedKbHandles?: string[];
  selectedLabels?: string[];
  resetVersion?: number;
  errorMessage?: string | null;
};

export const WorkflowResultGroup = ({
  tasks,
  nodes = [],
  routingNodeIds = [],
  knowledgeStep = null,
  setupStatus,
  presentationTaskId = null,
  isPresentationReentry = false,
  focusHeadingTaskId = null,
  onPresentationHeadingFocused,
  onFocusNode,
  onOpenNodeSettings,
  onSecretSubmit,
  onSecretClear,
  onKnowledgeSubmit,
  onKnowledgeHierarchySubmit,
  onDecision,
  onCancel,
  disabled = false,
}: {
  tasks: AgentBuilderParameterTask[];
  nodes?: Node[];
  routingNodeIds?: string[];
  knowledgeStep?: WorkflowKnowledgeStep | null;
  setupStatus?: WorkflowSetupStatus;
  presentationTaskId?: string | null;
  isPresentationReentry?: boolean;
  focusHeadingTaskId?: string | null;
  onPresentationHeadingFocused?: (taskId: string) => void;
  onFocusNode: (nodeId: string) => void;
  onOpenNodeSettings?: (nodeId: string, section?: 'routing') => void;
  onSecretSubmit?: (
    task: AgentBuilderParameterTask,
    value: string,
  ) => boolean | Promise<boolean>;
  onSecretClear?: (task: AgentBuilderParameterTask) => void;
  onKnowledgeSubmit?: (selectionIds: string[]) => void;
  onKnowledgeHierarchySubmit?: (
    selection: KnowledgeHierarchySubmission,
  ) => void;
  onDecision: (decision: ParameterDecisionInput) => void;
  onCancel?: () => void;
  disabled?: boolean;
}) => {
  const orderedTasks = useMemo(
    () => [...tasks].sort((a, b) => a.stable_order - b.stable_order),
    [tasks],
  );
  const groups = useMemo(() => {
    const grouped = new Map<string, AgentBuilderParameterTask[]>();
    orderedTasks.forEach((task) => {
      grouped.set(task.node_id, [...(grouped.get(task.node_id) || []), task]);
    });
    return grouped;
  }, [orderedTasks]);
  const canonicalActiveTask = orderedTasks.find((task) =>
    ['active', 'invalid'].includes(task.status),
  );
  const presentationTask = presentationTaskId
    ? orderedTasks.find((task) => task.task_id === presentationTaskId)
    : null;
  const hasBlockingKnowledgeStep =
    knowledgeStep?.status === 'active' ||
    knowledgeStep?.status === 'confirming';
  const activeTask = hasBlockingKnowledgeStep
    ? undefined
    : (presentationTask ?? canonicalActiveTask);
  const activeTaskIndex = activeTask ? orderedTasks.indexOf(activeTask) : -1;
  const hasPreviousTask =
    activeTaskIndex > 0 &&
    orderedTasks
      .slice(0, activeTaskIndex)
      .some((task) =>
        ['completed', 'skipped', 'deferred'].includes(task.status),
      );
  const activeTaskId = activeTask?.task_id ?? null;
  const activeNodeId = activeTask?.node_id ?? null;
  const [expandedSecondaryNodeId, setExpandedSecondaryNodeId] = useState<
    string | null
  >(null);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const wasEditingTaskRef = useRef(false);
  const editingNodeId = editingTaskId
    ? (orderedTasks.find((task) => task.task_id === editingTaskId)?.node_id ??
      null)
    : null;
  const lastFocusedTaskIdRef = useRef<string | null>(null);
  const nodeDataById = useMemo(
    () =>
      new Map(
        nodes.map((node) => [
          node.id,
          node.data as unknown as Record<string, unknown>,
        ]),
      ),
    [nodes],
  );
  const routingTaskNodeIds = useMemo(
    () =>
      new Set(
        orderedTasks
          .filter(
            (task) =>
              task.node_type === 'llmNode' &&
              (task.task_group === 'model_routing' ||
                task.parameter_key === 'auto_model_routing'),
          )
          .map((task) => task.node_id),
      ),
    [orderedTasks],
  );
  const routingNodes = useMemo(() => {
    const resultNodeIds = new Set(routingNodeIds);
    return nodes.flatMap((node) => {
      if (
        node.type !== 'llmNode' ||
        !resultNodeIds.has(node.id) ||
        routingTaskNodeIds.has(node.id)
      ) {
        return [];
      }
      const data = node.data as unknown as Record<string, unknown>;
      if (data.auto_model_routing === true) {
        return [];
      }
      return [
        {
          id: node.id,
          label:
            typeof data.title === 'string' && data.title.trim().length > 0
              ? data.title
              : 'LLM',
        },
      ];
    });
  }, [nodes, routingNodeIds, routingTaskNodeIds]);

  useEffect(() => {
    setExpandedSecondaryNodeId(null);
  }, [activeNodeId]);

  useEffect(() => {
    if (wasEditingTaskRef.current && !editingTaskId) {
      setExpandedSecondaryNodeId(null);
    }
    wasEditingTaskRef.current = Boolean(editingTaskId);
  }, [editingTaskId]);

  useEffect(() => {
    if (!activeTaskId || !activeNodeId) {
      lastFocusedTaskIdRef.current = null;
      return;
    }
    if (lastFocusedTaskIdRef.current === activeTaskId) return;
    lastFocusedTaskIdRef.current = activeTaskId;
    onFocusNode(activeNodeId);
  }, [activeNodeId, activeTaskId, onFocusNode]);

  const completed = tasks.filter((task) =>
    ['completed', 'skipped', 'deferred'].includes(task.status),
  ).length;
  const resolvedSetupStatus: WorkflowSetupStatus = editingTaskId
    ? 'editing'
    : (setupStatus ??
      (activeTask?.resolution_source
        ? 'awaiting_confirmation'
        : activeTask
          ? 'configuring'
          : tasks.length > 0 && completed === tasks.length
            ? 'completed'
            : 'configuring'));
  const statusLabel =
    resolvedSetupStatus === 'planning'
      ? 'Workflow 계획 중'
      : resolvedSetupStatus === 'recovery_required'
        ? '서버 확인 대기'
        : resolvedSetupStatus === 'saving'
          ? 'Workflow 저장 중'
          : resolvedSetupStatus === 'awaiting_confirmation'
            ? hasBlockingKnowledgeStep
              ? 'Knowledge 확인 필요'
              : '추천값 확인 필요'
            : resolvedSetupStatus === 'failed'
              ? 'Workflow 설정 실패'
              : resolvedSetupStatus === 'editing'
                ? '설정 수정 중'
                : resolvedSetupStatus === 'completed'
                  ? 'Workflow 생성 완료'
                  : '설정 입력 필요';
  const effectiveStatusLabel =
    resolvedSetupStatus === 'confirming'
      ? 'Workflow \uD655\uC778 \uC911'
      : statusLabel;
  const statusClassName =
    resolvedSetupStatus === 'completed'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200'
      : resolvedSetupStatus === 'failed'
        ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200'
        : resolvedSetupStatus === 'saving' ||
            resolvedSetupStatus === 'confirming'
          ? 'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-200'
          : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200';
  const advancedRoutingNodes = useMemo(() => {
    if (resolvedSetupStatus !== 'completed') return [];
    return nodes.flatMap((node) => {
      if (node.type !== 'llmNode') return [];
      const routingTasks = orderedTasks.filter(
        (task) =>
          task.node_id === node.id && task.task_group === 'model_routing',
      );
      if (
        routingTasks.length === 0 ||
        routingTasks.some(
          (task) =>
            !['completed', 'skipped', 'deferred'].includes(task.status),
        )
      ) {
        return [];
      }
      const data = node.data as unknown as Record<string, unknown>;
      return [
        {
          id: node.id,
          label:
            typeof data.title === 'string' && data.title.trim().length > 0
              ? data.title
              : 'LLM',
        },
      ];
    });
  }, [nodes, orderedTasks, resolvedSetupStatus]);
  return (
    <div
      data-testid="workflow-result-group"
      className="space-y-3 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950"
      aria-label="Workflow setup"
      onKeyDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          Workflow 설정
        </h2>
        {onCancel && canonicalActiveTask && !hasBlockingKnowledgeStep ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={disabled}
            className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            설정 종료
          </button>
        ) : null}
      </div>
      <div
        role={resolvedSetupStatus === 'failed' ? 'alert' : 'status'}
        aria-live={resolvedSetupStatus === 'failed' ? 'assertive' : 'polite'}
        aria-atomic="true"
        className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-xs ${statusClassName}`}
      >
        <span className="font-medium">{effectiveStatusLabel}</span>
        {tasks.length > 0 ? (
          <span>
            {completed} / {tasks.length} 완료
          </span>
        ) : null}
      </div>

      {advancedRoutingNodes.length > 0 ? (
        <section
          data-testid="agent-builder-advanced-routing-settings"
          className="border-t border-neutral-200 pt-3 dark:border-neutral-800"
        >
          <h3 className="text-xs font-medium text-neutral-900 dark:text-neutral-100">
            {'\uACE0\uAE09 \uBAA8\uB378 Routing \uC124\uC815'}
          </h3>
          <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-300">
            {
              '\uAE30\uBCF8 Routing \uD30C\uB77C\uBBF8\uD130 \uD655\uC778\uC774 \uC644\uB8CC\uB410\uC2B5\uB2C8\uB2E4. cohort\uC640 \uC6B4\uC601 \uC815\uCC45\uC740 \uAE30\uC874 LLM Routing \uD654\uBA74\uC5D0\uC11C \uAD00\uB9AC\uD569\uB2C8\uB2E4.'
            }
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {advancedRoutingNodes.map((node) => (
              <button
                key={node.id}
                type="button"
                aria-label={`${node.label} \uACE0\uAE09 Routing \uC124\uC815`}
                onClick={() =>
                  onOpenNodeSettings?.(node.id, 'routing') ??
                  onFocusNode(node.id)
                }
                className="inline-flex items-center gap-1.5 rounded border border-slate-200 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-900"
              >
                <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                {node.label} {'\uACE0\uAE09 Routing \uC124\uC815'}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {routingNodes.length > 0 ? (
        <section
          data-testid="agent-builder-routing-guidance"
          aria-labelledby="agent-builder-routing-guidance-heading"
          className="border-t border-neutral-200 pt-3 dark:border-neutral-800"
        >
          <h3
            id="agent-builder-routing-guidance-heading"
            className="text-xs font-medium text-neutral-900 dark:text-neutral-100"
          >
            모델 자동 라우팅
          </h3>
          <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-300">
            모델 자동 라우팅은 LLM 노드를 선택한 뒤 Routing 설정할 수 있습니다.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {routingNodes.map((node) => (
              <button
                key={node.id}
                type="button"
                onClick={() =>
                  onOpenNodeSettings?.(node.id, 'routing') ??
                  onFocusNode(node.id)
                }
                className="inline-flex items-center gap-1.5 rounded border border-slate-200 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-900"
              >
                <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                {node.label} Routing 설정으로 이동
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {knowledgeStep ? (
        knowledgeStep.status !== 'completed' ? (
          <section
            aria-label="Knowledge setup"
            className="space-y-3 rounded-md border border-neutral-200 p-3 dark:border-neutral-800"
          >
            <div>
              <h3 className="text-sm font-medium text-neutral-900 dark:text-neutral-100">
                {knowledgeStep.timing === 'before_graph'
                  ? 'Graph 생성 전 Knowledge'
                  : 'Graph 생성 후 Knowledge'}
              </h3>
              {knowledgeStep.question ? (
                <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-300">
                  {knowledgeStep.question}
                </p>
              ) : null}
            </div>
            <div data-testid="agent-builder-kb-candidate-list">
              <KnowledgeSelectionControl
                key={`${knowledgeStep.timing}:${knowledgeStep.candidates
                  .map(
                    (candidate) =>
                      candidate.selection_id ?? candidate.candidate_id,
                  )
                  .join(':')}`}
                candidates={knowledgeStep.candidates}
                collections={knowledgeStep.collections}
                ungroupedKbs={knowledgeStep.ungroupedKbs}
                initialSelectedIds={knowledgeStep.selectedCandidateIds}
                initialSelectedCollectionHandles={
                  knowledgeStep.selectedCollectionHandles
                }
                initialSelectedKbHandles={knowledgeStep.selectedKbHandles}
                resetVersion={knowledgeStep.resetVersion}
                timing={knowledgeStep.timing}
                errorMessage={knowledgeStep.errorMessage}
                onSubmit={(selectionIds) => onKnowledgeSubmit?.(selectionIds)}
                onSubmitHierarchy={onKnowledgeHierarchySubmit}
                disabled={
                  disabled ||
                  knowledgeStep.status === 'confirming' ||
                  !onKnowledgeSubmit
                }
              />
            </div>
          </section>
        ) : (
          <div className="rounded-md border border-neutral-200 px-3 py-2 text-xs text-neutral-600 dark:border-neutral-800 dark:text-neutral-300">
            <span className="font-medium">Knowledge 설정 완료</span>
            <span className="ml-2">
              {(() => {
                const collectionCount = new Set(
                  knowledgeStep.selectedCollectionHandles ?? [],
                ).size;
                const kbCount = new Set(knowledgeStep.selectedKbHandles ?? [])
                  .size;
                if (collectionCount > 0 && kbCount > 0) {
                  return `Collection ${collectionCount}개 · Knowledge Base ${kbCount}개 선택`;
                }
                if (collectionCount > 0) {
                  return `Collection ${collectionCount}개 선택`;
                }
                if (kbCount > 0) {
                  return `Knowledge Base ${kbCount}개 선택`;
                }
                const legacyCount = (knowledgeStep.selectedLabels ?? []).length;
                return legacyCount > 0
                  ? `Knowledge Base ${legacyCount}개 선택`
                  : 'Knowledge Base 없이 진행';
              })()}
            </span>
          </div>
        )
      ) : null}

      {!hasBlockingKnowledgeStep && tasks.length > 0 ? (
        <div className="flex items-center justify-between gap-3 text-xs text-neutral-500">
          <span>노드 설정</span>
          {onCancel && canonicalActiveTask ? <span>현재 항목 1개</span> : null}
        </div>
      ) : null}
      {!hasBlockingKnowledgeStep
        ? [...groups.entries()].map(([nodeId, nodeTasks]) => (
            <NodeParameterCard
              key={nodeId}
              nodeId={nodeId}
              tasks={nodeTasks}
              nodeData={nodeDataById.get(nodeId)}
              presentationTaskId={nodeId === activeNodeId ? activeTaskId : null}
              isPresentationReentry={
                isPresentationReentry && nodeId === activeNodeId
              }
              isCurrent={nodeId === activeNodeId}
              focusHeading={
                nodeId === activeNodeId && focusHeadingTaskId === activeTaskId
              }
              onHeadingFocused={onPresentationHeadingFocused}
              onEditingChange={setEditingTaskId}
              onSecretSubmit={onSecretSubmit}
              onSecretClear={onSecretClear}
              expanded={
                nodeId === activeNodeId ||
                nodeId === editingNodeId ||
                nodeId === expandedSecondaryNodeId
              }
              onToggle={
                nodeId === activeNodeId
                  ? undefined
                  : () =>
                      setExpandedSecondaryNodeId((current) =>
                        current === nodeId ? null : nodeId,
                      )
              }
              onDecision={onDecision}
              hasPrevious={nodeId === activeNodeId && hasPreviousTask}
              disabled={disabled}
            />
          ))
        : null}
    </div>
  );
};
