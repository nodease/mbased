import type { Connection } from '@xyflow/react';
import type { Edge, WorkflowDraftRequest } from '../types/Workflow';
import type { AppNode } from '../types/Nodes';

export type GraphValidationIssueCode =
  | 'MISSING_SOURCE_NODE'
  | 'MISSING_TARGET_NODE'
  | 'START_NODE_HAS_INCOMING_EDGE'
  | 'TRIGGER_NODE_HAS_INCOMING_EDGE'
  | 'TERMINAL_NODE_HAS_OUTGOING_EDGE'
  | 'INVALID_CONDITION_SOURCE_HANDLE'
  | 'SLACK_LEGACY_CONFIGURATION'
  | 'SLACK_REMOVED_OUTPUT_SELECTOR'
  | 'KNOWLEDGE_REFERENCE_INVALID'
  | 'KNOWLEDGE_REFERENCE_LIMIT_EXCEEDED'
  | 'DUPLICATE_EDGE'
  | 'CYCLE_DETECTED';

export type GraphValidationIssue = {
  level: 'error' | 'warning';
  code: GraphValidationIssueCode;
  message: string;
  edgeId?: string;
  nodeId?: string;
  sourceNodeId?: string;
  targetNodeId?: string;
  sourceNodeTitle?: string;
  targetNodeTitle?: string;
};

export type GraphValidationResult = {
  ok: boolean;
  errors: GraphValidationIssue[];
  warnings: GraphValidationIssue[];
};

export type GraphSnapshot = WorkflowDraftRequest;

export type GraphCleanupResult = {
  graph: GraphSnapshot;
  removedIssues: GraphValidationIssue[];
  unresolvedIssues: GraphValidationIssue[];
};

const START_NODE_TYPES = new Set(['startNode']);
const TRIGGER_NODE_TYPES = new Set(['webhookTrigger', 'scheduleTrigger']);
const SOURCE_ONLY_NODE_TYPES = new Set([
  ...START_NODE_TYPES,
  ...TRIGGER_NODE_TYPES,
]);
const TERMINAL_NODE_TYPES = new Set(['answerNode', 'mailAcknowledgeNode']);
const MAX_KNOWLEDGE_REFERENCES_PER_TYPE = 20;
const CANONICAL_UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const getNodeTitle = (node?: AppNode) => {
  const title = String(node?.data?.title || '').trim();
  return title || node?.id || '알 수 없는 노드';
};

const getEdgeKey = (edge: Pick<Edge, 'source' | 'target'> & Partial<Edge>) =>
  [
    edge.source,
    edge.sourceHandle || '',
    edge.target,
    edge.targetHandle || '',
  ].join('__');

const toIssue = (
  code: GraphValidationIssueCode,
  message: string,
  edge: Edge,
  sourceNode?: AppNode,
  targetNode?: AppNode,
): GraphValidationIssue => ({
  level: 'error',
  code,
  message,
  edgeId: edge.id,
  nodeId: targetNode?.id || sourceNode?.id,
  sourceNodeId: edge.source,
  targetNodeId: edge.target,
  sourceNodeTitle: getNodeTitle(sourceNode),
  targetNodeTitle: getNodeTitle(targetNode),
});

const getConditionSourceHandles = (node: AppNode) => {
  const cases = Array.isArray(node.data?.cases) ? node.data.cases : [];
  return new Set([
    'default',
    ...cases
      .map((caseItem) =>
        typeof caseItem === 'object' && caseItem !== null
          ? String((caseItem as { id?: unknown }).id || '')
          : '',
      )
      .filter(Boolean),
  ]);
};

const createsCycle = (nodes: AppNode[], edges: Edge[]) => {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const graph = new Map<string, string[]>();

  for (const node of nodes) {
    graph.set(node.id, []);
  }

  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    graph.get(edge.source)?.push(edge.target);
  }

  const visiting = new Set<string>();
  const visited = new Set<string>();

  const visit = (nodeId: string): string | null => {
    if (visiting.has(nodeId)) return nodeId;
    if (visited.has(nodeId)) return null;

    visiting.add(nodeId);
    for (const nextNodeId of graph.get(nodeId) || []) {
      const cycleNodeId = visit(nextNodeId);
      if (cycleNodeId) return cycleNodeId;
    }
    visiting.delete(nodeId);
    visited.add(nodeId);
    return null;
  };

  for (const node of nodes) {
    const cycleNodeId = visit(node.id);
    if (cycleNodeId) return cycleNodeId;
  }

  return null;
};

