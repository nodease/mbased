import { describe, expect, it } from 'vitest';
import type { Node } from '../types/Workflow';
import {
  assignMissingNodeDisplayNumbers,
  assignNewNodeDisplayNumbers,
  getNextNodeDisplayNumber,
} from './nodeNumbering';

const createNode = (
  id: string,
  displayNumber?: number,
  type: NonNullable<Node['type']> = 'startNode',
): Node =>
  ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: {
      title: id,
      ...(displayNumber ? { displayNumber } : {}),
    },
  }) as Node;

describe('nodeNumbering', () => {
  it('복제된 workflow node에는 기존 displayNumber 대신 새 번호를 발급한다', () => {
    const existingNodes = [createNode('node-1', 1), createNode('node-2', 2)];
    const copiedNodes = [createNode('node-1-copy', 1)];

    const result = assignNewNodeDisplayNumbers(copiedNodes, existingNodes, {
      nextNodeDisplayNumber: 3,
    });

    expect(result.nodes[0].data.displayNumber).toBe(3);
    expect(result.features.nextNodeDisplayNumber).toBe(4);
  });

  it('삭제로 비어 있는 한 자리 번호를 먼저 재사용한다', () => {
    const existingNodes = [createNode('node-1', 1), createNode('node-3', 3)];
    const copiedNodes = [createNode('node-copy', 1)];

    const result = assignNewNodeDisplayNumbers(copiedNodes, existingNodes, {
      nextNodeDisplayNumber: 4,
    });

    expect(result.nodes[0].data.displayNumber).toBe(2);
    expect(result.features.nextNodeDisplayNumber).toBe(4);
  });

  it('한 자리 번호가 모두 사용 중이면 앞자리가 분산되는 두 자리 번호를 발급한다', () => {
    const existingNodes = Array.from({ length: 9 }, (_, index) =>
      createNode(`node-${index + 1}`, index + 1),
    );

    const result = assignNewNodeDisplayNumbers(
      [
        createNode('node-copy-1', 1),
        createNode('node-copy-2', 1),
        createNode('node-copy-3', 1),
      ],
      existingNodes,
      { nextNodeDisplayNumber: 10 },
    );

    expect(result.nodes.map((node) => node.data.displayNumber)).toEqual([
      21,
      31,
      41,
    ]);
    expect(result.features.nextNodeDisplayNumber).toBe(51);
  });

  it('두 자리 번호도 같은 앞자리로 몰리지 않게 다음 번호를 고른다', () => {
    const existingNumbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 21, 31, 41];
    const existingNodes = existingNumbers.map((displayNumber) =>
      createNode(`node-${displayNumber}`, displayNumber),
    );

    expect(getNextNodeDisplayNumber(existingNodes)).toBe(51);
  });

  it('note는 번호 발급 대상에서 제외하고 기존 displayNumber도 제거한다', () => {
    const note = createNode('note-1', 99, 'note');
    const result = assignNewNodeDisplayNumbers(
      [note],
      [createNode('node-1', 1)],
      {
        nextNodeDisplayNumber: 2,
      },
    );

    expect(result.nodes[0].data.displayNumber).toBeUndefined();
    expect(result.features.nextNodeDisplayNumber).toBe(2);
  });

  it('로드 보정 시 번호 누락과 중복을 보정하고 note 번호는 제거한다', () => {
    const numbered = createNode('node-1', 1);
    const duplicate = createNode('node-2', 1, 'answerNode');
    const missing = createNode('node-3', undefined, 'llmNode');
    const note = createNode('note-1', 42, 'note');

    const result = assignMissingNodeDisplayNumbers(
      [numbered, duplicate, missing, note],
      { nextNodeDisplayNumber: 2 },
    );

    expect(result.nodes.map((node) => node.data.displayNumber)).toEqual([
      1,
      2,
      3,
      undefined,
    ]);
    expect(result.features.nextNodeDisplayNumber).toBe(4);
  });
});
