import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosPostMock = vi.hoisted(() => vi.fn());
const attachActiveOrganizationHeaderMock = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: axiosPostMock,
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

describe('FR-004/FR-005 Cost Optimizer compare API client', () => {
  beforeEach(() => {
    vi.resetModules();
    axiosPostMock.mockReset();
    attachActiveOrganizationHeaderMock.mockClear();
  });

  it('compare API client는 baseline_id와 candidate만 문서 계약 경로로 전송한다', async () => {
    axiosPostMock.mockResolvedValue({
      data: {
        comparison_id: 'comparison-1',
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        baseline: { baseline_id: 'baseline-1' },
        candidate: { status: 'success' },
        diff: {},
        downstream_compatibility: { state: 'unknown' },
      },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    await workflowApi.compareCostOptimizerCandidate(
      'workflow-1',
      'llm-triage',
      {
        baseline_id: 'baseline-1',
        candidate: {
          label: 'B',
          model_id: 'gpt-4.1-mini',
          parameters: { max_tokens: 800, temperature: 0.1 },
        },
      },
    );

    expect(axiosPostMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/compare',
      {
        baseline_id: 'baseline-1',
        candidate: {
          label: 'B',
          model_id: 'gpt-4.1-mini',
          parameters: { max_tokens: 800, temperature: 0.1 },
        },
      },
    );
    expect(axiosPostMock.mock.calls[0][1]).not.toHaveProperty('inputs');
  });
});
