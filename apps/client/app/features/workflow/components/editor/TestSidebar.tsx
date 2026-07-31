import React, { useState, useEffect, useMemo } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { workflowApi } from '../../api/workflowApi';
import {
  agentBuilderApi,
  type AgentBuilderSessionResponse,
} from '../../api/agentBuilderApi';
import { knowledgeApi } from '@/app/features/knowledge/api/knowledgeApi';
import {
  BUDGET_EXCEEDED_MESSAGE,
  isBudgetExceededError,
} from '@/app/features/budget/utils/budgetGuard';
import {
  X,
  Play,
  RefreshCw,
  Loader2,
  CheckCircle,
  AlertCircle,
  ArrowLeft,
  ChevronRight,
  Clock,
  Coins,
  GitCompareArrows,
} from 'lucide-react';
import { toast } from 'sonner';
import { StartNodeData, WorkflowVariable } from '../../types/Nodes';
import { getNodeOutputVariables } from '../../utils/nodeVariablePorts';
import type { WorkflowDraftRequest } from '../../types/Workflow';
import {
  formatGraphIssue,
  type GraphValidationIssue,
  validateWorkflowGraph,
} from '../../utils/validateWorkflowGraph';
import { buildWorkflowDraftPayload } from '../../utils/workflowDraftPayload';
import {
  canonicalDraftMatchesSnapshot,
  workflowDraftSnapshotsEqual,
} from '../../utils/workflowDraftComparison';
import {
  formatCost,
  formatLatency,
  formatTokens,
  isTestExecutionActionDisabled,
  readNodeFinishExecutionSummary,
  readCost,
  readTokenUsage,
  summarizeWorkflowExecution,
} from '../../utils/testExecutionSummary';
import {
  getFinalResponsePreview,
  shouldShowFinalResponseCard,
} from '../../utils/testExecutionFinalResponse';
import { getDeploymentRunCitations } from '../../utils/deploymentRunResult';
import { CitationList } from '../execution/CitationList';
import { FinalResponseCard } from '../execution/FinalResponseCard';
import { deploymentApiErrorMessage } from '../../utils/deploymentPreflightMessage';
import { ModelRoutingDecisionDetails } from '../modelRouting/ModelRoutingDecisionDetails';
import { restoreTestExecutionFromWorkflowRun } from '../../utils/testExecutionRestore';
import {
  TEST_COMPARISON_BASELINE_QUERY_KEY,
  TEST_COMPARISON_ENABLED_VALUE,
  TEST_COMPARISON_NODE_QUERY_KEY,
  TEST_COMPARISON_QUERY_KEY,
  TEST_NODE_QUERY_KEY,
  TEST_RUN_QUERY_KEY,
} from '../../utils/testExecutionLocation';
import { ExecutionComparisonPanel } from './ExecutionComparisonPanel';
import {
  getWorkflowDraftSaveOwner,
  startWorkflowExecutionFromPreflight,
  tryAcquireWorkflowDraftSave,
} from '../../utils/workflowDraftSaveCoordinator';
import { workflowDraftTimestampsEqual } from '../../utils/workflowDraftCAS';

export { ModelRoutingDecisionDetails } from '../modelRouting/ModelRoutingDecisionDetails';

export { FinalResponseCard } from '../execution/FinalResponseCard';

type TestSidebarProps = {
  appendMemoryFlag?: (
    inputs: Record<string, any> | FormData,
  ) => Record<string, any> | FormData;
};

const STREAM_IDLE_TIMEOUT_MS = 60_000;
const TEST_SIDEBAR_DEFAULT_WIDTH = 560;
const TEST_SIDEBAR_COMPARISON_WIDTH = 720;
const TEST_SIDEBAR_MIN_WIDTH = 440;
const TEST_SIDEBAR_MAX_WIDTH = 720;
const TEST_SIDEBAR_VIEWPORT_GUTTER = 24;
const TEST_SIDEBAR_MIN_CANVAS_WIDTH = 420;
const TEST_SIDEBAR_KEYBOARD_STEP = 20;
const TEST_RUN_RESTORE_RETRY_DELAYS_MS = [
  400, 800, 1_200, 2_000, 3_000, 4_000, 5_000, 6_000,
] as const;

type PreflightStatus = 'idle' | 'validating' | 'saving';
type TestRunRestoreState = 'idle' | 'restoring' | 'delayed' | 'failed';
type ComparisonLocationUpdate = {
  comparisonMode?: boolean;
  baselineRunId?: string | null;
  comparisonNodeId?: string | null;
};

export const TEST_INPUT_CLASS_NAME =
  'w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-100 dark:placeholder:text-gray-500';
const testTextAreaClassName = `${TEST_INPUT_CLASS_NAME} min-h-[180px]`;
const testJsonTextAreaClassName = `${TEST_INPUT_CLASS_NAME} min-h-[200px] font-mono text-sm`;

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

