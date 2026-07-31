import { App, AppIcon } from '../../app/api/appApi';
import {
  Connection,
  Edge,
  EdgeChange,
  NodeChange,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  OnNodesChange,
  OnEdgesChange,
  OnConnect,
} from '@xyflow/react';
import {
  Features,
  EnvVariable,
  RuntimeVariable,
  Node,
} from '../types/Workflow';
import type { AppNode } from '../types/Nodes';
import { validateConnection } from '../utils/validateWorkflowGraph';
import { DeploymentResponse } from '../types/Deployment';
import { WorkflowPermissionResponse } from '../types/Api';

import { create } from 'zustand';
import { DEFAULT_NODES } from '../constants';
import { workflowApi } from '../api/workflowApi';
import {
  assignMissingNodeDisplayNumbers,
  assignNewNodeDisplayNumbers,
  createNumberedNode,
  NODE_NUMBER_FEATURE_KEY,
} from '../utils/nodeNumbering';
import {
  DEFAULT_SNAP_GRID_SIZE,
  type SnapGridSize,
  snapPositionChanges,
} from '../utils/gridSnap';
import {
  applyAgentBuilderOperations,
  type AgentBuilderGraphMutation,
} from '../components/agentBuilder/agentBuilderGraphMutation';
import type { AgentBuilderParameterGroup } from '../api/agentBuilderApi';
import { buildWorkflowDraftPayload } from '../utils/workflowDraftPayload';
import { acquireWorkflowDraftSave } from '../utils/workflowDraftSaveCoordinator';
import { isWorkflowNodeSecretReference } from '../utils/workflowNodeSecret';

export type { SnapGridSize } from '../utils/gridSnap';

export interface Workflow {
  id: string;
  appId: string;
  nodes: Node[];
  edges: Edge[];
  features: Features;
  viewport?: {
    x: number;
    y: number;
    zoom: number;
  };
}

export type CanonicalDraftMetadata = {
  workflowId: string;
  graphHash: string;
  updatedAt: string;
};

const createIdleTestExecutionState = () => ({
  isTestPanelOpen: false,
  testExecutionStatus: 'idle' as const,
  testExecutionRunId: null,
  testSelectedNodeId: null,
  testExecutionStartedAt: null,
  testExecutionFinishedAt: null,
  testExecutionResult: null,
  testNodeResults: [],
  testExecutionError: null,
  currentExecutingNodeId: null,
  isTestUploading: false,
});

export type TestNodeResult = {
  nodeId: string;
  nodeType: string;
  output: unknown;
  traceMetadata?: Record<string, unknown>;
  title?: string;
  status?: 'running' | 'success' | 'failure';
  latencyMs?: number;
  totalTokens?: number;
  totalCost?: number;
};

export type RestoredTestExecution = {
  runId: string;
  status: 'running' | 'success' | 'failure';
  startedAt: number | null;
  finishedAt: number | null;
  workflowResult: unknown;
  nodeResults: TestNodeResult[];
  error: string | null;
};

type WorkflowState = {
  // === Editor UI 상태 (editorStore에서 유래) ===
  workflows: Workflow[];
  activeWorkflowId: string;

  projectName: string;
  projectIcon: AppIcon;
  projectDescription: string;
  projectApp: App | null; // Full app object for editing
  workflowAccess: WorkflowPermissionResponse | null;
  interactiveMode: 'mouse' | 'touchpad'; // 입력 모드 (마우스/터치패드)
  snapGridSize: SnapGridSize;
  isSnapTemporarilyDisabled: boolean;
  isFullscreen: boolean;

  // === 설정 패널 상태 ===
  isSettingsOpen: boolean;
  toggleSettings: () => void;

  // === 버전 기록 상태 ===
  isVersionHistoryOpen: boolean;
  previewingVersion: DeploymentResponse | null;
  lastDeployedAt: Date | null; // 배포 완료 시점 (리스트 갱신 트리거)

  // === 테스트 패널 상태 ===
  isTestPanelOpen: boolean;
  toggleTestPanel: () => void;
  openTestPanel: () => void;

  // === 테스트 실행 상태 ===
  testExecutionStatus: 'idle' | 'running' | 'success' | 'failure';
  testExecutionRunId: string | null;
  testSelectedNodeId: string | null;
  testExecutionStartedAt: number | null;
  testExecutionFinishedAt: number | null;
  testExecutionResult: unknown;
  testNodeResults: TestNodeResult[];
  testExecutionError: string | null;
  currentExecutingNodeId: string | null;
  isTestUploading: boolean;
  beginTestExecution: () => void;
  setTestUploading: (isUploading: boolean) => void;
  setCurrentExecutingNode: (nodeId: string | null) => void;
  setTestExecutionRunId: (runId: string | null) => void;
  selectTestExecutionNode: (nodeId: string | null) => void;
  addTestNodeResult: (result: TestNodeResult) => void;
  finishTestExecution: (result: unknown) => void;
  failTestExecution: (error: string) => void;
  restoreTestExecution: (execution: RestoredTestExecution) => void;
  resetTestExecution: () => void;

  // === 노드 전체화면 설정(NDV) 상태 ===
  fullscreenNodeId: string | null;
  fullscreenNodeSettingsSection: 'routing' | 'connection' | null;
  openNodeFullscreen: (
    nodeId: string,
    section?: 'routing' | 'connection',
  ) => void;
  closeNodeFullscreen: () => void;
  syncNodeFullscreenFromUrl: (nodeId: string | null) => void;

  // === 번호 기반 빠른 연결 상태 ===
  numberConnection: {
    sourceNodeId: string;
    sourceHandleId: string;
    input: string;
  } | null;
  startNumberConnection: (sourceNodeId: string, sourceHandleId: string) => void;
  updateNumberConnectionInput: (input: string) => void;
  cancelNumberConnection: () => void;

  // === 그래프 데이터 (ReactFlow) ===
  nodes: Node[];
  edges: Edge[];

  // === 추가 필드 (API 동기화용) ===
  features: Features; // 워크플로우 기능 설정
  envVariables: EnvVariable[]; // 환경 변수
  runtimeVariables: RuntimeVariable[]; // 런타임 변수
  hasUnsavedChanges: boolean;
  isAgentBuilderMutationSaving: boolean;
  agentBuilderMutationSaveCount: number;
  pendingAgentBuilderRevert: PersistedAgentBuilderOperation | null;
  recoveredAgentBuilderParameterGroup: {
    sessionId: string;
    parameterGroup: AgentBuilderParameterGroup | null;
  } | null;
  agentBuilderHistoryNotice: string | null;
  canonicalDraftMetadata: Record<string, CanonicalDraftMetadata>;

  // === ReactFlow 액션 ===
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
  addNode: <T extends Node>(
    node: T,
    buildNodes?: (nodes: Node[], node: T) => Node[],
  ) => T;
  addNodeWithEdge: <T extends Node>(
    node: T,
    edge: Omit<Edge, 'target'>,
    buildNodes?: (nodes: Node[], node: T) => Node[],
  ) => T | null;
  undo: () => void;
  redo: () => void;
  copySelectedNodes: () => void;
  pasteCopiedNodes: () => void;
  duplicateSelectedNodes: () => void;
  hasSelectedElements: () => boolean;
  deleteSelectedElements: () => void;
  clearSelection: () => void;

  // === Inner Node Selection (for Loop/Workflow nodes) ===
  selectedInnerNode: { parentNodeId: string; nodeId: string } | null;
  setSelectedInnerNode: (parentNodeId: string, nodeId: string) => void;
  clearInnerNodeSelection: () => void;
  updateInnerNodeData: (
    parentNodeId: string,
    nodeId: string,
    newData: Record<string, unknown>,
  ) => void;

  // === Editor UI 액션 ===
  toggleVersionHistory: () => void;
  previewVersion: (version: DeploymentResponse) => void;
  exitPreview: () => void;
  restoreVersion: (version: DeploymentResponse) => Promise<void>;
  notifyDeploymentComplete: () => void; // 배포 완료 알림

  // === Remote Execution Trigger ===
  runTrigger: number;
  triggerWorkflowRun: () => void;

  // === Editor UI 액션 ===

  setProjectInfo: (name: string, icon: AppIcon, description?: string) => void;
  setProjectApp: (app: App) => void;
  setWorkflowAccess: (access: WorkflowPermissionResponse | null) => void;
  setInteractiveMode: (mode: 'mouse' | 'touchpad') => void;
  setSnapGridSize: (size: SnapGridSize) => void;
  setSnapTemporarilyDisabled: (disabled: boolean) => void;
  toggleFullscreen: () => void;
  addWorkflow: (
    workflow: Omit<Workflow, 'id'>,
    appId: string,
  ) => Promise<string>;
  loadWorkflowsByApp: (appId: string) => Promise<void>;
  setActiveWorkflow: (id: string) => void;
  setActiveWorkflowIdSafe: (id: string) => void;
  deleteWorkflow: (id: string) => void;
  updateWorkflowViewport: (
    id: string,
    viewport: { x: number; y: number; zoom: number },
  ) => void;

  // === 시작노드 검증 핼퍼 ===
  getStartNodeType: () =>
    'startNode' | 'webhookTrigger' | 'scheduleTrigger' | null;
  getStartNodeCount: () => number;
  canPublish: () => boolean;

  // === API 동기화 액션 ===
  setFeatures: (features: Features) => void;
  setEnvVariables: (vars: EnvVariable[]) => void;
  setRuntimeVariables: (vars: RuntimeVariable[]) => void;
  setHasUnsavedChanges: (hasUnsavedChanges: boolean) => void;
  setCanonicalDraftMetadata: (metadata: CanonicalDraftMetadata) => void;
  ingestCanonicalDraftMetadata: (
    value: unknown,
    workflowId?: string,
    options?: { applyDeferredProjection?: boolean },
  ) => CanonicalDraftMetadata | null;
  getCanonicalDraftMetadata: (
    workflowId?: string,
  ) => CanonicalDraftMetadata | null;
  clearCanonicalDraftMetadata: (workflowId?: string) => void;
  applyAgentBuilderGraphMutation: (
    mutation: AgentBuilderGraphMutation,
    sessionId?: string,
    canonicalBaseGraph?: { nodes: Node[]; edges: Edge[] },
  ) => void;
  rollbackLatestAgentBuilderGraphMutation: () => void;
  setAgentBuilderMutationSaving: (saving: boolean) => void;
  markLatestAgentBuilderMutationPersisted: (
    operation: PersistedAgentBuilderOperation,
  ) => void;
  markLatestAgentBuilderMutationAcknowledged: (
    operationId: string,
    completionEligible?: boolean,
  ) => void;
  setAgentBuilderParameterHistory: (
    sessionId: string,
    parameterGroup: AgentBuilderParameterGroup | null,
    lastManuallyConfiguredTaskId?: string | null,
    completionEligible?: boolean,
  ) => void;
  refreshNextAgentBuilderRevertBoundary: (
    boundary: Pick<
      PersistedAgentBuilderOperation,
      'resultGraphHash' | 'workflowUpdatedAt'
    >,
  ) => void;
  clearPendingAgentBuilderRevert: () => void;
  setRecoveredAgentBuilderParameterGroup: (
    recovery: {
      sessionId: string;
      parameterGroup: AgentBuilderParameterGroup | null;
    } | null,
  ) => void;
  clearAgentBuilderHistoryNotice: () => void;
  updateNodeData: (nodeId: string, newData: Record<string, unknown>) => void;
  updateNodeExecutionData: (
    nodeId: string,
    newData: Record<string, unknown>,
  ) => void;
  resetNodeExecutionData: () => void;
  setWorkflowData: (
    data: {
      nodes: Node[];
      edges: Edge[];
      viewport: { x: number; y: number; zoom: number };
      appId?: string;
      app_id?: string;
      features?: Features;
      envVariables?: EnvVariable[];
      runtimeVariables?: RuntimeVariable[];
    },
    workflowId?: string,
  ) => void;
};

