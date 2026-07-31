import { describe, expect, it } from 'vitest';

import {
  CONDITION_NODE_HANDLE_GAP,
  getConditionNodeHandleTop,
  getConditionNodeMinimumHeight,
  getStandardNodeHandleStyle,
  STANDARD_NODE_HANDLE_TOP,
} from './nodeHandleLayout';
import { WORKFLOW_NODE_SIZE } from './workflowCanvasGeometry';

describe('getStandardNodeHandleStyle', () => {
  it('keeps every base-node input handle on the shared vertical center', () => {
    expect(
      getStandardNodeHandleStyle('left', {
        left: '-12px',
        top: '56px',
        transform: 'translateY(-50%)',
      }),
    ).toEqual({
      left: 0,
      top: WORKFLOW_NODE_SIZE.height / 2,
    });
  });

  it('keeps every base-node output handle on the shared vertical center', () => {
    expect(
      getStandardNodeHandleStyle('right', {
        right: '-12px',
        top: '56px',
      }),
    ).toEqual({
      right: 0,
      top: WORKFLOW_NODE_SIZE.height / 2,
    });
  });
});

describe('condition-node handle layout', () => {
  it('aligns Default with the standard input handle', () => {
    expect(getConditionNodeHandleTop(0)).toBe(STANDARD_NODE_HANDLE_TOP);
  });

  it('adds each explicit branch below Default at a stable interval', () => {
    expect([
      getConditionNodeHandleTop(0),
      getConditionNodeHandleTop(1),
      getConditionNodeHandleTop(2),
    ]).toEqual([
      STANDARD_NODE_HANDLE_TOP,
      STANDARD_NODE_HANDLE_TOP + CONDITION_NODE_HANDLE_GAP,
      STANDARD_NODE_HANDLE_TOP + CONDITION_NODE_HANDLE_GAP * 2,
    ]);
  });

  it('grows the node enough to keep the final branch handle inside its shape', () => {
    expect(getConditionNodeMinimumHeight(0)).toBe(WORKFLOW_NODE_SIZE.height);
    expect(getConditionNodeMinimumHeight(3)).toBeGreaterThan(
      getConditionNodeHandleTop(3),
    );
  });
});
