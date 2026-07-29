import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosGetMock = vi.hoisted(() => vi.fn());
const attachActiveOrganizationHeaderMock = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: axiosGetMock,
      post: vi.fn(),
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

describe('FR-002 Cost Optimizer baseline API client', () => {
  beforeEach(() => {
    vi.resetModules();
    axiosGetMock.mockReset();
    attachActiveOrganizationHeaderMock.mockClear();
  });

  it('latest baseline API client는 문서 계약 경로를 호출한다', async () => {
    axiosGetMock.mockResolvedValue({
      data: { baseline: { baseline_id: 'baseline-1' } },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    const result = await (workflowApi as any).getCostOptimizerLatestBaseline(
      'workflow-1',
      'llm-triage',
    );

    expect(axiosGetMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/baselines/latest',
    );
    expect(result).toEqual({ baseline: { baseline_id: 'baseline-1' } });
  });

  it('baseline list API client는 검색/필터/정렬/pagination query를 전달한다', async () => {
    axiosGetMock.mockResolvedValue({
      data: { total: 0, limit: 10, offset: 20, items: [] },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    const result = await (workflowApi as any).listCostOptimizerBaselines(
      'workflow-1',
      'llm-triage',
      {
        q: 'billing',
        model: 'gpt-4.1-mini',
        date_from: '2026-07-01T00:00:00Z',
        date_to: '2026-07-04T23:59:59Z',
        sort: 'cost_desc',
        compare_available: true,
        limit: 10,
        offset: 20,
      },
    );

    expect(axiosGetMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/baselines',
      {
        params: {
          q: 'billing',
          model: 'gpt-4.1-mini',
          date_from: '2026-07-01T00:00:00Z',
          date_to: '2026-07-04T23:59:59Z',
          sort: 'cost_desc',
          compare_available: true,
          limit: 10,
          offset: 20,
        },
      },
    );
    expect(result).toEqual({ total: 0, limit: 10, offset: 20, items: [] });
  });
});
