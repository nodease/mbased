import { useMemo } from 'react';
import { Edge } from '@xyflow/react';

import { useWorkflowStore } from '../store/useWorkflowStore';
import { AppNode } from '../types/Nodes';

export type NodeNavigationBadge = {
  id: string;
  label: string;
};

export type NodeNavigationItem = {
  node: AppNode;
  badges: NodeNavigationBadge[];
};

export type NodeNavigation = {
  previousNodes: NodeNavigationItem[];
  nextNodes: NodeNavigationItem[];
  primaryPreviousNode: NodeNavigationItem | null;
};

const SELECTOR_KEYS = new Set([
  'value_selector',
  'variable_selector',
  'source_selector',
  'target_selector',
]);

const getNodeTitle = (node: AppNode) => String(node.data?.title || node.id);

const getNodeCenter = (node: AppNode) => ({
  x: node.position.x + (node.measured?.width || node.width || 0) / 2,
  y: node.position.y + (node.measured?.height || node.height || 0) / 2,
});

const getDistance = (from: AppNode, to: AppNode) => {
  const fromCenter = getNodeCenter(from);
  const toCenter = getNodeCenter(to);
  const dx = fromCenter.x - toCenter.x;
  const dy = fromCenter.y - toCenter.y;
  return Math.sqrt(dx * dx + dy * dy);
};

const isSelectorTuple = (value: unknown): value is [string, unknown] =>
  Array.isArray(value) && typeof value[0] === 'string' && value.length >= 2;

const collectReferencedSourceIds = (
  value: unknown,
  sourceIds = new Set<string>(),
  parentKey?: string,
) => {
  if (isSelectorTuple(value) && parentKey && SELECTOR_KEYS.has(parentKey)) {
    sourceIds.add(value[0]);
    return sourceIds;
  }

  if (typeof value === 'string' && parentKey === 'source') {
    const separatorIndex = value.indexOf('.');
    if (separatorIndex > 0) {
      sourceIds.add(value.slice(0, separatorIndex));
    }
    return sourceIds;
  }

  if (Array.isArray(value)) {
    value.forEach((item) => collectReferencedSourceIds(item, sourceIds));
    return sourceIds;
  }

  if (value && typeof value === 'object') {
    Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
      collectReferencedSourceIds(item, sourceIds, key);
    });
  }

  return sourceIds;
};

const getEdgeLabel = (edge: Edge) => {
  const label = edge.label;
  if (typeof label === 'string' && label.trim()) return label.trim();
  if (typeof label === 'number') return String(label);

  const dataLabel = edge.data?.label;
  if (typeof dataLabel === 'string' && dataLabel.trim()) {
    return dataLabel.trim();
  }
  if (typeof dataLabel === 'number') return String(dataLabel);

  if (edge.sourceHandle) return edge.sourceHandle;
  return '';
};

const buildNavigationItems = (
  edges: Edge[],
  nodesById: Map<string, AppNode>,
  getConnectedNodeId: (edge: Edge) => string,
) => {
  const itemMap = new Map<string, NodeNavigationItem>();

  edges.forEach((edge) => {
    const nodeId = getConnectedNodeId(edge);
    const connectedNode = nodesById.get(nodeId);
    if (!connectedNode) return;

    const current = itemMap.get(nodeId) || {
      node: connectedNode,
      badges: [],
    };
    const label = getEdgeLabel(edge);
    if (label) {
      current.badges.push({ id: edge.id, label });
    }
    itemMap.set(nodeId, current);
  });

  return Array.from(itemMap.values()).sort((a, b) =>
    getNodeTitle(a.node).localeCompare(getNodeTitle(b.node), 'ko'),
  );
};

export function useNodeNavigation(nodeId: string | null | undefined) {
  const nodes = useWorkflowStore((state) => state.nodes) as AppNode[];
  const edges = useWorkflowStore((state) => state.edges);

  return useMemo<NodeNavigation>(() => {
    if (!nodeId) {
      return {
        previousNodes: [],
        nextNodes: [],
        primaryPreviousNode: null,
      };
    }

    const currentNode = nodes.find((item) => item.id === nodeId);
    if (!currentNode) {
      return {
        previousNodes: [],
        nextNodes: [],
        primaryPreviousNode: null,
      };
    }

    const nodesById = new Map(nodes.map((item) => [item.id, item]));
    const incomingEdges = edges.filter((edge) => edge.target === nodeId);
    const outgoingEdges = edges.filter((edge) => edge.source === nodeId);
    const previousNodes = buildNavigationItems(
      incomingEdges,
      nodesById,
      (edge) => edge.source,
    );
    const nextNodes = buildNavigationItems(
      outgoingEdges,
      nodesById,
      (edge) => edge.target,
    );

    const referencedSourceIds = collectReferencedSourceIds(currentNode.data);
    const primaryPreviousNode =
      previousNodes
        .map((item, index) => ({
          item,
          index,
          isReferenced: referencedSourceIds.has(item.node.id),
          distance: getDistance(currentNode, item.node),
        }))
        .sort((a, b) => {
          if (a.isReferenced !== b.isReferenced) {
            return a.isReferenced ? -1 : 1;
          }
          if (a.distance !== b.distance) return a.distance - b.distance;
          return a.index - b.index;
        })[0]?.item ?? null;

    return {
      previousNodes,
      nextNodes,
      primaryPreviousNode,
    };
  }, [edges, nodeId, nodes]);
}
