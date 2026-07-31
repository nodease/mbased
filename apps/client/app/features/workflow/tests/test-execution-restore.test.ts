import { describe, expect, it } from 'vitest';

import { restoreTestExecutionFromWorkflowRun } from '../utils/testExecutionRestore';

describe('test execution restore', () => {
  it('저장된 workflow run을 새로고침 뒤 TestSidebar가 사용할 실행 결과로 복원한다', () => {
    const restored = restoreTestExecutionFromWorkflowRun({
      id: '11111111-1111-1111-1111-111111111111',
      workflow_id: 'workflow-1',
      user_id: 'user-1',
      status: 'success',
      trigger_mode: 'manual',
      outputs: { answer: '처리 완료' },
      started_at: '2026-07-14T01:00:00.000Z',
      finished_at: '2026-07-14T01:00:02.500Z',
      duration: 2.5,
      total_tokens: 120,
      total_cost: 0.0012,
      node_runs: [
        {
          id: 'node-run-1',
          node_id: 'llm-triage',
          node_type: 'llmNode',
          status: 'success',
          outputs: {
            text: '분류 완료',
            usage: { total_tokens: 120 },
            cost: 0.0012,
          },
          trace_metadata: {
            model_routing: { selected_model: 'gpt-5.6-mini' },
          },
          started_at: '2026-07-14T01:00:00.500Z',
          finished_at: '2026-07-14T01:00:02.000Z',
          duration: 1.5,
        },
      ],
    }, [
      {
        id: 'llm-triage',
        type: 'llmNode',
        position: { x: 0, y: 0 },
        data: { title: '문의 분류' },
      },
    ] as never);

    expect(restored.runId).toBe('11111111-1111-1111-1111-111111111111');
    expect(restored.status).toBe('success');
    expect(restored.workflowResult).toMatchObject({
      run_id: '11111111-1111-1111-1111-111111111111',
      output: { answer: '처리 완료' },
      duration: 2.5,
    });
    expect(restored.nodeResults).toEqual([
      expect.objectContaining({
        nodeId: 'llm-triage',
        title: '문의 분류',
        status: 'success',
        latencyMs: 1500,
        totalTokens: 120,
        totalCost: 0.0012,
        output: expect.objectContaining({
          metadata: {
            model_routing: { selected_model: 'gpt-5.6-mini' },
          },
        }),
        traceMetadata: {
          model_routing: { selected_model: 'gpt-5.6-mini' },
        },
      }),
    ]);
  });

  it('진행 중인 workflow run은 실패가 아니라 실행 중 상태로 복원한다', () => {
    const restored = restoreTestExecutionFromWorkflowRun(
      {
        id: '22222222-2222-2222-2222-222222222222',
        workflow_id: 'workflow-1',
        user_id: 'user-1',
        status: 'running',
        trigger_mode: 'manual',
        outputs: {},
        started_at: '2026-07-14T01:00:00.000Z',
        finished_at: null,
        node_runs: [],
      },
      [],
    );

    expect(restored.status).toBe('running');
    expect(restored.finishedAt).toBeNull();
  });
});
