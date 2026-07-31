import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosPatchMock = vi.hoisted(() => vi.fn());
const attachActiveOrganizationHeaderMock = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: vi.fn(),
      patch: axiosPatchMock,
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

describe('FR-008 Cost Optimizer apply API client', () => {
  beforeEach(() => {
    vi.resetModules();
    axiosPatchMock.mockReset();
    attachActiveOrganizationHeaderMock.mockClear();
  });

  it('apply API client는 candidate_settings를 문서 계약 경로로 전송한다', async () => {
    axiosPatchMock.mockResolvedValue({
      data: {
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        applied: true,
        downstream_compatibility: { state: 'compatible', label: '검증 가능' },
          graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-14T00:00:00Z',
      },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    await workflowApi.applyCostOptimizerCandidate(
      'workflow-1',
      'llm-triage',
      {
        comparison_id: 'comparison-1',
        candidate_settings: {
          label: 'B',
          model_id: 'gpt-4.1-mini',
          parameters: { max_tokens: 800, temperature: 0.1 },
        },
        acknowledge_downstream_warning: true,
        expected_graph_hash: 'b'.repeat(64),
        expected_updated_at: '2026-07-14T00:00:00Z',
      },
    );

    expect(axiosPatchMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/apply',
      {
        comparison_id: 'comparison-1',
        candidate_settings: {
          label: 'B',
          model_id: 'gpt-4.1-mini',
          parameters: { max_tokens: 800, temperature: 0.1 },
        },
        acknowledge_downstream_warning: true,
        expected_graph_hash: 'b'.repeat(64),
        expected_updated_at: '2026-07-14T00:00:00Z',
      },
    );
  });

  it('추천 적용 API client는 선택한 추천 ID를 전송한다', async () => {
    axiosPatchMock.mockResolvedValue({
      data: {
        workflow_id: 'workflow-1',
        node_id: 'llm-triage',
        applied: true,
        downstream_compatibility: { state: 'unknown', label: '판정 전' },
          graph_hash: 'c'.repeat(64),
        updated_at: '2026-07-14T00:00:01Z',
      },
    });
    const { workflowApi } = await import('../../api/workflowApi');

    await workflowApi.applyCostOptimizerRecommendations(
      'workflow-1',
      'llm-triage',
      {
        recommendation_ids: ['max_tokens', 'rag.top_k'],
        expected_graph_hash: 'd'.repeat(64),
        expected_updated_at: '2026-07-14T00:00:01Z',
      },
    );

    expect(axiosPatchMock).toHaveBeenCalledWith(
      '/workflows/workflow-1/llm-nodes/llm-triage/cost-optimizer/apply-recommendations',
      {
        recommendation_ids: ['max_tokens', 'rag.top_k'],
        expected_graph_hash: 'd'.repeat(64),
        expected_updated_at: '2026-07-14T00:00:01Z',
      },
    );
  });
});
