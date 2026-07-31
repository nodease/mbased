'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  CSSProperties,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  Loader2,
  Minus,
  Send,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { useReactFlow, type Edge } from '@xyflow/react';
import {
  agentBuilderApi,
  type AgentBuilderIntentModelOption,
  type AgentBuilderIntentModelProvider,
  type AgentBuilderIntentModelSelection,
  type AgentBuilderKnowledgeCandidateSelection,
  type AgentBuilderMessageResponse,
  type AgentBuilderGraphMutation,
  type AgentBuilderParameterGroup,
  type AgentBuilderParameterTask,
  type AgentBuilderSessionMessage,
} from '../../api/agentBuilderApi';
import { workflowApi } from '../../api/workflowApi';
import type { Node } from '../../types/Workflow';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { workflowDraftTimestampsEqual } from '../../utils/workflowDraftCAS';
import {
  WorkflowResultGroup,
  type WorkflowKnowledgeStep,
  type WorkflowSetupStatus,
} from './WorkflowResultGroup';
import { applyAndSaveAgentBuilderMutation } from './useAgentBuilderEditor';
import { useParameterTasks } from './useParameterTasks';
import { calculateAgentBuilderNodeFocusViewport } from './agentBuilderNodeFocus';
import {
  AGENT_BUILDER_KNOWLEDGE_SELECTION_FROM_NODE,
  type AgentBuilderNodeKnowledgeSelectionEventDetail,
} from './agentBuilderKnowledgeBridge';

type Props = {
  workflowId: string;
  appId?: string | null;
  nodes: Node[];
  edges: Edge[];
  hasUnsavedChanges: boolean;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
};

type ConversationItem =
  | { kind: 'user'; id: string; content: string }
  | { kind: 'assistant'; id: string; response: AgentBuilderMessageResponse };

const NO_KB_CANDIDATE_ID = '__agent_builder_no_kb__';
const SAFE_SERVER_ERROR_CODE = /^[a-z][a-z0-9_]{0,79}$/;
const SESSION_RECOVERY_DELAYS_MS = [1000, 2000, 4000] as const;
const AGENT_BUILDER_MIN_PANEL_WIDTH = 360;
const AGENT_BUILDER_VIEWPORT_GUTTER = 40;
const AGENT_BUILDER_RESIZE_KEYBOARD_STEP = 24;
const AGENT_BUILDER_DEFAULT_PANEL_WIDTH =
  'clamp(360px, 50vw, calc(100vw - 40px))';
const PENDING_REQUEST_NORMAL_POLL_MS = 5_000;
const PENDING_REQUEST_NORMAL_WINDOW_MS = 60_000;
const PENDING_REQUEST_LONG_POLL_MS = 10_000;
const PENDING_REQUEST_DEADLINE_MS = 4 * 60_000;
const KNOWLEDGE_REASON_LABELS: Record<string, string> = {
  topic_keyword_match: '\uC694\uCCAD \uC8FC\uC81C\uC640 \uC77C\uCE58',
  metadata_match:
    '\uBB38\uC11C \uBA54\uD0C0\uB370\uC774\uD130\uC640 \uC77C\uCE58',
  semantic_similarity: '\uB0B4\uC6A9 \uC720\uC0AC\uB3C4\uAC00 \uB192\uC74C',
  recent_usage: '\uCD5C\uADFC \uC0AC\uC6A9\uB41C Knowledge Base',
};

const secretParameterPatch = (
  task: AgentBuilderParameterTask,
  nodeData: Record<string, unknown>,
  value: string | undefined,
): Record<string, unknown> | null => {
  if (task.node_type === 'slackPostNode' && task.parameter_key === 'bot_token') {
    const currentAuthConfig =
      nodeData.authConfig &&
      typeof nodeData.authConfig === 'object' &&
      !Array.isArray(nodeData.authConfig)
        ? (nodeData.authConfig as Record<string, unknown>)
        : {};
    const authConfig = { ...currentAuthConfig };
    if (value === undefined) {
      delete authConfig.token;
    } else {
      authConfig.token = value;
    }
    return { authConfig };
  }
  if (task.node_type === 'slackPostNode' && task.parameter_key === 'url') {
    return { url: value };
  }
  if (task.node_type === 'githubNode' && task.parameter_key === 'api_token') {
    return { api_token: value };
  }
  return null;
};

export const isAgentBuilderSetupCompleted = (
  requestStatus: string | null | undefined,
  parameterGroup: AgentBuilderParameterGroup | null,
): boolean => {
  void parameterGroup;
  return requestStatus === 'completed';
};

const agentBuilderErrorCode = (error: unknown): string | null => {
  if (!error || typeof error !== 'object') return null;
  const clientCode = (error as { code?: unknown }).code;
  if (
    typeof clientCode === 'string' &&
    SAFE_SERVER_ERROR_CODE.test(clientCode)
  ) {
    return clientCode;
  }
  const response = (error as { response?: { data?: unknown } }).response;
  const data = response?.data;
  if (!data || typeof data !== 'object') return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string') {
    return SAFE_SERVER_ERROR_CODE.test(detail) ? detail : null;
  }
  if (detail && typeof detail === 'object') {
    const code = (detail as { code?: unknown }).code;
    return typeof code === 'string' && SAFE_SERVER_ERROR_CODE.test(code)
      ? code
      : null;
  }
  return null;
};

const agentBuilderErrorMessage = (error: unknown): string => {
  const code = agentBuilderErrorCode(error);
  switch (code) {
    case 'invalid_request':
      return '현재 Agent Builder에서는 대화 메시지로 Knowledge Base 선택을 제출할 수 없습니다. 표시된 Knowledge Base 선택 화면에서 선택해주세요.';
    case 'stale_protocol':
      return '이전 Agent Builder 세션을 새 세션으로 전환하지 못했습니다. 다시 시도해주세요.';
    case 'workflow_context_required':
      return '현재 workflow 정보를 확인할 수 없습니다. 편집기를 새로고침한 뒤 다시 시도해주세요.';
    case 'workflow_context_changed':
      return 'Workflow가 전환되어 이전 Agent Builder 작업을 적용하지 않았습니다.';
    case 'task_conflict':
      return '다른 설정 변경이 먼저 반영되었습니다. 최신 상태를 확인한 뒤 다시 시도해주세요.';
    case 'stale_graph':
      return 'workflow가 변경되어 요청을 적용할 수 없습니다. 최신 상태를 확인해주세요.';
    case 'stale_workflow_updated_at':
      return 'workflow 저장 시점이 달라 요청을 적용할 수 없습니다. 최신 상태를 확인해주세요.';
    case 'result_graph_hash_mismatch':
    case 'revert_graph_hash_mismatch':
      return '생성된 workflow와 서버 검증 결과가 일치하지 않아 저장하지 않았습니다.';
    case 'operation_payload_unavailable':
      return '이전 Agent Builder 작업 내용을 복구할 수 없습니다. 요청을 다시 입력해주세요.';
    case 'mutation_context_required':
      return 'Agent Builder 저장 정보가 누락되어 요청을 적용하지 않았습니다.';
  }
  const response = (
    error as { response?: { status?: unknown }; isAxiosError?: unknown }
  )?.response;
  const status = typeof response?.status === 'number' ? response.status : null;
  if (status && [502, 503, 504].includes(status)) {
    return `Agent Builder 서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요. (HTTP ${status})`;
  }
  if (status === 401) {
    return '로그인 세션이 만료되었습니다. 다시 로그인해주세요. (HTTP 401)';
  }
  if (status === 403) {
    return 'Agent Builder를 사용할 권한이 없습니다. (HTTP 403)';
  }
  if (status === 404) {
    return 'Agent Builder 세션 또는 workflow를 찾을 수 없습니다. (HTTP 404)';
  }
  if (status === 409) {
    return 'Agent Builder 작업이 현재 workflow 상태와 충돌했습니다. 최신 상태를 확인해주세요. (HTTP 409)';
  }
  if (status === 400 || status === 422) {
    return `Agent Builder 요청 형식이 올바르지 않습니다. (HTTP ${status})`;
  }
  if (code) {
    return `Agent Builder 요청이 거부되었습니다. (오류 코드: ${code})`;
  }
  if (
    error &&
    typeof error === 'object' &&
    (error as { isAxiosError?: unknown }).isAxiosError === true &&
    !response
  ) {
    return 'Agent Builder 서버와 통신할 수 없습니다. 네트워크 상태를 확인해주세요.';
  }
  if (status) {
    return `Agent Builder 요청에 실패했습니다. (HTTP ${status})`;
  }
  return 'Agent Builder 요청 처리 중 오류가 발생했습니다.';
};

const knowledgeSelectionErrorMessage = (error: unknown): string => {
  const code = agentBuilderErrorCode(error);
  if (code === 'knowledge_selection_stale') {
    return 'Knowledge 후보가 변경되어 최신 목록으로 갱신했습니다. 다시 선택해주세요.';
  }
  if (code === 'workflow_context_changed') {
    return 'Workflow가 전환되어 이전 Knowledge 선택을 적용하지 않았습니다.';
  }
  if (code === 'permission_denied' || code === 'knowledge_access_denied') {
    return 'Knowledge Base 사용 권한이 변경되었습니다. 목록을 확인한 뒤 다시 선택해주세요.';
  }
  if (
    code === 'stale_graph' ||
    code === 'stale_workflow_updated_at' ||
    code === 'task_conflict'
  ) {
    return 'Workflow 상태가 변경되었습니다. 현재 선택은 유지되며 최신 상태를 확인한 뒤 다시 적용할 수 있습니다.';
  }
  if (isRecoverableTransportError(error)) {
    return '저장 결과를 아직 확인하지 못했습니다. 현재 선택은 유지됩니다. 잠시 후 같은 버튼으로 다시 시도해주세요.';
  }
  return 'Knowledge Base 선택을 적용하지 못했습니다. 현재 선택은 유지됩니다. 다시 시도해주세요.';
};

const isRecoverableTransportError = (error: unknown): boolean => {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as {
    isAxiosError?: unknown;
    response?: { status?: unknown };
  };
  if (candidate.isAxiosError !== true) return false;
  const status = candidate.response?.status;
  return status === undefined || (typeof status === 'number' && status >= 500);
};

const shouldDiscardStoredSession = (error: unknown): boolean => {
  const code = agentBuilderErrorCode(error);
  if (
    code === 'stale_protocol' ||
    code === 'session_not_found' ||
    code === 'invalid_session'
  ) {
    return true;
  }
  const response = (error as { response?: { status?: unknown } })?.response;
  return response?.status === 404;
};

const mutationWithBaseHash = (
  mutation: AgentBuilderGraphMutation,
): AgentBuilderGraphMutation & { base_graph_hash: string } => {
  if (!mutation.base_graph_hash) {
    throw new Error('Agent Builder mutation base hash is missing');
  }
  return mutation as AgentBuilderGraphMutation & { base_graph_hash: string };
};

const isAgentBuilderMessageResponse = (
  value: unknown,
): value is AgentBuilderMessageResponse => {
  return (
    Boolean(value) &&
    typeof value === 'object' &&
    typeof (value as AgentBuilderMessageResponse).request_id === 'string' &&
    typeof (value as AgentBuilderMessageResponse).status === 'string'
  );
};

const conversationItemsFromSessionMessages = (
  messages: AgentBuilderSessionMessage[],
): {
  responses: AgentBuilderMessageResponse[];
  conversationItems: ConversationItem[];
} => {
  const responses: AgentBuilderMessageResponse[] = [];
  const conversationItems: ConversationItem[] = [];

  messages.forEach((message, index) => {
    if (isAgentBuilderMessageResponse(message)) {
      responses.push(message);
      conversationItems.push({
        kind: 'assistant',
        id: `assistant-${message.request_id}`,
        response: message,
      });
      return;
    }
    if (message.kind === 'user') {
      conversationItems.push({
        kind: 'user',
        id: `user-${message.request_id}-${index}`,
        content: message.content,
      });
      return;
    }
    if (message.kind === 'assistant' && message.response) {
      responses.push(message.response);
      conversationItems.push({
        kind: 'assistant',
        id: `assistant-${message.response.request_id}`,
        response: message.response,
      });
    }
  });

  return { responses, conversationItems };
};

