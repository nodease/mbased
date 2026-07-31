import type { Edge } from '@xyflow/react';
import type { AppNode } from '../types/Nodes';
import {
  snapCanvasPosition,
  WORKFLOW_NODE_GAP,
  WORKFLOW_NODE_SIZE,
} from './workflowCanvasGeometry';

const ORPHAN_GAP_X = 50;
const ORPHAN_GAP_Y = 50;
const MIN_ORPHAN_ROW_WIDTH = 1000;

type LayoutNode = AppNode & {
  measured?: {
    width?: number | null;
    height?: number | null;
  };
  width?: number;
  height?: number;
};

type SortKey = [number, number, number];

const getLayoutNodeSize = (node: AppNode) => {
  const layoutNode = node as LayoutNode;
  const measuredWidth = layoutNode.measured?.width;
  const measuredHeight = layoutNode.measured?.height;
  return {
    width:
      typeof measuredWidth === 'number' && measuredWidth > 0
        ? measuredWidth
        : typeof layoutNode.width === 'number' && layoutNode.width > 0
        ? layoutNode.width
        : WORKFLOW_NODE_SIZE.width,
    height:
      typeof measuredHeight === 'number' && measuredHeight > 0
        ? measuredHeight
        : typeof layoutNode.height === 'number' && layoutNode.height > 0
        ? layoutNode.height
        : WORKFLOW_NODE_SIZE.height,
  };
};

const compareSortKeys = (left: SortKey, right: SortKey) => {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return left[index] - right[index];
    }
  }
  return 0;
};

