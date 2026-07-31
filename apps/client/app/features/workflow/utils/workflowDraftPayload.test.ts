import { describe, expect, it } from 'vitest';

import type { WorkflowDraftRequest } from '../types/Workflow';
import { buildWorkflowDraftPayload } from './workflowDraftPayload';

describe('buildWorkflowDraftPayload', () => {
  it('execution presentation fields를 canonical draft에서 제거한다', () => {
    const draft = {
      nodes: [
        {
          id: 'code-1',
          type: 'codeNode',
          position: { x: 0, y: 0 },
          data: {
            title: '코드 실행',
            code: 'return inputs',
            configuration_state: 'resolved',
            status: 'success',
            observability: { latency_ms: 20 },
          },
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
      envVariables: [],
      runtimeVariables: [],
    } as unknown as WorkflowDraftRequest;

    const payload = buildWorkflowDraftPayload(draft, draft.viewport);

    expect(payload.nodes[0].data).toMatchObject({
      title: '코드 실행',
      code: 'return inputs',
    });
    expect(payload.nodes[0].data).not.toHaveProperty('status');
    expect(payload.nodes[0].data).not.toHaveProperty('observability');
    expect(payload.nodes[0].data).not.toHaveProperty('configuration_state');
  });

  it('removes editor-only fields recursively and preserves canonical note nodes', () => {
    const canonicalNote = {
      id: 'note-1',
      type: 'note',
      position: { x: 20, y: 20 },
      width: 240,
      height: 120,
      measured: { width: 240, height: 120 },
      selected: true,
      data: {
        text: 'server note',
        displayNumber: 9,
        configuration_state: 'resolved',
        status: 'success',
      },
    };
    const draft = {
      nodes: [
        {
          id: 'loop-1',
          type: 'loopNode',
          position: { x: 0, y: 0 },
          width: 320,
          height: 180,
          measured: { width: 320, height: 180 },
          dragging: true,
          data: {
            title: 'Loop',
            displayNumber: 1,
            configuration_state: 'resolved',
            status: 'running',
            observability: { latency_ms: 10 },
            subGraph: {
              nodes: [
                {
                  id: 'nested-1',
                  type: 'codeNode',
                  position: { x: 0, y: 0 },
                  width: 200,
                  height: 96,
                  measured: { width: 200, height: 96 },
                  positionAbsolute: { x: 10, y: 10 },
                  data: {
                    code: 'return inputs',
                    displayNumber: 2,
                    configuration_state: 'unresolved',
                    status: 'success',
                    observability: { latency_ms: 5 },
                  },
                },
              ],
              edges: [
                {
                  id: 'nested-edge',
                  source: 'nested-1',
                  target: 'nested-1',
                  selected: true,
                },
              ],
            },
          },
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: { noteNodes: [canonicalNote] },
      envVariables: [],
      runtimeVariables: [],
    } as unknown as WorkflowDraftRequest;

    const payload = buildWorkflowDraftPayload(draft, draft.viewport, {
      noteNodesSource: 'features',
    });
    const loopData = payload.nodes[0].data as Record<string, any>;
    const nestedData = loopData.subGraph.nodes[0].data;
    const noteData = (payload.features?.noteNodes?.[0] as any).data;

    expect(loopData).not.toHaveProperty('displayNumber');
    expect(loopData).not.toHaveProperty('configuration_state');
    expect(loopData).not.toHaveProperty('status');
    expect(loopData).not.toHaveProperty('observability');
    expect(nestedData).toEqual({ code: 'return inputs' });
    expect(loopData.subGraph.edges[0]).toEqual({
      id: 'nested-edge',
      source: 'nested-1',
      target: 'nested-1',
    });
    expect(noteData).toEqual({ text: 'server note' });
    for (const field of [
      'width',
      'height',
      'measured',
      'dragging',
      'selected',
      'positionAbsolute',
    ]) {
      expect(payload.nodes[0]).not.toHaveProperty(field);
      expect(loopData.subGraph.nodes[0]).not.toHaveProperty(field);
      expect(payload.features?.noteNodes?.[0]).not.toHaveProperty(field);
    }
  });
});
