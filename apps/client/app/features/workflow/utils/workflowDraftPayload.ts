import type { WorkflowDraftRequest } from '../types/Workflow';

const PRESENTATION_DATA_FIELDS = new Set([
  'displayNumber',
  'status',
  'observability',
  'configuration_state',
]);
const PRESENTATION_NODE_FIELDS = new Set([
  'dragging',
  'height',
  'measured',
  'positionAbsolute',
  'resizing',
  'selected',
  'width',
]);
const PRESENTATION_EDGE_FIELDS = new Set(['selected']);

const withoutEdgePresentation = (
  edge: WorkflowDraftRequest['edges'][number],
): WorkflowDraftRequest['edges'][number] => {
  const canonicalEdge = { ...edge } as Record<string, unknown>;
  for (const field of PRESENTATION_EDGE_FIELDS) delete canonicalEdge[field];
  return canonicalEdge as WorkflowDraftRequest['edges'][number];
};

const withoutExecutionPresentation = (
  node: WorkflowDraftRequest['nodes'][number],
): WorkflowDraftRequest['nodes'][number] => {
  const canonicalNode = { ...node } as Record<string, unknown>;
  for (const field of PRESENTATION_NODE_FIELDS) delete canonicalNode[field];
  const data = { ...node.data } as Record<string, unknown>;
  for (const field of PRESENTATION_DATA_FIELDS) delete data[field];
  const subGraph = data.subGraph;
  if (subGraph && typeof subGraph === 'object' && !Array.isArray(subGraph)) {
    const nested = subGraph as {
      nodes?: WorkflowDraftRequest['nodes'];
      edges?: WorkflowDraftRequest['edges'];
      [key: string]: unknown;
    };
    data.subGraph = {
      ...nested,
      nodes: Array.isArray(nested.nodes)
        ? nested.nodes.map(withoutExecutionPresentation)
        : [],
      edges: Array.isArray(nested.edges)
        ? nested.edges.map(withoutEdgePresentation)
        : [],
    };
  }
  canonicalNode.data = data;
  return canonicalNode as WorkflowDraftRequest['nodes'][number];
};

export const buildWorkflowDraftPayload = (
  data: WorkflowDraftRequest,
  viewport: WorkflowDraftRequest['viewport'],
  options: { noteNodesSource?: 'nodes' | 'features' } = {},
): WorkflowDraftRequest => {
  const realNodes = data.nodes
    .filter((node) => node.type !== 'note')
    .map(withoutExecutionPresentation);
  const noteNodesFromGraph = data.nodes.filter((node) => node.type === 'note');
  const featureNoteNodes = Array.isArray(data.features?.noteNodes)
    ? (data.features.noteNodes as WorkflowDraftRequest['nodes'])
    : [];
  const noteNodes = (
    options.noteNodesSource === 'features'
      ? featureNoteNodes
      : noteNodesFromGraph
  ).map(withoutExecutionPresentation);

  return {
    nodes: realNodes,
    edges: data.edges.map(withoutEdgePresentation),
    viewport,
    features: {
      ...data.features,
      noteNodes,
    },
    envVariables: data.envVariables,
    runtimeVariables: data.runtimeVariables,
  };
};