const getDirectEdgeIssues = (
  nodes: AppNode[],
  edges: Edge[],
  options?: { includeDuplicateWarnings?: boolean },
) => {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const issues: GraphValidationIssue[] = [];
  const seenEdgeKeys = new Map<string, Edge>();

  for (const edge of edges) {
    const sourceNode = nodeMap.get(edge.source);
    const targetNode = nodeMap.get(edge.target);

    if (!sourceNode) {
      issues.push(
        toIssue(
          'MISSING_SOURCE_NODE',
          '존재하지 않는 노드에서 시작하는 연결입니다.',
          edge,
          sourceNode,
          targetNode,
        ),
      );
      continue;
    }

    if (!targetNode) {
      issues.push(
        toIssue(
          'MISSING_TARGET_NODE',
          '존재하지 않는 노드로 향하는 연결입니다.',
          edge,
          sourceNode,
          targetNode,
        ),
      );
      continue;
    }

    if (START_NODE_TYPES.has(targetNode.type || '')) {
      issues.push(
        toIssue(
          'START_NODE_HAS_INCOMING_EDGE',
          '입력 노드에는 다른 노드를 연결할 수 없습니다.',
          edge,
          sourceNode,
          targetNode,
        ),
      );
    } else if (TRIGGER_NODE_TYPES.has(targetNode.type || '')) {
      issues.push(
        toIssue(
          'TRIGGER_NODE_HAS_INCOMING_EDGE',
          '트리거 노드에는 다른 노드를 연결할 수 없습니다.',
          edge,
          sourceNode,
          targetNode,
        ),
      );
    }

    if (TERMINAL_NODE_TYPES.has(sourceNode.type || '')) {
      issues.push(
        toIssue(
          'TERMINAL_NODE_HAS_OUTGOING_EDGE',
          '종료 노드에서는 다른 노드로 연결할 수 없습니다.',
          edge,
          sourceNode,
          targetNode,
        ),
      );
    }

    if (sourceNode.type === 'conditionNode') {
      const sourceHandle =
        typeof edge.sourceHandle === 'string' ? edge.sourceHandle : '';
      if (!sourceHandle || !getConditionSourceHandles(sourceNode).has(sourceHandle)) {
        issues.push(
          toIssue(
            'INVALID_CONDITION_SOURCE_HANDLE',
            '존재하지 않는 IF/ELSE 분기에서 시작하는 연결입니다.',
            edge,
            sourceNode,
            targetNode,
          ),
        );
      }
    }

    if (options?.includeDuplicateWarnings) {
      const edgeKey = getEdgeKey(edge);
      const firstEdge = seenEdgeKeys.get(edgeKey);
      if (firstEdge) {
        issues.push({
          ...toIssue(
            'DUPLICATE_EDGE',
            '같은 노드 사이에 중복된 연결이 있습니다.',
            edge,
            sourceNode,
            targetNode,
          ),
          level: 'warning',
          edgeId: edge.id || firstEdge.id,
        });
      } else {
        seenEdgeKeys.set(edgeKey, edge);
      }
    }
  }

  return issues;
};

const getAllWorkflowNodes = (nodes: AppNode[]): AppNode[] => {
  const pending = [...nodes];
  const collected: AppNode[] = [];
  const seen = new Set<object>();
  while (pending.length > 0) {
    const node = pending.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    collected.push(node);
    const data = node.data as Record<string, unknown>;
    const subGraph = data.subGraph;
    if (!subGraph || typeof subGraph !== 'object') continue;
    const nestedNodes = (subGraph as { nodes?: unknown }).nodes;
    if (Array.isArray(nestedNodes)) pending.push(...(nestedNodes as AppNode[]));
  }
  return collected;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const hasOnlyKeys = (value: Record<string, unknown>, allowed: Set<string>) =>
  Object.keys(value).every((key) => allowed.has(key));

const isSafeDisplay = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 255 &&
  !Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127;
  });

