import type { NodeChange } from '@xyflow/react';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_BACKGROUND_GAP,
  getSnapBackgroundGap,
  snapPositionChanges,
} from './gridSnap';

type TestNode = {
  id: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};

const createNode = (
  id: string,
  position: { x: number; y: number },
): TestNode => ({
  id,
  position,
  data: {},
});

describe('gridSnap', () => {
  it('snap off 상태에서는 기존 background gap을 유지한다', () => {
    expect(getSnapBackgroundGap('off')).toBe(DEFAULT_BACKGROUND_GAP);
  });

  it('snap on 상태에서는 선택된 snap 크기로 background gap을 맞춘다', () => {
    expect(getSnapBackgroundGap(5)).toBe(5);
    expect(getSnapBackgroundGap(10)).toBe(10);
    expect(getSnapBackgroundGap(20)).toBe(20);
  });

  it('음수 좌표도 가장 가까운 grid로 snap한다', () => {
    const changes = snapPositionChanges(
      [
        {
          type: 'position',
          id: 'node-1',
          position: { x: -14, y: -26 },
        },
      ],
      [createNode('node-1', { x: -20, y: -20 })],
      10,
    );

    expect(changes[0]).toMatchObject({
      position: { x: -10, y: -30 },
    });
  });

  it('unknown node의 position change는 그대로 둔다', () => {
    const changes: NodeChange[] = [
      {
        type: 'position',
        id: 'unknown',
        position: { x: 104, y: 207 },
      },
    ];

    expect(snapPositionChanges(changes, [], 10)).toBe(changes);
  });

  it('position이 아닌 mixed change는 변경하지 않고 position만 snap한다', () => {
    const changes: NodeChange[] = [
      { type: 'select', id: 'node-1', selected: true },
      {
        type: 'position',
        id: 'node-1',
        position: { x: 104, y: 207 },
      },
    ];

    const snapped = snapPositionChanges(
      changes,
      [createNode('node-1', { x: 0, y: 0 })],
      10,
    );

    expect(snapped[0]).toBe(changes[0]);
    expect(snapped[1]).toMatchObject({
      position: { x: 100, y: 210 },
    });
  });

  it('다중 이동 change 순서가 node 순서와 달라도 그룹 상대 위치를 유지한다', () => {
    const snapped = snapPositionChanges(
      [
        {
          type: 'position',
          id: 'node-3',
          position: { x: 54, y: 27 },
        },
        {
          type: 'position',
          id: 'node-2',
          position: { x: 31, y: 48 },
        },
        {
          type: 'position',
          id: 'node-1',
          position: { x: 16, y: 23 },
        },
      ],
      [
        createNode('node-1', { x: 3, y: 7 }),
        createNode('node-2', { x: 18, y: 32 }),
        createNode('node-3', { x: 41, y: 11 }),
      ],
      10,
    );

    expect(snapped).toMatchObject([
      { id: 'node-3', position: { x: 58, y: 24 } },
      { id: 'node-2', position: { x: 35, y: 45 } },
      { id: 'node-1', position: { x: 20, y: 20 } },
    ]);
  });
});
