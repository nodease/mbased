import { DEFAULT_SNAP_GRID_SIZE, roundToGrid } from './gridSnap';

export const WORKFLOW_NODE_SIZE = {
  width: 420,
  height: 200,
} as const;

export const WORKFLOW_NODE_GAP = {
  rank: 160,
  sibling: 100,
  addAfterX: 520,
  addAfterY: 240,
  dragPreview: 100,
} as const;

export const snapCanvasCoordinate = (value: number) =>
  roundToGrid(value, DEFAULT_SNAP_GRID_SIZE);

export const snapCanvasPosition = (position: { x: number; y: number }) => ({
  x: snapCanvasCoordinate(position.x),
  y: snapCanvasCoordinate(position.y),
});