export type PersistedAgentBuilderOperation = {
  operationId: string;
  resultGraphHash: string;
  workflowUpdatedAt: string;
  sessionId: string;
  revertGraph?: {
    nodes: Node[];
    edges: Edge[];
  };
};

type GraphSnapshot = {
  nodes: Node[];
  edges: Edge[];
  agentBuilderOperation?: PersistedAgentBuilderOperation;
  agentBuilderHistory?: AgentBuilderHistoryContext;
};

type AgentBuilderHistoryContext = {
  sessionId?: string;
  latestOperationId?: string;
  acknowledged: boolean;
  completionEligible: boolean;
  parameterGroup: AgentBuilderParameterGroup | null;
  lastManuallyConfiguredTaskId: string | null;
  presentation: 'none' | 'active' | 'completed' | 'reopened' | 'canceled';
};

const HISTORY_LIMIT = 50;
const PASTE_OFFSET = 40;
const NDV_NODE_QUERY_KEY = 'node';

const updateNodeFullscreenUrl = (
  nodeId: string | null,
  mode: 'push' | 'replace',
) => {
  if (typeof window === 'undefined') return;

  const url = new URL(window.location.href);
  const currentNodeId = url.searchParams.get(NDV_NODE_QUERY_KEY);
  if (currentNodeId === nodeId) return;

  if (nodeId) {
    url.searchParams.set(NDV_NODE_QUERY_KEY, nodeId);
  } else {
    url.searchParams.delete(NDV_NODE_QUERY_KEY);
  }

  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  if (mode === 'push') {
    window.history.pushState(window.history.state, '', nextUrl);
  } else {
    window.history.replaceState(window.history.state, '', nextUrl);
  }
};

const cloneGraph = (nodes: Node[], edges: Edge[]): GraphSnapshot => ({
  nodes: structuredClone(nodes),
  edges: structuredClone(edges),
});

const canonicalDraftMetadataFrom = (
  value: unknown,
  fallbackWorkflowId?: string,
): CanonicalDraftMetadata | null => {
  if (!value || typeof value !== 'object') return null;
  const graphHash = (value as { graph_hash?: unknown }).graph_hash;
  const updatedAt = (value as { updated_at?: unknown }).updated_at;
  const workflowId =
    (value as { workflow_id?: unknown }).workflow_id ?? fallbackWorkflowId;

  return typeof workflowId === 'string' &&
    typeof graphHash === 'string' &&
    typeof updatedAt === 'string'
    ? { workflowId, graphHash, updatedAt }
    : null;
};

const mergeNodeDataForExplicitEdit = (
  currentData: Record<string, unknown>,
  newData: Record<string, unknown>,
): Record<string, unknown> => ({ ...currentData, ...newData });

const canonicalDeferredParametersFrom = (
  value: unknown,
): Array<{ nodePath: string[]; parameterKeys: string[] }> | null => {
  if (!value || typeof value !== 'object') return null;
  const raw = (
    value as { canonical_deferred_parameters?: unknown }
  ).canonical_deferred_parameters;
  if (!Array.isArray(raw)) return null;
  const projection = raw.flatMap((entry) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return [];
    const nodePath = (entry as { node_path?: unknown }).node_path;
    const parameterKeys = (entry as { parameter_keys?: unknown })
      .parameter_keys;
    return Array.isArray(nodePath) &&
      nodePath.length > 0 &&
      nodePath.every((nodeId) => typeof nodeId === 'string') &&
      Array.isArray(parameterKeys) &&
      parameterKeys.every((key) => typeof key === 'string')
      ? [{ nodePath, parameterKeys }]
      : [];
  });
  return projection.length === raw.length ? projection : null;
};

const reconcileCanonicalDeferredParameters = (
  nodes: Node[],
  projection: Array<{ nodePath: string[]; parameterKeys: string[] }>,
  parentPath: string[] = [],
): Node[] => {
  const projectionByPath = new Map(
    projection.map((entry) => [
      JSON.stringify(entry.nodePath),
      entry.parameterKeys,
    ]),
  );
  const reconcile = (currentNodes: Node[], currentParentPath: string[]) =>
    currentNodes.map((node) => {
      const nodePath = [...currentParentPath, node.id];
      const data = { ...(node.data as Record<string, unknown>) };
      const subGraph = data.subGraph;
      if (subGraph && typeof subGraph === 'object' && !Array.isArray(subGraph)) {
        const nestedNodes = (subGraph as { nodes?: unknown }).nodes;
        if (Array.isArray(nestedNodes)) {
          data.subGraph = {
            ...subGraph,
            nodes: reconcile(nestedNodes as Node[], nodePath),
          };
        }
      }
      const deferred = projectionByPath.get(JSON.stringify(nodePath));
      if (deferred) {
        if (deferred.length > 0) data._deferred_parameters = [...deferred];
        else delete data._deferred_parameters;
      }
      return { ...node, data } as Node;
    });

  return reconcile(nodes, parentPath);
};

const STRUCTURAL_AGENT_BUILDER_MUTATIONS = new Set<
  AgentBuilderGraphMutation['kind']
>(['initial_graph', 'graph_edit', 'replace_workflow']);

const findLatestAgentBuilderBoundaryIndex = (
  stack: GraphSnapshot[],
  sessionId?: string,
) => {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    const snapshot = stack[index];
    if (!snapshot.agentBuilderHistory && !snapshot.agentBuilderOperation) {
      continue;
    }
    const boundarySessionId =
      snapshot.agentBuilderHistory?.sessionId ??
      snapshot.agentBuilderOperation?.sessionId;
    if (!sessionId || !boundarySessionId || boundarySessionId === sessionId) {
      return index;
    }
  }
  return -1;
};

const reopenParameterGroup = (
  parameterGroup: AgentBuilderParameterGroup,
  taskId: string,
): AgentBuilderParameterGroup | null => {
  if (!parameterGroup.tasks.some((task) => task.task_id === taskId)) {
    return null;
  }
  return {
    ...structuredClone(parameterGroup),
    status: 'active',
    tasks: parameterGroup.tasks.map((task) =>
      task.task_id === taskId
        ? { ...structuredClone(task), status: 'active' }
        : structuredClone(task),
    ),
  };
};

const REENTERABLE_PARAMETER_STATUSES = new Set([
  'completed',
  'skipped',
  'deferred',
]);

const latestReenterableParameterTaskId = (
  parameterGroup: AgentBuilderParameterGroup | null,
) =>
  parameterGroup?.tasks.reduce<
    AgentBuilderParameterGroup['tasks'][number] | null
  >((latest, task) => {
    if (!REENTERABLE_PARAMETER_STATUSES.has(task.status)) {
      return latest;
    }
    if (!latest || task.stable_order > latest.stable_order) return task;
    if (
      task.stable_order === latest.stable_order &&
      task.task_id.localeCompare(latest.task_id) > 0
    ) {
      return task;
    }
    return latest;
  }, null)?.task_id ?? null;

const parameterPresentation = (
  parameterGroup: AgentBuilderParameterGroup | null,
  lastManuallyConfiguredTaskId: string | null,
  completionEligible: boolean,
): AgentBuilderHistoryContext['presentation'] => {
  if (!parameterGroup) return 'none';
  if (parameterGroup.status === 'canceled') return 'canceled';
  if (
    completionEligible &&
    parameterGroup.status === 'completed' &&
    lastManuallyConfiguredTaskId
  ) {
    return 'completed';
  }
  if (
    parameterGroup.status === 'active' ||
    parameterGroup.tasks.some((task) =>
      ['active', 'invalid'].includes(task.status),
    )
  ) {
    return 'active';
  }
  return 'none';
};

const syncActiveWorkflow = (
  workflows: Workflow[],
  activeWorkflowId: string,
  nodes: Node[],
  edges: Edge[],
  features?: Features,
) =>
  workflows.map((workflow) =>
    workflow.id === activeWorkflowId
      ? { ...workflow, nodes, edges, ...(features ? { features } : {}) }
      : workflow,
  );

