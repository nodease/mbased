import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OptimizationRecommendationModal } from '../../components/costOptimizer/OptimizationRecommendationModal';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type { CostOptimizerRecommendationVerificationResponse } from '../../types/Api';

const workflowApiMock = vi.hoisted(() => ({
  getCostOptimizerParameterRecommendations: vi.fn(),
  verifyCostOptimizerRecommendations: vi.fn(),
  applyCostOptimizerCandidate: vi.fn(),
  applyCostOptimizerRecommendations: vi.fn(),
}));

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock('../../api/workflowApi', () => ({ workflowApi: workflowApiMock }));

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
}));

const recommendationResponse = {
  analysis_stage: 'recommendations_available',
  policy_version: 'recommendation-v2',
  recommendation_fingerprint: 'recommendations-fingerprint',
  recommendations: [
    {
      recommendation_type: 'llm_parameter',
      parameter_key: 'max_tokens',
      current_value: 2000,
      suggested_value: 600,
      apply_mode: 'experiment_required',
      candidate_patch: { parameters: { max_tokens: 600 } },
      reason: '최근 완료 토큰 사용량이 낮습니다.',
    },
  ],
  warnings: [],
  profile: { node_config_fingerprint: 'node-fingerprint' },
};

const verificationResponse = {
  verification_status: 'completed',
  comparison_id: 'comparison-1',
  candidate_id: 'candidate-1',
  baseline: {
    label: '최신 비교 가능한 성공 기록',
    executed_at: '2026-07-11T10:00:00.000Z',
    model: 'gpt-4.1',
    metrics: { cost: 0.02, latency_ms: 4200, total_tokens: 1800 },
  },
  candidate: {
    status: 'success',
    model: 'gpt-4.1-mini',
    metrics: { cost: 0.004, latency_ms: 1700, total_tokens: 700 },
  },
  metrics: {
    cost: { baseline: 0.02, candidate: 0.004, delta: -0.016 },
    latency_ms: { baseline: 4200, candidate: 1700, delta: -2500 },
    total_tokens: { baseline: 1800, candidate: 700, delta: -1100 },
  },
  quality_evaluation: {
    status: 'completed',
    baseline: { score: 78 },
    candidate: { score: 80 },
    delta: 2,
    dimensions: { clarity_consistency: { baseline: 77, candidate: 81 } },
    confidence: 'high',
    safe_summary: '후보 출력의 품질이 기준 실행과 비슷하거나 더 좋습니다.',
    judge_cost: 0.001,
  },
  schema_validation: { status: 'passed', issues: [] },
  downstream_compatibility: {
    state: 'compatible',
    label: '호환',
    contract_check: { checked_node_ids: ['answer-1'], warnings: [] },
  },
  incurred_cost: {
    candidate_execution_cost: 0.004,
    quality_judge_cost: 0.001,
    total_new_cost: 0.005,
    currency: 'USD',
  },
  apply: { allowed: true, requires_confirmation: false, reasons: [] },
  verification_context: {
    node_config_fingerprint: 'node-fingerprint',
    recommendation_policy_version: 'recommendation-v2',
  },
};

const staleVerificationResponse: CostOptimizerRecommendationVerificationResponse = {
  verification_status: 'stale',
  comparison_id: null,
  candidate_id: null,
  baseline: null,
  candidate: null,
  metrics: {},
  quality_evaluation: {
    status: 'unavailable',
    baseline: { score: null },
    candidate: { score: null },
    safe_summary: '추천 설정이 최신 node 설정과 일치하지 않습니다.',
  },
  schema_validation: { status: 'not_applicable', issues: [] },
  downstream_compatibility: { state: 'unknown' },
  incurred_cost: {
    candidate_execution_cost: null,
    quality_judge_cost: null,
    total_new_cost: null,
    currency: 'USD',
  },
  apply: {
    allowed: false,
    requires_confirmation: false,
    reasons: ['recommendation_stale'],
  },
};