const assignLayers = (
  nodeIds: string[],
  edges: Edge[],
  nodeOrder: Map<string, number>,
) => {
  const incomingCount = new Map(nodeIds.map((nodeId) => [nodeId, 0]));
  const outgoing = new Map<string, string[]>();

  edges.forEach((edge) => {
    if (!incomingCount.has(edge.source) || !incomingCount.has(edge.target)) {
      return;
    }
    incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1);
    const targets = outgoing.get(edge.source) ?? [];
    targets.push(edge.target);
    outgoing.set(edge.source, targets);
  });

  outgoing.forEach((targets) => {
    targets.sort(
      (left, right) =>
        (nodeOrder.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (nodeOrder.get(right) ?? Number.MAX_SAFE_INTEGER),
    );
  });

  const queue = nodeIds
    .filter((nodeId) => incomingCount.get(nodeId) === 0)
    .sort(
      (left, right) =>
        (nodeOrder.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (nodeOrder.get(right) ?? Number.MAX_SAFE_INTEGER),
    );
  const layers = new Map(nodeIds.map((nodeId) => [nodeId, 0]));
  const processed = new Set<string>();

  while (queue.length > 0) {
    const nodeId = queue.shift();
    if (!nodeId) break;
    processed.add(nodeId);

    (outgoing.get(nodeId) ?? []).forEach((target) => {
      layers.set(
        target,
        Math.max(layers.get(target) ?? 0, (layers.get(nodeId) ?? 0) + 1),
      );
      const remaining = (incomingCount.get(target) ?? 0) - 1;
      incomingCount.set(target, remaining);
      if (remaining === 0) {
        queue.push(target);
        queue.sort(
          (left, right) =>
            (nodeOrder.get(left) ?? Number.MAX_SAFE_INTEGER) -
            (nodeOrder.get(right) ?? Number.MAX_SAFE_INTEGER),
        );
      }
    });
  }

  const processedLayers = [...processed].map(
    (nodeId) => layers.get(nodeId) ?? 0,
  );
  let nextCycleLayer =
    (processedLayers.length > 0 ? Math.max(...processedLayers) : -1) + 1;
  nodeIds.forEach((nodeId) => {
    if (processed.has(nodeId)) return;
    layers.set(nodeId, nextCycleLayer);
    nextCycleLayer += 1;
  });

  return layers;
};

const conditionBranchOrder = (
  edges: Edge[],
  nodeById: Map<string, AppNode>,
  nodeOrder: Map<string, number>,
) => {
  const orderedTargets = new Map<string, SortKey>();

  edges.forEach((edge) => {
    const sourceNode = nodeById.get(edge.source);
    if (!sourceNode || sourceNode.type !== 'conditionNode') return;

    const cases = Array.isArray(sourceNode.data.cases)
      ? (sourceNode.data.cases as Array<{ id?: string }>)
      : [];
    const caseOrder = new Map(
      cases
        .map((caseItem, index) => [caseItem.id, index + 1] as const)
        .filter(([caseId]) => Boolean(caseId)),
    );
    const sourceHandle = edge.sourceHandle ?? '';
    const branchOrder =
      sourceHandle === 'default'
        ? 0
        : (caseOrder.get(sourceHandle) ?? caseOrder.size + 1);
    const candidate: SortKey = [
      nodeOrder.get(edge.source) ?? Number.MAX_SAFE_INTEGER,
      branchOrder,
      nodeOrder.get(edge.target) ?? Number.MAX_SAFE_INTEGER,
    ];
    const current = orderedTargets.get(edge.target);
    if (!current || compareSortKeys(candidate, current) < 0) {
      orderedTargets.set(edge.target, candidate);
    }
  });

  return orderedTargets;
};

/** Apply the same deterministic layout contract used by backend apply/save. */
export function calculateAutoLayout(
  nodes: AppNode[],
  edges: Edge[],
): AppNode[] {
  const layoutedNodes = nodes.map((node) => ({ ...node }));
  const nodeById = new Map(
    layoutedNodes
      .filter((node) => node.type !== 'note')
      .map((node) => [node.id, node]),
  );
  const nodeOrder = new Map(
    [...nodeById.keys()].map((nodeId, index) => [nodeId, index]),
  );
  const validEdges = edges.filter(
    (edge) => nodeById.has(edge.source) && nodeById.has(edge.target),
  );
  const connectedIds = new Set(
    validEdges.flatMap((edge) => [edge.source, edge.target]),
  );
  const connectedOrder = [...nodeById.keys()].filter((nodeId) =>
    connectedIds.has(nodeId),
  );
  const orphanOrder = [...nodeById.keys()].filter(
    (nodeId) => !connectedIds.has(nodeId),
  );

  const layers = assignLayers(connectedOrder, validEdges, nodeOrder);
  const layerNodes = new Map<number, string[]>();
  connectedOrder.forEach((nodeId) => {
    const layer = layers.get(nodeId) ?? 0;
    const ids = layerNodes.get(layer) ?? [];
    ids.push(nodeId);
    layerNodes.set(layer, ids);
  });

  const branchOrder = conditionBranchOrder(validEdges, nodeById, nodeOrder);
  layerNodes.forEach((nodeIds) => {
    nodeIds.sort((left, right) => {
      const leftOrder = nodeOrder.get(left) ?? Number.MAX_SAFE_INTEGER;
      const rightOrder = nodeOrder.get(right) ?? Number.MAX_SAFE_INTEGER;
      return compareSortKeys(
        branchOrder.get(left) ?? [leftOrder, Number.POSITIVE_INFINITY, leftOrder],
        branchOrder.get(right) ?? [rightOrder, Number.POSITIVE_INFINITY, rightOrder],
      );
    });
  });

  const orderedLayers = [...layerNodes.keys()].sort((left, right) => left - right);
  const layerWidths = new Map<number, number>();
  const layerHeights = new Map<number, number>();
  orderedLayers.forEach((layer) => {
    const ids = layerNodes.get(layer) ?? [];
    const sizes = ids.map((nodeId) => getLayoutNodeSize(nodeById.get(nodeId)!));
    layerWidths.set(layer, Math.max(...sizes.map(({ width }) => width)));
    layerHeights.set(
      layer,
      sizes.reduce((total, { height }) => total + height, 0) +
        WORKFLOW_NODE_GAP.sibling * Math.max(0, ids.length - 1),
    );
  });

  const layerX = new Map<number, number>();
  let currentLayerX = 0;
  orderedLayers.forEach((layer) => {
    layerX.set(layer, currentLayerX);
    currentLayerX +=
      (layerWidths.get(layer) ?? WORKFLOW_NODE_SIZE.width) +
      WORKFLOW_NODE_GAP.rank;
  });

  orderedLayers.forEach((layer) => {
    let currentY = 0;
    (layerNodes.get(layer) ?? []).forEach((nodeId) => {
      const node = nodeById.get(nodeId)!;
      const { height } = getLayoutNodeSize(node);
      node.position = snapCanvasPosition({
        x: layerX.get(layer) ?? 0,
        y: currentY,
      });
      currentY += height + WORKFLOW_NODE_GAP.sibling;
    });
  });

  const maxConnectedHeight = Math.max(0, ...layerHeights.values());
  const connectedWidth = Math.max(
    MIN_ORPHAN_ROW_WIDTH,
    ...orderedLayers.map(
      (layer) =>
        (layerX.get(layer) ?? 0) +
        (layerWidths.get(layer) ?? WORKFLOW_NODE_SIZE.width),
    ),
  );
  const orphanRowWidth = Math.max(connectedWidth, MIN_ORPHAN_ROW_WIDTH);
  let orphanX = 0;
  let orphanY = maxConnectedHeight + WORKFLOW_NODE_GAP.rank;
  let currentRowHeight = 0;

  orphanOrder.forEach((nodeId) => {
    const node = nodeById.get(nodeId)!;
    const { width, height } = getLayoutNodeSize(node);
    node.position = snapCanvasPosition({ x: orphanX, y: orphanY });
    orphanX += width + ORPHAN_GAP_X;
    currentRowHeight = Math.max(currentRowHeight, height);
    if (orphanX > orphanRowWidth) {
      orphanX = 0;
      orphanY += currentRowHeight + ORPHAN_GAP_Y;
      currentRowHeight = 0;
    }
  });

  return layoutedNodes;
}