const getKnowledgeReferenceIssues = (
  nodes: AppNode[],
): GraphValidationIssue[] =>
  getAllWorkflowNodes(nodes).flatMap((node) => {
    if (node.type !== 'llmNode') return [];
    const data = node.data as Record<string, unknown>;
    const issues: GraphValidationIssue[] = [];

    const validateList = (
      key: 'knowledgeBases' | 'knowledgeCollections',
      itemIsValid: (value: unknown) => boolean,
    ) => {
      if (!(key in data)) return;
      const value = data[key];
      if (!Array.isArray(value)) {
        issues.push({
          level: 'error',
          code: 'KNOWLEDGE_REFERENCE_INVALID',
          message: 'Knowledge 참조 형식이 올바르지 않습니다.',
          nodeId: node.id,
        });
        return;
      }
      if (value.length > MAX_KNOWLEDGE_REFERENCES_PER_TYPE) {
        issues.push({
          level: 'error',
          code: 'KNOWLEDGE_REFERENCE_LIMIT_EXCEEDED',
          message: `Knowledge 참조는 유형별로 최대 ${MAX_KNOWLEDGE_REFERENCES_PER_TYPE}개까지 선택할 수 있습니다.`,
          nodeId: node.id,
        });
        return;
      }
      if (!value.every(itemIsValid)) {
        issues.push({
          level: 'error',
          code: 'KNOWLEDGE_REFERENCE_INVALID',
          message: 'Knowledge 참조 형식이 올바르지 않습니다.',
          nodeId: node.id,
        });
      }
    };

    validateList('knowledgeBases', (value) => {
      if (!isRecord(value)) return false;
      const keys = Object.keys(value);
      return (
        keys.length === 2 &&
        hasOnlyKeys(value, new Set(['id', 'name'])) &&
        CANONICAL_UUID_PATTERN.test(String(value.id ?? '')) &&
        isSafeDisplay(value.name)
      );
    });
    validateList('knowledgeCollections', (value) => {
      if (!isRecord(value) || !('id' in value)) return false;
      const keys = Object.keys(value);
      return (
        keys.length >= 1 &&
        keys.length <= 2 &&
        hasOnlyKeys(value, new Set(['id', 'safeLabel'])) &&
        CANONICAL_UUID_PATTERN.test(String(value.id ?? '')) &&
        (!('safeLabel' in value) || isSafeDisplay(value.safeLabel))
      );
    });
    return issues;
  });

const getSlackCompatibilityIssues = (
  nodes: AppNode[],
): GraphValidationIssue[] =>
  getAllWorkflowNodes(nodes).flatMap((node) => {
    if (node.type !== 'slackPostNode') return [];
    const data = node.data as Record<string, unknown>;
    const mode = data.slackMode === 'webhook' ? 'webhook' : 'api';
    const hasLegacyAuthType =
      mode === 'api'
        ? data.authType !== undefined && data.authType !== 'bearer'
        : data.authType !== undefined && data.authType !== 'none';
    const issues: GraphValidationIssue[] = [];
    const hasCustomLegacyConfig =
      (data.method !== undefined && data.method !== 'POST') ||
      (Array.isArray(data.headers) && data.headers.length > 0) ||
      (typeof data.body === 'string' && data.body.trim() !== '') ||
      (data.timeout !== undefined && data.timeout !== 5000) ||
      hasLegacyAuthType ||
      (mode === 'api' &&
        data.url !== undefined &&
        data.url !== '' &&
        data.url !== 'https://slack.com/api/chat.postMessage');
    if (hasCustomLegacyConfig) {
      issues.push({
        level: 'warning',
        code: 'SLACK_LEGACY_CONFIGURATION',
        message:
          'Slack 노드의 기존 HTTP 설정은 새 전송 런타임에서 사용되지 않습니다.',
        nodeId: node.id,
      });
    }
    return issues;
  });

const hasRemovedSlackSelector = (
  value: unknown,
  slackNodeModes: Map<string, 'api' | 'webhook'>,
  seen: Set<object>,
): boolean => {
  if (!value || typeof value !== 'object') return false;
  if (seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((item) =>
      hasRemovedSlackSelector(item, slackNodeModes, seen),
    );
  }
  const record = value as Record<string, unknown>;
  return Object.entries(record).some(([key, item]) => {
    if (
      (key.endsWith('_selector') || key.endsWith('_selectors')) &&
      hasUnsupportedSlackSelector(item, slackNodeModes)
    ) {
      return true;
    }
    return hasRemovedSlackSelector(item, slackNodeModes, seen);
  });
};

const hasUnsupportedSlackSelector = (
  value: unknown,
  slackNodeModes: Map<string, 'api' | 'webhook'>,
): boolean => {
  if (!Array.isArray(value) || value.length === 0) return false;
  if (value.every((item) => typeof item === 'string')) {
    const sourceMode = slackNodeModes.get(String(value[0] || ''));
    const outputKey = String(value[1] || '');
    return (
      sourceMode !== undefined &&
      (['data', 'headers'].includes(outputKey) ||
        (sourceMode === 'webhook' && outputKey === 'message_ref'))
    );
  }
  return value.some((item) =>
    hasUnsupportedSlackSelector(item, slackNodeModes),
  );
};

