import { describe, expect, it } from 'vitest';

import {
  baselineFromExperimentSummary,
  costOptimizerHistoryListParams,
  experimentFromCandidateDetail,
} from '../../components/costOptimizer/costOptimizerHistoryModel';

describe('FR-009 Cost Optimizer history view model', () => {
  it('입력 중인 필터를 API query 계약으로 한 곳에서 정규화한다', () => {
    expect(
      costOptimizerHistoryListParams('baseline-1', {
        dateFrom: '2026-07-01',
        dateTo: '2026-07-05',
        createdBy: ' user-1 ',
        isApplied: 'false',
        candidateStatus: 'schema_failed',
        model: ' gpt-4.1-mini ',
        schemaStatus: 'failed',
        downstreamState: 'incompatible',
      }),
    ).toEqual({
      baseline_id: 'baseline-1',
      date_from: '2026-07-01T00:00:00',
      date_to: '2026-07-05T23:59:59',
      created_by: 'user-1',
      candidate_status: 'schema_failed',
      model: 'gpt-4.1-mini',
      is_applied: false,
      schema_status: 'failed',
      downstream_state: 'incompatible',
      limit: 20,
      offset: 0,
    });
  });

  it('단건 candidate 응답을 목록과 독립적인 선택용 experiment로 변환한다', () => {
    const experiment = experimentFromCandidateDetail({
      experiment_id: 'comparison-101',
      workflow_id: 'workflow-1',
      node_id: 'llm-triage',
      candidate: {
        candidate_id: 'candidate-failed-1',
        status: 'schema_failed',
      },
    });

    expect(experiment.candidates).toEqual([
      {
        candidate_id: 'candidate-failed-1',
        status: 'schema_failed',
      },
    ]);
  });

  it('experiment baseline summary를 화면에서 사용하는 baseline row로 변환한다', () => {
    const baseline = baselineFromExperimentSummary(
      'workflow-1',
      'llm-triage',
      {
        experiment_id: 'comparison-1',
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        created_at: '2026-07-05T01:30:00Z',
        baseline_summary: {
          baseline_id: 'baseline-1',
          workflow_run_id: 'run-1',
          model: 'gpt-4.1',
          cost: 0.001,
          total_tokens: 150,
          latency_ms: 1000,
        },
        candidates: [],
      },
    );

    expect(baseline).toEqual(
      expect.objectContaining({
        baseline_id: 'baseline-1',
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        model: 'gpt-4.1',
        total_tokens: 150,
      }),
    );
  });
});