const cloneDraft = (value: WorkflowDraftRequest): WorkflowDraftRequest => {
  if (typeof structuredClone === 'function') {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as WorkflowDraftRequest;
};

const getHttpStatus = (error: unknown) => {
  if (
    typeof error === 'object' &&
    error !== null &&
    'response' in error &&
    typeof (error as { response?: { status?: unknown } }).response?.status ===
      'number'
  ) {
    return (error as { response: { status: number } }).response.status;
  }
  return undefined;
};

const getHttpDetail = (error: unknown) => {
  if (typeof error !== 'object' || error === null || !('response' in error)) {
    return undefined;
  }
  const response = (error as { response?: { data?: { detail?: unknown } } })
    .response;
  return typeof response?.data?.detail === 'string'
    ? response.data.detail
    : undefined;
};

const testPreflightSaveErrorMessage = (error: unknown) => {
  const status = getHttpStatus(error);
  const detail = getHttpDetail(error);
  if (status === 401) {
    return '로그인이 만료되었습니다. 다시 로그인한 뒤 시도해주세요.';
  }
  if (status === 403) {
    return '이 Workflow를 저장하거나 테스트할 권한이 없습니다.';
  }
  if (status === 409 && detail === 'operation envelope not found') {
    return 'Agent Builder 저장 상태를 다시 확인하고 있습니다. 확인이 끝난 뒤 다시 시도해주세요.';
  }
  if (status === 409) {
    return '다른 변경사항이 먼저 저장되었습니다. 서버 상태를 확인한 뒤 다시 시도해주세요.';
  }
  if (typeof status === 'number' && status >= 400 && status < 500) {
    return 'Workflow 저장 요청을 검증하지 못했습니다. 설정을 확인한 뒤 다시 시도해주세요.';
  }
  return 'Workflow 저장 중 오류가 발생했습니다. 서버 상태를 확인한 뒤 다시 시도해주세요.';
};

type OperationRecoveryClassification =
  | 'applied'
  | 'unapplied'
  | 'pending'
  | 'stale';

const operationRecoveryClassification = (
  session: AgentBuilderSessionResponse,
  canonical: unknown,
): OperationRecoveryClassification => {
  const envelope = session.active_graph_mutation;
  const canonicalHash =
    canonical && typeof canonical === 'object'
      ? (canonical as { graph_hash?: unknown }).graph_hash
      : undefined;
  const envelopeStatus =
    envelope && typeof envelope.status === 'string' ? envelope.status : null;
  const resultGraphHash =
    envelope && typeof envelope.result_graph_hash === 'string'
      ? envelope.result_graph_hash
      : null;
  const savedWorkflowUpdatedAt =
    envelope && typeof envelope.saved_workflow_updated_at === 'string'
      ? envelope.saved_workflow_updated_at
      : null;
  const canonicalUpdatedAt =
    canonical && typeof canonical === 'object'
      ? (canonical as { updated_at?: unknown }).updated_at
      : undefined;
  const baseGraphHash =
    envelope && typeof envelope.base_graph_hash === 'string'
      ? envelope.base_graph_hash
      : null;

  if (
    envelopeStatus === 'acknowledged' &&
    typeof canonicalHash === 'string' &&
    canonicalHash === resultGraphHash &&
    typeof canonicalUpdatedAt === 'string' &&
    canonicalUpdatedAt === savedWorkflowUpdatedAt
  ) {
    return 'applied';
  }
  if (
    ['pending_apply', 'pending_save', 'pending_ack'].includes(
      envelopeStatus ?? '',
    ) ||
    ['planning', 'processing', 'saving', 'pending_ack'].includes(session.status)
  ) {
    return 'pending';
  }
  if (
    ['blocked', 'failed', 'reverted'].includes(envelopeStatus ?? '') &&
    typeof canonicalHash === 'string' &&
    canonicalHash === baseGraphHash
  ) {
    return 'unapplied';
  }
  return 'stale';
};

const operationRecoveryFailureMessage = (
  classification: Exclude<OperationRecoveryClassification, 'applied'> | null,
) => {
  if (classification === 'pending') {
    return 'Agent Builder 저장을 확인하는 중입니다. 완료된 뒤 테스트를 다시 실행해주세요.';
  }
  if (classification === 'unapplied') {
    return 'Agent Builder 변경이 서버 Workflow에 적용되지 않았습니다. 현재 설정을 확인한 뒤 다시 시도해주세요.';
  }
  if (classification === 'stale') {
    return 'Agent Builder 저장 상태를 확인했습니다. 최신 Workflow 상태에서 테스트를 다시 실행해주세요.';
  }
  return '이전 Agent Builder 작업 정보를 확인할 수 없습니다. Workflow를 새로고침한 뒤 다시 시도해주세요.';
};

const latestAgentBuilderSessionId = () => {
  const state = useWorkflowStore.getState() as ReturnType<
    typeof useWorkflowStore.getState
  > & {
    undoStack?: Array<{
      agentBuilderHistory?: { sessionId?: string };
      agentBuilderOperation?: { sessionId?: string };
    }>;
  };
  for (const snapshot of [...(state.undoStack ?? [])].reverse()) {
    const sessionId =
      snapshot.agentBuilderHistory?.sessionId ??
      snapshot.agentBuilderOperation?.sessionId;
    if (sessionId) return sessionId;
  }
  return null;
};

const hasPendingAgentBuilderAcknowledgement = (
  undoStack: Array<{
    agentBuilderHistory?: { acknowledged?: boolean };
    agentBuilderOperation?: { operationId?: string };
  }> | undefined,
) => {
  for (const snapshot of [...(undoStack ?? [])].reverse()) {
    if (!snapshot.agentBuilderOperation) continue;
    return snapshot.agentBuilderHistory?.acknowledged !== true;
  }
  return false;
};

const AGENT_BUILDER_ACKNOWLEDGEMENT_PENDING_MESSAGE =
  'Agent Builder 저장 결과를 확인 중입니다. 확인이 끝난 뒤 다시 실행해주세요.';

const waitForTestRunRestore = (durationMs: number) =>
  new Promise((resolve) => setTimeout(resolve, durationMs));

const readTestExecutionLocation = () => {
  if (typeof window === 'undefined') {
    return {
      runId: null,
      nodeId: null,
      comparisonMode: false,
      baselineRunId: null,
      comparisonNodeId: null,
    };
  }

  const searchParams = new URLSearchParams(window.location.search);
  return {
    runId: searchParams.get(TEST_RUN_QUERY_KEY),
    nodeId: searchParams.get(TEST_NODE_QUERY_KEY),
    comparisonMode:
      searchParams.get(TEST_COMPARISON_QUERY_KEY) ===
      TEST_COMPARISON_ENABLED_VALUE,
    baselineRunId: searchParams.get(TEST_COMPARISON_BASELINE_QUERY_KEY),
    comparisonNodeId: searchParams.get(TEST_COMPARISON_NODE_QUERY_KEY),
  };
};

const setSearchParam = (
  searchParams: URLSearchParams,
  key: string,
  value: string | null,
) => {
  if (value) {
    searchParams.set(key, value);
  } else {
    searchParams.delete(key);
  }
};

const replaceTestExecutionLocation = (
  runId: string | null,
  nodeId: string | null,
  comparisonUpdate: ComparisonLocationUpdate = {},
) => {
  if (typeof window === 'undefined') return;

  const url = new URL(window.location.href);
  setSearchParam(url.searchParams, TEST_RUN_QUERY_KEY, runId);
  setSearchParam(url.searchParams, TEST_NODE_QUERY_KEY, nodeId);

  if ('comparisonMode' in comparisonUpdate) {
    setSearchParam(
      url.searchParams,
      TEST_COMPARISON_QUERY_KEY,
      comparisonUpdate.comparisonMode ? TEST_COMPARISON_ENABLED_VALUE : null,
    );
  }
  if ('baselineRunId' in comparisonUpdate) {
    setSearchParam(
      url.searchParams,
      TEST_COMPARISON_BASELINE_QUERY_KEY,
      comparisonUpdate.baselineRunId ?? null,
    );
  }
  if ('comparisonNodeId' in comparisonUpdate) {
    setSearchParam(
      url.searchParams,
      TEST_COMPARISON_NODE_QUERY_KEY,
      comparisonUpdate.comparisonNodeId ?? null,
    );
  }
  window.history.replaceState(window.history.state, '', url);
};

export function TestSidebar({ appendMemoryFlag }: TestSidebarProps) {
  const {
    isTestPanelOpen,
    toggleTestPanel,
    openTestPanel,
    nodes,
    activeWorkflowId,
    updateNodeExecutionData,
    resetNodeExecutionData,
    workflowAccess,
    edges,
    features,
    envVariables,
    runtimeVariables,
    canonicalDraftMetadata,
    isAgentBuilderMutationSaving,
    undoStack,
    testExecutionStatus,
    testExecutionRunId,
    testSelectedNodeId,
    testExecutionStartedAt,
    testExecutionFinishedAt,
    testExecutionResult,
    testNodeResults,
    testExecutionError,
    currentExecutingNodeId,
    isTestUploading,
    beginTestExecution,
    setTestUploading,
    setCurrentExecutingNode,
    setTestExecutionRunId,
    selectTestExecutionNode,
    addTestNodeResult,
    finishTestExecution,
    failTestExecution,
    restoreTestExecution,
    resetTestExecution,
  } = useWorkflowStore();
  const { setCenter, getViewport } = useReactFlow();

  const [inputs, setInputs] = useState<Record<string, any>>({});
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [preflightStatus, setPreflightStatus] =
    useState<PreflightStatus>('idle');
  const [validationErrors, setValidationErrors] = useState<
    GraphValidationIssue[]
  >([]);
  const [isComparisonMode, setIsComparisonMode] = useState(false);
  const [hasOpenedComparisonPanel, setHasOpenedComparisonPanel] =
    useState(false);
  const [comparisonListReloadKey, setComparisonListReloadKey] = useState(0);
  const [comparisonBaselineRunId, setComparisonBaselineRunId] = useState<
    string | null
  >(null);
  const [comparisonSelectedNodeId, setComparisonSelectedNodeId] = useState<
    string | null
  >(null);
  const [testExecutionLocationRevision, setTestExecutionLocationRevision] =
    useState(0);
  const [testRunRestoreState, setTestRunRestoreState] =
    useState<TestRunRestoreState>('idle');
  const [testRunRestoreRetry, setTestRunRestoreRetry] = useState(0);
  const [localSelectedTestNodeId, setLocalSelectedTestNodeId] = useState<
    string | null
  >(null);
  const [testSidebarWidth, setTestSidebarWidth] = useState(
    TEST_SIDEBAR_DEFAULT_WIDTH,
  );
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === 'undefined' ? 0 : window.innerWidth,
  );
  const clearResizeListenersRef = React.useRef<(() => void) | null>(null);
  const nodeStartedAtRef = React.useRef<Record<string, number>>({});
  const restoredRunRef = React.useRef<string | null>(null);
  const historyLocationChangeRef = React.useRef(false);
  const testExecutionContentRef = React.useRef<HTMLDivElement>(null);
  const latestNodesRef = React.useRef(nodes);
  const previousTestExecutionStatusRef = React.useRef(testExecutionStatus);
  const currentTestExecutionRef = React.useRef({
    runId: testExecutionRunId,
    status: testExecutionStatus,
    nodeResults: testNodeResults,
    selectedNodeId: testSelectedNodeId,
  });
  const isExecuting = testExecutionStatus === 'running';
  const hasPersistedActiveWorkflow =
    Boolean(activeWorkflowId) && activeWorkflowId !== 'default';
  const isActiveWorkflowDraftLoaded = Boolean(
    activeWorkflowId && canonicalDraftMetadata?.[activeWorkflowId],
  );
  const selectedTestNodeId = testSelectedNodeId ?? localSelectedTestNodeId;
  const selectExecutionNode = (nodeId: string | null) => {
    setLocalSelectedTestNodeId(nodeId);
    selectTestExecutionNode?.(nodeId);
  };
  const isPreparing =
    preflightStatus === 'validating' || preflightStatus === 'saving';
  const isAgentBuilderAcknowledgementBlocking =
    hasPendingAgentBuilderAcknowledgement(undoStack);
  const isAgentBuilderSaveBlocking =
    isAgentBuilderMutationSaving || isAgentBuilderAcknowledgementBlocking;
  const agentBuilderSaveBlockingMessage =
    isAgentBuilderAcknowledgementBlocking
      ? AGENT_BUILDER_ACKNOWLEDGEMENT_PENDING_MESSAGE
      : 'Agent Builder 변경사항 저장을 확인하는 중입니다.';
  const executionResult = testExecutionResult;
  const hasExecutionResult =
    executionResult !== null && executionResult !== undefined;
  const nodeResults = testNodeResults;
  const error = testExecutionError;
  const canExecute = workflowAccess?.can_execute !== false;
  const isExecuteActionDisabled =
    !hasPersistedActiveWorkflow ||
    isTestExecutionActionDisabled({
      isExecuting,
      isUploading: isTestUploading,
      isPreparing: isPreparing || isAgentBuilderSaveBlocking,
      canExecute,
    });

  const maxTestSidebarWidth = Math.min(
    TEST_SIDEBAR_MAX_WIDTH,
    Math.max(0, viewportWidth - TEST_SIDEBAR_VIEWPORT_GUTTER),
  );
  const minTestSidebarWidth = Math.min(
    TEST_SIDEBAR_MIN_WIDTH,
    maxTestSidebarWidth,
  );
  const renderedTestSidebarWidth = clamp(
    isComparisonMode
      ? Math.max(testSidebarWidth, TEST_SIDEBAR_COMPARISON_WIDTH)
      : testSidebarWidth,
    minTestSidebarWidth,
    maxTestSidebarWidth,
  );
  const canResizeTestSidebar =
    viewportWidth >= TEST_SIDEBAR_MIN_WIDTH + TEST_SIDEBAR_MIN_CANVAS_WIDTH;

  useEffect(() => {
    const syncViewportWidth = () => setViewportWidth(window.innerWidth);

    window.addEventListener('resize', syncViewportWidth);
    return () => window.removeEventListener('resize', syncViewportWidth);
  }, []);

  useEffect(
    () => () => {
      clearResizeListenersRef.current?.();
    },
    [],
  );

  useEffect(() => {
    const syncLocationState = () => {
      historyLocationChangeRef.current = true;
      setTestExecutionLocationRevision((value) => value + 1);
    };

    window.addEventListener('popstate', syncLocationState);
    return () => window.removeEventListener('popstate', syncLocationState);
  }, []);

  useEffect(() => {
    latestNodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    if (!activeWorkflowId) return;

    const location = readTestExecutionLocation();
    setComparisonBaselineRunId(location.baselineRunId);
    setComparisonSelectedNodeId(location.comparisonNodeId);
    setHasOpenedComparisonPanel(location.comparisonMode);
    setIsComparisonMode(location.comparisonMode);
  }, [activeWorkflowId, testExecutionLocationRevision]);

  useEffect(() => {
    currentTestExecutionRef.current = {
      runId: testExecutionRunId,
      status: testExecutionStatus,
      nodeResults: testNodeResults,
      selectedNodeId: testSelectedNodeId,
    };
  }, [
    testExecutionRunId,
    testExecutionStatus,
    testNodeResults,
    testSelectedNodeId,
  ]);

  useEffect(() => {
    const previousStatus = previousTestExecutionStatusRef.current;
    previousTestExecutionStatusRef.current = testExecutionStatus;
    const isTerminalStatus =
      testExecutionStatus === 'success' || testExecutionStatus === 'failure';
    if (previousStatus === 'running' && isTerminalStatus) {
      setComparisonListReloadKey((value) => value + 1);
    }
  }, [testExecutionStatus]);

  useEffect(() => {
    const { runId, nodeId } = readTestExecutionLocation();
    if (
      !runId ||
      !hasPersistedActiveWorkflow ||
      !isActiveWorkflowDraftLoaded
    ) {
      if (!runId && historyLocationChangeRef.current) {
        historyLocationChangeRef.current = false;
        restoredRunRef.current = null;
        setLocalSelectedTestNodeId(null);
        selectTestExecutionNode?.(null);
        resetTestExecution();
      }
      setTestRunRestoreState('idle');
      return;
    }

    historyLocationChangeRef.current = false;
    // URL에 실행 식별자가 있으면 아직 DB 조회가 지연 중이어도 복원 상태를 보여준다.
    openTestPanel();

    const currentExecution = currentTestExecutionRef.current;
    if (
      currentExecution.runId === runId &&
      currentExecution.status !== 'running'
    ) {
      setTestRunRestoreState('idle');
      const selectedNodeId =
        nodeId &&
        currentExecution.nodeResults.some((result) => result.nodeId === nodeId)
          ? nodeId
          : null;
      if (selectedNodeId !== currentExecution.selectedNodeId) {
        setLocalSelectedTestNodeId(selectedNodeId);
        selectTestExecutionNode?.(selectedNodeId);
      }
      return;
    }

    const restoreKey = `${activeWorkflowId}:${runId}`;
    if (restoredRunRef.current === restoreKey) {
      setTestRunRestoreState('idle');
      return;
    }

    let cancelled = false;
    const restore = async () => {
      setTestRunRestoreState('restoring');
      for (
        let attempt = 0;
        attempt <= TEST_RUN_RESTORE_RETRY_DELAYS_MS.length;
        attempt += 1
      ) {
        try {
          const run = await workflowApi.getWorkflowRun(activeWorkflowId, runId);
          if (cancelled) return;

          const restored = restoreTestExecutionFromWorkflowRun(
            run,
            latestNodesRef.current,
          );
          restoreTestExecution(restored);

          const selectedNodeId =
            nodeId &&
            restored.nodeResults.some((result) => result.nodeId === nodeId)
              ? nodeId
              : null;
          setLocalSelectedTestNodeId(selectedNodeId);
          selectTestExecutionNode?.(selectedNodeId);

          if (run.status.toLowerCase() !== 'running') {
            restoredRunRef.current = restoreKey;
            setTestRunRestoreState('idle');
            return;
          }
        } catch (error) {
          if (getHttpStatus(error) !== 404) {
            if (!cancelled) {
              setTestRunRestoreState('failed');
              toast.error('이전 테스트 실행 기록을 불러오지 못했습니다.');
            }
            return;
          }
        }

        const retryDelay = TEST_RUN_RESTORE_RETRY_DELAYS_MS[attempt];
        if (retryDelay !== undefined) {
          await waitForTestRunRestore(retryDelay);
          if (cancelled) return;
        }
      }

      if (!cancelled) {
        setTestRunRestoreState('delayed');
        toast.error(
          '이전 테스트 실행 기록이 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요.',
        );
      }
    };

    void restore();

    return () => {
      cancelled = true;
    };
  }, [
    activeWorkflowId,
    hasPersistedActiveWorkflow,
    isActiveWorkflowDraftLoaded,
    openTestPanel,
    restoreTestExecution,
    resetTestExecution,
    selectTestExecutionNode,
    testExecutionLocationRevision,
    testRunRestoreRetry,
  ]);

  const outputLabelByNodeId = useMemo(() => {
    const labelMap = new Map<string, Map<string, string>>();

    for (const node of nodes) {
      const outputLabels = new Map<string, string>();
      for (const output of getNodeOutputVariables(node)) {
        outputLabels.set(output.key, output.label || output.key);
        if (output.outputId) {
          outputLabels.set(output.outputId, output.label || output.key);
        }
      }
      labelMap.set(node.id, outputLabels);
    }

    return labelMap;
  }, [nodes]);

  const getNodeDisplayName = (nodeId: string) => {
    const node = nodes.find((item) => item.id === nodeId);
    const title = String(node?.data?.title || '').trim();
    return title || nodeId;
  };

  const getOutputDisplayKey = (nodeId: string, key: string) => {
    const label = outputLabelByNodeId.get(nodeId)?.get(key)?.trim();
    if (!label || label === key) return key;
    return `${label} (${key})`;
  };

  const stringifyOutputForDisplay = (nodeId: string, output: unknown) => {
    if (!output || typeof output !== 'object' || Array.isArray(output)) {
      return JSON.stringify(output, null, 2);
    }

    const displayOutput = Object.fromEntries(
      Object.entries(output as Record<string, unknown>).map(([key, value]) => [
        getOutputDisplayKey(nodeId, key),
        value,
      ]),
    );

    return JSON.stringify(displayOutput, null, 2);
  };

  // Start Node 찾기 및 변수 초기화
  const startNode = nodes.find(
    (n) =>
      n.type === 'startNode' ||
      n.type === 'webhookTrigger' ||
      n.type === 'scheduleTrigger',
  );

  let variables: WorkflowVariable[] = [];
  if (startNode?.type === 'startNode') {
    variables = (startNode.data as StartNodeData)?.variables || [];
  } else if (startNode?.type === 'webhookTrigger') {
    // Webhook의 경우 내부적으로만 사용, UI에서는 특별 처리
    variables = [
      {
        id: '__json_payload__',
        name: '__json_payload__',
        label: '웹훅 페이로드',
        type: 'paragraph',
        required: true,
        placeholder: '{"user": "john", "action": "signup"}',
      },
    ];
  }

  // 패널이 열릴 때 입력값 초기화 (실행 결과는 유지)
  useEffect(() => {
    if (!isTestPanelOpen) return;

    const timeout = window.setTimeout(() => {
      if (isTestPanelOpen) {
        const initial: Record<string, any> = {};
        variables.forEach((v) => {
          if (v.type === 'number') {
            initial[v.name] = 0;
          } else if (v.type === 'checkbox') {
            initial[v.name] = false;
          } else if (v.type === 'select') {
            initial[v.name] = v.options?.[0]?.value || '';
          } else if (v.type === 'file') {
            initial[v.name] = null;
          } else {
            initial[v.name] = '';
          }
        });

        // 웹훅의 경우 캡처된 데이터가 있으면 자동 채우기
        if (startNode?.type === 'webhookTrigger') {
          const data = startNode.data as any;
          if (data.captured_payload) {
            initial['__json_payload__'] = JSON.stringify(
              data.captured_payload,
              null,
              2,
            );
          }
        }

        setInputs(initial);
        setFiles({});
      }
    }, 0);

    return () => window.clearTimeout(timeout);
  }, [isTestPanelOpen]);

  if (!isTestPanelOpen) return null;

  const setClampedTestSidebarWidth = (width: number) => {
    setTestSidebarWidth(clamp(width, minTestSidebarWidth, maxTestSidebarWidth));
  };

  const handleTestSidebarResizeStart = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    if (!canResizeTestSidebar) return;

    event.preventDefault();
    clearResizeListenersRef.current?.();

    const startClientX = event.clientX;
    const startWidth = renderedTestSidebarWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handlePointerMove = (moveEvent: PointerEvent) => {
      setClampedTestSidebarWidth(startWidth + startClientX - moveEvent.clientX);
    };
    const clearResizeListeners = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', clearResizeListeners);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      clearResizeListenersRef.current = null;
    };

    clearResizeListenersRef.current = clearResizeListeners;
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', clearResizeListeners, { once: true });
  };

  const handleTestSidebarResizeKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
  ) => {
    if (!canResizeTestSidebar) return;

    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      setClampedTestSidebarWidth(
        renderedTestSidebarWidth - TEST_SIDEBAR_KEYBOARD_STEP,
      );
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      setClampedTestSidebarWidth(
        renderedTestSidebarWidth + TEST_SIDEBAR_KEYBOARD_STEP,
      );
    } else if (event.key === 'Home') {
      event.preventDefault();
      setClampedTestSidebarWidth(minTestSidebarWidth);
    } else if (event.key === 'End') {
      event.preventDefault();
      setClampedTestSidebarWidth(maxTestSidebarWidth);
    }
  };

  const handleChange = (name: string, value: any) => {
    setInputs((prev) => ({ ...prev, [name]: value }));
  };

  const buildObservability = (
    output: any,
    status: 'success' | 'failure' | 'running',
    latencyMs?: number,
    totalTokens?: number,
    totalCost?: number,
  ) => {
    return {
      status,
      model: output?.model,
      total_tokens: totalTokens ?? readTokenUsage(output),
      total_cost: totalCost ?? readCost(output),
      latency_ms: latencyMs,
    };
  };

  const resultOutputByNodeId = new Map<string, unknown>();
  if (
    executionResult &&
    typeof executionResult === 'object' &&
    !Array.isArray(executionResult)
  ) {
    for (const [nodeId, output] of Object.entries(
      executionResult as Record<string, unknown>,
    )) {
      resultOutputByNodeId.set(nodeId, output);
    }
  }
  // 스트림의 node_finish 결과에는 routing/usage 메타데이터가 더 풍부하므로
  // workflow 최종 결과의 축약 output보다 우선합니다.
  for (const result of nodeResults) {
    resultOutputByNodeId.set(result.nodeId, result.output);
  }

  const nodeResultById = new Map(
    nodeResults.map((result) => [result.nodeId, result]),
  );
  const executionNodeIds = new Set([
    ...nodes.map((node) => node.id),
    ...nodeResults.map((result) => result.nodeId),
  ]);
  const nodeExecutionSummaries = Array.from(executionNodeIds)
    .map((nodeId) => {
      const node = nodes.find((item) => item.id === nodeId);
      const storedResult = nodeResultById.get(nodeId);
      if (!node && !storedResult) return null;
      const data = (node?.data ?? {}) as {
        title?: string;
        name?: string;
        status?: string;
        observability?: {
          status?: 'success' | 'failure' | 'running';
          total_tokens?: number;
          total_cost?: number;
          latency_ms?: number;
        };
      };
      const output = resultOutputByNodeId.get(nodeId);
      const status =
        currentExecutingNodeId === nodeId
          ? 'running'
          : storedResult?.status ||
            data.observability?.status ||
            data.status ||
            'idle';

      return {
        nodeId,
        nodeType: storedResult?.nodeType || node?.type || 'node',
        title:
          storedResult?.title ||
          data.title ||
          data.name ||
          getNodeDisplayName(nodeId),
        status,
        output,
        traceMetadata: storedResult?.traceMetadata,
        latencyMs: storedResult?.latencyMs ?? data.observability?.latency_ms,
        totalTokens:
          storedResult?.totalTokens ??
          data.observability?.total_tokens ??
          readTokenUsage(output),
        totalCost:
          storedResult?.totalCost ??
          data.observability?.total_cost ??
          readCost(output),
      };
    })
    .filter(
      (summary): summary is NonNullable<typeof summary> => summary !== null,
    )
    .filter((summary) =>
      ['running', 'success', 'failure'].includes(summary.status),
    );

  const workflowExecutionSummary = summarizeWorkflowExecution(
    nodeExecutionSummaries.map((summary) => ({
      nodeId: summary.nodeId,
      status: summary.status as 'running' | 'success' | 'failure',
      latencyMs: summary.latencyMs,
      totalTokens: summary.totalTokens,
      cost: summary.totalCost,
    })),
    testExecutionStartedAt,
    testExecutionFinishedAt,
    executionResult as
      | {
          duration?: unknown;
          total_tokens?: unknown;
          total_cost?: unknown;
        }
      | null
      | undefined,
  );
  const finalResponsePreview = getFinalResponsePreview({
    workflowResult: executionResult,
    nodeResults,
    nodes,
  });
  const finalResponseCitations = getDeploymentRunCitations(executionResult, {
    knownNodeIds: nodes.map((node) => node.id),
  });
  const showFinalResponseCard = shouldShowFinalResponseCard({
    hasExecutionResult,
    error,
  });
  const selectedNodeExecutionSummary = selectedTestNodeId
    ? nodeExecutionSummaries.find(
        (summary) => summary.nodeId === selectedTestNodeId,
      )
    : null;

  const renderNodeExecutionSummary = (
    summary: (typeof nodeExecutionSummaries)[number],
  ) => {
    const hasLlmUsageMetrics = summary.nodeType === 'llmNode';
    const isRunning = summary.status === 'running';
    const isFailure = summary.status === 'failure';
    const statusLabel = isRunning ? '실행 중' : isFailure ? '실패' : '성공';
    const statusClassName = isRunning
      ? 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300'
      : isFailure
        ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'
        : 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300';
    const StatusIcon = isRunning
      ? Loader2
      : isFailure
        ? AlertCircle
        : CheckCircle;

    return (
      <div
        key={summary.nodeId}
        className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
      >
        <div className="flex items-start justify-between gap-3 border-b border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
              {summary.title}
            </div>
            <div className="mt-1 text-xs text-gray-500">{summary.nodeType}</div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <span
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs font-semibold ${statusClassName}`}
            >
              <StatusIcon
                className={`h-3.5 w-3.5 ${isRunning ? 'animate-spin' : ''}`}
              />
              {statusLabel}
            </span>
            {!isRunning ? (
              <button
                type="button"
                aria-label={`${summary.title} 상세 보기`}
                title="상세 보기"
                onClick={() => {
                  selectExecutionNode(summary.nodeId);
                  replaceTestExecutionLocation(
                    testExecutionRunId,
                    summary.nodeId,
                  );
                }}
                className="rounded-md border border-gray-200 bg-white p-1.5 text-gray-500 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-blue-800 dark:hover:bg-blue-950/30"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>
        <dl
          className={`grid gap-2 bg-white px-4 py-3 text-xs dark:bg-gray-900 ${
            hasLlmUsageMetrics ? 'grid-cols-3' : 'grid-cols-1'
          }`}
        >
          <div>
            <dt className="flex items-center gap-1 text-gray-500">
              <Clock className="h-3.5 w-3.5" />
              시간
            </dt>
            <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
              {formatLatency(summary.latencyMs)}
            </dd>
          </div>
          {hasLlmUsageMetrics ? (
            <>
              <div>
                <dt className="flex items-center gap-1 text-gray-500">
                  <Coins className="h-3.5 w-3.5" />
                  비용
                </dt>
                <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
                  {formatCost(summary.totalCost)}
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">토큰</dt>
                <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
                  {formatTokens(summary.totalTokens)}
                </dd>
              </div>
            </>
          ) : null}
        </dl>
      </div>
    );
  };

  const renderNodeExecutionDetail = (
    summary: (typeof nodeExecutionSummaries)[number],
  ) => {
    const hasLlmUsageMetrics = summary.nodeType === 'llmNode';
    const outputMetadata =
      summary.output &&
      typeof summary.output === 'object' &&
      !Array.isArray(summary.output) &&
      (summary.output as Record<string, unknown>).metadata &&
      typeof (summary.output as Record<string, unknown>).metadata === 'object' &&
      !Array.isArray((summary.output as Record<string, unknown>).metadata)
        ? ((summary.output as Record<string, unknown>).metadata as Record<
            string,
            unknown
          >)
        : null;
    const traceMetadata =
      summary.traceMetadata &&
      typeof summary.traceMetadata === 'object' &&
      !Array.isArray(summary.traceMetadata)
        ? (summary.traceMetadata as Record<string, unknown>)
        : null;
    const traceLlm =
      traceMetadata?.llm &&
      typeof traceMetadata.llm === 'object' &&
      !Array.isArray(traceMetadata.llm)
        ? (traceMetadata.llm as Record<string, unknown>)
        : null;
    const hasRoutingTrace =
      hasLlmUsageMetrics &&
      (typeof outputMetadata?.model_routing === 'object' ||
        typeof traceMetadata?.model_routing === 'object' ||
        typeof traceLlm?.model_routing === 'object' ||
        typeof traceLlm?.selected_model === 'string' ||
        typeof traceLlm?.decision_source === 'string' ||
        typeof traceLlm?.reason_code === 'string');

    return (
      <div className="space-y-5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="테스트 결과로 돌아가기"
            title="테스트 결과로 돌아가기"
            onClick={() => {
              selectExecutionNode(null);
              replaceTestExecutionLocation(testExecutionRunId, null);
            }}
            className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
          </button>
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {summary.title} 실행 상세
          </h3>
        </div>

        <dl
          className={`grid gap-3 rounded-lg border border-gray-200 bg-gray-50 p-4 text-xs dark:border-gray-700 dark:bg-gray-800/60 ${
            hasLlmUsageMetrics ? 'grid-cols-3' : 'grid-cols-2'
          }`}
        >
          <div>
            <dt className="text-gray-500">상태</dt>
            <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
              {summary.status === 'failure' ? '실패' : '성공'}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">실행 시간</dt>
            <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
              {formatLatency(summary.latencyMs)}
            </dd>
          </div>
          {hasLlmUsageMetrics ? (
            <div>
              <dt className="text-gray-500">비용</dt>
              <dd className="mt-1 font-semibold text-gray-900 dark:text-gray-100">
                {formatCost(summary.totalCost)}
              </dd>
            </div>
          ) : null}
        </dl>

        <section>
          <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            출력 데이터
          </h4>
          <pre className="mt-2 max-h-[420px] overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-4 text-xs leading-5 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
            {stringifyOutputForDisplay(summary.nodeId, summary.output)}
          </pre>
        </section>

        {hasRoutingTrace ? (
          <ModelRoutingDecisionDetails
            output={summary.output}
            traceMetadata={summary.traceMetadata}
          />
        ) : null}
      </div>
    );
  };

  const renderExecutionTotalSummary = () => (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-900/20">
      <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-100">
        최종 실행 요약
      </h3>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-blue-700 dark:text-blue-300">서버 실행</dt>
          <dd className="mt-1 font-semibold text-blue-950 dark:text-blue-50">
            {formatLatency(workflowExecutionSummary.serverDurationMs)}
          </dd>
        </div>
        <div>
          <dt className="text-blue-700 dark:text-blue-300">화면 완료</dt>
          <dd className="mt-1 font-semibold text-blue-950 dark:text-blue-50">
            {formatLatency(workflowExecutionSummary.screenCompletionDurationMs)}
          </dd>
        </div>
        <div>
          <dt className="text-blue-700 dark:text-blue-300">전체 비용</dt>
          <dd className="mt-1 font-semibold text-blue-950 dark:text-blue-50">
            {formatCost(workflowExecutionSummary.totalCost)}
          </dd>
        </div>
        <div>
          <dt className="text-blue-700 dark:text-blue-300">전체 토큰</dt>
          <dd className="mt-1 font-semibold text-blue-950 dark:text-blue-50">
            {formatTokens(workflowExecutionSummary.totalTokens)}
          </dd>
        </div>
      </dl>
    </div>
  );

  const handleExecute = async () => {
    if (!hasPersistedActiveWorkflow) return;
    if (!canExecute) {
      failTestExecution('현재 권한으로는 실행할 수 없습니다.');
      return;
    }
    const currentWorkflowState = useWorkflowStore.getState();
    if (
      currentWorkflowState.isAgentBuilderMutationSaving ||
      hasPendingAgentBuilderAcknowledgement(
        currentWorkflowState.undoStack,
      ) ||
      getWorkflowDraftSaveOwner(activeWorkflowId) === 'agent_builder'
    ) {
      toast.info(
        hasPendingAgentBuilderAcknowledgement(
          currentWorkflowState.undoStack,
        )
          ? AGENT_BUILDER_ACKNOWLEDGEMENT_PENDING_MESSAGE
          : 'Agent Builder 변경사항 저장을 확인하는 중입니다.',
      );
      return;
    }

    selectExecutionNode(null);
    setComparisonSelectedNodeId(null);
    restoredRunRef.current = null;
    setTestRunRestoreState('idle');
    replaceTestExecutionLocation(null, null, { comparisonNodeId: null });
    setValidationErrors([]);
    setPreflightStatus('validating');
    let releaseTestPreflightSave: (() => void) | null = null;

    try {
      const hasFiles = Object.values(files).some((file) => file !== null);
      let finalInputs: Record<string, any> = { ...inputs };

      // Webhook인 경우 JSON 파싱
      if (startNode?.type === 'webhookTrigger') {
        try {
          const rawJson = inputs['__json_payload__'];
          finalInputs = JSON.parse(rawJson);
        } catch {
          toast.error('유효하지 않은 JSON 형식입니다.');
          failTestExecution('JSON 파싱 실패');
          setPreflightStatus('idle');
          return;
        }
      }

      const graphSnapshot = cloneDraft({
        nodes,
        edges,
        viewport: getViewport(),
        features,
        envVariables,
        runtimeVariables,
      });
      const validation = validateWorkflowGraph(graphSnapshot);

      if (!validation.ok) {
        setValidationErrors(validation.errors);
        failTestExecution(
          '워크플로우 연결에 문제가 있어 테스트를 실행하지 않았습니다.',
        );
        toast.error('실행 전 그래프 검증 실패');
        setPreflightStatus('idle');
        return;
      }

      setPreflightStatus('saving');

      releaseTestPreflightSave = tryAcquireWorkflowDraftSave(
        activeWorkflowId,
        'test_preflight',
      );
      if (!releaseTestPreflightSave) {
        toast.info(
          '다른 Workflow 저장이 진행 중입니다. 완료 후 다시 시도해주세요.',
        );
        setPreflightStatus('idle');
        return;
      }

      try {
        const canonical = await workflowApi.getDraftWorkflow(activeWorkflowId);
        const currentState = useWorkflowStore.getState();
        if (
          currentState.isAgentBuilderMutationSaving ||
          hasPendingAgentBuilderAcknowledgement(currentState.undoStack)
        ) {
          const message =
            'Agent Builder 저장이 시작되어 테스트 실행을 중단했습니다. 저장 완료 후 다시 시도해주세요.';
          failTestExecution(message);
          toast.info(message);
          return;
        }

        const snapshotMatchesCanonical = canonicalDraftMatchesSnapshot(
          canonical,
          graphSnapshot,
        );
        const localBase = currentState.getCanonicalDraftMetadata(
          activeWorkflowId,
        );
        // 일부 편집 경로에서 dirty 표시보다 graph 변경이 먼저 관측될 수 있다.
        // 서버가 마지막으로 읽은 기준점에 그대로 있을 때만 현재 snapshot을
        // CAS 저장해 테스트를 계속하고, 실제 동시 변경은 아래에서 차단한다.
        const shouldSave =
          currentState.hasUnsavedChanges || !snapshotMatchesCanonical;
        if (!shouldSave) {
          currentState.ingestCanonicalDraftMetadata(
            canonical,
            activeWorkflowId,
          );
        } else {
          if (
            !localBase ||
            canonical.graph_hash !== localBase.graphHash ||
            !workflowDraftTimestampsEqual(
              canonical.updated_at,
              localBase.updatedAt,
            )
          ) {
            const message =
              '서버의 Workflow가 현재 편집 기준보다 앞서 있습니다. 최신 상태를 불러온 뒤 다시 시도해주세요.';
            failTestExecution(message);
            toast.error(message);
            return;
          }
          const saveResponse = await workflowApi.syncDraftWorkflow(
            activeWorkflowId,
            {
              ...buildWorkflowDraftPayload(
                graphSnapshot,
                graphSnapshot.viewport,
              ),
              expected_graph_hash: localBase.graphHash,
              expected_updated_at: localBase.updatedAt,
            },
          );
          useWorkflowStore
            .getState()
            .ingestCanonicalDraftMetadata(saveResponse, activeWorkflowId);

          const latestState = useWorkflowStore.getState();
          const latestSnapshot = cloneDraft({
            nodes: latestState.nodes,
            edges: latestState.edges,
            viewport: graphSnapshot.viewport,
            features: latestState.features,
            envVariables: latestState.envVariables,
            runtimeVariables: latestState.runtimeVariables,
          });
          if (workflowDraftSnapshotsEqual(latestSnapshot, graphSnapshot)) {
            latestState.setHasUnsavedChanges(false);
          }
        }

        const latestWorkflowState = useWorkflowStore.getState();
        if (
          latestWorkflowState.isAgentBuilderMutationSaving ||
          hasPendingAgentBuilderAcknowledgement(
            latestWorkflowState.undoStack,
          )
        ) {
          const message =
            'Agent Builder 저장이 시작되어 테스트 실행을 중단했습니다. 저장 완료 후 다시 시도해주세요.';
          failTestExecution(message);
          toast.info(message);
          return;
        }
      } catch (saveError) {
        const isOperationEnvelopeMissing =
          getHttpStatus(saveError) === 409 &&
          getHttpDetail(saveError) === 'operation envelope not found';
        if (isOperationEnvelopeMissing) {
          const sessionId = latestAgentBuilderSessionId();
          let classification: OperationRecoveryClassification | null = null;
          let recoveredAppliedSave = false;
          try {
            if (!sessionId) throw new Error('agent builder session unavailable');
            const session = await agentBuilderApi.getSession(sessionId);
            const canonical =
              await workflowApi.getDraftWorkflow(activeWorkflowId);
            classification = operationRecoveryClassification(session, canonical);
            recoveredAppliedSave =
              classification === 'applied' &&
              canonicalDraftMatchesSnapshot(canonical, graphSnapshot);
            if (recoveredAppliedSave) {
              useWorkflowStore
                .getState()
                .ingestCanonicalDraftMetadata(canonical, activeWorkflowId);
            }
          } catch {
            classification = null;
          }
          if (!recoveredAppliedSave) {
            const message = operationRecoveryFailureMessage(
              classification === 'applied' ? 'stale' : classification,
            );
            failTestExecution(message);
            toast.error(message);
            setPreflightStatus('idle');
            return;
          }
        } else {
          const isStaleGraph =
            getHttpStatus(saveError) === 409 &&
            getHttpDetail(saveError) === 'stale_graph';
          let recoveredAppliedSave = false;
          if (isStaleGraph) {
            try {
              const canonical =
                await workflowApi.getDraftWorkflow(activeWorkflowId);
              recoveredAppliedSave = canonicalDraftMatchesSnapshot(
                canonical,
                graphSnapshot,
              );
              if (recoveredAppliedSave) {
                useWorkflowStore
                  .getState()
                  .ingestCanonicalDraftMetadata(canonical, activeWorkflowId);
              }
            } catch {
              recoveredAppliedSave = false;
            }
          }
          if (!recoveredAppliedSave) {
            const message = testPreflightSaveErrorMessage(saveError);
            failTestExecution(message);
            toast.error(message);
            setPreflightStatus('idle');
            return;
          }
        }
      }

      setPreflightStatus('idle');

      // 파일 업로드 처리
      if (hasFiles) {
        setTestUploading(true);
        try {
          for (const [key, file] of Object.entries(files)) {
            if (file) {
              const presignedData = await knowledgeApi.getPresignedUploadUrl(
                file.name,
                file.type || 'application/octet-stream',
              );

              await knowledgeApi.uploadToS3(
                presignedData.upload_url,
                file,
                file.type || 'application/octet-stream',
              );

              const s3Url = presignedData.upload_url.split('?')[0];
              finalInputs[key] = s3Url;
            }
          }
        } catch (uploadError: any) {
          toast.error(`파일 업로드 실패: ${uploadError.message}`);
          failTestExecution('파일 업로드 실패');
          return;
        }
        setTestUploading(false);
      }

      // 1. 실행 표시만 초기화하고 저장 대상 graph는 변경하지 않는다.
      resetNodeExecutionData();

      let finalResult: any = null;

      // 2. 스트리밍 실행 (기억모드 플래그 적용)
      const inputsWithMemory = appendMemoryFlag
        ? appendMemoryFlag(finalInputs)
        : finalInputs;
      const abortController = new AbortController();
      let streamIdleTimeout: ReturnType<typeof setTimeout> | null = null;
      let streamTimedOut = false;
      const resetStreamIdleTimeout = () => {
        if (streamIdleTimeout) clearTimeout(streamIdleTimeout);
        streamIdleTimeout = setTimeout(() => {
          streamTimedOut = true;
          abortController.abort();
        }, STREAM_IDLE_TIMEOUT_MS);
      };

      try {
        resetStreamIdleTimeout();
        const releaseForStart = releaseTestPreflightSave;
        if (!releaseForStart) {
          throw new Error('테스트 실행 준비 잠금을 확인할 수 없습니다.');
        }
        const executionStart = startWorkflowExecutionFromPreflight(
          activeWorkflowId,
          releaseForStart,
          () => {
            beginTestExecution();
            return workflowApi.executeWorkflowStream(
              activeWorkflowId,
              inputsWithMemory as Record<string, any>,
              async (event) => {
            resetStreamIdleTimeout();
            const { type, data } = event;

            if (type === 'workflow_start') {
              if (
                typeof data?.run_id !== 'string' ||
                data.run_id.trim() === ''
              ) {
                throw new Error(
                  '테스트 실행 식별자를 확인할 수 없습니다.',
                );
              }
              setTestExecutionRunId(data.run_id);
              replaceTestExecutionLocation(data.run_id, null);
              releaseTestPreflightSave?.();
              releaseTestPreflightSave = null;
              return;
            } else if (type === 'node_start') {
              // 노드 상태 변화만 짧게 늦춰 시각적 피드백을 유지한다.
              await new Promise((resolve) => setTimeout(resolve, 500));
              nodeStartedAtRef.current[data.node_id] = performance.now();
              setCurrentExecutingNode(data.node_id);
              updateNodeExecutionData(data.node_id, {
                status: 'running',
                observability: buildObservability(null, 'running'),
              });

              // 실행 중인 노드로 화면 중심 이동 및 줌인
              const latestNodes = useWorkflowStore.getState().nodes;
              const currentNode = latestNodes.find(
                (n) => n.id === data.node_id,
              );
              if (currentNode) {
                setCenter(
                  currentNode.position.x +
                    (currentNode.measured?.width || 200) / 2,
                  currentNode.position.y +
                    (currentNode.measured?.height || 100) / 2,
                  { zoom: 1.2, duration: 800 },
                );
              }
            } else if (type === 'node_finish') {
              // 노드 상태 변화만 짧게 늦춰 시각적 피드백을 유지한다.
              await new Promise((resolve) => setTimeout(resolve, 500));
              const startedAt = nodeStartedAtRef.current[data.node_id];
              const fallbackLatencyMs = startedAt
                ? Math.round(performance.now() - startedAt)
                : undefined;
              const metrics = readNodeFinishExecutionSummary(
                data,
                fallbackLatencyMs,
              );
              updateNodeExecutionData(data.node_id, {
                status: 'success',
                observability: buildObservability(
                  data.output,
                  'success',
                  metrics.latencyMs,
                  metrics.totalTokens,
                  metrics.totalCost,
                ),
              });

              // 노드 실행 완료 토스트

              // 노드 결과 누적
              addTestNodeResult({
                nodeId: data.node_id,
                nodeType: data.node_type,
                output: data.output,
                traceMetadata:
                  data.trace_metadata && typeof data.trace_metadata === 'object'
                    ? data.trace_metadata
                    : undefined,
                title: getNodeDisplayName(data.node_id),
                status: 'success',
                latencyMs: metrics.latencyMs,
                totalTokens: metrics.totalTokens,
                totalCost: metrics.totalCost,
              });
            } else if (type === 'workflow_finish') {
              // 최종 결과를 즉시 반영해 완료 시점을 늦추지 않는다.
              finalResult = data;
            } else if (type === 'error') {
              // 오류는 즉시 보여야 사용자가 재실행 여부를 판단할 수 있다.
              if (data.node_id) {
                const startedAt = nodeStartedAtRef.current[data.node_id];
                const latencyMs = startedAt
                  ? Math.round(performance.now() - startedAt)
                  : undefined;
                updateNodeExecutionData(data.node_id, {
                  status: 'failure',
                  observability: buildObservability(null, 'failure', latencyMs),
                });
                addTestNodeResult({
                  nodeId: data.node_id,
                  nodeType: data.node_type || 'node',
                  output: {},
                  title: getNodeDisplayName(data.node_id),
                  status: 'failure',
                  latencyMs,
                });
              }
              toast.error(`모듈 실행 실패: ${data.message}`);
              throw new Error(data.message);
            }
              },
              {
                signal: abortController.signal,
                graphSnapshot,
                // 테스트 결과는 운영 정책 학습에 포함하지 않지만, 현재 draft와
                // 같은 활성 배포 정책은 실제 실행처럼 평가해 확인한다.
                useActiveDeploymentRoutingPolicy: true,
              },
            );
          },
        );
        if (!executionStart.started) {
          releaseTestPreflightSave = null;
          const message =
            'Workflow 변경사항을 저장하는 중입니다. 저장 완료 후 다시 실행해주세요.';
          failTestExecution(message);
          toast.info(message);
          return;
        }
        await executionStart.value;
      } catch (streamError) {
        if (streamTimedOut) {
          throw new Error(
            '실행 이벤트가 60초 이상 도착하지 않았습니다. 실행 엔진 또는 Redis 스트림 상태를 확인해주세요.',
          );
        }
        throw streamError;
      } finally {
        if (streamIdleTimeout) clearTimeout(streamIdleTimeout);
      }

      // 최종 결과 저장
      if (finalResult) {
        finishTestExecution(finalResult);
      } else {
        finishTestExecution({});
      }
    } catch (err: any) {
      console.error('Execution failed:', err);
      const budgetExceeded = isBudgetExceededError(err);
      const message = budgetExceeded
        ? BUDGET_EXCEEDED_MESSAGE
        : deploymentApiErrorMessage(
            err,
            err.message || '실행 중 오류가 발생했습니다.',
          );
      failTestExecution(message);
      toast.error(message);
    } finally {
      releaseTestPreflightSave?.();
      releaseTestPreflightSave = null;
      setTestUploading(false);
      setPreflightStatus('idle');
    }
  };

  const handleReset = () => {
    selectExecutionNode(null);
    setComparisonSelectedNodeId(null);
    restoredRunRef.current = null;
    setTestRunRestoreState('idle');
    replaceTestExecutionLocation(null, null, { comparisonNodeId: null });
    setValidationErrors([]);
    setPreflightStatus('idle');
    resetTestExecution();
  };

  const handleComparisonBaselineRunIdChange = (runId: string | null) => {
    setComparisonBaselineRunId(runId);
    setComparisonSelectedNodeId(null);
    replaceTestExecutionLocation(testExecutionRunId, testSelectedNodeId, {
      baselineRunId: runId,
      comparisonNodeId: null,
    });
  };

  const handleComparisonSelectedNodeIdChange = (nodeId: string | null) => {
    if (nodeId && testExecutionContentRef.current) {
      testExecutionContentRef.current.scrollTop = 0;
    }
    setComparisonSelectedNodeId(nodeId);
    replaceTestExecutionLocation(testExecutionRunId, testSelectedNodeId, {
      comparisonNodeId: nodeId,
    });
  };

  const getVariableDisplayName = (variable: WorkflowVariable) =>
    variable.label?.trim() || variable.name;

  return (
    <div
      data-testid="test-execution-sidebar"
      className="absolute top-2 right-2 bottom-2 z-50 flex min-w-0 flex-col rounded-xl border-l border-gray-200 bg-white text-lg shadow-xl animate-in slide-in-from-right duration-200 [&_.text-2xl]:text-3xl [&_.text-base]:text-lg [&_.text-lg]:text-xl [&_.text-sm]:text-base [&_.text-xl]:text-2xl [&_.text-xs]:text-sm dark:border-gray-800 dark:bg-gray-900"
      style={{ width: `${renderedTestSidebarWidth}px` }}
    >
      {canResizeTestSidebar && (
        <div
          role="separator"
          aria-label="테스트 실행 패널 너비 조절"
          aria-orientation="vertical"
          aria-valuemin={minTestSidebarWidth}
          aria-valuemax={maxTestSidebarWidth}
          aria-valuenow={renderedTestSidebarWidth}
          tabIndex={0}
          onPointerDown={handleTestSidebarResizeStart}
          onKeyDown={handleTestSidebarResizeKeyDown}
          className="group absolute inset-y-0 -left-1 z-10 w-3 touch-none cursor-col-resize outline-none"
        >
          <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-blue-300 group-focus-visible:bg-blue-500" />
        </div>
      )}
      {/* Header */}
      <div className="border-b border-gray-200 px-6 py-4 dark:border-gray-800">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
            <Play className="h-5 w-5 text-blue-600" />
            테스트 실행
          </h2>
          <button
            onClick={toggleTestPanel}
            className="rounded-full p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-3 grid grid-cols-2 rounded-lg border border-gray-200 bg-gray-50 p-1 dark:border-gray-700 dark:bg-gray-800">
          <button
            type="button"
            aria-pressed={!isComparisonMode}
            onClick={() => {
              setIsComparisonMode(false);
              replaceTestExecutionLocation(
                testExecutionRunId,
                testSelectedNodeId,
                { comparisonMode: false },
              );
            }}
            className={`rounded-md px-3 py-2 text-xs font-semibold transition-colors ${
              !isComparisonMode
                ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-900 dark:text-gray-100'
                : 'text-gray-500 hover:text-gray-800 dark:text-gray-300'
            }`}
          >
            단일 결과
          </button>
          <button
            type="button"
            aria-pressed={isComparisonMode}
            onClick={() => {
              setHasOpenedComparisonPanel(true);
              setIsComparisonMode(true);
              setComparisonListReloadKey((value) => value + 1);
              replaceTestExecutionLocation(
                testExecutionRunId,
                testSelectedNodeId,
                { comparisonMode: true },
              );
            }}
            className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-semibold transition-colors ${
              isComparisonMode
                ? 'bg-white text-blue-700 shadow-sm dark:bg-gray-900 dark:text-blue-300'
                : 'text-gray-500 hover:text-gray-800 dark:text-gray-300'
            }`}
          >
            <GitCompareArrows className="h-3.5 w-3.5" /> 실행 비교
          </button>
        </div>
      </div>

      {/* Content */}
      <div
        ref={testExecutionContentRef}
        data-testid="test-execution-content"
        className="flex-1 overflow-y-auto p-6"
      >
        {testRunRestoreState === 'restoring' && !isExecuting ? (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300">
            <Loader2 className="h-4 w-4 animate-spin" />
            저장된 테스트 실행 기록을 불러오는 중입니다.
          </div>
        ) : null}
        {testRunRestoreState === 'delayed' ||
        testRunRestoreState === 'failed' ? (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            <p>
              {testRunRestoreState === 'delayed'
                ? '실행 기록이 아직 준비되지 않았습니다.'
                : '실행 기록을 불러오는 중 오류가 발생했습니다.'}
            </p>
            <button
              type="button"
              onClick={() => {
                restoredRunRef.current = null;
                setTestRunRestoreState('idle');
                setTestRunRestoreRetry((value) => value + 1);
              }}
              className="shrink-0 rounded-md border border-amber-300 bg-white px-2.5 py-1.5 font-semibold text-amber-800 hover:bg-amber-100 dark:bg-gray-900 dark:text-amber-200"
            >
              다시 시도
            </button>
          </div>
        ) : null}
        {hasOpenedComparisonPanel && hasPersistedActiveWorkflow ? (
          <div className={isComparisonMode ? undefined : 'hidden'}>
            <ExecutionComparisonPanel
              workflowId={activeWorkflowId}
              nodes={nodes}
              baselineRunId={comparisonBaselineRunId}
              currentRunId={testExecutionRunId}
              currentExecutionStatus={testExecutionStatus}
              currentExecutionError={testExecutionError}
              reloadRequestKey={testRunRestoreRetry + comparisonListReloadKey}
              selectedNodeId={comparisonSelectedNodeId}
              onBaselineRunIdChange={handleComparisonBaselineRunIdChange}
              onSelectedNodeIdChange={handleComparisonSelectedNodeIdChange}
            />
          </div>
        ) : null}
        {isExecuting ? (
          /* Execution Progress - Show node results as they come in */
          <div className="space-y-4">
            {nodeExecutionSummaries.length > 0 ? (
              nodeExecutionSummaries.map(renderNodeExecutionSummary)
            ) : (
              <div
                data-testid="execution-progress-status"
                className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300"
              >
                <div className="flex items-center gap-2 font-medium">
                  <Loader2 className="h-4 w-4 animate-spin" />첫 노드 실행
                  결과를 기다리는 중입니다.
                </div>
                {isComparisonMode ? (
                  <p className="mt-2 pl-6 text-xs leading-5 text-blue-600 dark:text-blue-300">
                    실행이 완료되면 비교 결과를 준비합니다.
                  </p>
                ) : null}
              </div>
            )}
          </div>
        ) : !hasExecutionResult && !error ? (
          /* Input Form */
          <div className="space-y-6">
            {(isPreparing || isAgentBuilderSaveBlocking) && (
              <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
                <div className="flex items-center gap-2 font-medium">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {isAgentBuilderSaveBlocking
                    ? agentBuilderSaveBlockingMessage
                    : preflightStatus === 'validating'
                      ? '워크플로우 연결을 검증하는 중입니다.'
                      : '현재 워크플로우를 저장하는 중입니다.'}
                </div>
              </div>
            )}
            <div>
              <h3 className="text-sm font-medium text-gray-900 mb-4 dark:text-gray-200">
                입력 변수 설정
              </h3>

              {variables.length === 0 ? (
                <div className="text-center py-8 text-gray-500 bg-gray-50 rounded-lg dark:bg-gray-800">
                  입력 변수가 없는 모듈입니다.
                  <br />
                  바로 실행할 수 있습니다.
                </div>
              ) : startNode?.type === 'webhookTrigger' ? (
                /* 웹훅 전용 UI */
                <div className="space-y-3">
                  {!(startNode.data as any).captured_payload && (
                    <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg dark:bg-blue-900/20 dark:border-blue-800">
                      <p className="text-xs text-blue-700 dark:text-blue-300 leading-relaxed">
                        캡처된 데이터가 없습니다. 웹훅 트리거의{' '}
                        <strong>[캡처 시작]</strong> 기능을 사용하면 실제
                        데이터가 테스트 입력에 자동으로 채워집니다.
                      </p>
                    </div>
                  )}

                  {(startNode.data as any).captured_payload && (
                    <div className="p-3 bg-green-50 border border-green-200 rounded-lg dark:bg-green-900/20 dark:border-green-800">
                      <p className="text-xs text-green-700 dark:text-green-300 leading-relaxed">
                        ✅ 최신 데이터가 자동으로 로드되었습니다. 캡처된
                        Payload를 확인하고 바로 테스트를 진행해 보세요.
                      </p>
                    </div>
                  )}

                  <div className="flex items-center justify-between">
                    {(startNode.data as any).captured_payload && (
                      <span className="text-xs text-green-600 dark:text-green-400">
                        {/* ✓ 캡처된 데이터 사용 중 (메시지로 대체됨) */}
                      </span>
                    )}
                  </div>

                  <div>
                    <textarea
                      value={inputs['__json_payload__'] || ''}
                      onChange={(e) =>
                        handleChange('__json_payload__', e.target.value)
                      }
                      className={testJsonTextAreaClassName}
                      placeholder='webhook payload: {"user": "john", "action": "signup"}'
                    />
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  {variables.map((variable) => (
                    <div key={variable.id}>
                      {variable.type === 'checkbox' ? (
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={inputs[variable.name] || false}
                            onChange={(e) =>
                              handleChange(variable.name, e.target.checked)
                            }
                            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                          />
                          <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                            {getVariableDisplayName(variable)}
                            {variable.required && (
                              <span className="text-red-500 ml-1">*</span>
                            )}
                          </span>
                        </label>
                      ) : variable.type === 'select' ? (
                        <>
                          <label className="block text-sm font-medium text-gray-700 mb-1 dark:text-gray-300">
                            {getVariableDisplayName(variable)}
                            {variable.required && (
                              <span className="text-red-500 ml-1">*</span>
                            )}
                          </label>
                          <select
                            value={inputs[variable.name] || ''}
                            onChange={(e) =>
                              handleChange(variable.name, e.target.value)
                            }
                            className={TEST_INPUT_CLASS_NAME}
                          >
                            {variable.options?.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </>
                      ) : variable.type === 'file' ? (
                        <>
                          <label className="block text-sm font-medium text-gray-700 mb-1 dark:text-gray-300">
                            {getVariableDisplayName(variable)}
                            {variable.required && (
                              <span className="text-red-500 ml-1">*</span>
                            )}
                          </label>
                          <input
                            type="file"
                            onChange={(e) => {
                              const file = e.target.files?.[0] || null;
                              setFiles((prev) => ({
                                ...prev,
                                [variable.name]: file,
                              }));
                            }}
                            className={TEST_INPUT_CLASS_NAME}
                          />
                        </>
                      ) : (
                        <>
                          <label className="block text-sm font-medium text-gray-700 mb-1 dark:text-gray-300">
                            {getVariableDisplayName(variable)}
                            {variable.required && (
                              <span className="text-red-500 ml-1">*</span>
                            )}
                          </label>
                          {variable.type === 'paragraph' ? (
                            <textarea
                              value={inputs[variable.name] || ''}
                              onChange={(e) =>
                                handleChange(variable.name, e.target.value)
                              }
                              className={testTextAreaClassName}
                              placeholder={variable.placeholder}
                            />
                          ) : (
                            <input
                              type={
                                variable.type === 'number' ? 'number' : 'text'
                              }
                              value={inputs[variable.name] || ''}
                              onChange={(e) =>
                                handleChange(
                                  variable.name,
                                  variable.type === 'number'
                                    ? Number(e.target.value)
                                    : e.target.value,
                                )
                              }
                              className={TEST_INPUT_CLASS_NAME}
                              placeholder={variable.placeholder}
                            />
                          )}
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : isComparisonMode ? null : (
          /* Execution Result */
          <div className="space-y-6">
            {error ? (
              <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-sm font-medium text-red-800">
                    실행 실패
                  </h3>
                  <p className="text-sm text-red-600 mt-1">{error}</p>
                  {validationErrors.length > 0 && (
                    <ul className="mt-3 space-y-1 text-sm text-red-700">
                      {validationErrors.slice(0, 5).map((issue, index) => (
                        <li key={`${issue.code}-${issue.edgeId || index}`}>
                          - {formatGraphIssue(issue)}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : (
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg flex items-start gap-3">
                <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-sm font-medium text-green-800">
                    실행 성공
                  </h3>
                  <p className="text-sm text-green-600 mt-1">
                    모듈이 성공적으로 실행되었습니다.
                  </p>
                </div>
              </div>
            )}

            {hasExecutionResult &&
              (selectedNodeExecutionSummary ? (
                renderNodeExecutionDetail(selectedNodeExecutionSummary)
              ) : (
                <div className="space-y-4">
                  {showFinalResponseCard && (
                    <div>
                      <FinalResponseCard preview={finalResponsePreview} />
                      <CitationList items={finalResponseCitations} />
                    </div>
                  )}
                  {renderExecutionTotalSummary()}
                  <h3 className="text-sm font-medium text-gray-900 mb-3 dark:text-gray-200">
                    노드별 실행 결과
                  </h3>
                  <div className="space-y-3">
                    {nodeExecutionSummaries.length > 0 ? (
                      nodeExecutionSummaries.map(renderNodeExecutionSummary)
                    ) : (
                      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800">
                        노드별 실행 결과가 없습니다.
                      </div>
                    )}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-gray-200 bg-gray-50 dark:bg-gray-900 dark:border-gray-800">
        {!hasExecutionResult && !error ? (
          <div>
            <button
              onClick={handleExecute}
              disabled={isExecuteActionDisabled}
              className="px-3 py-2 text-white bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 disabled:cursor-not-allowed rounded-lg transition-colors flex items-center justify-center gap-2 text-sm font-medium"
            >
              {isExecuting ||
              isTestUploading ||
              isPreparing ||
              isAgentBuilderSaveBlocking ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {isAgentBuilderSaveBlocking
                    ? isAgentBuilderAcknowledgementBlocking
                      ? 'Agent Builder 저장 결과 확인 중...'
                      : 'Agent Builder 저장 확인 중...'
                    : isTestUploading
                      ? '파일 업로드 중...'
                      : preflightStatus === 'validating'
                        ? '검증 중...'
                        : preflightStatus === 'saving'
                          ? '저장 중...'
                          : '테스트 실행 중...'}
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" />
                  {canExecute ? '테스트 실행하기' : '테스트 실행 권한 없음'}
                </>
              )}
            </button>
          </div>
        ) : (
          <button
            onClick={handleReset}
            className="w-full px-4 py-2 text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 rounded-lg transition-colors flex items-center justify-center gap-2 font-medium dark:bg-gray-800 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <RefreshCw className="w-4 h-4" />
            다시 테스트하기
          </button>
        )}
      </div>
    </div>
  );
}
