'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  LoaderCircle,
  Play,
  Wand2,
  X,
} from 'lucide-react';

import { workflowApi } from '../../api/workflowApi';
import type {
  CostOptimizerMetricComparison,
  CostOptimizerParameterRecommendation,
  CostOptimizerParameterRecommendationsResponse,
  CostOptimizerRecommendationVerificationResponse,
} from '../../types/Api';
import {
  ingestWorkflowDraftCASResult,
  resolveWorkflowDraftCASExpectation,
} from '../../utils/workflowDraftCAS';
import {
  applyCandidatePatchesToDraft,
  compareRequestCandidateFromDraft,
  type CandidateDraft,
} from './costOptimizerPlaygroundModel';

export type OptimizationRecommendationNode = {
  id: string;
  title: string;
  candidateDraft?: CandidateDraft;
};

interface OptimizationRecommendationModalProps {
  workflowId: string;
  workflowName?: string;
  llmNodes: OptimizationRecommendationNode[];
  initialNodeId?: string;
  appliedIds?: string[];
  onClose: () => void;
  onMarkForReview?: (recommendationIds: string[]) => void;
  onApplyPatches?: (patches: Record<string, unknown>[]) => void;
}

type VerificationState = {
  result: CostOptimizerRecommendationVerificationResponse;
  nodeId: string;
  selectionKey: string;
  candidateSettings: ReturnType<typeof compareRequestCandidateFromDraft>;
  patches: Record<string, unknown>[];
};

const parameterRecommendationLabelOf = (parameterKey: string) =>
  ({
    max_tokens: '최대 응답 길이 줄이기',
    temperature: '출력 안정성 높이기',
    top_p: 'Claude 호환 설정 정리',
    frequency_penalty: '반복 답변 줄이기',
    'rag.top_k': '검색 문서 개수 줄이기',
    'rag.retrieved_context_max_chars': '검색 문서 길이 제한하기',
    'rag.retrieved_context_compression': '검색 문서 압축 켜기',
    'model_routing.enable': '자동 모델 라우팅 켜기',
    'model_routing.refresh_interval_shorten': '정책 점검 주기 단축',
    'model_routing.refresh_interval_relax': '정책 점검 주기 완화',
  })[parameterKey] || parameterKey;

const parameterRecommendationTargetOf = (parameterKey: string) =>
  parameterKey.startsWith('rag.')
    ? '지식 베이스'
    : parameterKey.startsWith('model_routing.')
      ? '모델 라우팅'
      : '고급 설정';

const formatRecommendationValue = (
  value: unknown,
  parameterKey?: string,
): string => {
  if (value == null) return '설정 없음';
  if (typeof value === 'number') {
    const formatted = value.toLocaleString('ko-KR');
    if (parameterKey === 'max_tokens') return `${formatted} 토큰`;
    if (parameterKey === 'rag.top_k') return `${formatted}개`;
    if (parameterKey === 'rag.retrieved_context_max_chars') {
      return `${formatted}자`;
    }
    return formatted;
  }
  if (typeof value === 'string') return value || '설정 없음';
  if (typeof value === 'boolean') return value ? '켜짐' : '꺼짐';
  return JSON.stringify(value);
};

const evidenceLabelOf = (key: string) =>
  ({
    sample_count: '운영 로그 수',
    completion_tokens_p95: '대부분의 최근 응답 길이',
    prompt_tokens_p95: '대부분의 최근 입력 길이',
    context_token_estimate_p95: '검색 문서가 차지한 길이',
    retrieved_chunk_count_p95: '불러온 검색 문서 수',
    schema_pass_rate: '스키마 통과율',
    downstream_success_rate: '후속 노드 성공률',
    schema_fail_rate: '스키마 실패율',
    downstream_fail_rate: '후속 노드 실패율',
    truncation_rate: '길이 잘림률',
    retry_rate: '재시도율',
    fallback_rate: 'Fallback 비율',
    repetition_rate: '반복률',
    model_family: '모델 계열',
    compatibility: '호환성',
    current_auto_model_routing: '현재 자동 라우팅',
    current_refresh_every_runs: '현재 점검 주기',
    recommended_min: '권장 최소 주기',
    recommended_max: '권장 최대 주기',
  })[key] || key;