const latestResponseRequestId = (
  responses: AgentBuilderMessageResponse[],
): string | null => responses.at(-1)?.request_id ?? null;

const safeAffectedNodeIds = (mutation: unknown): string[] => {
  if (!mutation || typeof mutation !== 'object') return [];
  const candidate = (mutation as { affected_node_ids?: unknown })
    .affected_node_ids;
  if (!Array.isArray(candidate)) return [];
  return Array.from(
    new Set(
      candidate.filter(
        (nodeId): nodeId is string =>
          typeof nodeId === 'string' && nodeId.length > 0,
      ),
    ),
  );
};

const lastUserMessageFromConversation = (items: ConversationItem[]) => {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === 'user') {
      return item.content;
    }
  }
  return '';
};

const isWorkflowNodeClarificationOption = (option: Record<string, unknown>) =>
  option.type === 'workflow_node' && typeof option.node_id === 'string';

const hasPendingKnowledgeResolution = (response: AgentBuilderMessageResponse) =>
  Boolean(response.knowledge_resolution) &&
  Boolean(
    formatClarificationOptionValue(
      response.knowledge_resolution?.resolution_id,
    ),
  ) &&
  (response.knowledge_resolution?.selection_status === 'unapplied' ||
    (response.knowledge_resolution?.selection_status !== 'pending_ack' &&
      response.knowledge_resolution?.selection_status !== 'completed' &&
      (response.knowledge_resolution?.selected ?? []).length === 0));

const latestKnowledgeClarification = (
  items: ConversationItem[],
): AgentBuilderMessageResponse | null => {
  const latestItem = items[items.length - 1];
  if (
    !latestItem ||
    latestItem.kind !== 'assistant' ||
    ['pending_ack', 'completed'].includes(
      latestItem.response.knowledge_resolution?.selection_status ?? '',
    ) ||
    !['clarification_required', 'graph_mutation_ready'].includes(
      latestItem.response.status,
    ) ||
    (!hasPendingKnowledgeResolution(latestItem.response) &&
      knowledgeOptionsFromResponse(latestItem.response).length === 0)
  ) {
    return null;
  }
  return latestItem.response;
};

const latestWorkflowTargetClarification = (
  items: ConversationItem[],
): AgentBuilderMessageResponse | null => {
  const latestItem = items[items.length - 1];
  if (
    !latestItem ||
    latestItem.kind !== 'assistant' ||
    latestItem.response.status !== 'clarification_required' ||
    !latestItem.response.clarification_options?.some(
      isWorkflowNodeClarificationOption,
    )
  ) {
    return null;
  }
  return latestItem.response;
};

function formatClarificationOptionValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return value.toFixed(2);
  return String(value);
}

const formatKnowledgeReasonLabel = (value: unknown): string | null =>
  typeof value === 'string' ? (KNOWLEDGE_REASON_LABELS[value] ?? null) : null;

const createLocalUserMessageId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `user-${crypto.randomUUID()}`;
  }
  return `user-${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

const knowledgeSelectionMessage = (
  selections: Array<
    AgentBuilderKnowledgeCandidateSelection & { label?: string | null }
  >,
  timing: 'before_graph' | 'after_graph',
) =>
  selections.length > 0
    ? `Knowledge Base \uC120\uD0DD: ${selections
        .map((candidate) => candidate.label || 'Knowledge Base')
        .join(', ')}`
    : timing === 'after_graph'
      ? 'Knowledge Base \uC5C6\uC774 \uACC4\uC18D'
      : 'Knowledge Base \uC5C6\uC774 \uC0DD\uC131';

const hierarchyKnowledgeSelectionMessage = (
  response: AgentBuilderMessageResponse,
  selection: { collectionHandles: string[]; kbHandles: string[] },
  timing: 'before_graph' | 'after_graph',
) => {
  const resolution = response.knowledge_resolution;
  const collectionLabels = selection.collectionHandles.map(
    (handle) =>
      resolution?.collections?.find(
        (collection) => collection.collection_handle === handle,
      )?.safe_label ?? 'Knowledge Collection',
  );
  const kbCandidates = [
    ...(resolution?.collections?.flatMap((collection) => collection.children) ??
      []),
    ...(resolution?.ungrouped_kbs ?? []),
  ];
  const kbLabels = Array.from(
    new Set(
      selection.kbHandles.map(
        (handle) =>
          kbCandidates.find((candidate) => candidate.kb_handle === handle)
            ?.safe_label ?? 'Knowledge Base',
      ),
    ),
  );
  if (collectionLabels.length === 0 && kbLabels.length === 0) {
    return timing === 'after_graph'
      ? 'Knowledge Base 없이 계속'
      : 'Knowledge Base 없이 생성';
  }
  return [
    collectionLabels.length > 0
      ? `Collection 선택: ${collectionLabels.join(', ')}`
      : null,
    kbLabels.length > 0 ? `Knowledge Base 선택: ${kbLabels.join(', ')}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(' / ');
};

const isNoKnowledgeBaseOption = (option: Record<string, unknown>) =>
  formatClarificationOptionValue(option.candidate_id) === NO_KB_CANDIDATE_ID ||
  formatClarificationOptionValue(option.type) === 'no_knowledge_base';

const knowledgeSelectionKey = (
  selection: AgentBuilderKnowledgeCandidateSelection,
) =>
  [
    selection.candidate_id,
    selection.resolution_id ?? '',
    selection.requirement_id ?? '',
  ].join(':');

const knowledgeOptionsFromResolution = (
  response: AgentBuilderMessageResponse,
) => {
  const resolution = response.knowledge_resolution;
  const retainsSelectionControl = ['pending_ack', 'unapplied'].includes(
    resolution?.selection_status ?? '',
  );
  if (
    !resolution ||
    ((resolution.selected ?? []).length > 0 && !retainsSelectionControl)
  ) {
    return [];
  }
  const resolutionId = formatClarificationOptionValue(resolution.resolution_id);
  return (resolution.candidates ?? [])
    .map((candidate, index) => {
      const option = candidate as Record<string, unknown>;
      if (isNoKnowledgeBaseOption(option)) return null;
      const candidateId = formatClarificationOptionValue(option.candidate_id);
      if (!candidateId) return null;
      const selection: AgentBuilderKnowledgeCandidateSelection & {
        label?: string | null;
      } = {
        candidate_id: candidateId,
        resolution_id:
          formatClarificationOptionValue(option.resolution_id) ??
          resolutionId ??
          null,
        requirement_id:
          formatClarificationOptionValue(option.requirement_id) ?? null,
        label:
          formatClarificationOptionValue(option.safe_label) ??
          formatClarificationOptionValue(option.label),
      };
      const rawScore = option.score;
      const rawConfidence = option.confidence;
      return {
        selection,
        selectionId:
          typeof option.selection_id === 'string'
            ? option.selection_id
            : knowledgeSelectionKey(selection),
        label: selection.label ?? `Knowledge Base \uD6C4\uBCF4 ${index + 1}`,
        score:
          typeof rawScore === 'number' && Number.isFinite(rawScore)
            ? rawScore
            : null,
        confidence:
          typeof rawConfidence === 'number' && Number.isFinite(rawConfidence)
            ? rawConfidence
            : null,
        reason: formatKnowledgeReasonLabel(option.reason_category),
      };
    })
    .filter((option): option is NonNullable<typeof option> => Boolean(option));
};

const knowledgeOptionsFromResponse = (response: AgentBuilderMessageResponse) =>
  knowledgeOptionsFromResolution(response);

const clampAgentBuilderPanelWidth = (
  width: number,
  viewportWidth: number,
): number => {
  const maxWidth = Math.max(0, viewportWidth - AGENT_BUILDER_VIEWPORT_GUTTER);
  const minWidth = Math.min(AGENT_BUILDER_MIN_PANEL_WIDTH, maxWidth);
  return Math.min(Math.max(width, minWidth), maxWidth);
};

const hasKnowledgeHierarchyOptions = (response: AgentBuilderMessageResponse) =>
  (response.knowledge_resolution?.collections?.length ?? 0) > 0 ||
  (response.knowledge_resolution?.ungrouped_kbs?.length ?? 0) > 0;

const hasKnowledgeSelectionOptions = (response: AgentBuilderMessageResponse) =>
  knowledgeOptionsFromResponse(response).length > 0 ||
  hasKnowledgeHierarchyOptions(response);

