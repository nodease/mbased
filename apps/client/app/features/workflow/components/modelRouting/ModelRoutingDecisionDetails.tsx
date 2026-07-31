type ModelRoutingDecisionDetailsProps = {
  output?: unknown;
  traceMetadata?: unknown;
};

type RoutingContext = {
  inputLengthBucket?: string;
  outputFormat?: string;
  schemaRequired?: boolean;
  knowledgeEnabled?: boolean;
  hasFileInput?: boolean;
};

type JudgeStatus = 'selected' | 'failed' | 'unavailable' | 'not_called' | 'unknown';

type JudgeSummary = {
  status: JudgeStatus;
  attempted: boolean | undefined;
  model?: string;
  confidence?: number;
  reasonCode?: string;
  reasonShort?: string;
  reasonFactors: string[];
  candidateModelCount?: number;
  cost?: number;
  errorCode?: string;
  notCalledReason?: string;
};

type ModelRoutingSummary = {
  selectedModel?: string;
  actualModel?: string;
  fallbackModel?: string;
  fallbackUsed?: boolean;
  fallbackFromModel?: string;
  fallbackReasonCode?: string;
  reasonCode?: string;
  policyVersion?: string;
  decisionSource?: string;
  executionMode?: string;
  judge: JudgeSummary;
  policySource?: string;
  includedInRoutingLearning?: boolean;
  runtimeContext: RoutingContext;
  localConfidence?: number;
  localConfidenceThreshold?: number;
  learningMode?: string;
  learningStatus?: string;
  learningOutcomeReason?: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const stringValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

const booleanValue = (value: unknown): boolean | undefined =>
  typeof value === 'boolean' ? value : undefined;

const numberValue = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined;

const stringArrayValue = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : [];

const routingRecordOf = (
  output: unknown,
  traceMetadata: unknown,
): Record<string, unknown> | null => {
  const traceRoot = isRecord(traceMetadata) ? traceMetadata : null;
  const traceLlm = traceRoot && isRecord(traceRoot.llm) ? traceRoot.llm : null;
  const traceRouting =
    traceLlm && isRecord(traceLlm.model_routing)
      ? traceLlm.model_routing
      : traceRoot && isRecord(traceRoot.model_routing)
        ? traceRoot.model_routing
        : traceLlm || traceRoot;
  const outputRecord = isRecord(output) ? output : null;
  const metadata = isRecord(outputRecord?.metadata)
    ? outputRecord.metadata
    : null;
  const outputRouting = metadata && isRecord(metadata.model_routing)
    ? metadata.model_routing
    : metadata && isRecord(metadata.llm)
      ? metadata.llm
      : metadata && isRecord(metadata.model_routing_metadata)
        ? metadata.model_routing_metadata
        : null;

  if (!traceRouting) return outputRouting;
  if (!outputRouting) return traceRouting;

  const traceJudge = isRecord(traceRouting.judge) ? traceRouting.judge : {};
  const outputJudge = isRecord(outputRouting.judge) ? outputRouting.judge : {};
  return {
    ...outputRouting,
    ...traceRouting,
    judge: { ...outputJudge, ...traceJudge },
  };
};

const contextOf = (value: unknown): RoutingContext => {
  if (!isRecord(value)) return {};
  return {
    inputLengthBucket: stringValue(value.input_length_bucket),
    outputFormat: stringValue(value.output_format),
    schemaRequired: booleanValue(value.schema_required),
    knowledgeEnabled: booleanValue(value.knowledge_enabled),
    hasFileInput: booleanValue(value.has_file_input),
  };
};

const judgeStatusOf = (
  value: unknown,
  decisionSource?: string,
  reasonCode?: string,
): JudgeStatus => {
  const status = isRecord(value) ? stringValue(value.status) : undefined;
  if (
    status === 'selected' ||
    status === 'failed' ||
    status === 'unavailable' ||
    status === 'not_called'
  ) {
    return status;
  }
  if (decisionSource === 'runtime_judge') return 'selected';
  // 이전 로그에는 Judge 시도 여부가 없어 실패로 단정하지 않는다.
  if (reasonCode === 'runtime_judge_unavailable') return 'unknown';
  return 'not_called';
};

const judgeOf = (
  value: unknown,
  decisionSource?: string,
  reasonCode?: string,
): JudgeSummary => {
  const judge = isRecord(value) ? value : {};
  return {
    status: judgeStatusOf(value, decisionSource, reasonCode),
    attempted: booleanValue(judge.attempted),
    model: stringValue(judge.model),
    confidence: numberValue(judge.confidence),
    reasonCode: stringValue(judge.reason_code),
    reasonShort: stringValue(judge.reason_short),
    reasonFactors: stringArrayValue(judge.reason_factors),
    candidateModelCount: numberValue(judge.candidate_model_count),
    cost: numberValue(judge.cost),
    errorCode: stringValue(judge.error_code),
    notCalledReason: stringValue(judge.not_called_reason),
  };
};

const summaryOf = ({
  output,
  traceMetadata,
}: ModelRoutingDecisionDetailsProps): ModelRoutingSummary | null => {
  const routing = routingRecordOf(output, traceMetadata);
  if (!routing) return null;
  const outputRecord = isRecord(output) ? output : null;
  const outputMetadata = isRecord(outputRecord?.metadata)
    ? outputRecord.metadata
    : null;
  const decisionFactors = isRecord(routing.decision_factors)
    ? routing.decision_factors
    : {};
  const decisionSource = stringValue(routing.decision_source);
  const reasonCode = stringValue(routing.reason_code);

  const summary: ModelRoutingSummary = {
    selectedModel:
      stringValue(routing.selected_model) || stringValue(outputRecord?.model),
    actualModel: stringValue(outputRecord?.model),
    fallbackModel: stringValue(routing.fallback_model),
    fallbackUsed:
      booleanValue(routing.fallback_used) ??
      booleanValue(outputMetadata?.fallback_used),
    fallbackFromModel: stringValue(routing.fallback_from_model),
    fallbackReasonCode: stringValue(routing.fallback_reason_code),
    reasonCode,
    policyVersion: stringValue(routing.policy_version),
    decisionSource,
    executionMode: stringValue(routing.execution_mode),
    judge: judgeOf(routing.judge, decisionSource, reasonCode),
    policySource: stringValue(routing.policy_source),
    includedInRoutingLearning:
      booleanValue(routing.included_in_routing_learning) ??
      booleanValue(routing.included_in_policy_learning),
    runtimeContext: contextOf(routing.runtime_context || routing),
    localConfidence: numberValue(decisionFactors.local_confidence),
    localConfidenceThreshold: numberValue(
      decisionFactors.local_confidence_threshold,
    ),
    learningMode: stringValue(decisionFactors.learning_mode),
    learningStatus: stringValue(routing.learning_status),
    learningOutcomeReason: stringValue(routing.learning_outcome_reason),
  };

  return summary.selectedModel || summary.reasonCode ? summary : null;
};

const lengthBucketLabel = (bucket?: string): string => {
  if (bucket === 'short') return '짧은 입력';
  if (bucket === 'medium') return '보통 입력';
  if (bucket === 'long') return '긴 입력';
  return '입력 길이 정보 없음';
};

const reasonText = (reasonCode?: string, reasonShort?: string): string => {
  if (reasonShort) return reasonShort;
  switch (reasonCode) {
    case 'judge_bootstrap_required':
      return '학습 초기 단계';
    case 'local_router_confident':
      return '로컬 라우터 확신 충족';
    case 'local_router_uncertain':
      return '로컬 판단이 불확실함';
    case 'runtime_judge_unavailable':
      return 'Judge 결과를 사용할 수 없음';
    case 'legacy_policy_ignored':
      return '과거 정책 미사용';
    case 'active_policy_unavailable':
    case 'policy_unavailable':
      return '활성 정책 없음';
    case 'structured_reasoning_required':
      return '구조적 추론 필요';
    case 'multi_constraint':
      return '여러 조건 종합';
    case 'simple_response':
      return '단순 응답 처리';
    case 'policy_default':
      return '기본 라우팅 규칙 일치';
    default:
      return '실행 정책에 따른 선택';
  }
};

const reasonFactorLabel = (factor: string): string | null => {
  switch (factor) {
    case 'high_decision_impact':
      return '영향이 큰 판단';
    case 'security_or_compliance_risk':
      return '보안·규정 위험';
    case 'multi_step_reasoning':
      return '다단계 판단 필요';
    case 'evidence_conflict':
      return '근거 충돌 해석';
    case 'broad_context_synthesis':
      return '여러 정보 종합';
    case 'strict_output_reliability':
      return '형식 정확성 요구';
    case 'long_context_handling':
      return '긴 문맥 처리';
    default:
      return null;
  }
};

const judgeErrorText = (errorCode?: string): string => {
  if (errorCode === 'responses_incomplete') return '응답이 완료되기 전에 종료됨';
  if (errorCode === 'RuntimeJudgeResponseError') return 'Judge 응답 형식이 올바르지 않음';
  if (errorCode === 'LLMCredentialNotAvailableError') return 'Judge 모델 credential을 사용할 수 없음';
  if (errorCode === 'ValueError') return 'Judge 실행 준비 정보가 부족함';
  return errorCode ? 'Judge 실행 중 처리 실패' : '실패 원인 정보 없음';
};

const decisionSourceLabel = (source?: string): string | null => {
  switch (source) {
    case 'runtime_judge':
    case 'test_policy_preview':
      return null;
    case 'local_router':
      return '로컬 라우터가 모델 선택';
    case 'active_policy':
      return '저장된 정책으로 모델 선택';
    case 'stored_model':
      return '기본 모델로 실행';
    default:
      return '선택 경로 정보 없음';
  }
};

const noJudgeMessage = (summary: ModelRoutingSummary): string => {
  if (summary.decisionSource === 'local_router') {
    return '로컬 라우터가 충분한 확신으로 모델을 선택했습니다.';
  }
  if (summary.executionMode === 'test') {
    return '테스트 실행은 정책 학습에서 제외되며, 이번 요청에는 Judge 호출이 필요하지 않았습니다.';
  }
  if (summary.judge.notCalledReason === 'policy_unavailable') {
    return '활성 정책이 없어 Judge를 호출하지 않고 기본 모델을 사용했습니다.';
  }
  return '이번 실행에서는 Judge 호출이 필요하지 않았습니다.';
};

const Detail = ({
  label,
  value,
}: {
  label: string;
  value: string;
}) => (
  <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
    <dt className="text-[11px] font-medium text-slate-500">{label}</dt>
    <dd className="mt-1 break-all font-semibold text-slate-900">{value}</dd>
  </div>
);

export function ModelRoutingDecisionDetails({
  output,
  traceMetadata,
}: ModelRoutingDecisionDetailsProps) {
  const summary = summaryOf({ output, traceMetadata });
  if (!summary) return null;

  const context = summary.runtimeContext;
  const isPolicyPreview =
    summary.includedInRoutingLearning === false &&
    (summary.policySource === 'active_deployment' ||
      summary.policySource === 'test_ephemeral');
  const isEphemeralPolicyPreview =
    summary.policySource === 'test_ephemeral' &&
    summary.includedInRoutingLearning === false;
  const showsGenericTestLearningExclusion =
    summary.executionMode === 'test' &&
    summary.includedInRoutingLearning !== true &&
    !isPolicyPreview;
  const judge = summary.judge;
  const shortReason = reasonText(summary.reasonCode, judge.reasonShort);
  // 자유형 Judge 설명은 저장하지 않는다. 정해진 reason code로만 표시한다.
  const selectionReason = shortReason;
  const sourceLabel = decisionSourceLabel(summary.decisionSource);

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-xs shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 pb-3 dark:border-slate-800">
        <div>
          <h4 className="font-semibold text-slate-950 dark:text-slate-50">
            모델 선택 결과
          </h4>
          <p className="mt-1 text-slate-600 dark:text-slate-300">
            이 실행에서 실제로 어떤 경로로 모델을 골랐는지 보여줍니다.
          </p>
        </div>
        {sourceLabel ? (
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100">
            {sourceLabel}
          </span>
        ) : null}
      </header>

      <dl className="grid gap-2 sm:grid-cols-2">
        <Detail label="실제 실행 모델" value={summary.actualModel || summary.selectedModel || '-'} />
        <Detail
          label="선택 근거"
          value={selectionReason}
        />
      </dl>

      <div className="flex flex-wrap gap-2">
        {judge.reasonShort ? (
          <span className="rounded border border-violet-200 bg-violet-50 px-2 py-1 text-violet-800">
            판단 분류: {shortReason}
          </span>
        ) : null}
        {judge.reasonFactors.map((factor) => {
          const label = reasonFactorLabel(factor);
          return label ? (
            <span
              key={factor}
              className="rounded border border-violet-200 bg-violet-50 px-2 py-1 text-violet-800"
            >
              {label}
            </span>
          ) : null;
        })}
        <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
          {lengthBucketLabel(context.inputLengthBucket)}
        </span>
        {context.outputFormat === 'json' && context.schemaRequired ? (
          <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
            JSON 스키마 필요
          </span>
        ) : null}
        <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
          {context.knowledgeEnabled ? '지식 베이스 사용' : '지식 베이스 사용 안 함'}
        </span>
        {context.hasFileInput ? (
          <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
            파일 입력 포함
          </span>
        ) : null}
      </div>

      <section
        aria-label="Judge 실행"
        data-testid="judge-execution-details"
        className="rounded-lg border border-violet-200 bg-violet-50/50 p-3 dark:border-violet-900 dark:bg-violet-950/20"
      >
        {judge.status === 'selected' ? (
          <>
            <h5 className="font-semibold text-violet-950 dark:text-violet-50">
              Judge 실행 성공
            </h5>
            <p className="mt-1 text-slate-700 dark:text-slate-200">
              Judge가 이번 요청과 후보 모델을 비교해 실제 실행 모델을 선택했습니다.
            </p>
            <dl className="mt-3 grid gap-2 sm:grid-cols-2">
              <Detail label="Judge 모델" value={judge.model || '-'} />
              <Detail
                label="판단 확신도"
                value={
                  judge.confidence === undefined
                    ? '-'
                    : `${(judge.confidence * 100).toFixed(1)}%`
                }
              />
              <Detail
                label="검토 후보 모델"
                value={`${judge.candidateModelCount ?? '-'}개`}
              />
              <Detail
                label="Judge 비용"
                value={
                  judge.cost === undefined ? '-' : `$${judge.cost.toFixed(6)}`
                }
              />
            </dl>
          </>
        ) : judge.status === 'failed' ? (
          <>
            <h5 className="font-semibold text-rose-900 dark:text-rose-100">
              Judge 호출 실패
            </h5>
            <p className="mt-1 text-slate-700 dark:text-slate-200">
              Judge 결과를 사용할 수 없어 기본 모델로 실행했습니다.
            </p>
            <dl className="mt-3 grid gap-2 sm:grid-cols-2">
              <Detail label="호출한 Judge 모델" value={judge.model || '-'} />
              <Detail
                label="검토 후보 모델"
                value={`${judge.candidateModelCount ?? '-'}개`}
              />
              <Detail label="실패 이유" value={judgeErrorText(judge.errorCode)} />
              <Detail label="오류 코드" value={judge.errorCode || '-'} />
            </dl>
          </>
        ) : judge.status === 'unavailable' ? (
          <>
            <h5 className="font-semibold text-amber-900 dark:text-amber-100">
              Judge 호출 준비 실패
            </h5>
            <p className="mt-1 text-slate-700 dark:text-slate-200">
              Judge를 호출하기 전 필요한 실행 조건을 만들지 못해 기본 모델로 실행했습니다.
            </p>
            <dl className="mt-3 grid gap-2 sm:grid-cols-2">
              <Detail label="Judge 모델" value={judge.model || '-'} />
              <Detail label="실패 이유" value={judgeErrorText(judge.errorCode)} />
            </dl>
          </>
        ) : judge.status === 'unknown' ? (
          <>
            <h5 className="font-semibold text-slate-900 dark:text-slate-100">
              Judge 실행 정보 없음
            </h5>
            <p className="mt-1 text-slate-700 dark:text-slate-200">
              이 이전 실행 로그에는 Judge 호출 여부와 결과가 기록되지 않았습니다.
            </p>
          </>
        ) : (
          <>
            <h5 className="font-semibold text-slate-900 dark:text-slate-100">
              Judge 호출 안 함
            </h5>
            <p className="mt-1 text-slate-700 dark:text-slate-200">
              {noJudgeMessage(summary)}
            </p>
          </>
        )}
      </section>

      {summary.fallbackUsed ? (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-slate-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-slate-100">
          <h5 className="font-semibold text-amber-900 dark:text-amber-100">
            모델 호출 대체 실행
          </h5>
          <dl className="mt-3 grid gap-2 sm:grid-cols-3">
            <Detail
              label="최초 선택 모델"
              value={summary.fallbackFromModel || summary.selectedModel || '-'}
            />
            <Detail
              label="실제 대체 모델"
              value={summary.actualModel || summary.fallbackModel || '-'}
            />
            <Detail
              label="대체 사유"
              value={summary.fallbackReasonCode === 'provider_call_failed' ? 'Provider 호출 실패' : '호출 준비 또는 실행 실패'}
            />
          </dl>
        </section>
      ) : null}

      {(summary.learningMode || summary.policyVersion) && (
        <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-slate-500 dark:border-slate-800">
          {summary.learningMode ? (
            <span>
              학습 방식: {summary.learningMode === 'local_first' ? '로컬 라우터 우선' : 'Judge 학습 중'}
              {summary.localConfidence !== undefined
                ? ` · 로컬 확신도 ${(summary.localConfidence * 100).toFixed(1)}%`
                : ''}
              {summary.localConfidenceThreshold !== undefined
                ? ` · 기준 ${(summary.localConfidenceThreshold * 100).toFixed(1)}%`
                : ''}
            </span>
          ) : null}
          {summary.policyVersion ? <span>정책 버전: {summary.policyVersion}</span> : null}
        </footer>
      )}

      {summary.learningStatus === 'pending_contract' ? (
        <p className="rounded-md border border-sky-200 bg-sky-50 p-3 text-sky-950 dark:border-sky-900 dark:bg-sky-950/20 dark:text-sky-100">
          실행 결과 계약을 확인한 뒤 이 선택을 로컬 학습에 반영합니다.
        </p>
      ) : null}
      {summary.learningStatus === 'accepted' ? (
        <p className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-100">
          스키마와 후속 단계 조건을 통과해 이 선택을 로컬 학습에 반영했습니다.
        </p>
      ) : null}
      {summary.learningStatus === 'rejected' ? (
        <p className="rounded-md border border-rose-200 bg-rose-50 p-3 text-rose-950 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-100">
          {summary.learningOutcomeReason === 'schema_failed'
            ? '스키마 또는 후속 단계 조건을 통과하지 못해 학습에서 제외되었습니다.'
            : '실행 계약을 통과하지 못해 학습에서 제외되었습니다.'}
        </p>
      ) : null}
      {isPolicyPreview ? (
        <p
          data-testid="model-routing-policy-preview"
          className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-blue-900 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100"
        >
          {isEphemeralPolicyPreview
            ? '활성 정책이 없어 현재 테스트에서만 사용할 임시 정책으로 모델을 선택했습니다. 이 결과는 정책 학습에 포함되지 않습니다.'
            : '이 테스트 실행은 배포 정책을 미리 적용한 결과이며, 정책 학습에는 포함되지 않습니다.'}
        </p>
      ) : showsGenericTestLearningExclusion ? (
        <p
          data-testid="model-routing-test-learning-exclusion"
          className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-blue-900 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100"
        >
          이 테스트 실행 결과는 정책 학습에 포함되지 않습니다.
        </p>
      ) : null}
    </section>
  );
}
