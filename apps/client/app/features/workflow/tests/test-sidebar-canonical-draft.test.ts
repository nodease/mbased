import { describe, expect, it } from 'vitest';

import type { WorkflowDraftRequest } from '../types/Workflow';
import {
  canonicalDraftMatchesSnapshot,
  workflowDraftSnapshotsEqual,
} from '../utils/workflowDraftComparison';

describe('TestSidebar canonical draft comparison', () => {
  it('ignores editor-only env and runtime variables omitted by the canonical draft response', () => {
    const snapshot: WorkflowDraftRequest = {
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
      envVariables: [
        {
          id: 'env-1',
          key: 'API_URL',
          value: 'https://example.invalid',
          type: 'string',
        },
      ],
      runtimeVariables: [
        { id: 'runtime-1', key: 'request_id', name: 'Request ID' },
      ],
    };
    const canonical = {
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
    };

    expect(canonicalDraftMatchesSnapshot(canonical, snapshot)).toBe(true);
  });

  it('keeps the editor dirty when env or runtime variables change during a save', () => {
    const saved: WorkflowDraftRequest = {
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
      envVariables: [{ id: 'env-1', key: 'MODE', value: 'before', type: 'string' }],
      runtimeVariables: [],
    };
    const latest: WorkflowDraftRequest = {
      ...saved,
      envVariables: [{ id: 'env-1', key: 'MODE', value: 'after', type: 'string' }],
    };

    expect(workflowDraftSnapshotsEqual(latest, saved)).toBe(false);
  });

  it('ignores server-derived configuration state when comparing a canonical draft', () => {
    const snapshot = {
      nodes: [
        {
          id: 'llm-1',
          type: 'llmNode',
          position: { x: 0, y: 0 },
          data: {
            model_id: 'gpt-5.5',
            configuration_state: 'unresolved',
          },
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
      envVariables: [],
      runtimeVariables: [],
    } as unknown as WorkflowDraftRequest;
    const canonical = {
      ...snapshot,
      nodes: [
        {
          ...snapshot.nodes[0],
          data: {
            ...snapshot.nodes[0].data,
            configuration_state: 'resolved',
          },
        },
      ],
    };

    expect(canonicalDraftMatchesSnapshot(canonical, snapshot)).toBe(true);
  });

  it('ignores viewport-only differences when checking the canonical graph before a test run', () => {
    const snapshot: WorkflowDraftRequest = {
      nodes: [],
      edges: [],
      viewport: { x: 320, y: -140, zoom: 1.75 },
      features: {},
      envVariables: [],
      runtimeVariables: [],
    };
    const canonical: WorkflowDraftRequest = {
      ...snapshot,
      viewport: { x: 0, y: 0, zoom: 1 },
    };

    expect(canonicalDraftMatchesSnapshot(canonical, snapshot)).toBe(true);
    expect(workflowDraftSnapshotsEqual(canonical, snapshot)).toBe(false);
  });

  it('ignores the next node number derived while loading a legacy canonical draft', () => {
    const canonical: WorkflowDraftRequest = {
      nodes: [
        {
          id: 'input-1',
          type: 'startNode',
          position: { x: 0, y: 0 },
          data: { displayNumber: 1 },
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      features: {},
      envVariables: [],
      runtimeVariables: [],
    } as unknown as WorkflowDraftRequest;
    const snapshot: WorkflowDraftRequest = {
      ...canonical,
      features: { nextNodeDisplayNumber: 2 },
    };

    expect(canonicalDraftMatchesSnapshot(canonical, snapshot)).toBe(true);
  });
});