const getRemovedSlackSelectorIssues = (
  nodes: AppNode[],
): GraphValidationIssue[] => {
  const allNodes = getAllWorkflowNodes(nodes);
  const slackNodeModes = new Map<string, 'api' | 'webhook'>(
    allNodes
      .filter((node) => node.type === 'slackPostNode')
      .map((node) => [
        node.id,
        (node.data as Record<string, unknown>).slackMode === 'webhook'
          ? 'webhook'
          : 'api',
      ]),
  );
  return allNodes.flatMap((node) => {
    return hasRemovedSlackSelector(node.data, slackNodeModes, new Set())
      ? [
          {
            level: 'error',
            code: 'SLACK_REMOVED_OUTPUT_SELECTOR' as const,
            message:
              'Slack 노드는 data/headers 출력을 제공하지 않으며 Webhook 모드에서는 message_ref를 제공하지 않습니다.',
            nodeId: node.id,
          },
        ]
      : [];
  });
};

export const validateWorkflowGraph = (
  graph: Pick<GraphSnapshot, 'nodes' | 'edges'>,
): GraphValidationResult => {
  const nodes = (graph.nodes || []) as AppNode[];
  const edges = (graph.edges || []) as Edge[];
  const directIssues = getDirectEdgeIssues(nodes, edges, {
    includeDuplicateWarnings: true,
  });
  const cycleNodeId = createsCycle(nodes, edges);

  const cycleIssue: GraphValidationIssue[] = cycleNodeId
    ? [
        {
          level: 'error',
          code: 'CYCLE_DETECTED',
          message: `워크플로우에 순환 연결이 있습니다. 문제 노드: ${getNodeTitle(
            nodes.find((node) => node.id === cycleNodeId),
          )}`,
          nodeId: cycleNodeId,
        },
      ]
    : [];

  const allIssues = [
    ...directIssues,
    ...getKnowledgeReferenceIssues(nodes),
    ...getSlackCompatibilityIssues(nodes),
    ...getRemovedSlackSelectorIssues(nodes),
    ...cycleIssue,
  ];
  const errors = allIssues.filter((issue) => issue.level === 'error');
  const warnings = allIssues.filter((issue) => issue.level === 'warning');

  return {
    ok: errors.length === 0,
    errors,
    warnings,
  };
};

export const cleanupInvalidEdges = (
  graph: GraphSnapshot,
): GraphCleanupResult => {
  const nodes = (graph.nodes || []) as AppNode[];
  const edges = (graph.edges || []) as Edge[];
  const directIssues = getDirectEdgeIssues(nodes, edges);
  const removableEdgeIds = new Set(
    directIssues
      .filter(
        (issue) =>
          issue.code !== 'DUPLICATE_EDGE' &&
          issue.edgeId &&
          issue.level === 'error',
      )
      .map((issue) => issue.edgeId),
  );
  const nextEdges = edges.filter((edge) => !removableEdgeIds.has(edge.id));
  const cleanedGraph = {
    ...graph,
    nodes,
    edges: nextEdges,
  };
  const validationAfterCleanup = validateWorkflowGraph(cleanedGraph);

  return {
    graph: cleanedGraph,
    removedIssues: directIssues.filter(
      (issue) => issue.edgeId && removableEdgeIds.has(issue.edgeId),
    ),
    unresolvedIssues: validationAfterCleanup.errors,
  };
};

export const validateConnection = (
  nodes: AppNode[],
  edges: Edge[],
  connection: Connection,
): GraphValidationResult => {
  if (!connection.source || !connection.target) {
    return {
      ok: false,
      errors: [
        {
          level: 'error',
          code: 'MISSING_TARGET_NODE',
          message: '연결할 노드를 찾을 수 없습니다.',
        },
      ],
      warnings: [],
    };
  }

  const nextEdge: Edge = {
    id: `__pending__${getEdgeKey({
      source: connection.source,
      sourceHandle: connection.sourceHandle,
      target: connection.target,
      targetHandle: connection.targetHandle,
    })}`,
    source: connection.source,
    target: connection.target,
    sourceHandle: connection.sourceHandle,
    targetHandle: connection.targetHandle,
  };

  return validateWorkflowGraph({
    nodes,
    edges: [...edges, nextEdge],
  });
};

export const formatGraphIssue = (issue: GraphValidationIssue) => {
  if (issue.sourceNodeTitle && issue.targetNodeTitle) {
    return `${issue.message} (${issue.sourceNodeTitle} -> ${issue.targetNodeTitle})`;
  }
  return issue.message;
};

export const hasIncomingHandle = (node: AppNode) =>
  !SOURCE_ONLY_NODE_TYPES.has(node.type || '');