export function AgentBuilderPanel({
  workflowId,
  appId,
  nodes,
  hasUnsavedChanges,
  selectedNodeId,
  selectedEdgeId,
}: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [panelWidth, setPanelWidth] = useState<number | null>(null);
  const [viewportWidth, setViewportWidth] = useState<number | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [authoritativeRequestStatus, setAuthoritativeRequestStatus] = useState<
    string | null
  >(null);
  const [sessionProtocolVersion, setSessionProtocolVersion] = useState<
    'direct_edit_v1' | null
  >(null);
  const [input, setInput] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [, setResponses] = useState<AgentBuilderMessageResponse[]>([]);
  const [conversationItems, setConversationItems] = useState<
    ConversationItem[]
  >([]);
  const [currentResultRequestId, setCurrentResultRequestId] = useState<
    string | null
  >(null);
  const [parameterGroupRequestId, setParameterGroupRequestId] = useState<
    string | null
  >(null);
  const [recoveredRoutingContext, setRecoveredRoutingContext] = useState<{
    requestId: string | null;
    affectedNodeIds: string[];
  }>({ requestId: null, affectedNodeIds: [] });
  const [pendingRequestId, setPendingRequestId] = useState<string | null>(null);
  const [isPendingRequestSlow, setIsPendingRequestSlow] = useState(false);
  const [sessionRestoreAttempt, setSessionRestoreAttempt] = useState(0);
  const [sessionRecoveryRetryVersion, setSessionRecoveryRetryVersion] =
    useState(0);
  const [sessionRecoveryRequired, setSessionRecoveryRequired] = useState<
    'restore' | 'pending_request' | 'acknowledgement' | null
  >(null);
  const [secretConfigurationRecoveryRequired, setSecretConfigurationRecoveryRequired] =
    useState(false);
  const [secretConfigurationRetryVersion, setSecretConfigurationRetryVersion] =
    useState(0);
  const pendingSecretSkipRef = useRef<{
    taskId: string;
    canonicalDraftVersion: string | null;
  } | null>(null);
  const [knowledgeSelectionError, setKnowledgeSelectionError] = useState<
    string | null
  >(null);
  const [knowledgeSelectionResetVersion, setKnowledgeSelectionResetVersion] =
    useState(0);
  const [lastSubmittedMessage, setLastSubmittedMessage] = useState('');
  const [generationMode, setGenerationMode] = useState<
    'configure_and_generate' | 'structure_only'
  >('configure_and_generate');
  const [intentModelGroups, setIntentModelGroups] = useState<
    AgentBuilderIntentModelProvider[]
  >([]);
  const [selectedIntentModel, setSelectedIntentModel] =
    useState<AgentBuilderIntentModelOption | null>(null);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const recoveredParameterGroup = useWorkflowStore(
    (state) => state.recoveredAgentBuilderParameterGroup,
  );
  const clearRecoveredParameterGroup = useWorkflowStore(
    (state) => state.setRecoveredAgentBuilderParameterGroup,
  );
  const isPersistedMutationSaving = useWorkflowStore(
    (state) => state.isAgentBuilderMutationSaving,
  );
  const canonicalDraftVersion = useWorkflowStore((state) => {
    const metadata = state.canonicalDraftMetadata[workflowId];
    return metadata ? `${metadata.graphHash}:${metadata.updatedAt}` : null;
  });
  const agentBuilderHistoryNotice = useWorkflowStore(
    (state) => state.agentBuilderHistoryNotice,
  );
  const { fitView, getNode, getViewport, setCenter, setViewport } =
    useReactFlow();
  const {
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
  } = useParameterTasks({
    sessionId,
    workflowId,
    getViewport,
    onRequestStatusChange: setAuthoritativeRequestStatus,
  });

  const updateSecretParameter = useCallback(
    (task: AgentBuilderParameterTask, value: string | undefined) => {
      const state = useWorkflowStore.getState();
      const targetNode = state.nodes.find((node) => node.id === task.node_id);
      const nodeData =
        targetNode?.data && typeof targetNode.data === 'object'
          ? (targetNode.data as Record<string, unknown>)
          : null;
      const patch = nodeData
        ? secretParameterPatch(task, nodeData, value)
        : null;
      if (!patch) {
        toast.error('보안 설정을 저장할 Workflow 노드를 찾지 못했습니다.');
        return false;
      }
      state.updateNodeData(task.node_id, patch);
      return true;
    },
    [],
  );

  const submitSecretParameter = useCallback(
    async (task: AgentBuilderParameterTask, value: string) => {
      if (
        task.node_type !== 'slackPostNode' &&
        task.node_type !== 'githubNode'
      ) {
        toast.error('지원하지 않는 보안 설정입니다.');
        return false;
      }
      try {
        const result = await workflowApi.storeNodeSecret(workflowId, {
          node_id: task.node_id,
          node_type: task.node_type,
          parameter_key: task.parameter_key as
            | 'bot_token'
            | 'url'
            | 'api_token',
          secret_value: value,
        });
        if (useWorkflowStore.getState().activeWorkflowId !== workflowId) {
          toast.error(
            'Workflow가 전환되어 이전 보안 설정 결과를 적용하지 않았습니다.',
          );
          return false;
        }
        return updateSecretParameter(task, result.secret_reference);
      } catch {
        toast.error(
          '보안 설정을 저장하지 못했습니다. 입력값은 graph에 저장되지 않았습니다.',
        );
        return false;
      }
    },
    [updateSecretParameter, workflowId],
  );

  const clearSecretParameter = useCallback(
    (task: AgentBuilderParameterTask) => {
      if (!updateSecretParameter(task, undefined)) return;
      pendingSecretSkipRef.current = {
        taskId: task.task_id,
        canonicalDraftVersion,
      };
    },
    [canonicalDraftVersion, updateSecretParameter],
  );

  useEffect(() => {
    const pending = pendingSecretSkipRef.current;
    if (
      !pending ||
      !canonicalDraftVersion ||
      pending.canonicalDraftVersion === canonicalDraftVersion ||
      isApplying ||
      isPersistedMutationSaving
    ) {
      return;
    }
    const task = parameterGroup?.tasks.find(
      (candidate) => candidate.task_id === pending.taskId,
    );
    if (!task || task.status === 'skipped') {
      pendingSecretSkipRef.current = null;
      return;
    }
    if (task.status !== 'active' || task.required || task.input_type !== 'secret') {
      return;
    }
    pendingSecretSkipRef.current = null;
    void decideParameter({ taskId: task.task_id, action: 'skip' });
  }, [
    canonicalDraftVersion,
    decideParameter,
    isApplying,
    isPersistedMutationSaving,
    parameterGroup,
  ]);

  const synchronizeParameterGroup = useCallback(
    (
      nextParameterGroup: AgentBuilderParameterGroup | null,
      requestId: string | null,
      completionEligible = false,
    ) => {
      setParameterGroup(nextParameterGroup, completionEligible);
      setParameterGroupRequestId(nextParameterGroup ? requestId : null);
    },
    [setParameterGroup],
  );

  const storageKey = workflowId
    ? `agent-builder:workflow:${workflowId}`
    : `agent-builder:app:${appId ?? 'none'}`;
  const legacyStorageKey = `agent-builder:${workflowId}:${appId ?? 'none'}`;
  const scopeRef = useRef(storageKey);
  const conversationScrollRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const resizeCleanupRef = useRef<(() => void) | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const submitLockRef = useRef(false);
  const sessionCreationRef = useRef<Promise<string> | null>(null);
  const pendingSessionReconciliationRef = useRef<string | null>(null);
  const lastSecretConfigurationSyncRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      resizeCleanupRef.current?.();
    },
    [],
  );

  useEffect(() => {
    const updateViewportWidth = () => {
      const nextViewportWidth = window.innerWidth;
      setViewportWidth(nextViewportWidth);
      setPanelWidth((current) =>
        current === null
          ? null
          : clampAgentBuilderPanelWidth(current, nextViewportWidth),
      );
    };
    updateViewportWidth();
    window.addEventListener('resize', updateViewportWidth);
    return () => window.removeEventListener('resize', updateViewportWidth);
  }, []);

  const clearStoredSession = useCallback(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.removeItem(storageKey);
    window.localStorage.removeItem(legacyStorageKey);
  }, [legacyStorageKey, storageKey]);

  const retrySessionRecovery = useCallback(() => {
    setSessionRecoveryRequired(null);
    setSessionRestoreAttempt(0);
    setSessionRecoveryRetryVersion((value) => value + 1);
  }, []);

  const retrySecretConfigurationSync = useCallback(() => {
    lastSecretConfigurationSyncRef.current = null;
    setSecretConfigurationRecoveryRequired(false);
    setSecretConfigurationRetryVersion((value) => value + 1);
  }, []);

  const ensureSession = useCallback(
    async (forceNew = false) => {
      if (!forceNew && sessionId) return sessionId;
      if (!sessionCreationRef.current) {
        sessionCreationRef.current = agentBuilderApi
          .createSession({ workflowId, appId })
          .then((session) => {
            if (scopeRef.current === storageKey) {
              setSessionId(session.session_id);
              setSessionProtocolVersion(session.protocol_version ?? null);
              if (typeof window !== 'undefined') {
                window.localStorage.setItem(storageKey, session.session_id);
              }
            }
            return session.session_id;
          })
          .finally(() => {
            sessionCreationRef.current = null;
          });
      }
      return sessionCreationRef.current;
    },
    [appId, sessionId, storageKey, workflowId],
  );

  useEffect(() => {
    if (!isOpen) return;
    let isCanceled = false;
    agentBuilderApi
      .getModelOptions()
      .then((groups) => {
        if (isCanceled) return;
        setIntentModelGroups(groups);
        setSelectedIntentModel((current) => {
          const options = groups.flatMap((group) => group.options);
          const currentOption = current
            ? options.find(
                (option) =>
                  option.model.id === current.model.id &&
                  option.credential.id === current.credential.id,
              )
            : null;
          return currentOption ?? options[0] ?? null;
        });
      })
      .catch(() => {
        if (isCanceled) return;
        setIntentModelGroups([]);
        setSelectedIntentModel(null);
      });
    return () => {
      isCanceled = true;
    };
  }, [isOpen, storageKey]);

  const appendResponse = useCallback(
    (response: AgentBuilderMessageResponse) => {
      setCurrentResultRequestId(response.request_id);
      setResponses((items) =>
        items.some((item) => item.request_id === response.request_id)
          ? items.map((item) =>
              item.request_id === response.request_id ? response : item,
            )
          : [...items, response],
      );
      setConversationItems((items) => {
        const existingIndex = items.findIndex(
          (item) =>
            item.kind === 'assistant' &&
            item.response.request_id === response.request_id,
        );
        if (existingIndex < 0) {
          return [
            ...items,
            {
              kind: 'assistant',
              id: `assistant-${response.request_id}`,
              response,
            },
          ];
        }
        return items.map((item, index) =>
          index === existingIndex && item.kind === 'assistant'
            ? { ...item, response }
            : item,
        );
      });
    },
    [],
  );

  const beginSessionReconciliation = useCallback((nextSessionId: string) => {
    pendingSessionReconciliationRef.current = nextSessionId;
    setAuthoritativeRequestStatus('completion_confirming');
  }, []);

  const confirmAcknowledgedHistoryBoundary = useCallback(
    async (
      session: Awaited<ReturnType<typeof agentBuilderApi.getSession>>,
    ): Promise<boolean> => {
      const activeMutation = session.active_graph_mutation as
        | Record<string, unknown>
        | null
        | undefined;
      const operationId = activeMutation?.operation_id;
      const resultGraphHash = activeMutation?.result_graph_hash;
      const savedWorkflowUpdatedAt =
        activeMutation?.saved_workflow_updated_at;
      if (
        activeMutation?.status !== 'acknowledged' ||
        typeof operationId !== 'string' ||
        typeof resultGraphHash !== 'string' ||
        typeof savedWorkflowUpdatedAt !== 'string'
      ) {
        return false;
      }

      const state = useWorkflowStore.getState();
      const boundary = [...state.undoStack]
        .reverse()
        .find(
          (snapshot) =>
            snapshot.agentBuilderOperation?.operationId === operationId ||
            snapshot.agentBuilderHistory?.latestOperationId === operationId,
        );
      const persistedOperation = boundary?.agentBuilderOperation;
      if (
        !persistedOperation ||
        persistedOperation.sessionId !== session.session_id ||
        persistedOperation.resultGraphHash !== resultGraphHash ||
        !workflowDraftTimestampsEqual(
          persistedOperation.workflowUpdatedAt,
          savedWorkflowUpdatedAt,
        )
      ) {
        return false;
      }

      const canonical = await workflowApi.getDraftWorkflow(workflowId);
      if (
        (canonical.workflow_id && canonical.workflow_id !== workflowId) ||
        canonical.graph_hash !== resultGraphHash ||
        !workflowDraftTimestampsEqual(
          canonical.updated_at,
          savedWorkflowUpdatedAt,
        )
      ) {
        return false;
      }

      state.ingestCanonicalDraftMetadata(canonical, workflowId);
      state.markLatestAgentBuilderMutationAcknowledged(
        operationId,
        session.status === 'completed',
      );
      return true;
    },
    [workflowId],
  );

  const reconcileCanonicalSession = useCallback(
    (
      session: Awaited<ReturnType<typeof agentBuilderApi.getSession>>,
      options: { acknowledgedBoundaryConfirmed?: boolean } = {},
    ) => {
      const activeMutation = session.active_graph_mutation as
        Record<string, unknown> | null | undefined;
      const activeOperationId =
        typeof activeMutation?.operation_id === 'string'
          ? activeMutation.operation_id
          : null;
      const isLocallyAcknowledged = Boolean(
        activeOperationId &&
          useWorkflowStore.getState().undoStack.some((snapshot) => {
            const operationMatches =
              snapshot.agentBuilderOperation?.operationId ===
                activeOperationId ||
              snapshot.agentBuilderHistory?.latestOperationId ===
                activeOperationId;
            return (
              operationMatches &&
              snapshot.agentBuilderHistory?.acknowledged === true
            );
          }),
      );
      const isPendingAcknowledgement =
        activeMutation?.status === 'pending_ack' &&
        activeOperationId !== null;
      const isUnconfirmedAcknowledgement =
        activeMutation?.status === 'acknowledged' &&
        activeOperationId !== null &&
        !isLocallyAcknowledged &&
        !options.acknowledgedBoundaryConfirmed;
      setSessionProtocolVersion(session.protocol_version ?? null);
      const restored = conversationItemsFromSessionMessages(session.messages);
      const requestId = latestResponseRequestId(restored.responses);
      synchronizeParameterGroup(
        session.parameter_group ?? null,
        requestId,
        session.status === 'completed',
      );
      setRecoveredRoutingContext({
        requestId,
        affectedNodeIds: safeAffectedNodeIds(activeMutation),
      });
      setCurrentResultRequestId(requestId);
      setResponses(restored.responses);
      setConversationItems(restored.conversationItems);
      setLastSubmittedMessage(
        lastUserMessageFromConversation(restored.conversationItems),
      );
      const pendingRequestId = session.pending_request?.request_id;
      setPendingRequestId(
        typeof pendingRequestId === 'string' ? pendingRequestId : null,
      );
      setSessionRecoveryRequired(null);
      setSecretConfigurationRecoveryRequired(false);
      if (isPendingAcknowledgement || isUnconfirmedAcknowledgement) {
        beginSessionReconciliation(session.session_id);
      } else {
        pendingSessionReconciliationRef.current = null;
        setAuthoritativeRequestStatus(session.status);
      }
    },
    [beginSessionReconciliation, synchronizeParameterGroup],
  );

  useEffect(() => {
    if (
      !sessionId ||
      !canonicalDraftVersion ||
      isPersistedMutationSaving ||
      !parameterGroup?.tasks.some((task) => task.input_type === 'secret')
    ) {
      return;
    }
    const synchronizationKey = `${sessionId}:${canonicalDraftVersion}`;
    if (lastSecretConfigurationSyncRef.current === synchronizationKey) return;
    lastSecretConfigurationSyncRef.current = synchronizationKey;
    let isCanceled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const waitForRetry = (delayMs: number) =>
      new Promise<void>((resolve) => {
        timeoutId = setTimeout(resolve, delayMs);
      });
    const synchronize = async () => {
      const retryDelays = [0, 1_000, 2_000, 4_000];
      setSecretConfigurationRecoveryRequired(false);
      for (const delayMs of retryDelays) {
        if (delayMs > 0) await waitForRetry(delayMs);
        if (isCanceled) return;
        try {
          const session = await agentBuilderApi.getSession(sessionId);
          if (isCanceled) return;
          reconcileCanonicalSession(session);
          return;
        } catch {
          if (isCanceled) return;
        }
      }
      if (lastSecretConfigurationSyncRef.current === synchronizationKey) {
        lastSecretConfigurationSyncRef.current = null;
      }
      setSecretConfigurationRecoveryRequired(true);
    };
    void synchronize();
    return () => {
      isCanceled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
    };
  }, [
    canonicalDraftVersion,
    isPersistedMutationSaving,
    parameterGroup,
    reconcileCanonicalSession,
    secretConfigurationRetryVersion,
    sessionId,
  ]);

  useEffect(() => {
    if (scopeRef.current === storageKey) return;
    scopeRef.current = storageKey;
    setSessionId(null);
    setAuthoritativeRequestStatus(null);
    setSessionProtocolVersion(null);
    setInput('');
    setIsSubmitting(false);
    setIsMinimized(false);
    setResponses([]);
    setConversationItems([]);
    setCurrentResultRequestId(null);
    setParameterGroupRequestId(null);
    setRecoveredRoutingContext({ requestId: null, affectedNodeIds: [] });
    setPendingRequestId(null);
    setSessionRestoreAttempt(0);
    setSessionRecoveryRequired(null);
    setSessionRecoveryRetryVersion(0);
    setSecretConfigurationRecoveryRequired(false);
    setSecretConfigurationRetryVersion(0);
    setKnowledgeSelectionResetVersion(0);
    setLastSubmittedMessage('');
    setGenerationMode('configure_and_generate');
    pendingSessionReconciliationRef.current = null;
    lastSecretConfigurationSyncRef.current = null;
    resetParameterTasks();
    setIsModelMenuOpen(false);
  }, [resetParameterTasks, storageKey]);

  useEffect(() => {
    if (
      !sessionId ||
      !recoveredParameterGroup ||
      recoveredParameterGroup.sessionId !== sessionId
    ) {
      return;
    }
    setIsOpen(true);
    setIsMinimized(false);
    shouldAutoScrollRef.current = true;
    presentParameterGroup(recoveredParameterGroup.parameterGroup);
    clearRecoveredParameterGroup(null);
  }, [
    clearRecoveredParameterGroup,
    presentParameterGroup,
    recoveredParameterGroup,
    sessionId,
  ]);

  useEffect(() => {
    if (!isOpen || sessionId || typeof window === 'undefined') return;
    const storedSessionId =
      window.localStorage.getItem(storageKey) ??
      window.localStorage.getItem(legacyStorageKey);
    if (!storedSessionId) return;
    window.localStorage.setItem(storageKey, storedSessionId);
    let isCanceled = false;
    let recoveryTimeoutId: number | null = null;
    agentBuilderApi
      .getSession(storedSessionId)
      .then(async (session) => {
        if (isCanceled) return;
        setSessionRestoreAttempt(0);
        setSessionRecoveryRequired(null);
        if (
          session.status === 'stale_protocol' ||
          session.protocol_version === null
        ) {
          const restored = conversationItemsFromSessionMessages(
            session.messages,
          );
          clearStoredSession();
          setSessionId(null);
          setSessionProtocolVersion(null);
          setAuthoritativeRequestStatus(session.status);
          setResponses(restored.responses);
          setConversationItems(restored.conversationItems);
          setCurrentResultRequestId(
            latestResponseRequestId(restored.responses),
          );
          setParameterGroupRequestId(null);
          setRecoveredRoutingContext({ requestId: null, affectedNodeIds: [] });
          setLastSubmittedMessage(
            lastUserMessageFromConversation(restored.conversationItems),
          );
          setPendingRequestId(null);
          synchronizeParameterGroup(null, null);
          await ensureSession(true);
          return;
        }
        setSessionId(session.session_id);
        const canonicalSession = session;
        setSessionProtocolVersion(session.protocol_version ?? null);
        setGenerationMode(
          session.default_generation_mode ?? 'configure_and_generate',
        );
        const restoredParameterGroup = session.parameter_group ?? null;
        if (
          ['pending_ack', 'acknowledged'].includes(
            String(
              (
                canonicalSession.active_graph_mutation as Record<
                  string,
                  unknown
                > | null
              )?.status ?? '',
            ),
          )
        ) {
          beginSessionReconciliation(canonicalSession.session_id);
        } else {
          setAuthoritativeRequestStatus(canonicalSession.status);
        }
        setSessionProtocolVersion(canonicalSession.protocol_version ?? null);
        const restored = conversationItemsFromSessionMessages(
          canonicalSession.messages,
        );
        const requestId = latestResponseRequestId(restored.responses);
        synchronizeParameterGroup(
          restoredParameterGroup,
          requestId,
          canonicalSession.status === 'completed',
        );
        setRecoveredRoutingContext({
          requestId,
          affectedNodeIds: safeAffectedNodeIds(
            canonicalSession.active_graph_mutation,
          ),
        });
        setCurrentResultRequestId(requestId);
        setResponses(restored.responses);
        setConversationItems(restored.conversationItems);
        setLastSubmittedMessage(
          lastUserMessageFromConversation(restored.conversationItems),
        );
        const pendingRequestId = canonicalSession.pending_request?.request_id;
        setPendingRequestId(
          typeof pendingRequestId === 'string' ? pendingRequestId : null,
        );
      })
      .catch((error) => {
        if (isCanceled) return;
        if (shouldDiscardStoredSession(error)) {
          clearStoredSession();
          toast.error(agentBuilderErrorMessage(error));
          return;
        }
        if (sessionRestoreAttempt >= SESSION_RECOVERY_DELAYS_MS.length) {
          setSessionRecoveryRequired('restore');
          return;
        }
        recoveryTimeoutId = window.setTimeout(() => {
          setSessionRestoreAttempt((attempt) => attempt + 1);
        }, SESSION_RECOVERY_DELAYS_MS[sessionRestoreAttempt]);
      });
    return () => {
      isCanceled = true;
      if (recoveryTimeoutId !== null) {
        window.clearTimeout(recoveryTimeoutId);
      }
    };
  }, [
    beginSessionReconciliation,
    clearStoredSession,
    ensureSession,
    isOpen,
    legacyStorageKey,
    sessionRecoveryRetryVersion,
    sessionRestoreAttempt,
    sessionId,
    synchronizeParameterGroup,
    storageKey,
    workflowId,
  ]);

  useEffect(() => {
    if (
      !isOpen ||
      !sessionId ||
      !pendingRequestId ||
      pendingRequestId === 'submitting'
    ) {
      return;
    }
    let isCanceled = false;
    let timeoutId: number | null = null;
    let pollAttempt = 0;
    let consecutivePollFailureCount = 0;
    const pollingStartedAt = Date.now();
    const schedulePoll = (
      continuePending = false,
      requestCreatedAt?: unknown,
    ) => {
      if (isCanceled) return;
      if (pollAttempt >= SESSION_RECOVERY_DELAYS_MS.length) {
        if (!continuePending) {
          setIsPendingRequestSlow(false);
          setSessionRecoveryRequired('pending_request');
          return;
        }
        const parsedCreatedAt =
          typeof requestCreatedAt === 'string'
            ? Date.parse(requestCreatedAt)
            : Number.NaN;
        const requestStartedAt = Number.isFinite(parsedCreatedAt)
          ? Math.min(parsedCreatedAt, Date.now())
          : pollingStartedAt;
        const elapsedMs = Math.max(0, Date.now() - requestStartedAt);
        if (elapsedMs >= PENDING_REQUEST_DEADLINE_MS) {
          setIsPendingRequestSlow(false);
          setSessionRecoveryRequired('pending_request');
          return;
        }
        const isLongRunning = elapsedMs >= PENDING_REQUEST_NORMAL_WINDOW_MS;
        setIsPendingRequestSlow(isLongRunning);
        const windowEndMs = isLongRunning
          ? PENDING_REQUEST_DEADLINE_MS
          : PENDING_REQUEST_NORMAL_WINDOW_MS;
        const intervalMs = isLongRunning
          ? PENDING_REQUEST_LONG_POLL_MS
          : PENDING_REQUEST_NORMAL_POLL_MS;
        timeoutId = window.setTimeout(
          pollSession,
          Math.min(intervalMs, Math.max(1, windowEndMs - elapsedMs)),
        );
        return;
      }
      const delay = SESSION_RECOVERY_DELAYS_MS[pollAttempt];
      pollAttempt += 1;
      timeoutId = window.setTimeout(pollSession, delay);
    };
    const scheduleFailurePoll = () => {
      if (isCanceled) return;
      consecutivePollFailureCount += 1;
      if (
        consecutivePollFailureCount >= SESSION_RECOVERY_DELAYS_MS.length
      ) {
        setIsPendingRequestSlow(false);
        setSessionRecoveryRequired('pending_request');
        return;
      }
      timeoutId = window.setTimeout(
        pollSession,
        SESSION_RECOVERY_DELAYS_MS[consecutivePollFailureCount],
      );
    };
    const pollSession = async () => {
      try {
        const session = await agentBuilderApi.getSession(sessionId);
        if (isCanceled) return;
        consecutivePollFailureCount = 0;
        setAuthoritativeRequestStatus(session.status);
        setSessionProtocolVersion(session.protocol_version ?? null);
        const restored = conversationItemsFromSessionMessages(session.messages);
        const resultRequestId = latestResponseRequestId(restored.responses);
        synchronizeParameterGroup(
          session.parameter_group ?? null,
          resultRequestId,
          session.status === 'completed',
        );
        setRecoveredRoutingContext({
          requestId: resultRequestId,
          affectedNodeIds: safeAffectedNodeIds(session.active_graph_mutation),
        });
        setCurrentResultRequestId(resultRequestId);
        const response = restored.responses.find(
          (item) => item.request_id === pendingRequestId,
        );
        if (response) {
          appendResponse(response);
        }
        const nextRequestId = session.pending_request?.request_id;
        if (typeof nextRequestId === 'string') {
          setPendingRequestId(nextRequestId);
          schedulePoll(true, session.pending_request?.created_at);
        } else {
          setIsPendingRequestSlow(false);
          setSessionRecoveryRequired(null);
          setPendingRequestId(null);
        }
      } catch {
        scheduleFailurePoll();
      }
    };
    schedulePoll();
    return () => {
      isCanceled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [
    appendResponse,
    isOpen,
    pendingRequestId,
    sessionRecoveryRetryVersion,
    sessionId,
    synchronizeParameterGroup,
  ]);

  useEffect(() => {
    if (!isOpen || authoritativeRequestStatus !== 'completion_confirming') {
      return;
    }
    const reconciliationSessionId =
      pendingSessionReconciliationRef.current ?? sessionId;
    if (!reconciliationSessionId) return;
    let isCanceled = false;
    let timeoutId: number | null = null;
    let reconciliationAttempt = 0;
    const scheduleReconciliation = () => {
      if (isCanceled) return;
      if (reconciliationAttempt >= SESSION_RECOVERY_DELAYS_MS.length) {
        setSessionRecoveryRequired('acknowledgement');
        return;
      }
      const delay =
        SESSION_RECOVERY_DELAYS_MS[
          Math.max(0, reconciliationAttempt - 1)
        ];
      timeoutId = window.setTimeout(reconcile, delay);
    };
    const reconcile = async () => {
      reconciliationAttempt += 1;
      try {
        const session = await agentBuilderApi.getSession(
          reconciliationSessionId,
        );
        if (isCanceled) return;
        const activeMutation = session.active_graph_mutation as
          Record<string, unknown> | null | undefined;
        if (activeMutation?.status === 'pending_ack') {
          reconcileCanonicalSession(session);
          scheduleReconciliation();
          return;
        }
        if (activeMutation?.status === 'acknowledged') {
          const boundaryConfirmed =
            await confirmAcknowledgedHistoryBoundary(session);
          if (isCanceled) return;
          if (!boundaryConfirmed) {
            reconcileCanonicalSession(session);
            scheduleReconciliation();
            return;
          }
          setSessionRecoveryRequired(null);
          reconcileCanonicalSession(session, {
            acknowledgedBoundaryConfirmed: true,
          });
          return;
        }
        setSessionRecoveryRequired(null);
        reconcileCanonicalSession(session);
      } catch {
        scheduleReconciliation();
      }
    };
    void reconcile();
    return () => {
      isCanceled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [
    authoritativeRequestStatus,
    confirmAcknowledgedHistoryBoundary,
    isOpen,
    reconcileCanonicalSession,
    sessionRecoveryRetryVersion,
    sessionId,
  ]);

  const activeParameterTaskId =
    parameterGroup?.tasks.find((task) =>
      ['active', 'invalid'].includes(task.status),
    )?.task_id ?? null;

  useEffect(() => {
    if (!isOpen || isMinimized) return;
    const element = conversationScrollRef.current;
    if (!element || !shouldAutoScrollRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [
    activeParameterTaskId,
    conversationItems.length,
    pendingRequestId,
    sessionRecoveryRequired,
    isOpen,
    isMinimized,
  ]);

  const submitAgentBuilderMessage = async (
    message: string,
    options?: {
      selectedNodeId?: string | null;
      displayContent?: string;
      clearInput?: boolean;
    },
  ) => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage || isSubmitting || submitLockRef.current) return;
    submitLockRef.current = true;
    let intentModel = selectedIntentModel;
    if (!intentModel) {
      try {
        const groups = await agentBuilderApi.getModelOptions();
        intentModel = groups.flatMap((group) => group.options)[0] ?? null;
        setIntentModelGroups(groups);
        setSelectedIntentModel(intentModel);
      } catch {
        toast.error('Agent Builder 모델 목록을 불러오지 못했습니다.');
        submitLockRef.current = false;
        return;
      }
    }
    if (!intentModel) {
      toast.warning('Agent Builder에서 사용할 수 있는 LLM 모델이 없습니다.');
      submitLockRef.current = false;
      return;
    }
    const selectedCandidates = undefined as
      | Array<
          AgentBuilderKnowledgeCandidateSelection & { label?: string | null }
        >
      | undefined;
    if (hasUnsavedChanges) {
      toast.warning(
        '저장되지 않은 변경이 있어 Agent Builder를 시작할 수 없습니다.',
      );
      submitLockRef.current = false;
      return;
    }
    setIsSubmitting(true);
    setCurrentResultRequestId(null);
    shouldAutoScrollRef.current = true;
    setConversationItems((items) => [
      ...items,
      {
        kind: 'user',
        id: createLocalUserMessageId(),
        content:
          options?.displayContent ??
          (selectedCandidates && selectedCandidates.length > 0
            ? `Knowledge Base 선택: ${selectedCandidates
                .map((candidate) => candidate.label || 'Knowledge Base')
                .join(', ')}`
            : selectedCandidates
              ? 'Knowledge Base 선택 없이 생성'
              : trimmedMessage),
      },
    ]);
    let requestSessionId: string | null = null;
    try {
      let nextSessionId = await ensureSession();
      requestSessionId = nextSessionId;
      setPendingRequestId('submitting');
      const requestInput = {
        message: trimmedMessage,
        workflowId,
        appId,
        selectedNodeId: options?.selectedNodeId ?? selectedNodeId,
        selectedEdgeId,
        intentModelSelection: intentModel
          ? ({
              credentialId: intentModel.credential.id,
              modelId: intentModel.model.id,
            } satisfies AgentBuilderIntentModelSelection)
          : undefined,
        generationMode,
      };
      let response: AgentBuilderMessageResponse;
      try {
        response = await agentBuilderApi.sendMessage(
          nextSessionId,
          requestInput,
        );
      } catch (error) {
        if (agentBuilderErrorCode(error) !== 'stale_protocol') throw error;
        clearStoredSession();
        setSessionId(null);
        nextSessionId = await ensureSession(true);
        requestSessionId = nextSessionId;
        response = await agentBuilderApi.sendMessage(
          nextSessionId,
          requestInput,
        );
      }
      setAuthoritativeRequestStatus(response.status);
      let nextResponse = response;
      if (response.graph_mutation) {
        const applied = await applyAndSaveAgentBuilderMutation({
          sessionId: nextSessionId,
          workflowId,
          viewport: getViewport(),
          mutation: mutationWithBaseHash(response.graph_mutation),
        });
        const acknowledgedGroup =
          applied.acknowledgement.parameter_group ??
          response.parameter_group ??
          null;
        const completionEligible = applied.session?.status === 'completed';
        if (applied.session?.status) {
          setAuthoritativeRequestStatus(applied.session.status);
        } else {
          beginSessionReconciliation(nextSessionId);
        }
        synchronizeParameterGroup(
          acknowledgedGroup,
          response.request_id,
          completionEligible,
        );
        const hasKnowledgeConfirmation = hasKnowledgeSelectionOptions(response);
        nextResponse = {
          ...response,
          status: hasKnowledgeConfirmation
            ? response.status
            : completionEligible
              ? 'completed'
              : 'parameter_configuration',
          parameter_group: acknowledgedGroup,
        };
        window.setTimeout(() => {
          fitView({ padding: 0.2, duration: 300, maxZoom: 1 });
        }, 0);
      }
      appendResponse(nextResponse);
      setPendingRequestId(null);
      setLastSubmittedMessage(trimmedMessage);
      if (options?.clearInput !== false) {
        setInput('');
      }
    } catch (error) {
      let recoveredPendingRequest = false;
      if (requestSessionId && isRecoverableTransportError(error)) {
        try {
          const session = await agentBuilderApi.getSession(requestSessionId);
          setAuthoritativeRequestStatus(session.status);
          const recoveredRequestId = session.pending_request?.request_id;
          const restored = conversationItemsFromSessionMessages(
            session.messages,
          );
          const resultRequestId = latestResponseRequestId(restored.responses);
          setRecoveredRoutingContext({
            requestId: resultRequestId,
            affectedNodeIds: safeAffectedNodeIds(session.active_graph_mutation),
          });
          if (typeof recoveredRequestId === 'string') {
            const response = restored.responses.find(
              (item) => item.request_id === recoveredRequestId,
            );
            if (response) {
              appendResponse(response);
            }
            setSessionProtocolVersion(session.protocol_version ?? null);
            synchronizeParameterGroup(
              session.parameter_group ?? null,
              resultRequestId,
              session.status === 'completed',
            );
            setPendingRequestId(recoveredRequestId);
            setLastSubmittedMessage(trimmedMessage);
            if (options?.clearInput !== false) {
              setInput('');
            }
            recoveredPendingRequest = true;
          } else if (
            session.status !== 'planning' &&
            restored.responses.length > 0
          ) {
            setCurrentResultRequestId(resultRequestId);
            setResponses(restored.responses);
            setConversationItems(restored.conversationItems);
            setSessionProtocolVersion(session.protocol_version ?? null);
            synchronizeParameterGroup(
              session.parameter_group ?? null,
              resultRequestId,
              session.status === 'completed',
            );
            setPendingRequestId(null);
            setLastSubmittedMessage(
              lastUserMessageFromConversation(restored.conversationItems) ||
                trimmedMessage,
            );
            if (options?.clearInput !== false) {
              setInput('');
            }
            recoveredPendingRequest = true;
          }
        } catch {
          recoveredPendingRequest = false;
        }
      }
      if (!recoveredPendingRequest) {
        toast.error(agentBuilderErrorMessage(error));
        setPendingRequestId(null);
      }
    } finally {
      setIsSubmitting(false);
      submitLockRef.current = false;
    }
  };

  const resolveKnowledgeSelection = useCallback(
    async (
      response: AgentBuilderMessageResponse,
      selections: Array<
        AgentBuilderKnowledgeCandidateSelection & { label?: string | null }
      >,
      hierarchySelection?: {
        collectionHandles: string[];
        kbHandles: string[];
      },
      editorSelection?: {
        targetNodeId: string;
        knowledgeBaseIds: string[];
        knowledgeCollectionIds: string[];
      },
    ) => {
    const firstResolutionCandidate =
      response.knowledge_resolution?.candidates.find(
        (candidate) =>
          !isNoKnowledgeBaseOption(candidate as Record<string, unknown>),
      ) as Record<string, unknown> | undefined;
    const resolutionId =
      selections[0]?.resolution_id ??
      formatClarificationOptionValue(
        response.knowledge_resolution?.resolution_id,
      ) ??
      (typeof firstResolutionCandidate?.resolution_id === 'string'
        ? firstResolutionCandidate.resolution_id
        : null);
    if (
      !sessionId ||
      hasUnsavedChanges ||
      isSubmitting ||
      isApplying ||
      isPersistedMutationSaving ||
      authoritativeRequestStatus === 'completion_confirming'
    ) {
      return;
    }
    if (submitLockRef.current) return;
    if (!resolutionId) {
      toast.error('Knowledge Base 선택 정보를 확인할 수 없습니다.');
      return;
    }
    setKnowledgeSelectionError(null);
    setIsSubmitting(true);
    submitLockRef.current = true;
    shouldAutoScrollRef.current = true;
    try {
      const selection = await agentBuilderApi.selectKnowledge(sessionId, {
        resolutionId,
        selectedCandidates: selections.map((candidate) => ({
          candidate_id: candidate.candidate_id,
          resolution_id: candidate.resolution_id,
          requirement_id: candidate.requirement_id,
        })),
        selectedCollectionHandles: hierarchySelection?.collectionHandles,
        selectedKbHandles: hierarchySelection?.kbHandles,
        editorTargetNodeId: editorSelection?.targetNodeId,
        selectedKnowledgeBaseIds: editorSelection?.knowledgeBaseIds,
        selectedKnowledgeCollectionIds: editorSelection?.knowledgeCollectionIds,
      });
      const applied = await applyAndSaveAgentBuilderMutation({
        sessionId,
        workflowId,
        viewport: getViewport(),
        mutation: mutationWithBaseHash(selection.graph_mutation),
      });
      const acknowledgedGroup = applied.acknowledgement.parameter_group ?? null;
      const completionEligible = applied.session?.status === 'completed';
      if (applied.session?.status) {
        setAuthoritativeRequestStatus(applied.session.status);
      } else {
        beginSessionReconciliation(sessionId);
      }
      synchronizeParameterGroup(
        acknowledgedGroup,
        response.request_id,
        completionEligible,
      );
      const timing =
        response.knowledge_resolution?.timing ??
        (response.graph_mutation ? 'after_graph' : 'before_graph');
      const effectiveHierarchySelection = hierarchySelection ??
        (editorSelection
          ? {
              collectionHandles:
                selection.selected_collection_handles ?? [],
              kbHandles: selection.selected_kb_handles ?? [],
            }
          : undefined);
      setConversationItems((items) => [
        ...items,
        {
          kind: 'user',
          id: createLocalUserMessageId(),
          content: effectiveHierarchySelection
            ? hierarchyKnowledgeSelectionMessage(
                response,
                effectiveHierarchySelection,
                timing,
              )
            : knowledgeSelectionMessage(selections, timing),
        },
      ]);
      appendResponse({
        ...response,
        status: completionEligible ? 'completed' : 'parameter_configuration',
        knowledge_resolution: {
          resolution_id: resolutionId,
          timing,
          required: response.knowledge_resolution?.required ?? true,
          candidates: response.knowledge_resolution?.candidates ?? [],
          collections: response.knowledge_resolution?.collections ?? [],
          ungrouped_kbs: response.knowledge_resolution?.ungrouped_kbs ?? [],
          selected: effectiveHierarchySelection
            ? [
                ...effectiveHierarchySelection.collectionHandles.map((handle) => {
                  const collection =
                    response.knowledge_resolution?.collections?.find(
                      (item) => item.collection_handle === handle,
                    );
                  return {
                    selection_type: 'collection' as const,
                    collection_handle: handle,
                    safe_label: collection?.safe_label ?? null,
                  };
                }),
                ...effectiveHierarchySelection.kbHandles.map((handle) => {
                  const kb = [
                    ...(response.knowledge_resolution?.collections?.flatMap(
                      (item) => item.children,
                    ) ?? []),
                    ...(response.knowledge_resolution?.ungrouped_kbs ?? []),
                  ].find((item) => item.kb_handle === handle);
                  return {
                    selection_type: 'knowledge_base' as const,
                    kb_handle: handle,
                    candidate_id: handle,
                    safe_label: kb?.safe_label ?? null,
                  };
                }),
              ]
            : selections.map((candidate) => ({
                candidate_id: candidate.candidate_id,
                safe_label: candidate.label ?? null,
              })),
        },
        graph_mutation: selection.graph_mutation,
        parameter_group: acknowledgedGroup,
        clarification_questions: [],
        clarification_options: [],
        warnings: response.warnings,
      });
      setKnowledgeSelectionError(null);
      setInput('');
    } catch (error) {
      let canonicalOutcomeConfirmed = false;
      try {
        const session = await agentBuilderApi.getSession(sessionId);
        if (session.session_id === sessionId) {
          reconcileCanonicalSession(session);
          const activeMutation = session.active_graph_mutation as
            Record<string, unknown> | null | undefined;
          canonicalOutcomeConfirmed =
            session.status === 'completed' ||
            activeMutation?.status === 'pending_ack';
        }
      } catch {
        // Keep the current card and selected values while the outcome is unknown.
      }
      if (canonicalOutcomeConfirmed) {
        setKnowledgeSelectionError(null);
        return;
      }
      const message = knowledgeSelectionErrorMessage(error);
      if (agentBuilderErrorCode(error) === 'knowledge_selection_stale') {
        setKnowledgeSelectionResetVersion((version) => version + 1);
      }
      setKnowledgeSelectionError(message);
      toast.error(message);
    } finally {
      setIsSubmitting(false);
      submitLockRef.current = false;
    }
    },
    [
      appendResponse,
      authoritativeRequestStatus,
      beginSessionReconciliation,
      getViewport,
      hasUnsavedChanges,
      isApplying,
      isPersistedMutationSaving,
      isSubmitting,
      reconcileCanonicalSession,
      sessionId,
      synchronizeParameterGroup,
      workflowId,
    ],
  );

  const submit = async () => {
    const typedMessage = input.trim();
    await submitAgentBuilderMessage(typedMessage, { clearInput: true });
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }
    event.preventDefault();
    void submit();
  };

  const resolvePendingRequestId = async () => {
    if (pendingRequestId && pendingRequestId !== 'submitting') {
      return pendingRequestId;
    }
    if (!sessionId) return null;
    const session = await agentBuilderApi.getSession(sessionId);
    setAuthoritativeRequestStatus(session.status);
    const restored = conversationItemsFromSessionMessages(session.messages);
    const resultRequestId = latestResponseRequestId(restored.responses);
    setCurrentResultRequestId(resultRequestId);
    synchronizeParameterGroup(
      session.parameter_group ?? null,
      resultRequestId,
      session.status === 'completed',
    );
    setRecoveredRoutingContext({
      requestId: resultRequestId,
      affectedNodeIds: safeAffectedNodeIds(session.active_graph_mutation),
    });
    setResponses(restored.responses);
    setConversationItems(restored.conversationItems);
    const requestId = session.pending_request?.request_id;
    if (typeof requestId === 'string') {
      setPendingRequestId(requestId);
      return requestId;
    }
    return null;
  };

  const cancelPendingRequest = async () => {
    if (!pendingRequestId) return;
    try {
      const requestId = await resolvePendingRequestId();
      if (!requestId) {
        toast.warning(
          '취소할 진행 중 요청을 아직 확인하지 못했습니다. 잠시 후 다시 시도해주세요.',
        );
        return;
      }
      const response = await agentBuilderApi.cancelRequest(requestId);
      appendResponse(response);
      setPendingRequestId(null);
      toast.success('진행 중인 Agent Builder 요청을 취소했습니다.');
    } catch {
      toast.error('진행 중 요청 취소에 실패했습니다.');
    }
  };

  const focusParameterNode = useCallback(
    (nodeId: string) => {
      const workflowState = useWorkflowStore.getState();
      workflowState.onNodesChange(
        workflowState.nodes.map((item) => ({
          id: item.id,
          type: 'select' as const,
          selected: item.id === nodeId,
        })),
      );
      const node = getNode(nodeId);
      if (!node) return;
      const width = node.measured?.width ?? node.width ?? 200;
      const height = node.measured?.height ?? node.height ?? 100;
      const canvas = document.querySelector<HTMLElement>('.react-flow');
      const canvasRect = canvas?.getBoundingClientRect();
      const panelRect = panelRef.current?.getBoundingClientRect() ?? null;
      if (canvasRect && canvasRect.width > 0 && canvasRect.height > 0) {
        const position = node.position;
        void setViewport(
          calculateAgentBuilderNodeFocusViewport({
            canvasRect,
            panelRect,
            node: { x: position.x, y: position.y, width, height },
          }),
          { duration: 300 },
        );
        return;
      }
      void setCenter(
        node.position.x + width / 2,
        node.position.y + height / 2,
        {
          zoom: 1.5,
          duration: 300,
        },
      );
    },
    [getNode, setCenter, setViewport],
  );

  const openNodeSettings = useCallback(
    (nodeId: string, section?: 'routing' | 'connection') => {
      focusParameterNode(nodeId);
      window.dispatchEvent(
        new CustomEvent('agent-builder:open-node-settings', {
          detail: { nodeId, section },
        }),
      );
    },
    [focusParameterNode],
  );

  const activeKnowledgeClarification =
    latestKnowledgeClarification(conversationItems);
  useEffect(() => {
    const handleNodeKnowledgeSelection = (event: Event) => {
      const detail = (
        event as CustomEvent<AgentBuilderNodeKnowledgeSelectionEventDetail>
      ).detail;
      const response = activeKnowledgeClarification;
      const resolution = response?.knowledge_resolution ?? null;
      if (
        !detail ||
        !response ||
        sessionProtocolVersion !== 'direct_edit_v1' ||
        resolution?.timing !== 'after_graph' ||
        !resolution.target_node_id ||
        resolution.target_node_id !== detail.nodeId
      ) {
        return;
      }
      detail.handled = true;
      if (
        hasUnsavedChanges ||
        isSubmitting ||
        isApplying ||
        isPersistedMutationSaving ||
        authoritativeRequestStatus === 'completion_confirming' ||
        submitLockRef.current
      ) {
        toast.error(
          'Agent Builder 저장이 끝난 뒤 Knowledge Base 선택을 다시 시도해주세요.',
        );
        return;
      }
      void resolveKnowledgeSelection(
        response,
        [],
        undefined,
        {
          targetNodeId: detail.nodeId,
          knowledgeBaseIds: detail.knowledgeBases.map((item) => item.id),
          knowledgeCollectionIds: detail.knowledgeCollections.map(
            (item) => item.id,
          ),
        },
      );
    };
    window.addEventListener(
      AGENT_BUILDER_KNOWLEDGE_SELECTION_FROM_NODE,
      handleNodeKnowledgeSelection,
    );
    return () => {
      window.removeEventListener(
        AGENT_BUILDER_KNOWLEDGE_SELECTION_FROM_NODE,
        handleNodeKnowledgeSelection,
      );
    };
  }, [
    activeKnowledgeClarification,
    authoritativeRequestStatus,
    hasUnsavedChanges,
    isApplying,
    isPersistedMutationSaving,
    isSubmitting,
    resolveKnowledgeSelection,
    sessionProtocolVersion,
  ]);
  const activeKnowledgeOptions = useMemo(
    () =>
      activeKnowledgeClarification
        ? knowledgeOptionsFromResponse(activeKnowledgeClarification)
        : [],
    [activeKnowledgeClarification],
  );
  const latestAssistantResponse = useMemo(() => {
    if (!currentResultRequestId) return null;
    for (let index = conversationItems.length - 1; index >= 0; index -= 1) {
      const item = conversationItems[index];
      if (
        item.kind === 'assistant' &&
        item.response.request_id === currentResultRequestId
      ) {
        return item.response;
      }
    }
    return null;
  }, [conversationItems, currentResultRequestId]);
  const currentParameterGroup =
    !pendingRequestId &&
    parameterGroup &&
    (parameterGroupRequestId === latestAssistantResponse?.request_id ||
      (isPresentationReentry && parameterGroupRequestId === null) ||
      (parameterGroupRequestId === null &&
        currentResultRequestId === null &&
        authoritativeRequestStatus === 'completed'))
      ? parameterGroup
      : null;
  const recoveredRoutingNodeIds = useMemo(
    () =>
      recoveredRoutingContext.requestId === latestAssistantResponse?.request_id
        ? recoveredRoutingContext.affectedNodeIds
        : [],
    [
      latestAssistantResponse?.request_id,
      recoveredRoutingContext.affectedNodeIds,
      recoveredRoutingContext.requestId,
    ],
  );
  const routingNodeIds = useMemo(
    () =>
      Array.from(
        new Set([
          ...(latestAssistantResponse?.graph_mutation?.affected_node_ids ?? []),
          ...(currentParameterGroup?.tasks.map((task) => task.node_id) ?? []),
          ...recoveredRoutingNodeIds,
        ]),
      ),
    [
      currentParameterGroup,
      latestAssistantResponse?.graph_mutation?.affected_node_ids,
      recoveredRoutingNodeIds,
    ],
  );
  const activeKnowledgeStep: WorkflowKnowledgeStep | null =
    activeKnowledgeClarification
      ? {
          status: 'active',
          timing:
            activeKnowledgeClarification.knowledge_resolution?.timing ??
            (activeKnowledgeClarification.graph_mutation
              ? 'after_graph'
              : 'before_graph'),
          question:
            activeKnowledgeClarification.clarification_questions[0] ?? null,
          candidates: activeKnowledgeOptions.map((option) => ({
            selection_id: option.selectionId,
            candidate_id: option.selection.candidate_id,
            label: option.label,
            confidence: option.confidence,
            score: option.score,
            reason: option.reason,
          })),
          collections:
            activeKnowledgeClarification.knowledge_resolution?.collections ??
            [],
          ungroupedKbs:
            activeKnowledgeClarification.knowledge_resolution?.ungrouped_kbs ??
            [],
          selectedCandidateIds:
            activeKnowledgeClarification.knowledge_resolution?.selected
              .map((selection) => selection.candidate_id)
              .filter(
                (candidateId): candidateId is string =>
                  typeof candidateId === 'string',
              ),
          selectedCollectionHandles:
            activeKnowledgeClarification.knowledge_resolution
              ?.selected_collection_handles ??
            activeKnowledgeClarification.knowledge_resolution?.selected
              .map((selection) => selection.collection_handle)
              .filter((handle): handle is string => typeof handle === 'string'),
          selectedKbHandles:
            activeKnowledgeClarification.knowledge_resolution
              ?.selected_kb_handles ??
            activeKnowledgeClarification.knowledge_resolution?.selected
              .map((selection) => selection.kb_handle ?? selection.candidate_id)
              .filter((handle): handle is string => typeof handle === 'string'),
          resetVersion: knowledgeSelectionResetVersion,
          errorMessage: knowledgeSelectionError,
        }
      : null;
  const pendingKnowledgeStep: WorkflowKnowledgeStep | null =
    !activeKnowledgeStep &&
    latestAssistantResponse?.knowledge_resolution?.selection_status ===
      'pending_ack'
      ? {
          status: 'confirming',
          timing: latestAssistantResponse.knowledge_resolution.timing,
          question: latestAssistantResponse.clarification_questions[0] ?? null,
          candidates: knowledgeOptionsFromResponse(latestAssistantResponse).map(
            (option) => ({
              selection_id: option.selectionId,
              candidate_id: option.selection.candidate_id,
              label: option.label,
              confidence: option.confidence,
              score: option.score,
              reason: option.reason,
            }),
          ),
          collections:
            latestAssistantResponse.knowledge_resolution.collections ?? [],
          ungroupedKbs:
            latestAssistantResponse.knowledge_resolution.ungrouped_kbs ?? [],
          selectedCandidateIds:
            latestAssistantResponse.knowledge_resolution.selected
              .map((selection) => selection.candidate_id)
              .filter(
                (candidateId): candidateId is string =>
                  typeof candidateId === 'string',
              ),
          selectedCollectionHandles:
            latestAssistantResponse.knowledge_resolution
              .selected_collection_handles ??
            latestAssistantResponse.knowledge_resolution.selected
              .map((selection) => selection.collection_handle)
              .filter((handle): handle is string => typeof handle === 'string'),
          selectedKbHandles:
            latestAssistantResponse.knowledge_resolution.selected_kb_handles ??
            latestAssistantResponse.knowledge_resolution.selected
              .map((selection) => selection.kb_handle ?? selection.candidate_id)
              .filter((handle): handle is string => typeof handle === 'string'),
          selectedLabels: latestAssistantResponse.knowledge_resolution.selected
            .map((selection) => selection.safe_label ?? selection.label)
            .filter((label): label is string => typeof label === 'string'),
          errorMessage: knowledgeSelectionError,
        }
      : null;
  const completedKnowledgeStep: WorkflowKnowledgeStep | null =
    !activeKnowledgeStep &&
    !pendingKnowledgeStep &&
    latestAssistantResponse?.knowledge_resolution
      ? {
          status: 'completed',
          timing: latestAssistantResponse.knowledge_resolution.timing,
          candidates: [],
          selectedCollectionHandles:
            latestAssistantResponse.knowledge_resolution
              .selected_collection_handles ?? [],
          selectedKbHandles:
            latestAssistantResponse.knowledge_resolution.selected_kb_handles ??
            [],
          selectedLabels: latestAssistantResponse.knowledge_resolution.selected
            .map((selection) => {
              const safeLabel = selection.safe_label ?? selection.label;
              return typeof safeLabel === 'string' ? safeLabel : null;
            })
            .filter((label): label is string => Boolean(label)),
        }
      : null;
  const knowledgeStep =
    activeKnowledgeStep ?? pendingKnowledgeStep ?? completedKnowledgeStep;
  const activeParameterTask = currentParameterGroup?.tasks.find((task) =>
    ['active', 'invalid'].includes(task.status),
  );
  const failedSetupStatuses = [
    'failed',
    'validation_failed',
    'unsupported',
    'stale',
    'stale_protocol',
    'canceled',
  ];
  const setupStatus: WorkflowSetupStatus =
    isPersistedMutationSaving || isApplying
      ? 'saving'
      : sessionRecoveryRequired || secretConfigurationRecoveryRequired
        ? 'recovery_required'
        : authoritativeRequestStatus === 'completion_confirming'
          ? 'confirming'
          : pendingKnowledgeStep
            ? 'confirming'
            : activeKnowledgeStep
              ? 'awaiting_confirmation'
              : pendingRequestId ||
                  isSubmitting ||
                  latestAssistantResponse?.status === 'planning'
                ? 'planning'
                : latestAssistantResponse &&
                    failedSetupStatuses.includes(latestAssistantResponse.status)
                  ? 'failed'
                  : activeParameterTask?.resolution_source
                    ? 'awaiting_confirmation'
                    : activeParameterTask
                      ? 'configuring'
                      : isAgentBuilderSetupCompleted(
                            authoritativeRequestStatus,
                            currentParameterGroup,
                          )
                        ? 'completed'
                        : 'configuring';
  const showUnifiedSetup = Boolean(
    knowledgeStep ||
    currentParameterGroup ||
    pendingRequestId ||
    isSubmitting ||
    latestAssistantResponse?.status === 'planning' ||
    latestAssistantResponse?.status === 'completed' ||
    routingNodeIds.length > 0 ||
    (latestAssistantResponse &&
      failedSetupStatuses.includes(latestAssistantResponse.status)),
  );
  const activeWorkflowTargetClarification =
    latestWorkflowTargetClarification(conversationItems);
  const activeWorkflowTargetClarificationRequestId =
    activeWorkflowTargetClarification?.request_id ?? null;
  const canSubmitMessage = Boolean(input.trim());

  const toggleAgentBuilder = () => {
    if (isMinimized) {
      shouldAutoScrollRef.current = true;
      setIsMinimized(false);
      setIsOpen(true);
      return;
    }
    shouldAutoScrollRef.current = true;
    setIsOpen((value) => !value);
  };

  const effectiveViewportWidth =
    viewportWidth ??
    (typeof window === 'undefined'
      ? AGENT_BUILDER_MIN_PANEL_WIDTH + AGENT_BUILDER_VIEWPORT_GUTTER
      : window.innerWidth);
  const maxPanelWidth = Math.max(
    0,
    effectiveViewportWidth - AGENT_BUILDER_VIEWPORT_GUTTER,
  );
  const minPanelWidth = Math.min(
    AGENT_BUILDER_MIN_PANEL_WIDTH,
    maxPanelWidth,
  );
  const currentPanelWidth = Math.round(
    panelWidth ??
      clampAgentBuilderPanelWidth(
        effectiveViewportWidth / 2,
        effectiveViewportWidth,
      ),
  );

  const renderedPanelWidth = () => {
    const measuredWidth = panelRef.current?.getBoundingClientRect().width;
    if (measuredWidth && measuredWidth > 0) return measuredWidth;
    return currentPanelWidth;
  };

  const setClampedPanelWidth = (width: number) => {
    setPanelWidth(clampAgentBuilderPanelWidth(width, window.innerWidth));
  };

  const handlePanelResizeStart = (
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    resizeCleanupRef.current?.();

    const startClientX = event.clientX;
    const startWidth = renderedPanelWidth();
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      setClampedPanelWidth(startWidth + startClientX - moveEvent.clientX);
    };
    const cleanup = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', cleanup);
      window.removeEventListener('pointercancel', cleanup);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      resizeCleanupRef.current = null;
    };

    resizeCleanupRef.current = cleanup;
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', cleanup, { once: true });
    window.addEventListener('pointercancel', cleanup, { once: true });
  };

  const handlePanelResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      setClampedPanelWidth(
        renderedPanelWidth() + AGENT_BUILDER_RESIZE_KEYBOARD_STEP,
      );
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      setClampedPanelWidth(
        renderedPanelWidth() - AGENT_BUILDER_RESIZE_KEYBOARD_STEP,
      );
    } else if (event.key === 'Home') {
      event.preventDefault();
      setClampedPanelWidth(AGENT_BUILDER_MIN_PANEL_WIDTH);
    } else if (event.key === 'End') {
      event.preventDefault();
      setClampedPanelWidth(window.innerWidth);
    }
  };

  const panelWidthStyle = {
    '--agent-builder-panel-width':
      panelWidth === null
        ? AGENT_BUILDER_DEFAULT_PANEL_WIDTH
        : `${panelWidth}px`,
  } as CSSProperties;

  return (
    <div
      className="agent-builder-panel-shell pointer-events-none fixed bottom-5 left-2 right-2 z-50 flex flex-col items-end gap-3 sm:left-auto sm:right-5"
      style={panelWidthStyle}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {isOpen && !isMinimized && (
        <section
          ref={panelRef}
          className="pointer-events-auto relative flex h-[calc(100dvh-7.75rem)] w-full flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl"
          aria-label="Agent Builder panel"
        >
          <div
            role="separator"
            aria-label="Agent Builder 너비 조절"
            aria-orientation="vertical"
            aria-valuenow={currentPanelWidth}
            aria-valuemin={Math.round(minPanelWidth)}
            aria-valuemax={Math.round(maxPanelWidth)}
            aria-valuetext={`${currentPanelWidth}픽셀`}
            tabIndex={0}
            onPointerDown={handlePanelResizeStart}
            onKeyDown={handlePanelResizeKeyDown}
            className="group absolute bottom-0 left-0 top-0 z-10 hidden w-2 cursor-col-resize touch-none outline-none sm:block"
          >
            <span className="absolute bottom-2 left-1/2 top-2 w-0.5 -translate-x-1/2 rounded-full bg-transparent transition-colors group-hover:bg-blue-400 group-focus-visible:bg-blue-500" />
          </div>
          <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <Bot className="h-4 w-4 text-slate-700" />
              <span className="shrink-0 text-sm font-semibold text-slate-900">
                Agent Builder
              </span>
              <div className="relative min-w-0">
                <button
                  type="button"
                  onClick={() => setIsModelMenuOpen((value) => !value)}
                  className="flex h-7 max-w-40 items-center gap-1 rounded-md border border-slate-200 px-2 text-xs text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
                  aria-label={`Agent Builder 모델: ${selectedIntentModel?.model.name ?? '선택 필요'}`}
                  disabled={isSubmitting}
                >
                  <span className="truncate">
                    {selectedIntentModel?.model.name ?? '모델 선택'}
                  </span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                </button>
                {isModelMenuOpen && (
                  <div
                    data-testid="agent-builder-model-menu"
                    className="absolute right-0 top-8 z-20 max-h-[264px] w-[190px] overflow-y-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg"
                  >
                    {intentModelGroups.map((group) => (
                      <div key={group.provider_name} className="py-1">
                        <div
                          data-testid="agent-builder-model-provider"
                          className="px-3 py-1 text-xs font-semibold text-slate-500"
                        >
                          {group.provider_name}
                        </div>
                        {group.options.map((option) => {
                          const isSelected =
                            selectedIntentModel?.model.id === option.model.id &&
                            selectedIntentModel.credential.id ===
                              option.credential.id;
                          return (
                            <button
                              key={`${option.credential.id}:${option.model.id}`}
                              type="button"
                              onClick={() => {
                                setSelectedIntentModel(option);
                                setIsModelMenuOpen(false);
                              }}
                              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-50"
                              aria-label={`${option.model.name}, ${option.credential.credential_name}`}
                            >
                              <Check
                                className={`h-3.5 w-3.5 shrink-0 ${isSelected ? 'text-slate-900' : 'text-transparent'}`}
                              />
                              <span className="min-w-0">
                                <span className="block truncate font-medium text-slate-800">
                                  {option.model.name}
                                </span>
                                <span className="block truncate text-slate-500">
                                  {option.credential.credential_name}
                                </span>
                              </span>
                            </button>
                          );
                        })}
                        {group.options.length === 0 && (
                          <p className="px-3 py-2 text-xs text-slate-400">
                            {group.unavailable_reason ===
                            'chat_model_not_supported'
                              ? '채팅 모델을 지원하지 않음'
                              : '사용 가능한 모델 없음'}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setIsModelMenuOpen(false);
                  setIsMinimized(true);
                }}
                className="rounded-md p-1 text-slate-500 hover:bg-slate-100"
                aria-label="Agent Builder 최소화"
              >
                <Minus className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsModelMenuOpen(false);
                  setIsOpen(false);
                  setIsMinimized(false);
                }}
                className="rounded-md p-1 text-slate-500 hover:bg-slate-100"
                aria-label="Close Agent Builder"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </header>

          <div
            ref={conversationScrollRef}
            data-testid="agent-builder-conversation"
            className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm"
            onScroll={(event) => {
              const element = event.currentTarget;
              const distanceFromBottom =
                element.scrollHeight - element.scrollTop - element.clientHeight;
              shouldAutoScrollRef.current = distanceFromBottom <= 48;
            }}
          >
            {agentBuilderHistoryNotice ? (
              <div
                role="status"
                aria-live="polite"
                className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800"
              >
                {agentBuilderHistoryNotice}
              </div>
            ) : null}
            {hasUnsavedChanges && (
              <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  저장되지 않은 변경이 있어 Agent Builder 생성을 시작할 수
                  없습니다.
                </span>
              </div>
            )}
            {conversationItems.length === 0 && (
              <p className="text-slate-500">
                만들고 싶은 workflow를 한국어로 입력하세요.
              </p>
            )}
            {pendingRequestId && !sessionRecoveryRequired && (
              <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800">
                <p>
                  {isPendingRequestSlow
                    ? '평소보다 오래 걸리고 있습니다'
                    : 'Agent Builder 요청이 진행 중입니다.'}
                </p>
                <button
                  type="button"
                  onClick={cancelPendingRequest}
                  className="mt-2 rounded-md border border-blue-300 px-2 py-1 font-semibold"
                >
                  요청 취소
                </button>
              </div>
            )}
            {sessionRecoveryRequired ? (
              <div
                role="status"
                aria-live="polite"
                className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"
              >
                <p className="font-semibold">결과 확인 필요</p>
                <p className="mt-1">
                  서버 응답을 아직 확인하지 못했습니다. 현재 작업은 유지됩니다.
                </p>
                <button
                  type="button"
                  onClick={retrySessionRecovery}
                  className="mt-2 rounded-md border border-amber-400 bg-white px-2 py-1 font-semibold text-amber-900"
                  aria-label="Agent Builder 상태 다시 확인"
                >
                  다시 확인
                </button>
              </div>
            ) : null}
            {secretConfigurationRecoveryRequired && !sessionRecoveryRequired ? (
              <div
                role="status"
                aria-live="polite"
                className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"
              >
                <p className="font-semibold">설정 상태 확인 필요</p>
                <p className="mt-1">
                  설정 저장 결과를 아직 확인하지 못했습니다. 입력한 설정과 현재 카드는 유지됩니다.
                </p>
                <button
                  type="button"
                  onClick={retrySecretConfigurationSync}
                  className="mt-2 rounded-md border border-amber-400 bg-white px-2 py-1 font-semibold text-amber-900"
                  aria-label="Agent Builder 설정 상태 다시 확인"
                >
                  다시 확인
                </button>
              </div>
            ) : null}
            {conversationItems.map((item) => {
              if (item.kind === 'user') {
                return (
                  <div key={item.id} className="flex justify-end">
                    <div className="max-w-[85%] whitespace-pre-wrap rounded-md bg-slate-900 px-3 py-2 text-sm text-white">
                      {item.content}
                    </div>
                  </div>
                );
              }
              if (
                sessionRecoveryRequired &&
                item.response.status === 'planning'
              ) {
                return null;
              }
              const response = item.response;
              const isActiveWorkflowTargetClarification =
                response.request_id ===
                activeWorkflowTargetClarificationRequestId;
              return (
                <div
                  key={item.id}
                  className="rounded-md border border-slate-200 bg-slate-50 p-3"
                >
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {response.status}
                  </div>
                  {!hasKnowledgeSelectionOptions(response)
                    ? response.clarification_questions?.map((question) => (
                        <p key={question} className="mt-2 text-slate-700">
                          {question}
                        </p>
                      ))
                    : null}
                  {response.clarification_options?.some(
                    isWorkflowNodeClarificationOption,
                  ) ? (
                    <div
                      data-testid="agent-builder-workflow-target-list"
                      className="mt-3 max-h-[13.5rem] space-y-2 overflow-y-auto pr-1"
                    >
                      {response.clarification_options
                        .filter(isWorkflowNodeClarificationOption)
                        .map((option, index) => {
                          const nodeId = formatClarificationOptionValue(
                            option.node_id,
                          );
                          const label =
                            formatClarificationOptionValue(option.label) ??
                            `Workflow node 후보 ${index + 1}`;
                          const nodeType = formatClarificationOptionValue(
                            option.node_type,
                          );
                          return (
                            <button
                              type="button"
                              key={`${nodeId ?? label}-${index}`}
                              disabled={
                                !nodeId ||
                                isSubmitting ||
                                !isActiveWorkflowTargetClarification
                              }
                              onClick={() => {
                                if (!nodeId || !lastSubmittedMessage) return;
                                void submitAgentBuilderMessage(
                                  lastSubmittedMessage,
                                  {
                                    selectedNodeId: nodeId,
                                    displayContent: `수정 대상 선택: ${label}`,
                                    clearInput: false,
                                  },
                                );
                              }}
                              className="min-h-14 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <div className="font-medium text-slate-900">
                                {label}
                              </div>
                              {nodeType ? (
                                <div className="mt-1 text-xs text-slate-500">
                                  {nodeType}
                                </div>
                              ) : null}
                            </button>
                          );
                        })}
                    </div>
                  ) : null}
                  {response.validation_result?.issues?.map((issue) => (
                    <p
                      key={`${issue.code}-${issue.path}`}
                      className="mt-2 text-red-700"
                    >
                      {issue.message}
                    </p>
                  ))}
                  {response.warnings?.map((warning) => (
                    <p key={warning} className="mt-2 text-xs text-slate-500">
                      {warning}
                    </p>
                  ))}
                </div>
              );
            })}
            {showUnifiedSetup ? (
              <WorkflowResultGroup
                tasks={currentParameterGroup?.tasks ?? []}
                nodes={nodes}
                routingNodeIds={routingNodeIds}
                knowledgeStep={knowledgeStep}
                setupStatus={setupStatus}
                presentationTaskId={presentationTaskId}
                isPresentationReentry={isPresentationReentry}
                focusHeadingTaskId={focusHeadingTaskId}
                onPresentationHeadingFocused={acknowledgePresentationFocus}
                onFocusNode={focusParameterNode}
                onOpenNodeSettings={openNodeSettings}
                onSecretSubmit={submitSecretParameter}
                onSecretClear={clearSecretParameter}
                onKnowledgeSubmit={(selectionIds) => {
                  if (!activeKnowledgeClarification) return;
                  const selected = activeKnowledgeOptions
                    .filter((option) =>
                      selectionIds.includes(option.selectionId),
                    )
                    .map((option) => ({
                      ...option.selection,
                      label: option.label,
                    }));
                  void resolveKnowledgeSelection(
                    activeKnowledgeClarification,
                    selected,
                  );
                }}
                onKnowledgeHierarchySubmit={
                  sessionProtocolVersion === 'direct_edit_v1'
                    ? (selection) => {
                        if (!activeKnowledgeClarification) return;
                        void resolveKnowledgeSelection(
                          activeKnowledgeClarification,
                          [],
                          selection,
                        );
                      }
                    : undefined
                }
                onDecision={(decision) => void decideParameter(decision)}
                onCancel={
                  currentParameterGroup?.status === 'active'
                    ? () => void cancelParameterFlow()
                    : undefined
                }
                disabled={
                  isSubmitting ||
                  isApplying ||
                  isPersistedMutationSaving ||
                  authoritativeRequestStatus === 'completion_confirming'
                }
              />
            ) : null}
          </div>

          <div className="border-t border-slate-200 p-3">
            <div
              className="mb-2 grid grid-cols-2 gap-1 rounded-md bg-slate-100 p-1"
              role="group"
              aria-label="Workflow generation mode"
            >
              <button
                type="button"
                aria-pressed={generationMode === 'configure_and_generate'}
                onClick={() => setGenerationMode('configure_and_generate')}
                disabled={
                  isSubmitting ||
                  isPersistedMutationSaving ||
                  Boolean(pendingRequestId)
                }
                className={`rounded px-2 py-1.5 text-xs font-medium ${
                  generationMode === 'configure_and_generate'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-500'
                }`}
              >
                설정하며 생성
              </button>
              <button
                type="button"
                aria-pressed={generationMode === 'structure_only'}
                onClick={() => setGenerationMode('structure_only')}
                disabled={
                  isSubmitting ||
                  isPersistedMutationSaving ||
                  Boolean(pendingRequestId)
                }
                className={`rounded px-2 py-1.5 text-xs font-medium ${
                  generationMode === 'structure_only'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-500'
                }`}
              >
                구조만 생성
              </button>
            </div>
            <div className="flex gap-2">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                disabled={
                  Boolean(pendingRequestId) ||
                  isSubmitting ||
                  isPersistedMutationSaving
                }
                className="min-h-16 flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
                placeholder="예: 입력값을 분석해서 답변하는 workflow를 만들어줘"
              />
              <button
                type="button"
                onClick={submit}
                disabled={
                  !canSubmitMessage ||
                  hasUnsavedChanges ||
                  isSubmitting ||
                  isPersistedMutationSaving ||
                  Boolean(pendingRequestId)
                }
                className="flex h-16 w-11 items-center justify-center rounded-md bg-slate-900 text-white disabled:cursor-not-allowed disabled:bg-slate-300"
                aria-label="Agent Builder 요청 보내기"
              >
                {isSubmitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <Send className="h-4 w-4" />
                    <span className="sr-only">요청 보내기</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </section>
      )}

      <button
        type="button"
        onClick={toggleAgentBuilder}
        className="pointer-events-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-950 text-white shadow-lg transition-colors hover:bg-slate-800"
        aria-label={isMinimized ? 'Agent Builder 펼치기' : 'Agent Builder 열기'}
      >
        <Bot className="h-5 w-5" />
      </button>
    </div>
  );
}
