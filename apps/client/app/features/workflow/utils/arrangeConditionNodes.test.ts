import { describe, expect, it } from 'vitest';
import type { Edge } from '@xyflow/react';

import type { AppNode } from '../types/Nodes';
import { arrangeConditionNodeChildren } from './arrangeConditionNodes';

const makeNode = (id: string, x: number, y: number): AppNode =>
  ({
    id,
    type: id === 'condition' ? 'conditionNode' : 'llmNode',
    position: { x, y },
    data: { title: id },
  }) as AppNode;

describe('arrangeConditionNodeChildren', () => {
  it('places Default above explicit branches', () => {
    const condition = makeNode('condition', 100, 400);
    const nodes = [
      condition,
      makeNode('case-one', 0, 0),
      makeNode('case-two', 0, 0),
      makeNode('default', 0, 0),
    ];
    const edges: Edge[] = [
      {
        id: 'case-one-edge',
        source: 'condition',
        sourceHandle: 'case-one-handle',
        target: 'case-one',
      },
      {
        id: 'case-two-edge',
        source: 'condition',
        sourceHandle: 'case-two-handle',
        target: 'case-two',
      },
      {
        id: 'default-edge',
        source: 'condition',
        sourceHandle: 'default',
        target: 'default',
      },
    ];

    const positioned = arrangeConditionNodeChildren(condition, nodes, edges);

    expect(positioned.find((node) => node.id === 'default')?.position).toEqual({
      x: 600,
      y: 400,
    });
    expect(positioned.find((node) => node.id === 'case-one')?.position).toEqual({
      x: 600,
      y: 650,
    });
    expect(positioned.find((node) => node.id === 'case-two')?.position).toEqual({
      x: 600,
      y: 900,
    });
  });
});