const shouldRecordEdgeChanges = (changes: EdgeChange[]) =>
  changes.some((change) => change.type !== 'select');

const isDraggingPositionChange = (change: NodeChange) =>
  change.type === 'position' &&
  'dragging' in change &&
  change.dragging === true;

const isPersistedNodeChange = (change: NodeChange) =>
  change.type !== 'select' && change.type !== 'dimensions';

const shouldRecordCompletedNodeChanges = (changes: NodeChange[]) =>
  changes.some(
    (change) =>
      isPersistedNodeChange(change) && !isDraggingPositionChange(change),
  );

const REFERENCE_FIELD_KEYS = new Set([
  'value_selector',
  'variable_selector',
  'source_selector',
  'target_selector',
]);

const remapSelectorValue = (
  value: unknown,
  idMap: Map<string, string>,
): unknown => {
  if (typeof value === 'string') {
    return idMap.get(value) || value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) =>
      index === 0 ? remapSelectorValue(item, idMap) : item,
    );
  }
  return value;
};

const remapCodeInputSource = (source: string, idMap: Map<string, string>) => {
  const separatorIndex = source.indexOf('.');
  if (separatorIndex <= 0) return source;

  const nodeId = source.slice(0, separatorIndex);
  const remappedNodeId = idMap.get(nodeId);
  if (!remappedNodeId) return source;

  return `${remappedNodeId}${source.slice(separatorIndex)}`;
};

const remapInputsArray = (
  inputs: unknown[],
  idMap: Map<string, string>,
): unknown[] =>
  inputs.map((input) => {
    if (!input || typeof input !== 'object') {
      return remapCopiedNodeReferences(input, idMap);
    }

    return Object.fromEntries(
      Object.entries(input).map(([key, item]) => [
        key,
        key === 'source' && typeof item === 'string'
          ? remapCodeInputSource(item, idMap)
          : remapCopiedNodeReferences(item, idMap),
      ]),
    );
  });

const remapCopiedNodeReferences = (
  value: unknown,
  idMap: Map<string, string>,
): unknown => {
  if (typeof value === 'string') {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => remapCopiedNodeReferences(item, idMap));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        REFERENCE_FIELD_KEYS.has(key)
          ? remapSelectorValue(item, idMap)
          : key === 'inputs' && Array.isArray(item)
            ? remapInputsArray(item, idMap)
            : remapCopiedNodeReferences(item, idMap),
      ]),
    );
  }
  return value;
};

const preparePastedNodeData = (
  data: Node['data'],
  idMap: Map<string, string>,
  nodeType: Node['type'],
): Node['data'] => {
  const remappedData = remapCopiedNodeReferences(
    data,
    idMap,
  ) as Node['data'] & {
    displayNumber?: unknown;
  };
  delete remappedData.displayNumber;
  if (nodeType === 'slackPostNode') {
    const authConfig = remappedData.authConfig;
    if (
      authConfig &&
      typeof authConfig === 'object' &&
      !Array.isArray(authConfig) &&
      isWorkflowNodeSecretReference(
        (authConfig as Record<string, unknown>).token,
      )
    ) {
      const nextAuthConfig = { ...authConfig } as Record<string, unknown>;
      delete nextAuthConfig.token;
      remappedData.authConfig = nextAuthConfig;
    }
    if (isWorkflowNodeSecretReference(remappedData.url)) {
      delete remappedData.url;
    }
  } else if (
    nodeType === 'githubNode' &&
    isWorkflowNodeSecretReference(remappedData.api_token)
  ) {
    delete remappedData.api_token;
  }
  return remappedData;
};

const getInternalEdges = (edges: Edge[], nodeIds: Set<string>) =>
  edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));

const getEdgeKey = (edge: Pick<Edge, 'source' | 'target'> & Partial<Edge>) =>
  [
    edge.source,
    edge.sourceHandle || '',
    edge.target,
    edge.targetHandle || '',
  ].join('__');

const buildReconnectEdgesAfterDelete = (
  nodes: Node[],
  edges: Edge[],
  deletedNodeIds: Set<string>,
) => {
  const nextNodes = nodes.filter((node) => !deletedNodeIds.has(node.id));
  const nextNodeIds = new Set(nextNodes.map((node) => node.id));
  const nextEdges = edges.filter(
    (edge) =>
      !deletedNodeIds.has(edge.source) && !deletedNodeIds.has(edge.target),
  );
  const existingKeys = new Set(nextEdges.map(getEdgeKey));
  const reconnectEdges: Edge[] = [];

  for (const deletedNodeId of deletedNodeIds) {
    const incomingEdges = edges.filter(
      (edge) => edge.target === deletedNodeId && nextNodeIds.has(edge.source),
    );
    const outgoingEdges = edges.filter(
      (edge) => edge.source === deletedNodeId && nextNodeIds.has(edge.target),
    );

    for (const incomingEdge of incomingEdges) {
      for (const outgoingEdge of outgoingEdges) {
        if (incomingEdge.source === outgoingEdge.target) continue;

        const reconnectEdge: Edge = {
          id: `reconnect-${incomingEdge.source}-${outgoingEdge.target}-${Date.now()}-${reconnectEdges.length}`,
          source: incomingEdge.source,
          sourceHandle: incomingEdge.sourceHandle,
          target: outgoingEdge.target,
          targetHandle: outgoingEdge.targetHandle,
        };
        const edgeKey = getEdgeKey(reconnectEdge);
        if (existingKeys.has(edgeKey)) continue;

        const validation = validateConnection(
          nextNodes as AppNode[],
          [...nextEdges, ...reconnectEdges],
          {
            source: reconnectEdge.source,
            sourceHandle: reconnectEdge.sourceHandle ?? null,
            target: reconnectEdge.target,
            targetHandle: reconnectEdge.targetHandle ?? null,
          },
        );
        if (!validation.ok) continue;

        existingKeys.add(edgeKey);
        reconnectEdges.push(reconnectEdge);
      }
    }
  }

  return {
    nodes: nextNodes,
    edges: [...nextEdges, ...reconnectEdges],
  };
};

const buildDuplicatedGraphElements = (
  sourceNodes: Node[],
  sourceEdges: Edge[],
) => {
  const idMap = new Map<string, string>();
  const timestamp = Date.now();
  sourceNodes.forEach((node, index) => {
    idMap.set(node.id, `${node.id}-copy-${timestamp}-${index}`);
  });

  const duplicatedNodes = sourceNodes.map((node) => {
    const newId = idMap.get(node.id);
    return {
      ...structuredClone(node),
      id: newId || `${node.id}-copy-${timestamp}`,
      data: preparePastedNodeData(node.data, idMap, node.type),
      selected: true,
      position: {
        x: node.position.x + PASTE_OFFSET,
        y: node.position.y + PASTE_OFFSET,
      },
    } as Node;
  });

  const duplicatedEdges = sourceEdges
    .map((edge, index) => {
      const source = idMap.get(edge.source);
      const target = idMap.get(edge.target);
      if (!source || !target) return null;
      return {
        ...structuredClone(edge),
        id: `${edge.id}-copy-${timestamp}-${index}`,
        source,
        target,
        selected: false,
      } as Edge;
    })
    .filter((edge): edge is Edge => edge !== null);

  return { duplicatedNodes, duplicatedEdges };
};

type InternalWorkflowState = WorkflowState & {
  undoStack: GraphSnapshot[];
  redoStack: GraphSnapshot[];
  copiedNodes: Node[];
  copiedEdges: Edge[];
  pendingDragStartSnapshot: GraphSnapshot | null;
  pendingAgentBuilderApplySnapshot: GraphSnapshot | null;
  pendingAgentBuilderApplyCreatedBoundary: boolean;
};

const removeLegacyDeletableFlag = (node: Node): Node => {
  const nodeWithoutDeletable = { ...node } as Node & { deletable?: unknown };
  delete nodeWithoutDeletable.deletable;
  return nodeWithoutDeletable;
};

const createInitialFeatures = (): Features => ({
  [NODE_NUMBER_FEATURE_KEY]: 1,
});
const createDefaultFeatures = (): Features => ({
  [NODE_NUMBER_FEATURE_KEY]: 2,
});

// Initial data
const initialNodes: Node[] = DEFAULT_NODES;
const initialEdges: Edge[] = [];

const initialWorkflows: Workflow[] = [
  {
    id: 'default',
    appId: '',
    nodes: initialNodes,
    edges: initialEdges,
    features: createDefaultFeatures(),
    viewport: { x: 0, y: 0, zoom: 1 },
  },
];

