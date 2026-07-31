import type { NodeChange } from '@xyflow/react';

export type SnapGridSize = 'off' | 5 | 10 | 20;
export type ActiveSnapGridSize = Exclude<SnapGridSize, 'off'>;

export const DEFAULT_SNAP_GRID_SIZE: ActiveSnapGridSize = 10;
export const DEFAULT_BACKGROUND_GAP = 16;
export const SNAP_GRID_SIZE_OPTIONS = [
  'off',
  5,
  DEFAULT_SNAP_GRID_SIZE,
  20,
] as const satisfies readonly SnapGridSize[];

type Position = { x: number; y: number };
type SnappableNode = {
  id: string;
  position: Position;
};

export const getSnapBackgroundGap = (snapGridSize: SnapGridSize) =>
  snapGridSize === 'off' ? DEFAULT_BACKGROUND_GAP : snapGridSize;

export const roundToGrid = (value: number, gridSize: ActiveSnapGridSize) =>
  Math.round(value / gridSize) * gridSize;

export const snapPositionChanges = (
  changes: NodeChange[],
  currentNodes: SnappableNode[],
  gridSize: ActiveSnapGridSize,
): NodeChange[] => {
  const positionChanges = changes.filter(
    (
      change,
    ): change is Extract<NodeChange, { type: 'position' }> =>
      change.type === 'position' && !!change.position,
  );

  if (positionChanges.length === 0) {
    return changes;
  }

  const currentNodeById = new Map(
    currentNodes.map((node) => [node.id, node] as const),
  );
  const changedNodes = positionChanges
    .map((change) => {
      const currentNode = currentNodeById.get(change.id);
      return currentNode && change.position
        ? { currentNode, nextPosition: change.position }
        : null;
    })
    .filter(
      (
        item,
      ): item is {
        currentNode: SnappableNode;
        nextPosition: Position;
      } => item !== null,
    );

  if (changedNodes.length === 0) {
    return changes;
  }

  const currentGroupOrigin = {
    x: Math.min(
      ...changedNodes.map(({ currentNode }) => currentNode.position.x),
    ),
    y: Math.min(
      ...changedNodes.map(({ currentNode }) => currentNode.position.y),
    ),
  };
  const nextGroupOrigin = {
    x: Math.min(...changedNodes.map(({ nextPosition }) => nextPosition.x)),
    y: Math.min(...changedNodes.map(({ nextPosition }) => nextPosition.y)),
  };
  const snappedGroupOrigin = {
    x: roundToGrid(nextGroupOrigin.x, gridSize),
    y: roundToGrid(nextGroupOrigin.y, gridSize),
  };
  const delta = {
    x: snappedGroupOrigin.x - currentGroupOrigin.x,
    y: snappedGroupOrigin.y - currentGroupOrigin.y,
  };

  return changes.map((change) => {
    if (change.type !== 'position' || !change.position) {
      return change;
    }

    const currentNode = currentNodeById.get(change.id);
    if (!currentNode) {
      return change;
    }

    return {
      ...change,
      position: {
        x: currentNode.position.x + delta.x,
        y: currentNode.position.y + delta.y,
      },
    };
  });
};
