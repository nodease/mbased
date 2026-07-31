import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { Edge } from '@xyflow/react';
import type { AppNode } from '../../types/Nodes';
import { calculateAutoLayout } from '../../utils/layoutHelpers';

type LayoutCase = {
  name: string;
  nodes: AppNode[];
  edges: Edge[];
  expected_positions: Record<string, { x: number; y: number }>;
};

const fixturePath = resolve(
  process.cwd(),
  '../../tests/fixtures/workflow_layout_cases.json',
);
const fixtures = JSON.parse(readFileSync(fixturePath, 'utf8')) as {
  cases: LayoutCase[];
};

describe('calculateAutoLayout canonical fixtures', () => {
  it.each(fixtures.cases)('$name', ({ nodes, edges, expected_positions }) => {
    const layouted = calculateAutoLayout(nodes, edges);
    const positions = Object.fromEntries(
      layouted.map((node) => [node.id, node.position]),
    );

    expect(positions).toEqual(expected_positions);
  });

  it('uses measured condition size before explicit size so adjacent layers do not overlap', () => {
    const measured = { width: 880, height: 520 };
    const condition = {
      id: 'condition',
      type: 'conditionNode',
      position: { x: 0, y: 0 },
      width: 300,
      height: 180,
      measured,
      data: {
        cases: [
          { id: 'case-a' },
          { id: 'case-b' },
          { id: 'case-c' },
          { id: 'case-d' },
        ],
      },
    } as AppNode;
    const targets = ['default-target', 'case-a-target', 'case-b-target'].map(
      (id) =>
        ({
          id,
          type: 'llmNode',
          position: { x: 0, y: 0 },
          data: {},
        }) as AppNode,
    );
    const layouted = calculateAutoLayout(
      [condition, ...targets],
      [
        {
          id: 'edge-default',
          source: 'condition',
          sourceHandle: 'default',
          target: 'default-target',
        },
        {
          id: 'edge-a',
          source: 'condition',
          sourceHandle: 'case-a',
          target: 'case-a-target',
        },
        {
          id: 'edge-b',
          source: 'condition',
          sourceHandle: 'case-b',
          target: 'case-b-target',
        },
      ],
    );
    const byId = new Map(layouted.map((node) => [node.id, node]));
    const conditionRight = byId.get('condition')!.position.x + measured.width;
    const nextLayerX = byId.get('default-target')!.position.x;

    expect(nextLayerX).toBeGreaterThanOrEqual(conditionRight + 160);
    expect(byId.get('case-a-target')!.position.x).toBe(nextLayerX);
    expect(byId.get('case-b-target')!.position.x).toBe(nextLayerX);
  });
});