export const useWorkflowStore = create<InternalWorkflowState>((set, get) => ({
  // === Editor UI 상태 ===
  workflows: initialWorkflows,
  activeWorkflowId: initialWorkflows[0]?.id || '',
  projectName: '',
  projectIcon: { type: 'emoji', content: '�', background_color: '#3b82f6' },
  projectDescription: '',
  projectApp: null,
  workflowAccess: null,
  interactiveMode: 'mouse',
  snapGridSize: DEFAULT_SNAP_GRID_SIZE,
  isSnapTemporarilyDisabled: false,
  isFullscreen: false,

  // === 설정 패널 상태 (초기값) ===
  isSettingsOpen: false,

  // === 버전 기록 상태 ===
  isVersionHistoryOpen: false,
  previewingVersion: null,
  lastDeployedAt: null,

  // === 테스트 패널 상태 ===
  isTestPanelOpen: false,
  testExecutionStatus: 'idle',
  testExecutionRunId: null,
  testSelectedNodeId: null,
  testExecutionStartedAt: null,
  testExecutionFinishedAt: null,
  testExecutionResult: null,
  testNodeResults: [],
  testExecutionError: null,
  currentExecutingNodeId: null,
  isTestUploading: false,

  // === 노드 전체화면 설정(NDV) 상태 ===
  fullscreenNodeId: null,
  fullscreenNodeSettingsSection: null,
  numberConnection: null,

  runTrigger: 0,
  triggerWorkflowRun: () =>
    set((state) => ({ runTrigger: state.runTrigger + 1 })),

  // === 그래프 데이터 ===
  nodes: initialNodes,
  edges: initialEdges,
  undoStack: [],
  redoStack: [],
  copiedNodes: [],
  copiedEdges: [],
  pendingDragStartSnapshot: null,
  pendingAgentBuilderApplySnapshot: null,
  pendingAgentBuilderApplyCreatedBoundary: false,
  features: createDefaultFeatures(),
  envVariables: [],
  runtimeVariables: [],
  hasUnsavedChanges: false,
  isAgentBuilderMutationSaving: false,
  agentBuilderMutationSaveCount: 0,
  pendingAgentBuilderRevert: null,
  recoveredAgentBuilderParameterGroup: null,
  agentBuilderHistoryNotice: null,
  canonicalDraftMetadata: {},

  // === Inner Node Selection ===
  selectedInnerNode: null,

  // === ReactFlow 액션 ===
  setNodes: (nodes) => {
    const { nodes: currentNodes, edges, workflows, activeWorkflowId } = get();
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      nodes,
      edges,
    );
    set((state) => ({
      nodes,
      workflows: updatedWorkflows,
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(currentNodes, edges),
      ],
      redoStack: [],
    }));
  },

  setEdges: (edges) => {
    const { nodes, edges: currentEdges, workflows, activeWorkflowId } = get();
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      nodes,
      edges,
    );
    set((state) => ({
      edges,
      workflows: updatedWorkflows,
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(nodes, currentEdges),
      ],
      redoStack: [],
    }));
  },

  addNode: (node, buildNodes) => {
    const { nodes, features, workflows, activeWorkflowId } = get();
    const { node: numberedNode, nextNodeDisplayNumber } = createNumberedNode(
      node,
      nodes,
    );
    const nextFeatures = {
      ...features,
      [NODE_NUMBER_FEATURE_KEY]: nextNodeDisplayNumber,
    };
    const nextNodes = buildNodes
      ? buildNodes(nodes, numberedNode)
      : [...nodes, numberedNode];
    const updatedWorkflows = workflows.map((w) =>
      w.id === activeWorkflowId
        ? { ...w, nodes: nextNodes, features: nextFeatures }
        : w,
    );

    set({
      nodes: nextNodes,
      features: nextFeatures,
      workflows: updatedWorkflows,
      hasUnsavedChanges: true,
    });

    return numberedNode;
  },

  addNodeWithEdge: (node, edge, buildNodes) => {
    const { nodes, edges, features, workflows, activeWorkflowId } = get();
    const { node: numberedNode, nextNodeDisplayNumber } = createNumberedNode(
      node,
      nodes,
    );
    const nextFeatures = {
      ...features,
      [NODE_NUMBER_FEATURE_KEY]: nextNodeDisplayNumber,
    };
    const nextNodes = buildNodes
      ? buildNodes(nodes, numberedNode)
      : [...nodes, numberedNode];
    const nextEdge: Edge = {
      ...edge,
      target: numberedNode.id,
    };
    const validation = validateConnection(nextNodes as AppNode[], edges, {
      source: nextEdge.source,
      sourceHandle: nextEdge.sourceHandle ?? null,
      target: nextEdge.target,
      targetHandle: nextEdge.targetHandle ?? null,
    });

    if (!validation.ok) {
      return null;
    }

    const nextEdges = [...edges, nextEdge];
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      nextNodes,
      nextEdges,
      nextFeatures,
    );

    set((state) => ({
      nodes: nextNodes,
      edges: nextEdges,
      features: nextFeatures,
      workflows: updatedWorkflows,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(nodes, edges),
      ],
      redoStack: [],
    }));

    return numberedNode;
  },

  onNodesChange: (changes: NodeChange[]) => {
    const currentNodes = get().nodes || [];
    const currentEdges = get().edges || [];
    const { snapGridSize, isSnapTemporarilyDisabled } = get();
    const pendingDragStartSnapshot = get().pendingDragStartSnapshot;
    // DB에 deletable:false로 저장된 노드도 삭제 가능하도록 속성 제거
    // TODO: 데이터 마이그레이션 후 제거 필요
    const deletableNodes = currentNodes.map(removeLegacyDeletableFlag);
    const positionChanges =
      snapGridSize === 'off' || isSnapTemporarilyDisabled
        ? changes
        : snapPositionChanges(changes, deletableNodes, snapGridSize);
    const newNodes = applyNodeChanges(positionChanges, deletableNodes);
    const { workflows, activeWorkflowId } = get();
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      newNodes as Node[],
      currentEdges,
    );
    const isDragging = changes.some(isDraggingPositionChange);
    const shouldRecord = shouldRecordCompletedNodeChanges(changes);
    const hasMeaningfulChange = changes.some(isPersistedNodeChange);
    const historySnapshot = pendingDragStartSnapshot
      ? pendingDragStartSnapshot
      : cloneGraph(currentNodes, currentEdges);

    set((state) => ({
      nodes: newNodes as Node[],
      workflows: updatedWorkflows,
      hasUnsavedChanges: hasMeaningfulChange ? true : state.hasUnsavedChanges,
      pendingDragStartSnapshot: isDragging
        ? state.pendingDragStartSnapshot ||
          cloneGraph(currentNodes, currentEdges)
        : null,
      ...(shouldRecord
        ? {
            undoStack: [
              ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
              historySnapshot,
            ],
            redoStack: [],
          }
        : {}),
    }));
  },

  onEdgesChange: (changes: EdgeChange[]) => {
    const currentEdges = get().edges || [];
    const currentNodes = get().nodes || [];
    const newEdges = applyEdgeChanges(changes, currentEdges);
    const { workflows, activeWorkflowId } = get();
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      currentNodes,
      newEdges,
    );
    const hasMeaningfulChange = changes.some(
      (change) => change.type !== 'select',
    );
    set((state) => ({
      edges: newEdges,
      workflows: updatedWorkflows,
      hasUnsavedChanges: hasMeaningfulChange ? true : state.hasUnsavedChanges,
      ...(shouldRecordEdgeChanges(changes)
        ? {
            undoStack: [
              ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
              cloneGraph(currentNodes, currentEdges),
            ],
            redoStack: [],
          }
        : {}),
    }));
  },

  onConnect: (connection: Connection) => {
    const currentEdges = get().edges || [];
    const currentNodes = get().nodes || [];
    const validation = validateConnection(
      currentNodes as AppNode[],
      currentEdges,
      connection,
    );

    if (!validation.ok) {
      set({ numberConnection: null });
      return;
    }

    const newEdges = addEdge(connection, currentEdges);
    const { workflows, activeWorkflowId } = get();
    const updatedWorkflows = syncActiveWorkflow(
      workflows,
      activeWorkflowId,
      currentNodes,
      newEdges,
    );
    set((state) => ({
      edges: newEdges,
      workflows: updatedWorkflows,
      numberConnection: null,
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(currentNodes, currentEdges),
      ],
      redoStack: [],
    }));
  },

  applyAgentBuilderGraphMutation: (mutation, sessionId, canonicalBaseGraph) => {
    const { nodes, edges, workflows, activeWorkflowId } = get();
    const baseNodes: Node[] = canonicalBaseGraph
      ? canonicalBaseGraph.nodes.map((node) => {
          const editorNode = nodes.find((current) => current.id === node.id);
          const displayNumber = (editorNode?.data as Record<string, unknown>)
            ?.displayNumber;
          const canonicalNode = structuredClone(node) as Node;
          return typeof displayNumber === 'number'
            ? ({
                ...structuredClone(node),
                data: {
                  ...structuredClone(node.data),
                  displayNumber,
                },
              } as Node)
            : canonicalNode;
        })
      : nodes;
    const baseEdges = canonicalBaseGraph
      ? structuredClone(canonicalBaseGraph.edges)
      : edges;
    const next = applyAgentBuilderOperations(
      baseNodes,
      baseEdges,
      mutation.operations,
    );
    const beforeMutation = cloneGraph(baseNodes, baseEdges);
    set((state) => {
      // Agent Builder operations come from the canonical server graph, which
      // deliberately excludes editor-only display numbers. Restore them only
      // in the canvas state so every generated node retains visible handles.
      const numbered = assignMissingNodeDisplayNumbers(
        next.nodes,
        state.features,
      );
      const existingBoundaryIndex = STRUCTURAL_AGENT_BUILDER_MUTATIONS.has(
        mutation.kind,
      )
        ? -1
        : findLatestAgentBuilderBoundaryIndex(state.undoStack, sessionId);
      const createdBoundary = existingBoundaryIndex < 0;
      const undoStack = createdBoundary
        ? [
            ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
            {
              ...beforeMutation,
              agentBuilderHistory: {
                sessionId,
                latestOperationId: mutation.operation_id,
                acknowledged: false,
                completionEligible: false,
                parameterGroup: null,
                lastManuallyConfiguredTaskId: null,
                presentation: 'none' as const,
              },
            },
          ]
        : state.undoStack;
      return {
        nodes: numbered.nodes,
        edges: next.edges,
        features: numbered.features,
        workflows: syncActiveWorkflow(
          workflows,
          activeWorkflowId,
          numbered.nodes,
          next.edges,
          numbered.features,
        ),
        hasUnsavedChanges: true,
        undoStack,
        redoStack: [],
        pendingDragStartSnapshot: null,
        pendingAgentBuilderApplySnapshot: beforeMutation,
        pendingAgentBuilderApplyCreatedBoundary: createdBoundary,
        agentBuilderHistoryNotice: null,
      };
    });
  },

  rollbackLatestAgentBuilderGraphMutation: () =>
    set((state) => {
      const previous =
        state.pendingAgentBuilderApplySnapshot ??
        state.undoStack[state.undoStack.length - 1];
      if (!previous) return {};
      return {
        nodes: previous.nodes,
        edges: previous.edges,
        workflows: syncActiveWorkflow(
          state.workflows,
          state.activeWorkflowId,
          previous.nodes,
          previous.edges,
        ),
        hasUnsavedChanges: false,
        undoStack: state.pendingAgentBuilderApplyCreatedBoundary
          ? state.undoStack.slice(0, -1)
          : state.undoStack,
        redoStack: [],
        pendingAgentBuilderRevert: null,
        pendingDragStartSnapshot: null,
        pendingAgentBuilderApplySnapshot: null,
        pendingAgentBuilderApplyCreatedBoundary: false,
      };
    }),

  setAgentBuilderMutationSaving: (saving) =>
    set((state) => {
      const agentBuilderMutationSaveCount = saving
        ? state.agentBuilderMutationSaveCount + 1
        : Math.max(0, state.agentBuilderMutationSaveCount - 1);
      return {
        agentBuilderMutationSaveCount,
        isAgentBuilderMutationSaving: agentBuilderMutationSaveCount > 0,
      };
    }),

  markLatestAgentBuilderMutationPersisted: (operation) =>
    set((state) => {
      const index = findLatestAgentBuilderBoundaryIndex(
        state.undoStack,
        operation.sessionId,
      );
      if (index < 0) return {};
      const undoStack = [...state.undoStack];
      const snapshot = undoStack[index];
      const existingOperation = snapshot.agentBuilderOperation;
      const originalRevertGraph =
        existingOperation?.revertGraph ?? operation.revertGraph;
      undoStack[index] = {
        ...snapshot,
        agentBuilderOperation: {
          ...operation,
          operationId: existingOperation?.operationId ?? operation.operationId,
          sessionId: existingOperation?.sessionId ?? operation.sessionId,
          ...(originalRevertGraph ? { revertGraph: originalRevertGraph } : {}),
        },
        agentBuilderHistory: {
          sessionId: operation.sessionId,
          latestOperationId: operation.operationId,
          acknowledged: false,
          completionEligible:
            snapshot.agentBuilderHistory?.completionEligible ?? false,
          parameterGroup: snapshot.agentBuilderHistory?.parameterGroup ?? null,
          lastManuallyConfiguredTaskId:
            snapshot.agentBuilderHistory?.lastManuallyConfiguredTaskId ?? null,
          presentation: snapshot.agentBuilderHistory?.presentation ?? 'none',
        },
      };
      return {
        undoStack,
        pendingAgentBuilderApplySnapshot: null,
        pendingAgentBuilderApplyCreatedBoundary: false,
      };
    }),

  markLatestAgentBuilderMutationAcknowledged: (
    operationId,
    completionEligible = false,
  ) =>
    set((state) => {
      const index = state.undoStack.findLastIndex(
        (snapshot) =>
          snapshot.agentBuilderOperation?.operationId === operationId ||
          snapshot.agentBuilderHistory?.latestOperationId === operationId,
      );
      if (index < 0) return {};
      const undoStack = [...state.undoStack];
      const snapshot = undoStack[index];
      undoStack[index] = {
        ...snapshot,
        agentBuilderHistory: {
          sessionId:
            snapshot.agentBuilderHistory?.sessionId ??
            snapshot.agentBuilderOperation?.sessionId,
          latestOperationId:
            snapshot.agentBuilderHistory?.latestOperationId ?? operationId,
          acknowledged: true,
          completionEligible:
            completionEligible ||
            snapshot.agentBuilderHistory?.completionEligible ||
            false,
          parameterGroup: snapshot.agentBuilderHistory?.parameterGroup ?? null,
          lastManuallyConfiguredTaskId:
            snapshot.agentBuilderHistory?.lastManuallyConfiguredTaskId ?? null,
          presentation: snapshot.agentBuilderHistory?.presentation ?? 'none',
        },
      };
      return { undoStack };
    }),

  setAgentBuilderParameterHistory: (
    sessionId,
    parameterGroup,
    _lastManuallyConfiguredTaskId,
    completionEligible = false,
  ) =>
    set((state) => {
      const index = findLatestAgentBuilderBoundaryIndex(
        state.undoStack,
        sessionId,
      );
      if (index < 0) return {};
      const undoStack = [...state.undoStack];
      const snapshot = undoStack[index];
      const reentryTaskId = latestReenterableParameterTaskId(parameterGroup);
      undoStack[index] = {
        ...snapshot,
        agentBuilderHistory: {
          sessionId,
          latestOperationId:
            snapshot.agentBuilderHistory?.latestOperationId ??
            snapshot.agentBuilderOperation?.operationId,
          acknowledged: snapshot.agentBuilderHistory?.acknowledged ?? false,
          completionEligible,
          parameterGroup: parameterGroup
            ? structuredClone(parameterGroup)
            : null,
          lastManuallyConfiguredTaskId: reentryTaskId,
          presentation: parameterPresentation(
            parameterGroup,
            reentryTaskId,
            completionEligible,
          ),
        },
      };
      return { undoStack };
    }),

  refreshNextAgentBuilderRevertBoundary: (boundary) =>
    set((state) => {
      const index = state.undoStack.length - 1;
      if (index < 0) return {};
      const snapshot = state.undoStack[index];
      const operation = snapshot.agentBuilderOperation;
      if (
        !operation ||
        operation.resultGraphHash !== boundary.resultGraphHash
      ) {
        return {};
      }
      const undoStack = [...state.undoStack];
      undoStack[index] = {
        ...snapshot,
        agentBuilderOperation: {
          ...operation,
          workflowUpdatedAt: boundary.workflowUpdatedAt,
        },
      };
      return { undoStack };
    }),

  clearPendingAgentBuilderRevert: () =>
    set({ pendingAgentBuilderRevert: null }),
  setRecoveredAgentBuilderParameterGroup: (recovery) =>
    set({ recoveredAgentBuilderParameterGroup: recovery }),
  clearAgentBuilderHistoryNotice: () =>
    set({ agentBuilderHistoryNotice: null }),

  undo: () => {
    const {
      undoStack,
      nodes,
      edges,
      activeWorkflowId,
      pendingAgentBuilderRevert,
      isAgentBuilderMutationSaving,
    } = get();
    if (pendingAgentBuilderRevert || isAgentBuilderMutationSaving) return;
    const previous = undoStack[undoStack.length - 1];
    if (!previous) return;

    const history = previous.agentBuilderHistory;
    if (
      previous.agentBuilderOperation &&
      history &&
      (!history.acknowledged || !history.completionEligible)
    ) {
      set({
        agentBuilderHistoryNotice:
          'Agent Builder 저장 확인이 끝난 뒤 Workflow Undo를 사용할 수 있습니다.',
      });
      return;
    }
    if (
      previous.agentBuilderOperation &&
      history?.acknowledged &&
      history.presentation === 'completed' &&
      history.parameterGroup?.status === 'completed' &&
      history.lastManuallyConfiguredTaskId
    ) {
      const reopenedGroup = reopenParameterGroup(
        history.parameterGroup,
        history.lastManuallyConfiguredTaskId,
      );
      if (reopenedGroup) {
        const nextUndoStack = [...undoStack];
        nextUndoStack[nextUndoStack.length - 1] = {
          ...previous,
          agentBuilderHistory: {
            ...history,
            presentation: 'reopened',
          },
        };
        set({
          undoStack: nextUndoStack,
          recoveredAgentBuilderParameterGroup: {
            sessionId:
              history.sessionId ?? previous.agentBuilderOperation.sessionId,
            parameterGroup: reopenedGroup,
          },
          agentBuilderHistoryNotice:
            '마지막 재편집 가능 설정 항목을 다시 열었습니다. Workflow graph와 저장 상태는 변경되지 않았습니다.',
        });
        return;
      }
    }

    const current = {
      ...cloneGraph(nodes, edges),
      agentBuilderOperation: previous.agentBuilderOperation,
      agentBuilderHistory: previous.agentBuilderHistory
        ? {
            ...previous.agentBuilderHistory,
            presentation: 'canceled' as const,
          }
        : undefined,
    };
    set((state) => ({
      nodes: previous.nodes,
      edges: previous.edges,
      workflows: syncActiveWorkflow(
        state.workflows,
        activeWorkflowId,
        previous.nodes,
        previous.edges,
      ),
      hasUnsavedChanges: true,
      undoStack: undoStack.slice(0, -1),
      redoStack: [...state.redoStack.slice(-(HISTORY_LIMIT - 1)), current],
      pendingAgentBuilderRevert:
        previous.agentBuilderOperation ?? state.pendingAgentBuilderRevert,
      pendingDragStartSnapshot: null,
      pendingAgentBuilderApplySnapshot: null,
      pendingAgentBuilderApplyCreatedBoundary: false,
      ...(history
        ? {
            recoveredAgentBuilderParameterGroup: {
              sessionId:
                history.sessionId ??
                previous.agentBuilderOperation?.sessionId ??
                '',
              parameterGroup: null,
            },
            agentBuilderHistoryNotice:
              'Agent Builder 실행 전 workflow로 되돌렸습니다. 설정 작업은 취소됩니다.',
          }
        : {}),
    }));
  },

  redo: () => {
    const {
      undoStack,
      redoStack,
      nodes,
      edges,
      activeWorkflowId,
      pendingAgentBuilderRevert,
      isAgentBuilderMutationSaving,
    } = get();
    if (pendingAgentBuilderRevert || isAgentBuilderMutationSaving) return;
    const currentBoundary = undoStack[undoStack.length - 1];
    const currentHistory = currentBoundary?.agentBuilderHistory;
    if (
      currentHistory?.presentation === 'reopened' &&
      currentHistory.parameterGroup?.status === 'completed'
    ) {
      const nextUndoStack = [...undoStack];
      nextUndoStack[nextUndoStack.length - 1] = {
        ...currentBoundary,
        agentBuilderHistory: {
          ...currentHistory,
          presentation: 'completed',
        },
      };
      set({
        undoStack: nextUndoStack,
        recoveredAgentBuilderParameterGroup: {
          sessionId:
            currentHistory.sessionId ??
            currentBoundary.agentBuilderOperation?.sessionId ??
            '',
          parameterGroup: structuredClone(currentHistory.parameterGroup),
        },
        agentBuilderHistoryNotice:
          '다시 연 설정 항목을 닫고 Agent Builder 완료 상태로 돌아왔습니다.',
      });
      return;
    }
    const next = redoStack[redoStack.length - 1];
    if (!next) return;

    const current = {
      ...cloneGraph(nodes, edges),
      ...(next.agentBuilderOperation
        ? { agentBuilderOperation: next.agentBuilderOperation }
        : {}),
      ...(next.agentBuilderHistory
        ? {
            agentBuilderHistory: {
              ...next.agentBuilderHistory,
              presentation: 'canceled' as const,
            },
          }
        : {}),
    };
    set((state) => ({
      nodes: next.nodes,
      edges: next.edges,
      workflows: syncActiveWorkflow(
        state.workflows,
        activeWorkflowId,
        next.nodes,
        next.edges,
      ),
      hasUnsavedChanges: true,
      undoStack: [...state.undoStack.slice(-(HISTORY_LIMIT - 1)), current],
      redoStack: redoStack.slice(0, -1),
      pendingAgentBuilderRevert: next.agentBuilderOperation
        ? null
        : state.pendingAgentBuilderRevert,
      pendingDragStartSnapshot: null,
      pendingAgentBuilderApplySnapshot: null,
      pendingAgentBuilderApplyCreatedBoundary: false,
      ...(next.agentBuilderHistory
        ? {
            recoveredAgentBuilderParameterGroup: {
              sessionId:
                next.agentBuilderHistory.sessionId ??
                next.agentBuilderOperation?.sessionId ??
                '',
              parameterGroup: null,
            },
            agentBuilderHistoryNotice:
              'Agent Builder 최종 graph를 다시 적용했습니다. 취소된 설정 작업은 다시 시작하지 않습니다.',
          }
        : {}),
    }));
  },

  copySelectedNodes: () => {
    const { nodes, edges } = get();
    const selectedNodes = nodes.filter((node) => node.selected);
    const selectedNodeIds = new Set(selectedNodes.map((node) => node.id));
    const selectedEdges = getInternalEdges(edges, selectedNodeIds);

    set({
      copiedNodes: structuredClone(selectedNodes),
      copiedEdges: structuredClone(selectedEdges),
    });
  },

  pasteCopiedNodes: () => {
    const {
      copiedNodes,
      copiedEdges,
      nodes,
      edges,
      activeWorkflowId,
      features,
    } = get();
    if (copiedNodes.length === 0) return;

    const { duplicatedNodes, duplicatedEdges } = buildDuplicatedGraphElements(
      copiedNodes,
      copiedEdges,
    );
    const numbered = assignNewNodeDisplayNumbers(
      duplicatedNodes,
      nodes,
      features,
    );

    const nextNodes = [
      ...nodes.map((node) => ({ ...node, selected: false }) as Node),
      ...numbered.nodes,
    ];
    const nextEdges = [
      ...edges.map((edge) => ({ ...edge, selected: false })),
      ...duplicatedEdges,
    ];

    set((state) => ({
      nodes: nextNodes,
      edges: nextEdges,
      features: numbered.features,
      workflows: syncActiveWorkflow(
        state.workflows,
        activeWorkflowId,
        nextNodes,
        nextEdges,
        numbered.features,
      ),
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(nodes, edges),
      ],
      redoStack: [],
    }));
  },

  duplicateSelectedNodes: () => {
    const { nodes, edges, activeWorkflowId, features } = get();
    const selectedNodes = nodes.filter((node) => node.selected);
    if (selectedNodes.length === 0) return;

    const selectedNodeIds = new Set(selectedNodes.map((node) => node.id));
    const selectedEdges = getInternalEdges(edges, selectedNodeIds);
    const { duplicatedNodes, duplicatedEdges } = buildDuplicatedGraphElements(
      selectedNodes,
      selectedEdges,
    );
    const numbered = assignNewNodeDisplayNumbers(
      duplicatedNodes,
      nodes,
      features,
    );

    const nextNodes = [
      ...nodes.map((node) => ({ ...node, selected: false }) as Node),
      ...numbered.nodes,
    ];
    const nextEdges = [
      ...edges.map((edge) => ({ ...edge, selected: false })),
      ...duplicatedEdges,
    ];

    set((state) => ({
      nodes: nextNodes,
      edges: nextEdges,
      features: numbered.features,
      workflows: syncActiveWorkflow(
        state.workflows,
        activeWorkflowId,
        nextNodes,
        nextEdges,
        numbered.features,
      ),
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(nodes, edges),
      ],
      redoStack: [],
    }));
  },

  hasSelectedElements: () => {
    const { nodes, edges } = get();
    return (
      nodes.some((node) => node.selected) || edges.some((edge) => edge.selected)
    );
  },

  deleteSelectedElements: () => {
    const { nodes, edges, activeWorkflowId } = get();
    const selectedNodeIds = new Set(
      nodes.filter((node) => node.selected).map((node) => node.id),
    );
    const selectedEdgeIds = new Set(
      edges.filter((edge) => edge.selected).map((edge) => edge.id),
    );

    if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return;

    const retainedEdges = edges.filter((edge) => !selectedEdgeIds.has(edge.id));
    const { nodes: nextNodes, edges: nextEdges } =
      buildReconnectEdgesAfterDelete(nodes, retainedEdges, selectedNodeIds);

    set((state) => ({
      nodes: nextNodes,
      edges: nextEdges,
      workflows: syncActiveWorkflow(
        state.workflows,
        activeWorkflowId,
        nextNodes,
        nextEdges,
      ),
      hasUnsavedChanges: true,
      undoStack: [
        ...state.undoStack.slice(-(HISTORY_LIMIT - 1)),
        cloneGraph(nodes, edges),
      ],
      redoStack: [],
    }));
  },

  clearSelection: () => {
    const { nodes, edges, workflows, activeWorkflowId } = get();
    const nextNodes = nodes.map((node) =>
      node.selected ? ({ ...node, selected: false } as Node) : node,
    );
    const nextEdges = edges.map((edge) =>
      edge.selected ? { ...edge, selected: false } : edge,
    );

    set({
      nodes: nextNodes,
      edges: nextEdges,
      workflows: syncActiveWorkflow(
        workflows,
        activeWorkflowId,
        nextNodes,
        nextEdges,
      ),
    });
  },

  setProjectInfo: (name, icon, description = '') =>
    set({
      projectName: name,
      projectIcon: icon,
      projectDescription: description,
    }),

  setProjectApp: (app) =>
    set({
      projectApp: app,
      projectName: app.name,
      projectIcon: app.icon,
      projectDescription: app.description || '',
    }),

  setWorkflowAccess: (workflowAccess) => set({ workflowAccess }),

  setInteractiveMode: (mode) => set({ interactiveMode: mode }),

  setSnapGridSize: (snapGridSize) => set({ snapGridSize }),

  setSnapTemporarilyDisabled: (isSnapTemporarilyDisabled) =>
    set({ isSnapTemporarilyDisabled }),

  toggleFullscreen: () =>
    set((state) => ({ isFullscreen: !state.isFullscreen })),

  // === 버전 기록 액션 ===
  toggleSettings: () =>
    set((state) => ({
      isSettingsOpen: !state.isSettingsOpen,
      isVersionHistoryOpen: false,
      isTestPanelOpen: false,
    })),

  toggleVersionHistory: () =>
    set((state) => ({
      isVersionHistoryOpen: !state.isVersionHistoryOpen,
      isSettingsOpen: false,
      isTestPanelOpen: false,
    })),

  toggleTestPanel: () =>
    set((state) => ({
      isTestPanelOpen: !state.isTestPanelOpen,
      isSettingsOpen: false,
      isVersionHistoryOpen: false,
    })),

  openTestPanel: () => {
    updateNodeFullscreenUrl(null, 'replace');
    set({
      isTestPanelOpen: true,
      isSettingsOpen: false,
      isVersionHistoryOpen: false,
      fullscreenNodeId: null,
      fullscreenNodeSettingsSection: null,
    });
  },

  beginTestExecution: () =>
    set({
      testExecutionStatus: 'running',
      testExecutionRunId: null,
      testSelectedNodeId: null,
      testExecutionStartedAt: Date.now(),
      testExecutionFinishedAt: null,
      testExecutionResult: null,
      testNodeResults: [],
      testExecutionError: null,
      currentExecutingNodeId: null,
      isTestUploading: false,
    }),

  setTestUploading: (isTestUploading) => set({ isTestUploading }),

  setCurrentExecutingNode: (currentExecutingNodeId) =>
    set({ currentExecutingNodeId }),

  setTestExecutionRunId: (testExecutionRunId) => set({ testExecutionRunId }),

  selectTestExecutionNode: (testSelectedNodeId) => set({ testSelectedNodeId }),

  addTestNodeResult: (result) =>
    set((state) => ({
      testNodeResults: [
        ...state.testNodeResults.filter(
          (item) => item.nodeId !== result.nodeId,
        ),
        result,
      ],
    })),

  finishTestExecution: (testExecutionResult) =>
    set({
      testExecutionStatus: 'success',
      testExecutionFinishedAt: Date.now(),
      testExecutionResult,
      currentExecutingNodeId: null,
      isTestUploading: false,
    }),

  failTestExecution: (testExecutionError) =>
    set({
      testExecutionStatus: 'failure',
      testExecutionFinishedAt: Date.now(),
      testExecutionError,
      currentExecutingNodeId: null,
      isTestUploading: false,
    }),

  restoreTestExecution: (execution) =>
    set({
      isTestPanelOpen: true,
      testExecutionStatus: execution.status,
      testExecutionRunId: execution.runId,
      testExecutionStartedAt: execution.startedAt,
      testExecutionFinishedAt: execution.finishedAt,
      testExecutionResult: execution.workflowResult,
      testNodeResults: execution.nodeResults,
      testExecutionError: execution.error,
      currentExecutingNodeId: null,
      isTestUploading: false,
    }),

  resetTestExecution: () =>
    set({
      testExecutionStatus: 'idle',
      testExecutionRunId: null,
      testSelectedNodeId: null,
      testExecutionStartedAt: null,
      testExecutionFinishedAt: null,
      testExecutionResult: null,
      testNodeResults: [],
      testExecutionError: null,
      currentExecutingNodeId: null,
      isTestUploading: false,
    }),

  // === 노드 전체화면 설정(NDV) 액션 ===
  openNodeFullscreen: (nodeId, section) => {
    updateNodeFullscreenUrl(nodeId, 'push');
    set({
      fullscreenNodeId: nodeId,
      fullscreenNodeSettingsSection: section ?? null,
      isSettingsOpen: false,
      isVersionHistoryOpen: false,
      isTestPanelOpen: false,
    });
  },

  closeNodeFullscreen: () => {
    updateNodeFullscreenUrl(null, 'replace');
    set({ fullscreenNodeId: null, fullscreenNodeSettingsSection: null });
  },

  syncNodeFullscreenFromUrl: (nodeId) =>
    set({
      fullscreenNodeId: nodeId,
      fullscreenNodeSettingsSection: null,
      ...(nodeId
        ? {
            isSettingsOpen: false,
            isVersionHistoryOpen: false,
            isTestPanelOpen: false,
          }
        : {}),
    }),

  startNumberConnection: (sourceNodeId, sourceHandleId) =>
    set({
      numberConnection: {
        sourceNodeId,
        sourceHandleId,
        input: '',
      },
    }),

  updateNumberConnectionInput: (input) =>
    set((state) => ({
      numberConnection: state.numberConnection
        ? { ...state.numberConnection, input }
        : null,
    })),

  cancelNumberConnection: () => set({ numberConnection: null }),

  previewVersion: (version) => {
    // 현재 스냅샷을 노드/엣지에 적용 (미리보기)
    const snapshot = version.graph_snapshot;
    set({
      previewingVersion: version,
      nodes: snapshot.nodes || [],
      edges: snapshot.edges || [],
    });
  },

  exitPreview: () => {
    // 미리보기 종료 시 현재 드래프트 상태로 복구
    // activeWorkflowId에 해당하는 데이터를 다시 로드
    const { workflows, activeWorkflowId } = get();
    const currentWorkflow = workflows.find((w) => w.id === activeWorkflowId);

    if (currentWorkflow) {
      set({
        previewingVersion: null,
        nodes: currentWorkflow.nodes,
        edges: currentWorkflow.edges,
      });
    } else {
      set({ previewingVersion: null });
    }
  },

  notifyDeploymentComplete: () => set({ lastDeployedAt: new Date() }),

  restoreVersion: async (version) => {
    const targetWorkflowId = get().activeWorkflowId;
    const releaseWorkflowSave = await acquireWorkflowDraftSave(
      targetWorkflowId,
      'version_restore',
    );

    try {
      const state = get();
      if (state.activeWorkflowId !== targetWorkflowId) return;
      const activeWorkflowId = targetWorkflowId;
      // 1. 스냅샷 데이터로 현재 드래프트 업데이트 API 호출
      const snapshot = version.graph_snapshot;
      const rawSnapshotFeatures =
        'features' in snapshot ? snapshot.features : null;
      const snapshotFeatures =
        rawSnapshotFeatures &&
        typeof rawSnapshotFeatures === 'object' &&
        !Array.isArray(rawSnapshotFeatures)
          ? (rawSnapshotFeatures as Features)
          : {};
      const snapshotNodes = (snapshot.nodes || []) as Node[];
      const hasFeatureNoteNodes = Object.prototype.hasOwnProperty.call(
        snapshotFeatures,
        'noteNodes',
      );
      if (
        hasFeatureNoteNodes &&
        !Array.isArray(snapshotFeatures.noteNodes)
      ) {
        throw new Error('Version snapshot contains invalid note nodes.');
      }
      const legacyNoteNodes = snapshotNodes.filter(
        (node) => node.type === 'note',
      );
      const currentNodeNotes = state.nodes.filter(
        (node) => node.type === 'note',
      );
      const currentFeatureNotes = Array.isArray(state.features?.noteNodes)
        ? (state.features.noteNodes as Node[])
        : [];
      const restoredNoteNodes = hasFeatureNoteNodes
        ? (snapshotFeatures.noteNodes as Node[])
        : legacyNoteNodes.length > 0
          ? legacyNoteNodes
          : currentNodeNotes.length > 0
            ? currentNodeNotes
            : currentFeatureNotes;
      const restoredNodes = [
        ...snapshotNodes.filter((node) => node.type !== 'note'),
        ...restoredNoteNodes,
      ];
      const restoredFeatures = {
        ...snapshotFeatures,
        noteNodes: restoredNoteNodes,
      };
      const normalized = assignMissingNodeDisplayNumbers(
        restoredNodes,
        restoredFeatures,
      );

      const canonical = await workflowApi.getDraftWorkflow(activeWorkflowId);
      get().ingestCanonicalDraftMetadata(canonical, activeWorkflowId);
      const restoredViewport = { x: 0, y: 0, zoom: 1 };
      const canonicalPayload = buildWorkflowDraftPayload(
        {
          nodes: normalized.nodes,
          edges: snapshot.edges || [],
          viewport: restoredViewport,
          features: normalized.features,
          envVariables: state.envVariables,
          runtimeVariables: state.runtimeVariables,
        },
        restoredViewport,
        { noteNodesSource: 'features' },
      );
      const saveResponse = await workflowApi.syncDraftWorkflow(
        activeWorkflowId,
        {
          ...canonicalPayload,
          expected_graph_hash: canonical.graph_hash,
          expected_updated_at: canonical.updated_at,
        },
      );
      get().ingestCanonicalDraftMetadata(saveResponse, activeWorkflowId);

      // 2. Store, local state 업데이트
      const { workflows } = get();
      const updatedWorkflows = workflows.map((w) =>
        w.id === activeWorkflowId
          ? {
              ...w,
              nodes: normalized.nodes,
              edges: snapshot.edges || [],
              features: normalized.features,
              viewport: restoredViewport,
            }
          : w,
      );

      set({
        workflows: updatedWorkflows,
        nodes: normalized.nodes,
        edges: snapshot.edges || [],
        features: normalized.features,
        previewingVersion: null, // 미리보기 종료
        hasUnsavedChanges: false,
      });
    } catch (error) {
      console.error('Failed to restore version:', error);
      throw error;
    } finally {
      releaseWorkflowSave();
    }
  },

  addWorkflow: async (workflow, appId) => {
    try {
      // Backend API 호출
      const created = await workflowApi.createWorkflow({
        app_id: appId,
      });

      // Store에 추가
      const newWorkflow: Workflow = {
        id: created.id,
        appId: created.app_id,
        nodes: [],
        edges: [],
        features: createInitialFeatures(),
        viewport: { x: 0, y: 0, zoom: 1 },
      };

      set((state) => ({
        workflows: [...state.workflows, newWorkflow],
      }));

      return created.id;
    } catch (error) {
      console.error('Failed to create workflow:', error);
      throw error;
    }
  },

  loadWorkflowsByApp: async (appId: string) => {
    try {
      const workflows = await workflowApi.listWorkflowsByApp(appId);
      const currentWorkflows = get().workflows;

      // Backend 워크플로우를 프론트엔드 포맷으로 변환
      const formattedWorkflows: Workflow[] = workflows.map((w) => {
        const existing = currentWorkflows.find((cw) => cw.id === w.id);
        return {
          id: w.id,
          appId: w.app_id,
          nodes: existing?.nodes?.length ? existing.nodes : [],
          edges: existing?.edges?.length ? existing.edges : [],
          features: existing?.features || createInitialFeatures(),
          viewport: existing?.viewport || { x: 0, y: 0, zoom: 1 },
        };
      });

      const activeWorkflow = formattedWorkflows.find(
        (w) => w.id === get().activeWorkflowId,
      );

      set({
        workflows: formattedWorkflows,
        ...(activeWorkflow ? { features: activeWorkflow.features } : {}),
      });
    } catch (error) {
      console.error('Failed to load workflows:', error);
      throw error;
    }
  },

  setActiveWorkflow: (id) => {
    const workflow = get().workflows.find((w) => w.id === id);
    if (workflow) {
      const isSameWorkflow = id === get().activeWorkflowId;
      set({
        activeWorkflowId: id,
        nodes: workflow.nodes,
        edges: workflow.edges,
        features: workflow.features,
        fullscreenNodeId: null,
        fullscreenNodeSettingsSection: null,
        undoStack: [],
        redoStack: [],
        ...(isSameWorkflow
          ? {}
          : {
              pendingAgentBuilderRevert: null,
              recoveredAgentBuilderParameterGroup: null,
              agentBuilderHistoryNotice: null,
              pendingAgentBuilderApplySnapshot: null,
              pendingAgentBuilderApplyCreatedBoundary: false,
              ...createIdleTestExecutionState(),
            }),
      });
    }
  },

  // **안전한 활성 워크플로우 ID 설정**
  // 대상 워크플로우 데이터가 로드되어 있으면 화면 store도 함께 전환합니다.
  // 아직 로드 전이면 ID만 바꿔 초기 빈 데이터로 화면을 덮어쓰지 않습니다.
  setActiveWorkflowIdSafe: (id: string) => {
    const isSameWorkflow = id === get().activeWorkflowId;
    const workflow = get().workflows.find((w) => w.id === id);

    if (!workflow) {
      set({
        activeWorkflowId: id,
        fullscreenNodeId: null,
        fullscreenNodeSettingsSection: null,
        undoStack: [],
        redoStack: [],
        ...(isSameWorkflow
          ? {}
          : {
              pendingAgentBuilderRevert: null,
              recoveredAgentBuilderParameterGroup: null,
              agentBuilderHistoryNotice: null,
              pendingAgentBuilderApplySnapshot: null,
              pendingAgentBuilderApplyCreatedBoundary: false,
              ...createIdleTestExecutionState(),
            }),
      });
      return;
    }

    const hasLoadedWorkflowData =
      workflow.nodes.length > 0 || workflow.edges.length > 0;

    set({
      activeWorkflowId: id,
      features: workflow.features,
      fullscreenNodeId: null,
      fullscreenNodeSettingsSection: null,
      undoStack: [],
      redoStack: [],
      ...(isSameWorkflow
        ? {}
        : {
            pendingAgentBuilderRevert: null,
            recoveredAgentBuilderParameterGroup: null,
            agentBuilderHistoryNotice: null,
            pendingAgentBuilderApplySnapshot: null,
            pendingAgentBuilderApplyCreatedBoundary: false,
            ...createIdleTestExecutionState(),
          }),
      ...(hasLoadedWorkflowData
        ? { nodes: workflow.nodes, edges: workflow.edges }
        : {}),
    });
  },

  deleteWorkflow: (id) => {
    const { workflows, activeWorkflowId } = get();
    const filteredWorkflows = workflows.filter((w) => w.id !== id);

    if (id === activeWorkflowId && filteredWorkflows.length > 0) {
      const newActive = filteredWorkflows[0];
      set({
        workflows: filteredWorkflows,
        activeWorkflowId: newActive.id,
        nodes: newActive.nodes,
        edges: newActive.edges,
        features: newActive.features,
        fullscreenNodeId: null,
        fullscreenNodeSettingsSection: null,
      });
    } else {
      set({ workflows: filteredWorkflows });
    }
  },

  updateWorkflowViewport: (id, viewport) => {
    const { workflows } = get();
    const updatedWorkflows = workflows.map((w) =>
      w.id === id ? { ...w, viewport } : w,
    );
    set({ workflows: updatedWorkflows });
  },

  // === 시작노드 검증 핼퍼 ===
  getStartNodeType: () => {
    const nodes = get().nodes;
    const startNode = nodes.find(
      (n) =>
        n.type === 'startNode' ||
        n.type === 'webhookTrigger' ||
        n.type === 'scheduleTrigger',
    );
    return startNode
      ? (startNode.type as 'startNode' | 'webhookTrigger' | 'scheduleTrigger')
      : null;
  },

  getStartNodeCount: () => {
    const nodes = get().nodes;
    return nodes.filter(
      (n) =>
        n.type === 'startNode' ||
        n.type === 'webhookTrigger' ||
        n.type === 'scheduleTrigger',
    ).length;
  },

  canPublish: () => {
    const count = get().getStartNodeCount();
    const access = get().workflowAccess;
    return count === 1 && access?.can_deploy !== false;
  },

  // === API 동기화 액션 ===
  setFeatures: (features) => {
    const { workflows, activeWorkflowId } = get();
    const updatedWorkflows = workflows.map((w) =>
      w.id === activeWorkflowId ? { ...w, features } : w,
    );
    set({ features, workflows: updatedWorkflows, hasUnsavedChanges: true });
  },
  setEnvVariables: (envVariables) =>
    set({ envVariables, hasUnsavedChanges: true }),
  setRuntimeVariables: (runtimeVariables) =>
    set({ runtimeVariables, hasUnsavedChanges: true }),
  setHasUnsavedChanges: (hasUnsavedChanges) => set({ hasUnsavedChanges }),
  setCanonicalDraftMetadata: (metadata) =>
    set((state) => ({
      canonicalDraftMetadata: {
        ...state.canonicalDraftMetadata,
        [metadata.workflowId]: metadata,
      },
    })),
  ingestCanonicalDraftMetadata: (value, workflowId, options) => {
    const metadata = canonicalDraftMetadataFrom(value, workflowId);
    if (metadata) {
      const projection =
        options?.applyDeferredProjection === false
          ? null
          : canonicalDeferredParametersFrom(value);
      set((state) => {
        const isActiveWorkflow =
          state.activeWorkflowId === metadata.workflowId;
        const nodes = projection && isActiveWorkflow
          ? reconcileCanonicalDeferredParameters(state.nodes, projection)
          : state.nodes;
        return {
          nodes,
          workflows: projection
            ? isActiveWorkflow && state.activeWorkflowId
              ? syncActiveWorkflow(
                  state.workflows,
                  state.activeWorkflowId,
                  nodes,
                  state.edges,
                  state.features,
                )
              : state.workflows.map((workflow) =>
                  workflow.id === metadata.workflowId
                    ? {
                        ...workflow,
                        nodes: reconcileCanonicalDeferredParameters(
                          workflow.nodes,
                          projection,
                        ),
                      }
                    : workflow,
                )
            : state.workflows,
          canonicalDraftMetadata: {
            ...state.canonicalDraftMetadata,
            [metadata.workflowId]: metadata,
          },
        };
      });
    }
    return metadata;
  },
  getCanonicalDraftMetadata: (workflowId) => {
    const targetId = workflowId || get().activeWorkflowId;
    return targetId ? (get().canonicalDraftMetadata[targetId] ?? null) : null;
  },
  clearCanonicalDraftMetadata: (workflowId) =>
    set((state) => {
      const targetId = workflowId || state.activeWorkflowId;
      if (!targetId || !state.canonicalDraftMetadata[targetId]) return {};
      const next = { ...state.canonicalDraftMetadata };
      delete next[targetId];
      return { canonicalDraftMetadata: next };
    }),

  updateNodeData: (nodeId, newData) => {
    set({
      hasUnsavedChanges: true,
      nodes: get().nodes.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: mergeNodeDataForExplicitEdit(
              node.data as Record<string, unknown>,
              newData,
            ),
          } as Node;
        }
        return node;
      }),
    });
  },

  updateNodeExecutionData: (nodeId, newData) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === nodeId
          ? ({
              ...node,
              data: { ...node.data, ...newData },
            } as Node)
          : node,
      ),
    }));
  },

  resetNodeExecutionData: () => {
    set((state) => ({
      nodes: state.nodes.map((node) => {
        const data = { ...node.data } as Record<string, unknown>;
        delete data.status;
        delete data.observability;
        return { ...node, data } as Node;
      }),
    }));
  },

  // === Inner Node Selection Methods ===
  setSelectedInnerNode: (parentNodeId, nodeId) =>
    set({ selectedInnerNode: { parentNodeId, nodeId } }),

  clearInnerNodeSelection: () => set({ selectedInnerNode: null }),

  updateInnerNodeData: (parentNodeId, nodeId, newData) => {
    set({
      hasUnsavedChanges: true,
      nodes: get().nodes.map((node) => {
        if (node.id === parentNodeId) {
          // Find and update the inner node within subGraph
          const subGraph = (node.data as any).subGraph;
          if (subGraph && subGraph.nodes) {
            const updatedSubNodes = subGraph.nodes.map((subNode: any) =>
              subNode.id === nodeId
                ? {
                    ...subNode,
                    data: mergeNodeDataForExplicitEdit(
                      subNode.data as Record<string, unknown>,
                      newData,
                    ),
                  }
                : subNode,
            );
            return {
              ...node,
              data: {
                ...node.data,
                subGraph: { ...subGraph, nodes: updatedSubNodes },
              },
            } as Node;
          }
        }
        return node;
      }),
    });
  },

  setWorkflowData: (data: any, workflowId?: string) => {
    const normalized = assignMissingNodeDisplayNumbers(
      (data.nodes || []) as Node[],
      data.features || {},
    );

    const { activeWorkflowId, workflows } = get();
    const targetId = workflowId || activeWorkflowId;
    const canonicalMetadata = canonicalDraftMetadataFrom(data, targetId);
    const shouldLoadIntoEditor =
      Boolean(targetId) && targetId === activeWorkflowId;

    if (shouldLoadIntoEditor && targetId) {
      set({
        activeWorkflowId: targetId,
        nodes: normalized.nodes,
        edges: data.edges || [],
        features: normalized.features,
        envVariables: data.envVariables || [],
        runtimeVariables: data.runtimeVariables || [],
        hasUnsavedChanges: false,
        undoStack: [],
        redoStack: [],
        pendingAgentBuilderRevert: null,
        recoveredAgentBuilderParameterGroup: null,
        agentBuilderHistoryNotice: null,
        pendingAgentBuilderApplySnapshot: null,
        pendingAgentBuilderApplyCreatedBoundary: false,
        ...(canonicalMetadata
          ? {
              canonicalDraftMetadata: {
                ...get().canonicalDraftMetadata,
                [canonicalMetadata.workflowId]: canonicalMetadata,
              },
            }
          : {}),
      });
    }

    if (targetId) {
      const exists = workflows.some((w) => w.id === targetId);
      let updatedWorkflows;

      if (exists) {
        updatedWorkflows = workflows.map((w) =>
          w.id === targetId
            ? {
                ...w,
                nodes: normalized.nodes,
                edges: data.edges || [],
                features: normalized.features,
                ...(data.viewport ? { viewport: data.viewport } : {}),
              }
            : w,
        );
      } else {
        updatedWorkflows = [
          ...workflows,
          {
            id: targetId,
            appId: data.appId ?? data.app_id ?? '',
            nodes: normalized.nodes,
            edges: data.edges || [],
            features: normalized.features,
            viewport: data.viewport || { x: 0, y: 0, zoom: 1 },
          },
        ];
      }
      set({ workflows: updatedWorkflows });
    }

    if (canonicalMetadata && !shouldLoadIntoEditor) {
      get().setCanonicalDraftMetadata(canonicalMetadata);
    }
  },
}));
