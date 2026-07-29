import { describe, expect, it } from 'vitest';

import type { Node } from '../types/Nodes';
import {
  collectDeploymentLlmNodes,
  publicChatConsumerSelectionKey,
} from './publicChatConversationConsumers';

describe('collectDeploymentLlmNodes', () => {
  it('collects top-level and nested loop LLM nodes with canonical paths', () => {
    const nodes = [
      {
        id: 'answer',
        type: 'llmNode',
        data: { title: '상위 답변' },
      },
      {
        id: 'loop-1',
        type: 'loopNode',
        data: {
          title: '항목 반복',
          subGraph: {
            nodes: [
              {
                id: 'answer',
                type: 'llmNode',
                data: { title: '중첩 답변' },
              },
              {
                id: 'loop-2',
                type: 'loopNode',
                data: {
                  title: '세부 반복',
                  subGraph: {
                    nodes: [
                      {
                        id: 'deep-answer',
                        type: 'llmNode',
                        data: { title: '최종 답변' },
                      },
                    ],
                    edges: [],
                  },
                },
              },
            ],
            edges: [],
          },
        },
      },
    ] as unknown as Node[];

    const consumers = collectDeploymentLlmNodes(nodes);

    expect(consumers).toEqual([
      {
        id: 'answer',
        title: '상위 답변',
        containerPath: [],
        selectionKey: publicChatConsumerSelectionKey('answer', []),
      },
      {
        id: 'answer',
        title: '항목 반복 / 중첩 답변',
        containerPath: [{ kind: 'loop', node_id: 'loop-1' }],
        selectionKey: publicChatConsumerSelectionKey('answer', [
          { kind: 'loop', node_id: 'loop-1' },
        ]),
      },
      {
        id: 'deep-answer',
        title: '항목 반복 / 세부 반복 / 최종 답변',
        containerPath: [
          { kind: 'loop', node_id: 'loop-1' },
          { kind: 'loop', node_id: 'loop-2' },
        ],
        selectionKey: publicChatConsumerSelectionKey('deep-answer', [
          { kind: 'loop', node_id: 'loop-1' },
          { kind: 'loop', node_id: 'loop-2' },
        ]),
      },
    ]);
    expect(new Set(consumers.map((node) => node.selectionKey)).size).toBe(3);
  });
});