const formatEvidenceValue = (key: string, value: unknown) => {
  if (typeof value === 'number') {
    if (
      key.endsWith('_rate') ||
      key === 'schema_pass_rate' ||
      key === 'downstream_success_rate'
    ) {
      return `${Math.round(value * 1000) / 10}%`;
    }
    if (key === 'completion_tokens_p95' || key === 'prompt_tokens_p95') {
      return `${value.toLocaleString('ko-KR')}토큰 이하`;
    }
    if (key === 'context_token_estimate_p95') {
      return `${value.toLocaleString('ko-KR')}토큰 정도`;
    }
    if (key === 'retrieved_chunk_count_p95') {
      return `${value.toLocaleString('ko-KR')}개 정도`;
    }
    return value.toLocaleString('ko-KR');
  }
  if (typeof value === 'boolean') return value ? '예' : '아니오';
  if (value == null) return '-';
  return String(value);
};

const collectCandidatePatches = (
  recommendations: CostOptimizerParameterRecommendation[],
) =>
  recommendations
    .map((recommendation) => recommendation.candidate_patch)
    .filter(
      (patch): patch is Record<string, unknown> =>
        typeof patch === 'object' && patch !== null && !Array.isArray(patch),
    );

const asNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const formatCost = (value: unknown) => {
  const cost = asNumber(value);
  return cost == null ? '계산 불가' : `$${cost.toFixed(cost < 0.01 ? 6 : 4)}`;
};

const formatLatency = (value: unknown) => {
  const latency = asNumber(value);
  return latency == null ? '계산 불가' : `${(latency / 1000).toFixed(1)}초`;
};

const formatTokens = (value: unknown) => {
  const tokens = asNumber(value);
  return tokens == null ? '계산 불가' : `${Math.round(tokens).toLocaleString('ko-KR')} 토큰`;
};

const formatScore = (value: unknown) => {
  const score = asNumber(value);
  return score == null ? '평가 불가' : `${Math.round(score)}점`;
};

const formatDateTime = (value: string | null | undefined) => {
  if (!value) return '실행 시각 정보 없음';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
};

const deltaLabel = (
  comparison: CostOptimizerMetricComparison | undefined,
  formatter: (value: unknown) => string,
) => {
  const delta = asNumber(comparison?.delta);
  if (delta == null) return '변화 계산 불가';
  const sign = delta > 0 ? '+' : '';
  const rate = asNumber(comparison?.change_rate);
  const rateLabel = rate == null ? '' : ` (${(rate * 100).toFixed(1)}%)`;
  return `${sign}${formatter(delta)}${rateLabel}`;
};

const schemaLabelOf = (status: string | undefined) =>
  ({
    passed: '통과',
    failed: '실패',
    not_applicable: '검사 대상 아님',
    not_configured: '스키마 미설정',
  })[status || ''] || '검사 결과 없음';

const downstreamLabelOf = (
  compatibility: CostOptimizerRecommendationVerificationResponse['downstream_compatibility'],
) =>
  compatibility.label ||
  ({
    compatible: '호환',
    warning: '주의 필요',
    incompatible: '호환 불가',
    unknown: '판정 불가',
  })[compatibility.state] ||
  '판정 불가';

const applyReasonLabelOf = (reason: string) =>
  ({
    candidate_execution_failed: '후보 실행에 실패했습니다.',
    schema_validation_failed: '출력 스키마 검증에 실패했습니다.',
    downstream_incompatible: '후속 노드 입력 계약과 호환되지 않습니다.',
    recommendation_stale: '추천 또는 노드 설정이 변경되었습니다.',
    quality_score_decreased: '품질 점수가 기준 실행보다 낮습니다.',
    quality_confidence_low: '품질 평가 신뢰도가 낮습니다.',
  })[reason] || reason;

const createIdempotencyKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `recommendation-verify-${crypto.randomUUID()}`;
  }
  return `recommendation-verify-${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

const EMPTY_APPLIED_IDS: string[] = [];

function MetricBar({
  label,
  comparison,
  formatter,
}: {
  label: string;
  comparison: CostOptimizerMetricComparison | undefined;
  formatter: (value: unknown) => string;
}) {
  const baseline = asNumber(comparison?.baseline);
  const candidate = asNumber(comparison?.candidate);
  const maximum = Math.max(baseline || 0, candidate || 0, 1);
  const baselineWidth = baseline == null ? 0 : Math.max((baseline / maximum) * 100, 2);
  const candidateWidth = candidate == null ? 0 : Math.max((candidate / maximum) * 100, 2);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-3" aria-label={label}>
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-bold text-slate-900">{label}</h4>
        <span className="text-xs font-semibold text-slate-500">
          {deltaLabel(comparison, formatter)}
        </span>
      </div>
      <div className="mt-3 space-y-2 text-xs">
        <div className="grid grid-cols-[4rem_1fr_auto] items-center gap-2">
          <span className="font-medium text-slate-500">A 기준</span>
          <span className="h-2 overflow-hidden rounded-full bg-slate-100">
            <span
              className="block h-full rounded-full bg-slate-400"
              style={{ width: `${baselineWidth}%` }}
            />
          </span>
          <span className="font-semibold text-slate-800">{formatter(baseline)}</span>
        </div>
        <div className="grid grid-cols-[4rem_1fr_auto] items-center gap-2">
          <span className="font-medium text-emerald-700">B 후보</span>
          <span className="h-2 overflow-hidden rounded-full bg-emerald-50">
            <span
              className="block h-full rounded-full bg-emerald-500"
              style={{ width: `${candidateWidth}%` }}
            />
          </span>
          <span className="font-semibold text-emerald-800">{formatter(candidate)}</span>
        </div>
      </div>
    </section>
  );
}

function VerificationResultPanel({
  verification,
  isStale,
}: {
  verification: CostOptimizerRecommendationVerificationResponse;
  isStale: boolean;
}) {
  if (verification.verification_status === 'stale') return null;

  const qualityMetric: CostOptimizerMetricComparison = {
    baseline: verification.quality_evaluation.baseline?.score ?? null,
    candidate: verification.quality_evaluation.candidate?.score ?? null,
    delta: verification.quality_evaluation.delta ?? null,
  };
  const schemaIssues = verification.schema_validation.issues || [];
  const checkedNodeCount =
    verification.downstream_compatibility.contract_check?.checked_node_ids?.length || 0;

  return (
    <section className="mt-6 space-y-4" aria-label="추천 설정 검증 결과">
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-bold text-emerald-900">
            <CheckCircle2 className="h-4 w-4" />
            추천 설정 검증 결과
          </div>
          <span className="rounded-md border border-emerald-200 bg-white px-2 py-1 text-xs font-semibold text-emerald-700">
            {verification.verification_status === 'partial'
              ? '일부 결과'
              : verification.verification_status === 'completed'
                ? '검증 완료'
                : verification.verification_status}
          </span>
        </div>
        <p className="mt-2 text-sm leading-6 text-emerald-800">
          A baseline은 다시 실행하지 않았습니다. B 후보 1회 실행과 품질 평가에 새 비용이 발생합니다.
        </p>
      </div>

      {isStale ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-center gap-2 font-bold">
            <AlertTriangle className="h-4 w-4" />
            검증 이후 선택한 추천 또는 대상 노드가 바뀌었습니다.
          </div>
          <p className="mt-1">이 결과는 참고용입니다. 적용하려면 현재 선택으로 다시 테스트하세요.</p>
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
        <p className="text-xs font-bold text-slate-500">A BASELINE</p>
        <h3 className="mt-1 text-base font-bold text-slate-950">{verification.baseline.label}</h3>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-700">
            실행: {formatDateTime(verification.baseline.executed_at)}
          </span>
          <span className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-700">
            모델: {verification.baseline.model || '정보 없음'}
          </span>
          <span className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-700">
            B 모델: {verification.candidate.model || '정보 없음'}
          </span>
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-violet-600" />
          <h3 className="text-sm font-bold text-slate-950">핵심 지표 비교</h3>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <MetricBar label="비용" comparison={verification.metrics.cost} formatter={formatCost} />
          <MetricBar label="실행 시간" comparison={verification.metrics.latency_ms} formatter={formatLatency} />
          <MetricBar label="전체 토큰" comparison={verification.metrics.total_tokens} formatter={formatTokens} />
          <MetricBar label="출력 품질 점수" comparison={qualityMetric} formatter={formatScore} />
        </div>
      </section>

      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-bold text-slate-500">스키마 검증</p>
          <p className="mt-2 text-sm font-bold text-slate-950">
            {schemaLabelOf(verification.schema_validation.status)}
          </p>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            {schemaIssues.length > 0
              ? schemaIssues
                  .map((issue) =>
                    typeof issue === 'string'
                      ? issue
                      : issue.message || issue.code || '스키마 이슈',
                  )
                  .join(', ')
              : 'JSON 출력일 때만 형식과 필수 항목을 검사합니다.'}
          </p>
        </section>
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-bold text-slate-500">후속 노드 호환성</p>
          <p className="mt-2 text-sm font-bold text-slate-950">
            {downstreamLabelOf(verification.downstream_compatibility)}
          </p>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            {verification.downstream_compatibility.message ||
              (checkedNodeCount > 0
                ? `${checkedNodeCount}개 후속 노드의 입력 계약을 확인했습니다.`
                : '자동 검사할 후속 노드 계약이 없습니다.')}
          </p>
        </section>
      </div>

      <section className="rounded-lg border border-blue-200 bg-blue-50 p-4">
        <p className="text-xs font-bold text-blue-700">출력 품질 평가</p>
        <p className="mt-2 text-sm font-semibold text-blue-950">
          {verification.quality_evaluation.safe_summary || '품질 평가 결과가 없습니다.'}
        </p>
        <p className="mt-1 text-xs text-blue-800">
          신뢰도: {verification.quality_evaluation.confidence || '평가 불가'}
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-xs font-bold text-slate-500">이번 검증에 새로 든 비용</p>
        <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
          <span>
            <span className="block text-xs text-slate-500">B 후보 실행</span>
            <span className="font-bold text-slate-900">
              {formatCost(verification.incurred_cost.candidate_execution_cost)}
            </span>
          </span>
          <span>
            <span className="block text-xs text-slate-500">품질 평가</span>
            <span className="font-bold text-slate-900">
              {formatCost(verification.incurred_cost.quality_judge_cost)}
            </span>
          </span>
          <span>
            <span className="block text-xs text-slate-500">신규 합계</span>
            <span className="font-bold text-slate-950">
              {formatCost(verification.incurred_cost.total_new_cost)}
            </span>
          </span>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          A baseline 비용은 과거 실행 비용이므로 이번 검증 합계에 포함하지 않습니다.
        </p>
      </section>
    </section>
  );
}

export function OptimizationRecommendationModal({
  workflowId,
  workflowName = '현재 workflow',
  llmNodes,
  initialNodeId,
  appliedIds = EMPTY_APPLIED_IDS,
  onClose,
  onMarkForReview,
  onApplyPatches,
}: OptimizationRecommendationModalProps) {
  const router = useRouter();
  const [selectedNodeId, setSelectedNodeId] = useState(
    initialNodeId || llmNodes[0]?.id || '',
  );
  const selectedNode =
    llmNodes.find((node) => node.id === selectedNodeId) || llmNodes[0];
  const selectedNodeTitle = selectedNode?.title || 'LLM 노드';
  const [recommendationResponse, setRecommendationResponse] =
    useState<CostOptimizerParameterRecommendationsResponse | null>(null);
  const [isLoadingRecommendations, setIsLoadingRecommendations] =
    useState(false);
  const [recommendationError, setRecommendationError] = useState('');
  const [actionError, setActionError] = useState('');
  const [isApplyingRecommendations, setIsApplyingRecommendations] =
    useState(false);
  const [isVerifyingRecommendations, setIsVerifyingRecommendations] =
    useState(false);
  const [verification, setVerification] = useState<VerificationState | null>(
    null,
  );
  const [isApplyConfirmationOpen, setIsApplyConfirmationOpen] = useState(false);
  const recommendations = recommendationResponse?.recommendations || [];
  const [selectedIds, setSelectedIds] = useState<string[]>(appliedIds);

  useEffect(() => {
    if (!workflowId || !selectedNodeId) return;

    let active = true;
    setIsLoadingRecommendations(true);
    setRecommendationError('');
    setRecommendationResponse(null);
    setVerification(null);
    setIsApplyConfirmationOpen(false);

    workflowApi
      .getCostOptimizerParameterRecommendations(workflowId, selectedNodeId)
      .then((response) => {
        if (!active) return;
        setRecommendationResponse(response);
        setSelectedIds(
          appliedIds.length > 0
            ? appliedIds
            : response.recommendations.map(
                (recommendation) => recommendation.parameter_key,
              ),
        );
      })
      .catch(() => {
        if (!active) return;
        setRecommendationError('파라미터 추천 결과를 불러오지 못했습니다.');
        setSelectedIds([]);
      })
      .finally(() => {
        if (active) setIsLoadingRecommendations(false);
      });

    return () => {
      active = false;
    };
  }, [appliedIds, selectedNodeId, workflowId]);

  const selectedRecommendations = recommendations.filter((recommendation) =>
    selectedIds.includes(recommendation.parameter_key),
  );
  const selectedPatches = useMemo(
    () => collectCandidatePatches(selectedRecommendations),
    [selectedRecommendations],
  );
  const selectionKey = selectedRecommendations
    .map((recommendation) => recommendation.parameter_key)
    .sort()
    .join('|');
  const candidateSettings = useMemo(() => {
    if (!selectedNode?.candidateDraft) return null;
    return compareRequestCandidateFromDraft(
      applyCandidatePatchesToDraft(selectedNode.candidateDraft, selectedPatches),
      '추천 설정 검증',
    );
  }, [selectedNode?.candidateDraft, selectedPatches]);
  const isVerificationStale =
    verification !== null &&
    (verification.result.verification_status === 'stale' ||
      verification.nodeId !== selectedNodeId ||
      verification.selectionKey !== selectionKey);
  const hasRenderableVerification = Boolean(
    verification && verification.result.verification_status !== 'stale',
  );
  const requiresExperimentVerification = selectedRecommendations.some(
    (recommendation) => recommendation.apply_mode !== 'direct_policy_update',
  );
  const hasOnlyDirectPolicyUpdates =
    selectedRecommendations.length > 0 &&
    !requiresExperimentVerification;
  const canApplyDirectRecommendations =
    hasOnlyDirectPolicyUpdates &&
    !isLoadingRecommendations &&
    !isApplyingRecommendations;
  const canRunAction =
    Boolean(workflowId && selectedNodeId && candidateSettings) &&
    selectedRecommendations.length > 0 &&
    !isLoadingRecommendations &&
    !isApplyingRecommendations &&
    !isVerifyingRecommendations;
  const canApplyVerification =
    Boolean(
      verification?.result.apply.allowed &&
        verification?.result.comparison_id &&
        verification?.candidateSettings,
    ) &&
    !isVerificationStale &&
    !isApplyingRecommendations;

  const toggleRecommendation = (id: string) => {
    setActionError('');
    setIsApplyConfirmationOpen(false);
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  };

  const handleTestRecommendations = async () => {
    if (!workflowId || !selectedNodeId || !candidateSettings) return;
    setActionError('');
    setIsApplyConfirmationOpen(false);
    const profile = recommendationResponse?.profile;
    const nodeConfigFingerprint =
      profile && typeof profile.node_config_fingerprint === 'string'
        ? profile.node_config_fingerprint
        : '';
    const recommendationPolicyVersion = recommendationResponse?.policy_version || '';
    const recommendationFingerprint =
      recommendationResponse?.recommendation_fingerprint || '';
    if (
      !nodeConfigFingerprint ||
      !recommendationPolicyVersion ||
      !recommendationFingerprint
    ) {
      setActionError('추천 정보가 최신성 검증 값을 포함하지 않습니다. 추천을 다시 불러오세요.');
      return;
    }
    setIsVerifyingRecommendations(true);
    try {
      const result = await workflowApi.verifyCostOptimizerRecommendations(
        workflowId,
        selectedNodeId,
        {
          recommendation_ids: selectedRecommendations.map(
            (recommendation) => recommendation.parameter_key,
          ),
          baseline_mode: 'latest_success',
          recommendation_policy_version: recommendationPolicyVersion,
          recommendation_fingerprint: recommendationFingerprint,
          node_config_fingerprint: nodeConfigFingerprint,
        },
        createIdempotencyKey(),
      );
      setVerification({
        result,
        nodeId: selectedNodeId,
        selectionKey,
        candidateSettings,
        patches: selectedPatches,
      });
      if (result.verification_status === 'stale') {
        setActionError('추천 또는 노드 설정이 바뀌었습니다. 추천을 다시 불러온 뒤 테스트하세요.');
      }
    } catch {
      setActionError(
        '추천 설정을 검증하지 못했습니다. 비교 가능한 최신 성공 기록과 모델 권한을 확인해 주세요.',
      );
    } finally {
      setIsVerifyingRecommendations(false);
    }
  };

  const handleApplyRecommendations = async () => {
    if (!workflowId || !selectedNodeId) return;
    setActionError('');
    setIsApplyingRecommendations(true);
    try {
      if (verification && !isVerificationStale) {
        if (!canApplyVerification || !verification.result.comparison_id) {
          throw new Error('verification_apply_not_allowed');
        }
        if (
          verification.result.apply.requires_confirmation &&
          !isApplyConfirmationOpen
        ) {
          setIsApplyConfirmationOpen(true);
          return;
        }
        const downstreamState = verification.result.downstream_compatibility.state;
        const expectation = await resolveWorkflowDraftCASExpectation(workflowId);
        const applyResult = await workflowApi.applyCostOptimizerCandidate(
          workflowId,
          selectedNodeId,
          {
            comparison_id: verification.result.comparison_id,
            candidate_settings: verification.candidateSettings,
            acknowledge_downstream_warning:
              downstreamState === 'warning' || downstreamState === 'incompatible',
            ...expectation,
          },
        );
        ingestWorkflowDraftCASResult(workflowId, applyResult);
        onApplyPatches?.(verification.patches);
        onMarkForReview?.(
          verification.result.applied_recommendation_ids || selectedIds,
        );
        return;
      }

      if (canApplyDirectRecommendations) {
        const recommendationIds = selectedRecommendations.map(
          (recommendation) => recommendation.parameter_key,
        );
        const expectation = await resolveWorkflowDraftCASExpectation(workflowId);
        const applyResult = await workflowApi.applyCostOptimizerRecommendations(
          workflowId,
          selectedNodeId,
          {
            recommendation_ids: recommendationIds,
            ...expectation,
          },
        );
        ingestWorkflowDraftCASResult(workflowId, applyResult);
        onApplyPatches?.(selectedPatches);
        onMarkForReview?.(recommendationIds);
        return;
      }

      setActionError('추천 설정은 테스트 결과를 확인한 뒤에만 적용할 수 있습니다.');
    } catch {
      setActionError(
        '추천 설정을 적용하지 못했습니다. 모델 권한, 지식 베이스 권한, 최신 추천 상태를 확인해 주세요.',
      );
    } finally {
      setIsApplyingRecommendations(false);
    }
  };

  const handleOpenDetailAnalysis = () => {
    if (!verification?.result.comparison_id || !verification.result.candidate_id) return;
    router.push(
      `/modules/${workflowId}/cost-optimizer/${selectedNodeId}?comparisonId=${encodeURIComponent(
        verification.result.comparison_id,
      )}&candidateId=${encodeURIComponent(verification.result.candidate_id)}`,
    );
  };

  const applyDisabledReason = verification?.result.apply.reasons
    .map(applyReasonLabelOf)
    .join(' ');

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="optimization-recommendation-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
          <div>
            <p className="text-xs font-bold text-violet-600">비용 최적화 검토</p>
            <h2
              id="optimization-recommendation-title"
              className="mt-1 text-xl font-bold text-slate-950"
            >
              LLM 노드 설정 추천
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {workflowName}의 최신 성공 운영 기록을 A baseline으로 고정하고, 선택한 추천 설정을 B 후보로 한 번만 실행합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"
            aria-label="나가기"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div
          data-testid="recommendation-verification-scroll-body"
          className="min-h-0 flex-1 overflow-y-auto px-6 py-5"
        >
          <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
            {llmNodes.length > 1 ? (
              <label className="block">
                <span className="text-xs font-bold text-slate-600">검토할 LLM 노드</span>
                <select
                  value={selectedNodeId}
                  onChange={(event) => setSelectedNodeId(event.target.value)}
                  className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-800 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100"
                >
                  {llmNodes.map((node) => (
                    <option key={node.id} value={node.id}>
                      {node.title}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div>
                <span className="text-xs font-bold text-slate-600">검토할 LLM 노드</span>
                <p className="mt-1 text-sm font-semibold text-slate-900">{selectedNodeTitle}</p>
              </div>
            )}
          </div>

          {isLoadingRecommendations ? (
            <div className="rounded-lg border border-slate-200 bg-white px-4 py-6 text-sm font-medium text-slate-500">
              추천 엔진 결과를 불러오는 중입니다.
            </div>
          ) : null}

          {recommendationError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">
              {recommendationError}
            </div>
          ) : null}

          {!isLoadingRecommendations &&
          !recommendationError &&
          recommendationResponse?.analysis_stage === 'insufficient_logs' ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <p className="font-semibold">배포 후 운영 로그가 부족해 추천을 만들지 않았습니다.</p>
              <p className="mt-1">
                현재 샘플 수: {formatRecommendationValue(recommendationResponse.profile?.sample_count)}
              </p>
            </div>
          ) : null}

          {!isLoadingRecommendations &&
          !recommendationError &&
          recommendationResponse?.warnings
            ? recommendationResponse.warnings.map((warning) => (
                <div
                  key={`${warning.code}-${warning.message}`}
                  className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
                >
                  <span className="font-semibold">추천 제한 안내</span>
                  {warning.message ? <span className="ml-2">{warning.message}</span> : null}
                </div>
              ))
            : null}

          <div className="space-y-3">
            {recommendations.map((recommendation) => {
              const recommendationId = recommendation.parameter_key;
              const checked = selectedIds.includes(recommendationId);
              return (
                <label
                  key={recommendationId}
                  className={`flex cursor-pointer gap-3 rounded-lg border p-4 transition-colors ${
                    checked
                      ? 'border-violet-200 bg-violet-50/70'
                      : 'border-slate-200 bg-white hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleRecommendation(recommendationId)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-slate-950">
                        {parameterRecommendationLabelOf(recommendation.parameter_key)}
                      </span>
                      <span className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-semibold text-slate-600">
                        {recommendation.parameter_key}
                      </span>
                      <span className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-semibold text-slate-600">
                        {parameterRecommendationTargetOf(recommendation.parameter_key)}
                      </span>
                      <span className="rounded-md border border-violet-100 bg-white px-1.5 py-0.5 text-[11px] font-semibold text-violet-700">
                        대상: {selectedNodeTitle}
                      </span>
                    </span>
                    <span className="mt-2 block text-sm leading-6 text-slate-600">
                      {recommendation.reason || '추천 엔진이 생성한 후보입니다.'}
                    </span>
                    <span className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                      <span className="rounded-md border border-slate-200 bg-white px-3 py-2">
                        <span className="block font-semibold text-slate-500">현재 설정</span>
                        <span className="mt-1 block font-medium text-slate-800">
                          {formatRecommendationValue(recommendation.current_value, recommendation.parameter_key)}
                        </span>
                      </span>
                      <span className="rounded-md border border-violet-200 bg-white px-3 py-2">
                        <span className="block font-semibold text-violet-600">추천 설정</span>
                        <span className="mt-1 block font-medium text-slate-800">
                          {formatRecommendationValue(recommendation.suggested_value, recommendation.parameter_key)}
                        </span>
                      </span>
                    </span>
                    {recommendation.evidence && Object.keys(recommendation.evidence).length > 0 ? (
                      <span className="mt-3 block rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                        <span className="block font-semibold text-slate-500">추천 근거</span>
                        <span className="mt-2 grid gap-2 sm:grid-cols-2">
                          {Object.entries(recommendation.evidence).map(([key, value]) => (
                            <span
                              key={key}
                              className="flex items-center justify-between gap-3 rounded-md bg-slate-50 px-2 py-1"
                            >
                              <span className="text-slate-500">{evidenceLabelOf(key)}</span>
                              <span className="font-semibold text-slate-800">
                                {formatEvidenceValue(key, value)}
                              </span>
                            </span>
                          ))}
                        </span>
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>

          {!verification ? (
            <div className="mt-5 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
              테스트하기를 누르면 최신 비교 가능한 성공 기록을 A baseline으로 고정합니다. B 후보 실행과 출력 품질 평가에 새 비용이 발생하며, 이 모달 안에서 비용·속도·품질·스키마·후속 노드 호환성을 확인할 수 있습니다.
            </div>
          ) : hasRenderableVerification ? (
            <VerificationResultPanel
              verification={verification.result}
              isStale={isVerificationStale}
            />
          ) : (
            <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <div className="flex items-center gap-2 font-bold">
                <AlertTriangle className="h-4 w-4" />
                추천 설정이 최신 상태가 아닙니다.
              </div>
              <p className="mt-1">추천 목록을 다시 확인한 뒤 현재 설정으로 테스트를 다시 실행하세요.</p>
            </div>
          )}

          {verification?.result.apply.requires_confirmation &&
          isApplyConfirmationOpen ? (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <div className="flex items-center gap-2 font-bold">
                <AlertTriangle className="h-4 w-4" />
                적용 전 확인이 필요합니다.
              </div>
              <p className="mt-1">
                {verification.result.apply.reasons.map(applyReasonLabelOf).join(' ')}
                {' '}이 결과를 이해했으면 아래 적용 버튼을 한 번 더 누르세요.
              </p>
            </div>
          ) : null}

          {actionError ? (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">
              {actionError}
            </div>
          ) : null}
        </div>

        <div
          data-testid="recommendation-verification-footer"
          className="sticky bottom-0 flex flex-wrap justify-end gap-2 border-t border-slate-100 bg-white px-6 py-4 shadow-[0_-8px_18px_rgba(15,23,42,0.04)]"
        >
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            닫기
          </button>
          {hasRenderableVerification && !isVerificationStale ? (
            <>
              <button
                type="button"
                onClick={handleOpenDetailAnalysis}
                disabled={
                  !verification?.result.comparison_id ||
                  !verification?.result.candidate_id
                }
                className="inline-flex items-center gap-2 rounded-md border border-violet-200 bg-white px-4 py-2 text-sm font-semibold text-violet-700 hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300"
              >
                <BarChart3 className="h-4 w-4" />
                상세 비교 분석하기
              </button>
              <button
                type="button"
                disabled={!canApplyVerification}
                onClick={handleApplyRecommendations}
                title={canApplyVerification ? undefined : applyDisabledReason}
                className="inline-flex items-center gap-2 rounded-md bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                <Wand2 className="h-4 w-4" />
                {isApplyingRecommendations
                  ? '적용 중'
                  : isApplyConfirmationOpen
                    ? '확인하고 적용하기'
                    : '적용하기'}
              </button>
            </>
          ) : hasOnlyDirectPolicyUpdates ? (
            <button
              type="button"
              disabled={!canApplyDirectRecommendations}
              onClick={handleApplyRecommendations}
              className="inline-flex items-center gap-2 rounded-md bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              <Wand2 className="h-4 w-4" />
              {isApplyingRecommendations ? '적용 중' : '적용하기'}
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={!canRunAction}
                onClick={handleTestRecommendations}
                className="inline-flex items-center gap-2 rounded-md border border-violet-200 bg-white px-4 py-2 text-sm font-semibold text-violet-700 hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300"
              >
                {isVerifyingRecommendations ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {isVerifyingRecommendations
                  ? '테스트 실행 중'
                  : verification
                    ? '다시 테스트하기'
                    : '테스트하기'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
