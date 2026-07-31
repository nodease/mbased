import type { AppNode } from '../types/Nodes';
import type { Edge } from '@xyflow/react';

const VERTICAL_SPACING = 250; // 노드 간 수직 간격
const HORIZONTAL_OFFSET = 500; // condition 노드로부터의 수평 거리

/**
 * Arrange nodes connected to a condition node in a vertical layout
 * @param conditionNode The condition node
 * @param nodes All nodes in the workflow
 * @param edges All edges in the workflow
 * @returns Updated nodes with arranged positions
 */
export function arrangeConditionNodeChildren(
  conditionNode: AppNode,
  nodes: AppNode[],
  edges: Edge[],
): AppNode[] {
  // Find all edges from the condition node with their source handles
  const connectedEdges = edges.filter(
    (edge) => edge.source === conditionNode.id,
  );

  if (connectedEdges.length === 0) {
    return nodes;
  }

  const caseOrder = new Map(
    ((conditionNode.data.cases as Array<{ id?: string }> | undefined) || [])
      .map((caseItem, index) => [caseItem.id, index + 1] as const)
      .filter(([caseId]) => Boolean(caseId)),
  );

  // Default is the top exit. Configured branches follow their configured order.
  const sortedEdges = [...connectedEdges].sort((a, b) => {
    const getHandleOrder = (handle?: string | null): number => {
      if (handle === 'default') return 0;
      return caseOrder.get(handle || '') ?? Number.MAX_SAFE_INTEGER;
    };

    return getHandleOrder(a.sourceHandle) - getHandleOrder(b.sourceHandle);
  });

  // Get condition node position
  const conditionPos = conditionNode.position;

  // Keep Default aligned with the condition and expand branches downward.
  const startY = conditionPos.y;

  // Create a map of node positions based on sorted order
  const nodePositions = new Map<string, { x: number; y: number }>();
  sortedEdges.forEach((edge, index) => {
    nodePositions.set(edge.target, {
      x: conditionPos.x + HORIZONTAL_OFFSET,
      y: startY + index * VERTICAL_SPACING,
    });
  });

  // Update positions of connected nodes
  return nodes.map((node) => {
    const newPosition = nodePositions.get(node.id);
    if (!newPosition) {
      return node; // Not a connected node, keep original position
    }

    return {
      ...node,
      position: newPosition,
    };
  });
}
