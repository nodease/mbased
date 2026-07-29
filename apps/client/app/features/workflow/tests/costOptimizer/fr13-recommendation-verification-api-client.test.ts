import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosPostMock = vi.hoisted(() => vi.fn());
const attachActiveOrganizationHeaderMock = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: axiosPostMock,
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

describe('FR-013 추천 설정 인라인 검증 API client', () => {
  beforeEach(() => {
    vi.resetModules();
    axiosPostMock.mockReset();
    attachActiveOrganizationHeaderMock.mockClear();
  });

  it('추천 id와 freshness 정보를 Idempotency-Key와 함께 검증 endpoint로 전송한다', async () => {
    axiosPostMock.mockResolvedValue({
      data: { verification_status: 'completed', comparison_id: 'comparison-1' },
    });
    const { workflowApi } = await import('../../api/workflowApi');
    const api = workflowApi as typeof workflowApi & {
      verifyCostOptimizerRecommendations: (
        workflowId: string,
        nodeId: string,
        data: {
          recommendation_ids: string[];
          baseline_mode: 'latest_success';
          recommendation_policy_version?: string;
          recommendation_fingerprint?: string;
          node_config_fingerprint?: string;
        },
        idempotencyKey: string,
      ) => Promise<{ verification_status: string; comparison_id: string }>;
    };

    await api.verifyCostOptimizerRecommendations(
      'workflow-1',
      'llm-triage',
      {
        recommendation_ids: ['max_tokens', 'rag.top_k'],
        baseline_mode: 'latest_success',
        recommendation_policy_version: 'recommendation-v2',
        recommendation_fingerprint: 'recommendations-fingerprint',
        node_config_fingerprint: 'node-fingerprint',
      },
      'verify-1',
    );

    expect(axiosPostMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/recommendations/verify',
      {
        recommendation_ids: ['max_tokens', 'rag.top_k'],
        baseline_mode: 'latest_success',
        recommendation_policy_version: 'recommendation-v2',
        recommendation_fingerprint: 'recommendations-fingerprint',
        node_config_fingerprint: 'node-fingerprint',
      },
      { headers: { 'Idempotency-Key': 'verify-1' } },
    );
  });
});
