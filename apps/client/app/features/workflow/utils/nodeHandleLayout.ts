import type { CSSProperties } from 'react';

import { WORKFLOW_NODE_SIZE } from './workflowCanvasGeometry';

type NodeHandleSide = 'left' | 'right';

export const STANDARD_NODE_HANDLE_TOP = WORKFLOW_NODE_SIZE.height / 2;
export const CONDITION_NODE_HANDLE_GAP = 40;

const SMART_HANDLE_RADIUS = 16;
const CONDITION_NODE_BOTTOM_PADDING = 28;

export function getConditionNodeHandleTop(outputIndex: number): number {
  return STANDARD_NODE_HANDLE_TOP +
    Math.max(0, Math.floor(outputIndex)) * CONDITION_NODE_HANDLE_GAP;
}

export function getConditionNodeMinimumHeight(caseCount: number): number {
  const lastHandleTop = getConditionNodeHandleTop(
    Math.max(0, Math.floor(caseCount)),
  );
  return Math.max(
    WORKFLOW_NODE_SIZE.height,
    lastHandleTop + SMART_HANDLE_RADIUS + CONDITION_NODE_BOTTOM_PADDING,
  );
}

export function getStandardNodeHandleStyle(
  side: NodeHandleSide,
  override?: CSSProperties,
): CSSProperties {
  const horizontalStyle = { ...(override ?? {}) };
  delete horizontalStyle.left;
  delete horizontalStyle.right;
  delete horizontalStyle.top;
  delete horizontalStyle.transform;

  return {
    [side]: 0,
    ...horizontalStyle,
    top: STANDARD_NODE_HANDLE_TOP,
  };
}