const renderModal = () =>
  render(
    <OptimizationRecommendationModal
      workflowId="workflow-1"
      workflowName="고객 지원 자동화"
      llmNodes={[
        {
          id: 'llm-triage',
          title: '티켓 처리 판단',
          candidateDraft: {
            model_id: 'gpt-4.1',
            fallback_model_id: '',
            auto_model_routing: false,
            task_type: 'generate',
            system_prompt: '고객 지원을 처리합니다.',
            user_prompt: '{{message}}',
            assistant_prompt: '',
            referenced_variables: [],
            max_tokens: 2000,
            temperature: 0.2,
            top_p: 1,
            presence_penalty: 0,
            frequency_penalty: 0,
            stop: [],
            output_format: 'text',
            json_schema_fields: [],
            knowledgeBases: [],
            topK: 3,
            scoreThreshold: 0.5,
            dedupeRetrievedContext: false,
            retrievedContextMaxChars: null,
            retrievedContextCompression: 'off',
            answerGroundingCheck: 'off',
          },
        },
      ]}
      onClose={vi.fn()}
      onApplyPatches={vi.fn()}
    />,
  );

const clickTestButton = async () => {
  const button = await screen.findByRole('button', { name: '테스트하기' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

describe('FR-013 추천 설정 인라인 검증 모달', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workflowApiMock.getCostOptimizerParameterRecommendations.mockResolvedValue(
      recommendationResponse,
    );
    workflowApiMock.verifyCostOptimizerRecommendations.mockResolvedValue(
      verificationResponse,
    );
    useWorkflowStore.setState({
      canonicalDraftMetadata: {
        'workflow-1': {
          workflowId: 'workflow-1',
          graphHash: 'a'.repeat(64),
          updatedAt: '2026-07-14T00:00:00Z',
        },
      },
    });
    workflowApiMock.applyCostOptimizerCandidate.mockResolvedValue({
      applied: true,
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-14T00:00:01Z',
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('테스트하기는 페이지 이동 없이 최신 성공 기록 기준 검증 결과와 독립 metric bar를 표시한다', async () => {
    renderModal();

    await clickTestButton();

    await waitFor(() => {
      expect(workflowApiMock.verifyCostOptimizerRecommendations).toHaveBeenCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({
          recommendation_ids: ['max_tokens'],
          baseline_mode: 'latest_success',
          recommendation_policy_version: 'recommendation-v2',
          recommendation_fingerprint: 'recommendations-fingerprint',
          node_config_fingerprint: 'node-fingerprint',
        }),
        expect.any(String),
      );
    });

    expect(routerMock.push).not.toHaveBeenCalled();
    expect(await screen.findByText('최신 비교 가능한 성공 기록')).toBeInTheDocument();
    expect(screen.getByText('비용')).toBeInTheDocument();
    expect(screen.getByText('실행 시간')).toBeInTheDocument();
    expect(screen.getByText('전체 토큰')).toBeInTheDocument();
    expect(screen.getByText('출력 품질 점수')).toBeInTheDocument();
    expect(screen.getByText('스키마 검증')).toBeInTheDocument();
    expect(screen.getByText('후속 노드 호환성')).toBeInTheDocument();
    expect(screen.getByText('이번 검증에 새로 든 비용')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '적용하기' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '상세 비교 분석하기' })).toBeEnabled();
    expect(screen.getByTestId('recommendation-verification-scroll-body')).toHaveClass(
      'overflow-y-auto',
    );
    expect(screen.getByTestId('recommendation-verification-footer')).toHaveClass(
      'sticky',
    );
  });

  it('검증한 동일 candidate 설정을 기존 apply API로 적용한다', async () => {
    renderModal();
    await clickTestButton();
    await screen.findByRole('button', { name: '적용하기' });

    fireEvent.click(screen.getByRole('button', { name: '적용하기' }));

    await waitFor(() => {
      expect(workflowApiMock.applyCostOptimizerCandidate).toHaveBeenCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({
          comparison_id: 'comparison-1',
          candidate_settings: expect.objectContaining({
            model_id: 'gpt-4.1',
            parameters: expect.objectContaining({ max_tokens: 600 }),
          }),
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        }),
      );
    });
    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'b'.repeat(64),
      updatedAt: '2026-07-14T00:00:01Z',
    });
  });

  it('상세 비교 분석하기는 같은 comparison/candidate 이력을 열고 다시 실행하지 않는다', async () => {
    renderModal();
    await clickTestButton();
    await screen.findByRole('button', { name: '상세 비교 분석하기' });

    fireEvent.click(screen.getByRole('button', { name: '상세 비교 분석하기' }));

    expect(routerMock.push).toHaveBeenCalledWith(
      '/modules/workflow-1/cost-optimizer/llm-triage?comparisonId=comparison-1&candidateId=candidate-1',
    );
    expect(workflowApiMock.verifyCostOptimizerRecommendations).toHaveBeenCalledTimes(1);
  });

  it('stale 응답은 null 결과를 읽지 않고 재시도 안내만 표시한다', async () => {
    workflowApiMock.verifyCostOptimizerRecommendations.mockResolvedValueOnce(
      staleVerificationResponse,
    );
    renderModal();

    await clickTestButton();

    expect(
      await screen.findByText('추천 설정이 최신 상태가 아닙니다.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('A BASELINE')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '적용하기' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '다시 테스트하기' })).toBeEnabled();
  });

  it('검증 뒤 추천 선택이 바뀌면 현재 선택으로 다시 테스트할 수 있다', async () => {
    workflowApiMock.getCostOptimizerParameterRecommendations.mockResolvedValueOnce({
      ...recommendationResponse,
      recommendations: [
        ...recommendationResponse.recommendations,
        {
          recommendation_type: 'llm_parameter',
          parameter_key: 'temperature',
          current_value: 0.7,
          suggested_value: 0.2,
          apply_mode: 'experiment_required',
          candidate_patch: { parameters: { temperature: 0.2 } },
          reason: '출력 안정성을 높입니다.',
        },
      ],
    });
    renderModal();

    await clickTestButton();
    await screen.findByRole('button', { name: '적용하기' });
    fireEvent.click(screen.getByText('출력 안정성 높이기'));

    expect(screen.getByRole('button', { name: '다시 테스트하기' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '다시 테스트하기' }));

    await waitFor(() => {
      expect(workflowApiMock.verifyCostOptimizerRecommendations).toHaveBeenCalledTimes(2);
    });
  });

  it('direct policy 추천은 baseline 검증 없이 즉시 적용한다', async () => {
    workflowApiMock.getCostOptimizerParameterRecommendations.mockResolvedValueOnce({
      ...recommendationResponse,
      analysis_stage: 'insufficient_logs',
      recommendations: [
        {
          recommendation_type: 'model_routing_policy',
          parameter_key: 'model_routing.enable',
          current_value: false,
          suggested_value: true,
          apply_mode: 'direct_policy_update',
          candidate_patch: { auto_model_routing: true },
          reason: '자동 모델 라우팅을 사용합니다.',
        },
      ],
    });
    workflowApiMock.applyCostOptimizerRecommendations.mockResolvedValueOnce({
      applied: true,
      graph_hash: 'c'.repeat(64),
      updated_at: '2026-07-14T00:00:02Z',
    });
    renderModal();

    fireEvent.click(await screen.findByRole('button', { name: '적용하기' }));

    await waitFor(() => {
      expect(workflowApiMock.applyCostOptimizerRecommendations).toHaveBeenCalledWith(
        'workflow-1',
        'llm-triage',
        {
          recommendation_ids: ['model_routing.enable'],
          expected_graph_hash: 'a'.repeat(64),
          expected_updated_at: '2026-07-14T00:00:00Z',
        },
      );
    });
    expect(
      useWorkflowStore.getState().getCanonicalDraftMetadata('workflow-1'),
    ).toEqual({
      workflowId: 'workflow-1',
      graphHash: 'c'.repeat(64),
      updatedAt: '2026-07-14T00:00:02Z',
    });
    expect(workflowApiMock.verifyCostOptimizerRecommendations).not.toHaveBeenCalled();
  });
});
