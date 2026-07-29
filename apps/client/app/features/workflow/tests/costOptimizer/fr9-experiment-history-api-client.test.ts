import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosGetMock = vi.hoisted(() => vi.fn());
const attachActiveOrganizationHeaderMock = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: axiosGetMock,
      post: vi.fn(),
      patch: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    })),
  },
}));

vi.mock('@/lib/activeOrganization', () => ({
  attachActiveOrganizationHeader: attachActiveOrganizationHeaderMock,
}));

describe('FR-009 Cost Optimizer experiment history API client', () => {
  beforeEach(() => {
    vi.resetModules();
    axiosGetMock.mockReset();
    attachActiveOrganizationHeaderMock.mockClear();
  });

  it('experiment history API client는 문서 계약 경로와 필터 query를 전달한다', async () => {
    axiosGetMock.mockResolvedValue({
      data: {
        total: 1,
        limit: 20,
        offset: 0,
        items: [
          {
            experiment_id: 'comparison-1',
            workflow_id: 'workflow-1',
            node_id: 'llm-triage',
            candidates: [{ candidate_id: 'candidate-1', status: 'success' }],
          },
        ],
      },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    const result = await workflowApi.listCostOptimizerExperiments(
      'workflow-1',
      'llm-triage',
      {
        baseline_id: 'baseline-1',
        date_from: '2026-07-01T00:00:00Z',
        date_to: '2026-07-05T23:59:59Z',
        created_by: 'user-1',
        candidate_status: 'success',
        model: 'gpt-4.1-mini',
        is_applied: false,
        schema_status: 'pass',
        downstream_state: 'compatible',
        limit: 20,
        offset: 0,
      },
    );

    expect(axiosGetMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/experiments',
      {
        params: {
          baseline_id: 'baseline-1',
          date_from: '2026-07-01T00:00:00Z',
          date_to: '2026-07-05T23:59:59Z',
          created_by: 'user-1',
          candidate_status: 'success',
          model: 'gpt-4.1-mini',
          is_applied: false,
          schema_status: 'pass',
          downstream_state: 'compatible',
          limit: 20,
          offset: 0,
        },
      },
    );
    expect(result.total).toBe(1);
    expect(result.items[0]?.candidates[0]?.candidate_id).toBe('candidate-1');
  });

  it('experiment candidate 상세 API client는 목록 pagination과 무관한 단건 경로를 호출한다', async () => {
    axiosGetMock.mockResolvedValue({
      data: {
        experiment_id: 'comparison-101',
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        baseline_summary: { baseline_id: 'baseline-1' },
        candidate: {
          candidate_id: 'candidate-failed-1',
          status: 'schema_failed',
        },
      },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    const result = await workflowApi.getCostOptimizerExperimentCandidate(
      'workflow-1',
      'llm-triage',
      'comparison-101',
      'candidate-failed-1',
    );

    expect(axiosGetMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/experiments/comparison-101/candidates/candidate-failed-1',
    );
    expect(result.candidate.candidate_id).toBe('candidate-failed-1');
  });
});
