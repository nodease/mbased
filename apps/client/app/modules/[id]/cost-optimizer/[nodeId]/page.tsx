'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  BarChart3,
  Clock3,
  FileText,
  FlaskConical,
  Play,
} from 'lucide-react';

import { CostOptimizerBaselineSelection } from '@/app/features/workflow/components/costOptimizer/CostOptimizerBaselineSelection';
import { CostOptimizerHistoryPanel } from '@/app/features/workflow/components/costOptimizer/CostOptimizerHistoryPanel';
import { CostOptimizerOutputPreviewPanel } from '@/app/features/workflow/components/costOptimizer/CostOptimizerPreviewViewer';
import { ModelRoutingDecisionDetails } from '@/app/features/workflow/components/modelRouting/ModelRoutingDecisionDetails';
import { fieldLabelsFromOutputFormat } from '@/app/features/workflow/components/costOptimizer/costOptimizerPreviewLabels';
import {
  ingestWorkflowDraftCASResult,
  resolveWorkflowDraftCASExpectation,
} from '@/app/features/workflow/utils/workflowDraftCAS';
import { baselineFromExperimentSummary } from '@/app/features/workflow/components/costOptimizer/costOptimizerHistoryModel';
import { NodeSettingsComparisonPanel } from '@/app/features/workflow/components/costOptimizer/NodeSettingsComparisonPanel';
import {
  downstreamLabelOf,
  downstreamStateLabelOf,
  formatCandidateCost,
  formatContextDateTime,
  formatCost,
  formatLatency,
  formatMetric,
  schemaStatusLabelOf,
} from '@/app/features/workflow/components/costOptimizer/costOptimizerPresentation';
import {
  applyCandidatePatchesToDraft,
  baselineOptionsOf,
  candidateFromNode,
  candidateFromOptions,
  compareRequestCandidateFromDraft,
  downstreamOutputContractChipsFromNodes,
  findTargetNode,
  inputContractChipsFromBaseline,
  nodesFromDraft,
  type BaselineNodeOptions,
  type CandidateDraft,
  type SettingsTab,
} from '@/app/features/workflow/components/costOptimizer/costOptimizerPlaygroundModel';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { useCostOptimizerHistory } from '@/app/features/workflow/hooks/useCostOptimizerHistory';
import { useResizableNodeEditorPanels } from '@/app/features/workflow/hooks/useResizableNodeEditorPanels';
import type {
  CostOptimizerBaselineRow,
  CostOptimizerCompareResponse,
  CostOptimizerDownstreamCompatibility,
  CostOptimizerExperimentSummary,
} from '@/app/features/workflow/types/Api';
import type { AppNode, LLMNodeData } from '@/app/features/workflow/types/Nodes';

type PlaygroundMode = 'setup' | 'report';
type InspectorTab = 'settings-diff' | 'trace' | 'downstream';

const inspectorTabs: Array<{ value: InspectorTab; label: string }> = [
  { value: 'settings-diff', label: '설정 차이' },
  { value: 'trace', label: '근거/Trace' },
  { value: 'downstream', label: '후속 노드 영향' },
];


const downstreamToneOf = (
  compatibility: CostOptimizerDownstreamCompatibility | null | undefined,
) => {
  if (compatibility?.state === 'compatible') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  }
  if (compatibility?.state === 'warning') {
    return 'border-amber-200 bg-amber-50 text-amber-800';
  }
  if (compatibility?.state === 'incompatible') {
    return 'border-red-200 bg-red-50 text-red-800';
  }
  return 'border-slate-200 bg-slate-50 text-slate-600';
};

const downstreamMessageOf = (
  compatibility: CostOptimizerDownstreamCompatibility | null | undefined,
) => {
  if (compatibility?.message) return compatibility.message;
  if (compatibility?.state === 'compatible') {
    return 'A/B 결과와 downstream 계약 검증을 현재 workflow 판단에 사용할 수 있습니다.';
  }
  if (compatibility?.state === 'warning') {
    return 'LLM output 비교는 가능하지만 최종 적용 전 현재 workflow 테스트 실행으로 확인해야 합니다.';
  }
  if (compatibility?.state === 'incompatible') {
    return 'LLM output 비교만 참고할 수 있습니다. workflow 전체 테스트 실행으로 downstream 성공 여부를 별도 확인하세요.';
  }
  return 'B 실행 결과가 생성되면 schema와 downstream 호환성 판단을 함께 표시합니다.';
};

const readNumber = (
  record: Record<string, unknown> | undefined,
  keys: string[],
) => {
  for (const key of keys) {
    const value = record?.[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
};

type ModelRoutingSummary = {
  selectedModel?: string;
  fallbackModel?: string;
  decisionSource?: string;
  reasonCode?: string;
  policyVersion?: string;
  matchedRuleId?: string;
};

const isUnknownRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const stringValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim().length > 0
    ? value.trim()
    : undefined;

const modelRoutingSummaryOf = (
  output: unknown,
  trace: Record<string, unknown> | undefined,
): ModelRoutingSummary | null => {
  const traceRouting = isUnknownRecord(trace?.model_routing)
    ? trace?.model_routing
    : isUnknownRecord(trace?.model_routing_metadata)
      ? trace?.model_routing_metadata
      : null;
  const outputRecord = isUnknownRecord(output) ? output : null;
  const metadata = isUnknownRecord(outputRecord?.metadata)
    ? outputRecord?.metadata
    : null;
  const outputRouting = isUnknownRecord(metadata?.model_routing)
    ? metadata?.model_routing
    : isUnknownRecord(metadata?.model_routing_metadata)
      ? metadata?.model_routing_metadata
      : null;
  const routing = traceRouting || outputRouting;
  if (!routing) return null;

  const summary = {
    selectedModel:
      stringValue(routing.selected_model) || stringValue(outputRecord?.model),
    fallbackModel: stringValue(routing.fallback_model),
    decisionSource: stringValue(routing.decision_source),
    reasonCode: stringValue(routing.reason_code),
    policyVersion: stringValue(routing.policy_version),
    matchedRuleId: stringValue(routing.matched_rule_id),
  };

  return summary.selectedModel || summary.decisionSource || summary.reasonCode
    ? summary
    : null;
};

const formatJsonSchemaSummary = (candidate: CandidateDraft) => {
  if (candidate.output_format !== 'json') return '사용 안 함';
  if (candidate.json_schema_fields.length === 0) return '정의된 필드 없음';

  return candidate.json_schema_fields
    .map((field) => {
      const key = field.key.trim() || '(이름 없음)';
      return `${key}: ${field.type}${field.required ? ', 필수' : ', 선택'}`;
    })
    .join('\n');
};

const compressionLabelOf = (
  value: CandidateDraft['retrievedContextCompression'],
) => {
  if (value === 'light') return '약하게 압축';
  if (value === 'strong') return '강하게 압축';
  return '사용 안 함';
};

const groundingLabelOf = (value: CandidateDraft['answerGroundingCheck']) => {
  if (value === 'basic') return '기본';
  if (value === 'strict') return '엄격';
  return '끄기';
};

const costOptimizerUnavailableMessage = (
  reason: string | null | undefined,
  canCompare: boolean | undefined,
) => {
  if (reason === 'permission.denied' || canCompare === false) {
    return '비용 비교 권한이 없습니다. builder 이상 권한이 필요합니다.';
  }
  if (reason === 'cost_optimizer.not_llm_node') {
    return 'LLM 노드에서만 비용 비교를 실행할 수 있습니다.';
  }
  if (reason === 'cost_optimizer.no_baseline') {
    return '비교할 실행 로그가 없습니다. 먼저 테스트 실행을 완료해 주세요.';
  }
  return '비용 비교를 시작할 수 없습니다.';
};

const responseDetailOf = (error: unknown): string | null => {
  if (typeof error !== 'object' || error === null) return null;
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return null;
  const data = (response as { data?: unknown }).data;
  if (typeof data === 'object' && data !== null) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    const code = (data as { error?: { code?: unknown } }).error?.code;
    if (typeof code === 'string') return code;
  }
  return null;
};

const compareCandidateErrorMessages: Record<string, string> = {
  'cost_optimizer.knowledge_unavailable':
    '선택한 지식 베이스를 사용할 수 없습니다. 지식 베이스 권한이나 선택 상태를 확인해 주세요.',
  'cost_optimizer.model_unavailable':
    '선택한 모델을 현재 계정에서 사용할 수 없습니다. LLM Credentials 또는 모델 권한을 확인해 주세요.',
  'cost_optimizer.baseline_input_unavailable':
    '선택한 기준 실행의 입력값을 사용할 수 없습니다. 다른 성공 로그를 선택해 주세요.',
  'cost_optimizer.invalid_candidate':
    '후보 설정 값이 유효하지 않습니다. 모델, 프롬프트, 출력 형식, 파라미터를 확인해 주세요.',
  'cost_optimizer.model_routing_policy_unavailable':
    '자동 라우팅 정책이 없습니다. 먼저 LLM 노드의 자동 라우팅 정책을 생성하거나 모델을 직접 선택해 주세요.',
  'permission.denied':
    'B 후보 실행 권한이 없습니다. builder 이상 권한이 필요합니다.',
};

const compareCandidateErrorMessage = (error: unknown) => {
  const detail = responseDetailOf(error);
  if (detail && compareCandidateErrorMessages[detail]) {
    return compareCandidateErrorMessages[detail];
  }
  return 'B 후보 실행에 실패했습니다.';
};

const readRecommendationPresetPatches = (
  presetKey: string | null,
): Array<Record<string, unknown>> => {
  if (!presetKey || typeof window === 'undefined') return [];

  try {
    const raw = window.sessionStorage.getItem(presetKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(
      (item): item is Record<string, unknown> =>
        typeof item === 'object' && item !== null && !Array.isArray(item),
    );
  } catch {
    return [];
  }
};

const formatKnowledgeSummary = (candidate: CandidateDraft) =>
  [
    `Knowledge Base ${candidate.knowledgeBases.length}개`,
    `topK ${candidate.topK}`,
    `scoreThreshold ${candidate.scoreThreshold.toFixed(2)}`,
    `중복 근거 제거 ${candidate.dedupeRetrievedContext ? '켜짐' : '꺼짐'}`,
    `참조 문서 길이 제한 ${
      candidate.retrievedContextMaxChars === null
        ? '제한 없음'
        : `${candidate.retrievedContextMaxChars.toLocaleString('ko-KR')}자`
    }`,
    `검색 문서 압축 ${compressionLabelOf(candidate.retrievedContextCompression)}`,
    `답변·검색 문서 어휘 일치도 ${groundingLabelOf(candidate.answerGroundingCheck)}`,
  ].join('\n');

const formatOutputFormatValue = (
  value:
    | BaselineNodeOptions['output_format']
    | CandidateDraft['output_format']
    | undefined,
) => {
  if (!value) return 'TEXT';
  if (typeof value === 'string') return value.toUpperCase();
  return (value.type || 'text').toUpperCase();
};

const formatPromptDiffSummary = (
  baselineOptions: BaselineNodeOptions | null,
  candidate: CandidateDraft,
) =>
  [
    `system: ${baselineOptions?.system_prompt || '-'} → ${
      candidate.system_prompt || '-'
    }`,
    `user: ${baselineOptions?.user_prompt || '-'} → ${candidate.user_prompt || '-'}`,
    `assistant: ${baselineOptions?.assistant_prompt || '-'} → ${
      candidate.assistant_prompt || '-'
    }`,
  ].join('\n');

const formatParameterDiffSummary = (
  baselineOptions: BaselineNodeOptions | null,
  candidate: CandidateDraft,
) => {
  const parameters =
    baselineOptions?.parameters &&
    typeof baselineOptions.parameters === 'object'
      ? baselineOptions.parameters
      : {};
  const baselineStop = Array.isArray(parameters.stop)
    ? parameters.stop.filter((item): item is string => typeof item === 'string')
    : [];

  return [
    `max_tokens: ${parameters.max_tokens ?? '-'} → ${candidate.max_tokens}`,
    `temperature: ${parameters.temperature ?? '-'} → ${candidate.temperature}`,
    `top_p: ${parameters.top_p ?? '-'} → ${candidate.top_p}`,
    `presence_penalty: ${parameters.presence_penalty ?? '-'} → ${
      candidate.presence_penalty
    }`,
    `frequency_penalty: ${parameters.frequency_penalty ?? '-'} → ${
      candidate.frequency_penalty
    }`,
    `stop: ${baselineStop.join(', ') || '-'} → ${candidate.stop.join(', ') || '-'}`,
  ].join('\n');
};

const formatMetricDiff = (
  baselineValue: number | null | undefined,
  candidateValue: number | null | undefined,
  formatter: (value: number) => string,
) => {
  if (
    typeof baselineValue !== 'number' ||
    !Number.isFinite(baselineValue) ||
    typeof candidateValue !== 'number' ||
    !Number.isFinite(candidateValue)
  ) {
    return '-';
  }
  const diff = candidateValue - baselineValue;
  const diffLabel = diff > 0 ? `+${formatter(diff)}` : formatter(diff);
  return `${formatter(baselineValue)} → ${formatter(candidateValue)} (${diffLabel})`;
};

const formatMetricChange = (
  baselineValue: number | null | undefined,
  candidateValue: number | null | undefined,
  formatter: (value: number) => string,
) => {
  if (
    typeof baselineValue !== 'number' ||
    !Number.isFinite(baselineValue) ||
    typeof candidateValue !== 'number' ||
    !Number.isFinite(candidateValue)
  ) {
    return '-';
  }
  const diff = candidateValue - baselineValue;
  if (diff === 0) return '변화 없음';
  return `${diff > 0 ? '+' : ''}${formatter(diff)}`;
};

const metricChangeDetailOf = (
  baselineValue: number | null | undefined,
  candidateValue: number | null | undefined,
  formatter: (value: number) => string,
) => {
  if (
    typeof baselineValue !== 'number' ||
    !Number.isFinite(baselineValue) ||
    typeof candidateValue !== 'number' ||
    !Number.isFinite(candidateValue)
  ) {
    return {
      summary: '-',
      detail: '비교 기준 부족',
      tone: 'text-slate-500',
    };
  }

  const diff = candidateValue - baselineValue;
  if (diff === 0) {
    return {
      summary: '변화 없음',
      detail: '100% · 1.00x',
      tone: 'text-slate-600',
    };
  }

  if (baselineValue === 0) {
    return {
      summary: `${diff > 0 ? '+' : ''}${formatter(diff)}`,
      detail: 'A baseline이 0이라 비율 비교 불가',
      tone: diff < 0 ? 'text-emerald-700' : 'text-amber-700',
    };
  }

  const ratio = candidateValue / baselineValue;
  const percentChange = (ratio - 1) * 100;
  const summary =
    diff < 0
      ? `${Math.abs(percentChange).toFixed(1)}% 감소`
      : `${percentChange.toFixed(1)}% 증가`;
  const diffLabel = `${diff > 0 ? '+' : ''}${formatter(diff)}`;

  return {
    summary,
    detail: `${ratio.toFixed(2)}x · ${diffLabel}`,
    tone: diff < 0 ? 'text-emerald-700' : 'text-amber-700',
  };
};

const formatQualityScore = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${Math.round(value)}점`
    : '평가 불가';

const qualityConfidenceLabelOf = (confidence: string | null | undefined) => {
  if (confidence === 'high') return '높음';
  if (confidence === 'medium') return '보통';
  if (confidence === 'low') return '낮음';
  return '평가 불가';
};

const qualityScoreChangeDetailOf = (
  baselineValue: number | null | undefined,
  candidateValue: number | null | undefined,
  confidence: string | null | undefined,
  safeSummary: string | null | undefined,
  judgeCost: number | null | undefined,
) => {
  const judgeCostDetail =
    typeof judgeCost === 'number' && Number.isFinite(judgeCost)
      ? `품질 평가 비용 ${formatCost(judgeCost)}`
      : null;
  const detail = [
    safeSummary || '동일 rubric의 A/B 출력 품질 비교 결과입니다.',
    judgeCostDetail,
  ]
    .filter(Boolean)
    .join(' · ');
  if (
    typeof baselineValue !== 'number' ||
    !Number.isFinite(baselineValue) ||
    typeof candidateValue !== 'number' ||
    !Number.isFinite(candidateValue)
  ) {
    return {
      summary: '평가 불가',
      detail,
      tone: 'text-slate-500',
    };
  }

  const diff = candidateValue - baselineValue;
  const confidenceLabel = qualityConfidenceLabelOf(confidence);
  const direction = diff > 0 ? '상승' : diff < 0 ? '하락' : '동일';
  const scoreChange = diff === 0 ? '동일' : `${Math.abs(diff)}점 ${direction}`;
  return {
    summary: `${scoreChange} · 신뢰도 ${confidenceLabel}`,
    detail,
    tone:
      diff > 0
        ? 'text-emerald-700'
        : diff < 0
          ? 'text-amber-700'
          : 'text-slate-600',
  };
};

const candidateStatusLabelOf = (status: string | null | undefined) => {
  if (status === 'success') return '성공';
  if (status === 'schema_failed') return 'Schema 실패';
  if (status === 'failed') return '실패';
  if (status === 'running') return '실행 중';
  return status || '-';
};

const schemaStatusToneOf = (status: string | null | undefined) => {
  if (status === 'schema_failed' || status === 'failed') {
    return 'border-red-200 bg-red-50 text-red-800';
  }
  if (status === 'valid' || status === 'pass') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  }
  return 'border-slate-200 bg-slate-50 text-slate-600';
};

const downstreamStateToneOf = (state: string | null | undefined) => {
  if (state === 'compatible') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  }
  if (state === 'warning') {
    return 'border-amber-200 bg-amber-50 text-amber-800';
  }
  if (state === 'incompatible') {
    return 'border-red-200 bg-red-50 text-red-800';
  }
  return 'border-slate-200 bg-slate-50 text-slate-600';
};

const schemaErrorsOf = (errors: unknown[] | undefined) =>
  (errors || []).map((error) => {
    if (typeof error === 'string') return error;
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  });

const retrievalSummaryOf = (trace: Record<string, unknown> | undefined) => {
  const summary = trace?.rag_summary ?? trace?.retrieval_summary;
  return summary && typeof summary === 'object'
    ? (summary as Record<string, unknown>)
    : null;
};

const formatRetrievalSummary = (summary: Record<string, unknown> | null) => {
  if (!summary) return '없음';
  try {
    return JSON.stringify(summary, null, 2);
  } catch {
    return String(summary);
  }
};

type PreviewRow = {
  label: string;
  value: string;
};

const normalizePreviewValue = (value: string) =>
  value.replace(/\s+/g, ' ').trim();

const parseJsonPreview = (value: string): unknown => {
  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
};

const collectPreviewRows = (
  value: unknown,
  prefix = '',
  rows: PreviewRow[] = [],
) => {
  if (value === null || value === undefined) return rows;

  if (typeof value !== 'object') {
    const label = prefix.split('.').pop() || prefix || '내용';
    const text = normalizePreviewValue(String(value));
    if (text) rows.push({ label, value: text });
    return rows;
  }

  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      collectPreviewRows(item, `${prefix}[${index}]`, rows);
    });
    return rows;
  }

  Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
    const nextPrefix = prefix ? `${prefix}.${key}` : key;
    collectPreviewRows(item, nextPrefix, rows);
  });
  return rows;
};

const getPreviewRows = (value: string) => {
  if (!value) return [];
  return collectPreviewRows(parseJsonPreview(value));
};

const PreviewSection = ({ title, value }: { title: string; value: string }) => {
  const rows = getPreviewRows(value);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-3">
      <h3 className="text-xs font-bold text-slate-700">{title}</h3>
      {rows.length > 0 ? (
        <dl className="mt-3 space-y-2">
          {rows.map((row, index) => (
            <div key={`${row.label}-${index}`} className="grid gap-1">
              <dt className="text-[11px] font-bold text-slate-400">
                {row.label}
              </dt>
              <dd className="break-words rounded-md bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-700">
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-400">
          보관된 preview가 없습니다.
        </p>
      )}
    </section>
  );
};

const PanelResizeHandle = ({
  label,
  onPointerDown,
  onKeyDown,
}: {
  label: string;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
}) => (
  <div
    role="separator"
    aria-orientation="vertical"
    aria-label={label}
    tabIndex={0}
    onPointerDown={onPointerDown}
    onKeyDown={onKeyDown}
    className="group relative z-10 w-2 shrink-0 cursor-col-resize bg-slate-100 transition-colors hover:bg-emerald-50 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-inset"
  >
    <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-slate-200 transition-colors group-hover:bg-emerald-400" />
    <div className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-300 transition-colors group-hover:bg-emerald-400" />
  </div>
);

interface CostOptimizerPlaygroundContentProps {
  workflowId: string;
  nodeId: string;
  recommendationPresetKey: string | null;
  comparisonId: string | null;
  candidateId: string | null;
}

export default function CostOptimizerPlaygroundPage() {
  const params = useParams<{ id: string; nodeId: string }>();
  const searchParams = useSearchParams();
  const workflowId = params.id;
  const nodeId = params.nodeId;
  const recommendationPresetKey = searchParams.get('recommendationPresetKey');
  const comparisonId = searchParams.get('comparisonId');
  const candidateId = searchParams.get('candidateId');
  const sessionKey = [
    workflowId,
    nodeId,
    recommendationPresetKey,
    comparisonId,
    candidateId,
  ].join(':');

  return (
    <CostOptimizerPlaygroundContent
      key={sessionKey}
      workflowId={workflowId}
      nodeId={nodeId}
      recommendationPresetKey={recommendationPresetKey}
      comparisonId={comparisonId}
      candidateId={candidateId}
    />
  );
}

function CostOptimizerPlaygroundContent({
  workflowId,
  nodeId,
  recommendationPresetKey,
  comparisonId,
  candidateId,
}: CostOptimizerPlaygroundContentProps) {
  const router = useRouter();

  const [targetNode, setTargetNode] = useState<AppNode | null>(null);
  const [workflowNodes, setWorkflowNodes] = useState<AppNode[]>([]);
  const [workflowTitle, setWorkflowTitle] = useState('');
  const [baseline, setBaseline] = useState<CostOptimizerBaselineRow | null>(
    null,
  );
  const [candidate, setCandidate] = useState<CandidateDraft>(() =>
    candidateFromNode(null),
  );
  const [baselineSettingsTab, setBaselineSettingsTab] =
    useState<SettingsTab>('basic');
  const [candidateSettingsTab, setCandidateSettingsTab] =
    useState<SettingsTab>('basic');
  const [testName, setTestName] = useState('');
  const [activeMode, setActiveMode] = useState<PlaygroundMode>('setup');
  const [activeInspectorTab, setActiveInspectorTab] =
    useState<InspectorTab>('settings-diff');
  const [isLoadingNode, setIsLoadingNode] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [compareResult, setCompareResult] =
    useState<CostOptimizerCompareResponse | null>(null);
  const [isRunningCandidate, setIsRunningCandidate] = useState(false);
  const [isApplyingCandidate, setIsApplyingCandidate] = useState(false);
  const [candidateError, setCandidateError] = useState('');
  const [applyError, setApplyError] = useState('');
  const [applySuccess, setApplySuccess] = useState(false);
  const [isApplyDialogOpen, setIsApplyDialogOpen] = useState(false);
  const [isStale, setIsStale] = useState(false);
  const {
    layoutShellRef,
    isResizableLayout,
    fittedPanelWidths,
    fittedLayoutWidth,
    handleHorizontalResizeStart,
    handleHorizontalResizeKeyDown,
  } = useResizableNodeEditorPanels();

  useEffect(() => {
    let active = true;

    const loadDraft = async () => {
      setIsLoadingNode(true);
      setLoadError('');
      try {
        const availability = await workflowApi.getCostOptimizerAvailability(
          workflowId,
          nodeId,
        );
        if (!active) return;
        if (!availability.available || !availability.permission?.can_compare) {
          setLoadError(
            costOptimizerUnavailableMessage(
              availability.reason,
              availability.permission?.can_compare,
            ),
          );
          setTargetNode(null);
          setWorkflowNodes([]);
          setWorkflowTitle('');
          return;
        }
        const draft = await workflowApi.getDraftWorkflow(workflowId);
        if (!active) return;
        const draftRecord = draft as {
          name?: unknown;
          title?: unknown;
          workflow_name?: unknown;
        };
        const nextWorkflowTitle = [
          draftRecord.name,
          draftRecord.title,
          draftRecord.workflow_name,
        ].find(
          (value): value is string =>
            typeof value === 'string' && value.trim().length > 0,
        );
        const node = findTargetNode(draft, nodeId);
        setWorkflowNodes(nodesFromDraft(draft));
        setWorkflowTitle(nextWorkflowTitle?.trim() || workflowId);
        setTargetNode(node);
        setCandidate(candidateFromNode(node));
      } catch {
        if (!active) return;
        setLoadError('워크플로우 정보를 불러오지 못했습니다.');
        setWorkflowNodes([]);
        setWorkflowTitle('');
      } finally {
        if (active) setIsLoadingNode(false);
      }
    };

    void loadDraft();

    return () => {
      active = false;
    };
  }, [nodeId, recommendationPresetKey, workflowId]);

  const handleDeepLinkResolved = useCallback(
    (experiment: CostOptimizerExperimentSummary) => {
      const historyBaseline = baselineFromExperimentSummary(
        workflowId,
        nodeId,
        experiment,
      );
      if (!historyBaseline) return;
      setBaseline(historyBaseline);
      setActiveMode('report');
    },
    [nodeId, workflowId],
  );
  const handleDeepLinkError = useCallback((message: string) => {
    setLoadError(message);
  }, []);
  const history = useCostOptimizerHistory({
    enabled: !isLoadingNode && !loadError,
    workflowId,
    nodeId,
    baselineId: baseline?.baseline_id || null,
    isReportMode: activeMode === 'report',
    deepLinkExperimentId: comparisonId,
    deepLinkCandidateId: candidateId,
    onDeepLinkResolved: handleDeepLinkResolved,
    onDeepLinkError: handleDeepLinkError,
  });
  const clearHistorySelection = history.clearSelection;
  const selectCurrentHistory = history.selectCurrent;

  const nodeTitle = useMemo(() => {
    const data = targetNode?.data as { title?: string; label?: string } | null;
    return data?.title || data?.label || nodeId;
  }, [nodeId, targetNode]);
  const targetNodeDetailPath = useMemo(
    () => `/modules/${workflowId}?node=${encodeURIComponent(nodeId)}`,
    [nodeId, workflowId],
  );
  const handleBaselineSelected = useCallback(
    (selectedBaseline: CostOptimizerBaselineRow) => {
      const baseCandidate = candidateFromOptions(
        baselineOptionsOf(selectedBaseline) ||
          ((targetNode?.data || {}) as BaselineNodeOptions),
      );
      const patches = readRecommendationPresetPatches(recommendationPresetKey);

      setBaseline(selectedBaseline);
      setCandidate(applyCandidatePatchesToDraft(baseCandidate, patches));
      setActiveMode('setup');
      setIsStale(false);
      setCompareResult(null);
      clearHistorySelection();
      setCandidateError('');
      setApplyError('');
      setApplySuccess(false);
    },
    [clearHistorySelection, recommendationPresetKey, targetNode],
  );

  const baselineNodeOptions = baselineOptionsOf(baseline);
  const candidateIoContract = useMemo(
    () => ({
      inputs: inputContractChipsFromBaseline(baseline),
      outputs: downstreamOutputContractChipsFromNodes(workflowNodes, nodeId),
    }),
    [baseline, nodeId, workflowNodes],
  );

  const updateCandidate = <K extends keyof CandidateDraft>(
    key: K,
    value: CandidateDraft[K],
  ) => {
    setCandidate((current) => ({ ...current, [key]: value }));
    setIsStale(Boolean(baseline));
    setApplySuccess(false);
  };

  const updateCandidateNodeData = (updates: Partial<LLMNodeData>) => {
    const params = updates.parameters || {};
    setCandidate((current) => ({
      ...current,
      model_id: updates.model_id ?? current.model_id,
      fallback_model_id: updates.fallback_model_id ?? current.fallback_model_id,
      system_prompt: updates.system_prompt ?? current.system_prompt,
      user_prompt: updates.user_prompt ?? current.user_prompt,
      assistant_prompt: updates.assistant_prompt ?? current.assistant_prompt,
      referenced_variables:
        updates.referenced_variables ?? current.referenced_variables,
      max_tokens:
        typeof params.max_tokens === 'number'
          ? params.max_tokens
          : current.max_tokens,
      temperature:
        typeof params.temperature === 'number'
          ? params.temperature
          : current.temperature,
      top_p: typeof params.top_p === 'number' ? params.top_p : current.top_p,
      presence_penalty:
        typeof params.presence_penalty === 'number'
          ? params.presence_penalty
          : current.presence_penalty,
      frequency_penalty:
        typeof params.frequency_penalty === 'number'
          ? params.frequency_penalty
          : current.frequency_penalty,
      stop: Object.prototype.hasOwnProperty.call(params, 'stop')
        ? Array.isArray(params.stop)
          ? params.stop.filter(
              (item): item is string => typeof item === 'string',
            )
          : []
        : current.stop,
      knowledgeBases: updates.knowledgeBases ?? current.knowledgeBases,
      topK: typeof updates.topK === 'number' ? updates.topK : current.topK,
      scoreThreshold:
        typeof updates.scoreThreshold === 'number'
          ? updates.scoreThreshold
          : current.scoreThreshold,
      dedupeRetrievedContext:
        typeof updates.dedupeRetrievedContext === 'boolean'
          ? updates.dedupeRetrievedContext
          : current.dedupeRetrievedContext,
      retrievedContextMaxChars: Object.prototype.hasOwnProperty.call(
        updates,
        'retrievedContextMaxChars',
      )
        ? typeof updates.retrievedContextMaxChars === 'number'
          ? updates.retrievedContextMaxChars
          : null
        : current.retrievedContextMaxChars,
      retrievedContextCompression:
        updates.retrievedContextCompression ??
        current.retrievedContextCompression,
      answerGroundingCheck:
        updates.answerGroundingCheck ?? current.answerGroundingCheck,
    }));
    setIsStale(Boolean(baseline));
    setApplySuccess(false);
  };

  const canRunCandidate =
    Boolean(baseline) &&
    (candidate.auto_model_routing || Boolean(candidate.model_id)) &&
    !isRunningCandidate;

  const handleRunCandidate = async () => {
    const selectedBaseline = baseline;
    if (
      !selectedBaseline ||
      !(candidate.auto_model_routing || Boolean(candidate.model_id)) ||
      isRunningCandidate
    ) {
      return;
    }

    setIsRunningCandidate(true);
    setCandidateError('');
    setApplyError('');
    setApplySuccess(false);
    try {
      const response = await workflowApi.compareCostOptimizerCandidate(
        workflowId,
        nodeId,
        {
          baseline_id: selectedBaseline.baseline_id,
          candidate: compareRequestCandidateFromDraft(
            candidate,
            testName.trim() || 'B',
          ),
        },
      );
      setCompareResult(response);
      selectCurrentHistory();
      setActiveInspectorTab(
        response.downstream_compatibility?.state === 'warning' ||
          response.downstream_compatibility?.state === 'incompatible'
          ? 'downstream'
          : 'settings-diff',
      );
      setIsStale(false);
      setActiveMode('report');
    } catch (error) {
      setCandidateError(compareCandidateErrorMessage(error));
    } finally {
      setIsRunningCandidate(false);
    }
  };

  const handleApplyCandidate = () => {
    if (
      !compareResult ||
      !candidateResult ||
      candidateResult.status !== 'success'
    ) {
      return;
    }
    setApplyError('');
    setApplySuccess(false);
    setIsApplyDialogOpen(true);
  };

  const confirmApplyCandidate = async () => {
    if (
      !compareResult ||
      !candidateResult ||
      candidateResult.status !== 'success'
    ) {
      return;
    }
    setIsApplyingCandidate(true);
    setApplyError('');
    setApplySuccess(false);
    try {
      if (!compareResult.comparison_id) {
        throw new Error('missing comparison id');
      }
      const downstreamState = compareResult.downstream_compatibility?.state;
      const needsDownstreamAck =
        downstreamState === 'warning' || downstreamState === 'incompatible';
      const expectation = await resolveWorkflowDraftCASExpectation(workflowId, {
        refresh: true,
      });
      const applyResult = await workflowApi.applyCostOptimizerCandidate(workflowId, nodeId, {
        comparison_id: compareResult.comparison_id,
        candidate_settings: compareRequestCandidateFromDraft(
          candidate,
          testName.trim() || 'B',
        ),
        acknowledge_downstream_warning: needsDownstreamAck,
        ...expectation,
      });
      ingestWorkflowDraftCASResult(workflowId, applyResult);
      setApplySuccess(true);
      setIsApplyDialogOpen(false);
    } catch {
      setApplyError('후보 설정 적용에 실패했습니다.');
    } finally {
      setIsApplyingCandidate(false);
    }
  };

  const candidateResult = compareResult?.candidate ?? null;
  const candidateUsage = candidateResult?.usage;
  const candidateTotalTokens = readNumber(candidateUsage, [
    'total_tokens',
    'totalTokens',
  ]);
  const candidateTotalCost = readNumber(candidateUsage, [
    'total_cost',
    'cost',
    'totalCost',
  ]);
  const candidateLatency =
    typeof candidateResult?.latency_ms === 'number'
      ? candidateResult.latency_ms
      : readNumber(candidateUsage, ['latency_ms', 'latencyMs']);
  const baselineUsage = compareResult?.baseline?.usage;
  const baselinePromptTokens = readNumber(baselineUsage, [
    'prompt_tokens',
    'promptTokens',
  ]);
  const baselineCompletionTokens = readNumber(baselineUsage, [
    'completion_tokens',
    'completionTokens',
  ]);
  const baselineLatency =
    readNumber(baselineUsage, ['latency_ms', 'latencyMs']) ??
    baseline?.latency_ms ??
    null;
  const candidatePromptTokens = readNumber(candidateUsage, [
    'prompt_tokens',
    'promptTokens',
  ]);
  const candidateCompletionTokens = readNumber(candidateUsage, [
    'completion_tokens',
    'completionTokens',
  ]);
  const candidateSchemaValidation = candidateResult?.schema_validation;
  const candidateSchemaErrors = schemaErrorsOf(
    candidateSchemaValidation?.errors,
  );
  const candidateModelRoutingSummary = modelRoutingSummaryOf(
    candidateResult?.output,
    candidateResult?.trace,
  );
  const baselineRetrievalSummary = retrievalSummaryOf(
    compareResult?.baseline?.trace,
  );
  const candidateRetrievalSummary = retrievalSummaryOf(candidateResult?.trace);
  const downstreamCompatibility =
    compareResult?.downstream_compatibility ??
    baseline?.downstream_compatibility ??
    null;
  const downstreamCheckedNodeIds =
    downstreamCompatibility?.contract_check?.checked_node_ids || [];
  const downstreamWarnings =
    downstreamCompatibility?.contract_check?.warnings || [];
  const selectedHistoryRow = history.selectedHistoryRow;
  const selectedHistoryBaseline =
    selectedHistoryRow?.experiment.baseline_summary ?? null;
  const activeCandidateMetrics = selectedHistoryRow
    ? {
        status: selectedHistoryRow.candidate.status,
        cost: selectedHistoryRow.candidate.total_cost,
        promptTokens: selectedHistoryRow.candidate.prompt_tokens ?? null,
        completionTokens: selectedHistoryRow.candidate.completion_tokens ?? null,
        totalTokens: selectedHistoryRow.candidate.total_tokens,
        latency: selectedHistoryRow.candidate.latency_ms,
      }
    : {
        status: candidateResult?.status,
        cost: candidateTotalCost,
        promptTokens: candidatePromptTokens,
        completionTokens: candidateCompletionTokens,
        totalTokens: candidateTotalTokens,
        latency: candidateLatency,
      };
  const activeBaselineMetrics = selectedHistoryRow
    ? {
        cost: selectedHistoryBaseline?.cost ?? null,
        promptTokens: selectedHistoryBaseline?.prompt_tokens ?? null,
        completionTokens: selectedHistoryBaseline?.completion_tokens ?? null,
        totalTokens: selectedHistoryBaseline?.total_tokens ?? null,
        latency: selectedHistoryBaseline?.latency_ms ?? null,
        model: selectedHistoryBaseline?.model || '-',
      }
    : {
        cost: baseline?.cost ?? null,
        promptTokens: baselinePromptTokens,
        completionTokens: baselineCompletionTokens,
        totalTokens: baseline?.total_tokens ?? null,
        latency: baselineLatency,
        model: baseline?.model || '-',
      };
  const activeCandidateStatus = activeCandidateMetrics.status;
  const activeCandidateCost = activeCandidateMetrics.cost;
  const activeCandidateTokens = activeCandidateMetrics.totalTokens;
  const activeCandidateLatency = activeCandidateMetrics.latency;
  const activePromptTokens = activeCandidateMetrics.promptTokens;
  const activeCompletionTokens = activeCandidateMetrics.completionTokens;
  const activeSchemaStatus = selectedHistoryRow
    ? selectedHistoryRow.candidate.schema_status
    : candidateSchemaValidation?.status;
  const activeSchemaStatusLabel = schemaStatusLabelOf(activeSchemaStatus);
  const activeDownstreamState = selectedHistoryRow
    ? selectedHistoryRow.candidate.downstream_state
    : downstreamCompatibility?.state;
  const activeDownstreamLabel = selectedHistoryRow
    ? downstreamStateLabelOf(activeDownstreamState)
    : downstreamLabelOf(downstreamCompatibility);
  const activeDownstreamMessage = selectedHistoryRow
    ? '저장된 이전 실험 summary 기준의 downstream 판정입니다. 상세 contract check와 warning은 해당 실험 상세 API가 제공될 때 확장합니다.'
    : downstreamMessageOf(downstreamCompatibility);
  const activeDownstreamTone = selectedHistoryRow
    ? downstreamStateToneOf(activeDownstreamState)
    : downstreamToneOf(downstreamCompatibility);
  const activeQualityEvaluation = selectedHistoryRow
    ? selectedHistoryRow.candidate.quality_evaluation
    : compareResult?.quality_evaluation;
  const activeBaselineQualityScore =
    activeQualityEvaluation?.baseline?.score ?? null;
  const activeCandidateQualityScore =
    activeQualityEvaluation?.candidate?.score ?? null;
  const activeQualityChange = qualityScoreChangeDetailOf(
    activeBaselineQualityScore,
    activeCandidateQualityScore,
    activeQualityEvaluation?.confidence,
    activeQualityEvaluation?.safe_summary,
    activeQualityEvaluation?.judge_cost,
  );
  const schemaStatusLabel = activeSchemaStatusLabel;
  const baselineCost = activeBaselineMetrics.cost;
  const baselineTotalTokens = activeBaselineMetrics.totalTokens;
  const activeBaselinePromptTokens = activeBaselineMetrics.promptTokens;
  const activeBaselineCompletionTokens = activeBaselineMetrics.completionTokens;
  const activeBaselineLatency = activeBaselineMetrics.latency;
  const activeBaselineModel = activeBaselineMetrics.model;
  const activeBaselineOutput = selectedHistoryRow
    ? selectedHistoryBaseline?.output ??
      selectedHistoryBaseline?.output_preview ??
      '선택한 이전 실험의 baseline 출력이 저장되어 있지 않습니다.'
    : baseline?.output ?? baseline?.output_preview;
  const activeCandidateOutput = selectedHistoryRow
    ? selectedHistoryRow.candidate.output ??
      selectedHistoryRow.candidate.output_preview ??
      '선택한 이전 실험의 B candidate 출력이 저장되어 있지 않습니다.'
    : candidateResult?.output;
  const baselineOutputFieldLabels = fieldLabelsFromOutputFormat(
    baselineNodeOptions?.output_format,
  );
  const activeCandidateActualModel =
    candidateModelRoutingSummary?.selectedModel ||
    (isUnknownRecord(activeCandidateOutput)
      ? stringValue(activeCandidateOutput.model)
      : undefined) ||
    (selectedHistoryRow ? selectedHistoryRow.candidate.model_id : undefined) ||
    candidate.model_id ||
    '-';
  const hasCandidateRoutingModelDiff =
    Boolean(candidate.auto_model_routing || candidateModelRoutingSummary) &&
    activeCandidateActualModel !== (candidate.model_id || '-');
  const activeCandidateUsage = selectedHistoryRow
    ? {
        cost: activeCandidateCost,
        total_tokens: activeCandidateTokens,
        latency_ms: activeCandidateLatency,
      }
    : candidateUsage;
  const candidateDecision = (() => {
    if (!candidateResult && !selectedHistoryRow) return null;
    const hasCostImprovement =
      typeof baselineCost === 'number' &&
      typeof activeCandidateCost === 'number' &&
      activeCandidateCost < baselineCost;
    const hasTokenImprovement =
      typeof baselineTotalTokens === 'number' &&
      typeof activeCandidateTokens === 'number' &&
      activeCandidateTokens < baselineTotalTokens;
    const hasCostWorsening =
      typeof baselineCost === 'number' &&
      typeof activeCandidateCost === 'number' &&
      activeCandidateCost > baselineCost;
    const hasTokenWorsening =
      typeof baselineTotalTokens === 'number' &&
      typeof activeCandidateTokens === 'number' &&
      activeCandidateTokens > baselineTotalTokens;
    const hasLatencyWorsening =
      typeof activeBaselineLatency === 'number' &&
      typeof activeCandidateLatency === 'number' &&
      activeCandidateLatency > activeBaselineLatency;
    const isSchemaFailed = schemaStatusLabel === '실패';
    const isDownstreamIncompatible = activeDownstreamState === 'incompatible';
    const isDownstreamWarning = activeDownstreamState === 'warning';
    const hasQualityScores =
      typeof activeBaselineQualityScore === 'number' &&
      typeof activeCandidateQualityScore === 'number';
    const hasQualityWarning =
      Boolean(activeQualityEvaluation) &&
      (activeQualityEvaluation?.status !== 'completed' ||
        !hasQualityScores ||
        activeQualityEvaluation?.confidence === 'low' ||
        (hasQualityScores &&
          activeCandidateQualityScore < activeBaselineQualityScore));
    const isCandidateFailed =
      Boolean(activeCandidateStatus) && activeCandidateStatus !== 'success';

    if (isCandidateFailed || isSchemaFailed || isDownstreamIncompatible) {
      return {
        label: '적용 비추천',
        tone: 'border-red-200 bg-red-50 text-red-800',
      };
    }
    if (
      hasCostWorsening ||
      hasTokenWorsening ||
      hasLatencyWorsening ||
      isDownstreamWarning ||
      hasQualityWarning
    ) {
      return {
        label: '주의 필요',
        tone: 'border-amber-200 bg-amber-50 text-amber-800',
      };
    }
    if (hasCostImprovement || hasTokenImprovement) {
      return {
        label: '적용 후보로 적합',
        tone: 'border-emerald-200 bg-emerald-50 text-emerald-800',
      };
    }
    return {
      label: '주의 필요',
      tone: 'border-amber-200 bg-amber-50 text-amber-800',
    };
  })();
  const decisionReasons = [
    `비용 변화: ${formatMetricChange(baselineCost, activeCandidateCost, formatCost)}`,
    `토큰 변화: ${formatMetricChange(
      baselineTotalTokens,
      activeCandidateTokens,
      (value) => formatMetric(value),
    )}`,
    `latency 변화: ${formatMetricChange(
      activeBaselineLatency,
      activeCandidateLatency,
      formatLatency,
    )}`,
    `출력 품질: ${activeQualityChange.summary}`,
    `B 실행 상태: ${candidateStatusLabelOf(activeCandidateStatus)}`,
    `Schema: ${schemaStatusLabel}`,
    `Downstream: ${activeDownstreamLabel}`,
  ];
  const comparisonRows = [
    {
      label: '비용',
      baseline: baselineCost === null ? '-' : formatCost(baselineCost),
      candidate: formatCandidateCost(activeCandidateCost),
      change: metricChangeDetailOf(
        baselineCost,
        activeCandidateCost,
        formatCost,
      ),
    },
    {
      label: '입력 토큰',
      baseline:
        activeBaselinePromptTokens === null
          ? '-'
          : formatMetric(activeBaselinePromptTokens),
      candidate:
        activePromptTokens === null
          ? '-'
          : formatMetric(activePromptTokens),
      change: metricChangeDetailOf(
        activeBaselinePromptTokens,
        activePromptTokens,
        (value) => formatMetric(value),
      ),
    },
    {
      label: '출력 토큰',
      baseline:
        activeBaselineCompletionTokens === null
          ? '-'
          : formatMetric(activeBaselineCompletionTokens),
      candidate:
        activeCompletionTokens === null
          ? '-'
          : formatMetric(activeCompletionTokens),
      change: metricChangeDetailOf(
        activeBaselineCompletionTokens,
        activeCompletionTokens,
        (value) => formatMetric(value),
      ),
    },
    {
      label: '전체 토큰',
      baseline:
        baselineTotalTokens === null ? '-' : formatMetric(baselineTotalTokens),
      candidate:
        activeCandidateTokens === null || activeCandidateTokens === undefined
          ? '-'
          : formatMetric(activeCandidateTokens),
      change: metricChangeDetailOf(
        baselineTotalTokens,
        activeCandidateTokens,
        (value) => formatMetric(value),
      ),
    },
    {
      label: '실행 시간',
      baseline:
        activeBaselineLatency === null ? '-' : formatLatency(activeBaselineLatency),
      candidate:
        activeCandidateLatency === null || activeCandidateLatency === undefined
          ? '-'
          : formatLatency(activeCandidateLatency),
      change: metricChangeDetailOf(
        activeBaselineLatency,
        activeCandidateLatency,
        formatLatency,
      ),
    },
    {
      label: '출력 품질 점수',
      baseline: formatQualityScore(activeBaselineQualityScore),
      candidate: formatQualityScore(activeCandidateQualityScore),
      change: activeQualityChange,
    },
    {
      label: '실행 상태',
      baseline: '성공',
      candidate: candidateStatusLabelOf(activeCandidateStatus),
      change: {
        summary: activeCandidateStatus === 'success' ? '유지' : '악화',
        detail: '상태 비교',
        tone:
          activeCandidateStatus === 'success'
            ? 'text-emerald-700'
            : 'text-red-700',
      },
    },
    {
      label: '출력 스키마',
      baseline: '기준',
      candidate: schemaStatusLabel,
      change: {
        summary: schemaStatusLabel === '실패' ? '확인 필요' : '허용',
        detail: 'schema check',
        tone:
          schemaStatusLabel === '실패'
            ? 'text-red-700'
            : 'text-emerald-700',
      },
    },
    {
      label: '후속 노드 영향',
      baseline: downstreamLabelOf(baseline?.downstream_compatibility),
      candidate: activeDownstreamLabel,
      change:
        activeDownstreamState === 'warning' ||
        activeDownstreamState === 'incompatible'
          ? {
              summary: '확인 필요',
              detail: 'downstream check',
              tone: 'text-amber-700',
            }
          : {
              summary: '허용',
              detail: 'downstream check',
              tone: 'text-emerald-700',
            },
    },
  ];
  const canApplyCurrentCandidate =
    Boolean(candidateResult) &&
    candidateResult?.status === 'success' &&
    !selectedHistoryRow &&
    !isStale &&
    !isApplyingCandidate;
  const applyCandidateDisabledReason = (() => {
    if (selectedHistoryRow) {
      return '이전 실험 이력은 현재 목록 응답에 candidate 설정 원문이 없어 바로 적용할 수 없습니다.';
    }
    if (!candidateResult) return '먼저 B 후보를 실행해야 적용할 수 있습니다.';
    if (candidateResult.status !== 'success') {
      return '성공한 B 후보 실행 결과만 적용할 수 있습니다.';
    }
    if (isStale) {
      return '현재 설정으로 다시 실행한 뒤 적용하세요.';
    }
    if (isApplyingCandidate) return '후보 설정을 적용하는 중입니다.';
    return 'B 후보 설정 전체를 현재 LLM 노드 draft에 적용합니다.';
  })();
  return (
    <main className="flex h-screen flex-col overflow-hidden bg-slate-100 text-slate-950">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 px-6 py-4 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => router.push(targetNodeDetailPath)}
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            >
              <ArrowLeft className="h-4 w-4" />
              워크플로우로 돌아가기
            </button>
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-emerald-600">
                Cost Optimizer Playground
              </p>
              <h1 className="truncate text-lg font-bold">
                {isLoadingNode ? 'LLM 노드 확인 중' : nodeTitle}
              </h1>
              <p className="mt-0.5 truncate text-xs font-semibold text-slate-500">
                {workflowTitle || `workflow ${workflowId.slice(0, 8)}`}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
            {baseline ? (
              <>
                <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-1">
                  {(
                    [
                      ['setup', '실험 설정'],
                      ['report', '결과 분석'],
                    ] as const
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      type="button"
                      aria-pressed={activeMode === mode}
                      onClick={() => setActiveMode(mode)}
                      className={`rounded-md px-3 py-1.5 text-xs font-bold transition-colors ${
                        activeMode === mode
                          ? 'bg-white text-emerald-700 shadow-sm'
                          : 'text-slate-500 hover:text-slate-800'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-blue-700">
                  같은 입력 기준
                </span>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-slate-600">
                  기준 실행 {formatContextDateTime(baseline.run_started_at)}
                </span>
                <span
                  className={`rounded-full border px-3 py-1 ${downstreamToneOf(
                    downstreamCompatibility,
                  )}`}
                >
                  {downstreamLabelOf(downstreamCompatibility)}
                </span>
              </>
            ) : (
              <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-amber-700">
                테스트 기준 선택 필요
              </span>
            )}
          </div>
        </div>
      </header>

      <section
        ref={layoutShellRef}
        className="flex min-h-0 flex-1 justify-center overflow-hidden p-4"
      >
        {loadError ? (
          <div className="flex h-full min-h-0 w-full max-w-[760px] flex-col justify-center">
            <div className="rounded-xl border border-red-200 bg-white p-6 shadow-sm">
              <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700">
                {loadError}
              </p>
              <button
                type="button"
                onClick={() => router.push(targetNodeDetailPath)}
                className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                <ArrowLeft className="h-4 w-4" />
                노드 상세로 돌아가기
              </button>
            </div>
          </div>
        ) : !baseline ? (
          <div className="flex h-full min-h-0 w-full max-w-[760px] flex-col overflow-y-auto py-6">
            <div className="shrink-0 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="mb-5 rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3">
                <div className="text-sm font-bold text-emerald-950">
                  먼저 A/B 테스트 기준을 선택하세요
                </div>
                <p className="mt-1 text-xs leading-relaxed text-emerald-800">
                  기준 실행 로그가 정해지면 B 후보 설정과 Inspector가 열립니다.
                  같은 입력을 기준으로 비교해야 비용, 출력, trace 차이가 의미를
                  갖습니다.
                </p>
              </div>
              <CostOptimizerBaselineSelection
                workflowId={workflowId}
                nodeId={nodeId}
                onBaselineSelected={handleBaselineSelected}
                onClose={() => router.push(targetNodeDetailPath)}
              />
            </div>
          </div>
        ) : activeMode === 'setup' ? (
          <div
            className="grid h-full min-h-0 w-full grid-cols-1 gap-4 overflow-hidden xl:gap-0"
            style={
              isResizableLayout
                ? {
                    width: `${fittedLayoutWidth}px`,
                    gridTemplateColumns: `${fittedPanelWidths.left}px 8px ${fittedPanelWidths.center}px 8px ${fittedPanelWidths.right}px`,
                  }
                : undefined
            }
          >
            <aside className="min-h-0 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Clock3 className="h-4 w-4 text-emerald-600" />
                  <h2 className="text-sm font-bold">A 실행 시점 옵션</h2>
                </div>
                {baseline ? (
                  <button
                    type="button"
                    onClick={() => {
                      setBaseline(null);
                      setActiveMode('setup');
                      setIsStale(false);
                      setCompareResult(null);
                      clearHistorySelection();
                      setCandidateError('');
                      setApplyError('');
                      setApplySuccess(false);
                    }}
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                  >
                    다시 선택
                  </button>
                ) : null}
              </div>
              {baseline ? (
                <div className="space-y-4">
                  {baselineNodeOptions ? (
                    <NodeSettingsComparisonPanel
                      title="실행 시점 옵션"
                      nodeId={`${nodeId}-baseline`}
                      tab={baselineSettingsTab}
                      onTabChange={setBaselineSettingsTab}
                      draft={candidateFromOptions(baselineNodeOptions)}
                      readOnly
                    />
                  ) : (
                    <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800">
                      이 baseline에는 실행 시점 노드 옵션 스냅샷이 없습니다. B
                      후보는 현재 노드 설정을 기준으로 유지됩니다.
                    </p>
                  )}
                </div>
              ) : (
                <CostOptimizerBaselineSelection
                  workflowId={workflowId}
                  nodeId={nodeId}
                  onBaselineSelected={handleBaselineSelected}
                  onClose={() => router.push(targetNodeDetailPath)}
                />
              )}
            </aside>

            {isResizableLayout ? (
              <PanelResizeHandle
                label="baseline 패널과 candidate 패널 사이 폭 조절"
                onPointerDown={(event) =>
                  handleHorizontalResizeStart('left-center', event)
                }
                onKeyDown={(event) =>
                  handleHorizontalResizeKeyDown('left-center', event)
                }
              />
            ) : null}

            <section className="min-h-0 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-sm">
              <div className="sticky top-0 z-[1] flex items-center justify-between border-b border-slate-200 bg-white px-5 py-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-bold">
                    <FlaskConical className="h-4 w-4 text-emerald-600" />B
                    candidate
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    현재 LLM 노드 설정 복사본을 기준으로 후보 옵션을 조정합니다.
                  </p>
                  <label className="mt-3 grid max-w-md gap-1 text-xs font-semibold text-slate-600">
                    <span>테스트명</span>
                    <input
                      value={testName}
                      onChange={(event) => setTestName(event.target.value)}
                      className="rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-900 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
                      placeholder="예: gpt-4.1-mini 비용 절감 테스트"
                    />
                  </label>
                </div>
                <button
                  type="button"
                  disabled={!canRunCandidate}
                  onClick={handleRunCandidate}
                  aria-label={isRunningCandidate ? 'B 실행 중' : 'B 후보 실행'}
                  className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
                  title={
                    candidate.auto_model_routing || candidate.model_id
                      ? 'A baseline 입력으로 B 후보만 실행합니다.'
                      : 'B 후보 모델을 선택하거나 자동 라우팅을 켜세요.'
                  }
                >
                  {isRunningCandidate ? (
                    <span
                      role="status"
                      className="inline-flex items-center gap-1.5"
                    >
                      <span
                        aria-hidden="true"
                        data-testid="cost-optimizer-running-spinner"
                        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-400 border-t-slate-700"
                      />
                      <span>B 실행 중</span>
                    </span>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5" />B 후보 실행
                    </>
                  )}
                </button>
              </div>

              {loadError ? (
                <p className="m-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {loadError}
                </p>
              ) : null}
              {candidateError ? (
                <p className="m-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {candidateError}
                </p>
              ) : null}

              <div className="p-5">
                <NodeSettingsComparisonPanel
                  title="후보 옵션"
                  hideTitle
                  nodeId={`${nodeId}-candidate`}
                  tab={candidateSettingsTab}
                  onTabChange={setCandidateSettingsTab}
                  draft={candidate}
                  onChange={updateCandidate}
                  onNodeDataChange={updateCandidateNodeData}
                  ioContract={candidateIoContract}
                />
              </div>
            </section>

            {isResizableLayout ? (
              <PanelResizeHandle
                label="candidate 패널과 inspector 패널 사이 폭 조절"
                onPointerDown={(event) =>
                  handleHorizontalResizeStart('center-right', event)
                }
                onKeyDown={(event) =>
                  handleHorizontalResizeKeyDown('center-right', event)
                }
              />
            ) : null}

            <aside className="min-h-0 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-4 flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-emerald-600" />
                <h2 className="text-sm font-bold">기준 실행 정보</h2>
              </div>
              <div className="space-y-3">
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                  <div className="text-xs font-semibold text-emerald-700">
                    선택된 기준 실행
                  </div>
                  <div className="mt-1 text-sm font-bold text-emerald-950">
                    {baseline.model}
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div className="rounded-md bg-white/70 p-2">
                      <dt className="text-emerald-700">비용</dt>
                      <dd className="font-bold">{formatCost(baseline.cost)}</dd>
                    </div>
                    <div className="rounded-md bg-white/70 p-2">
                      <dt className="text-emerald-700">토큰</dt>
                      <dd className="font-bold">
                        {formatMetric(baseline.total_tokens)}
                      </dd>
                    </div>
                    <div className="rounded-md bg-white/70 p-2">
                      <dt className="text-emerald-700">시간</dt>
                      <dd className="font-bold">
                        {formatLatency(baseline.latency_ms)}
                      </dd>
                    </div>
                  </dl>
                </div>
                <PreviewSection
                  title="기준 입력"
                  value={baseline.input_preview}
                />
                <PreviewSection
                  title="기준 출력"
                  value={baseline.output_preview}
                />
                <div className="rounded-lg border border-slate-200 p-3">
                  <div className="text-xs font-bold text-slate-500">
                    B 후보 비교 컨텍스트
                  </div>
                  <dl className="mt-2 space-y-2 text-xs">
                    <div className="flex justify-between gap-3">
                      <dt className="text-slate-500">B 모델</dt>
                      <dd className="font-semibold">
                        {candidate.model_id || '-'}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-slate-500">B 출력</dt>
                      <dd className="font-semibold">
                        {candidate.output_format.toUpperCase()}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-slate-500">B 지식 베이스</dt>
                      <dd className="font-semibold">
                        {candidate.knowledgeBases.length}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-slate-500">B Threshold</dt>
                      <dd className="font-semibold">
                        {candidate.scoreThreshold.toFixed(2)}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="rounded-lg border border-slate-200 p-3">
                  <div className="text-xs font-bold text-slate-500">상태</div>
                  <p className="mt-2 text-xs leading-relaxed text-slate-500">
                    {baseline
                      ? 'A baseline이 고정되었습니다. B 옵션을 조정하면서 실행 결과를 비교할 수 있습니다.'
                      : '먼저 왼쪽에서 A baseline을 선택하세요.'}
                  </p>
                </div>
              </div>
            </aside>
          </div>
        ) : (
          <div className="flex h-full min-h-0 w-full max-w-[90vw] flex-col gap-4 overflow-y-auto">
            <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-emerald-600" />
                  <h2 className="text-base font-bold">비교 리포트</h2>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setActiveMode('setup')}
                    className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
                  >
                    실험 설정으로 돌아가기
                  </button>
                  <button
                    type="button"
                    disabled={!canApplyCurrentCandidate}
                    onClick={handleApplyCandidate}
                    className="rounded-md bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
                    title={applyCandidateDisabledReason}
                  >
                    {isApplyingCandidate ? '적용 중' : '현재 노드에 적용'}
                  </button>
                </div>
              </div>
              {applySuccess ? (
                <p className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800">
                  현재 노드에 후보 설정을 적용했습니다.
                </p>
              ) : null}
              {applyError ? (
                <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700">
                  {applyError}
                </p>
              ) : null}
              {isStale ? (
                <p className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800">
                  후보 설정이 마지막 B 실행 이후 변경되었습니다. 현재 설정으로
                  다시 실행해야 합니다.
                </p>
              ) : null}
              <p className="mt-4 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800">
                비교 실행에서 발생한 LLM 비용도 usage 기록에 포함됩니다. B
                후보를 반복 실행하면 각 실행 비용이 별도로 추적됩니다.
              </p>
            </div>

            <CostOptimizerHistoryPanel
              history={history}
              currentRow={
                candidateResult
                  ? {
                      baselineId: baseline?.baseline_id,
                      baselineModel: baseline?.model,
                      testName: testName.trim() || 'B',
                      model: candidate.model_id,
                      totalCost: candidateTotalCost,
                      totalTokens: candidateTotalTokens,
                      latencyMs: candidateLatency,
                      schemaLabel: schemaStatusLabel,
                      downstreamLabel: downstreamLabelOf(downstreamCompatibility),
                    }
                  : null
              }
            />

            {!candidateResult && !selectedHistoryRow ? (
              <section className="rounded-lg border border-dashed border-amber-200 bg-amber-50 p-5 text-sm font-semibold leading-relaxed text-amber-800">
                B 실행 후 결과 분석이 표시됩니다. 지금은 후보 설정만 준비된
                상태입니다.
              </section>
            ) : null}

            {candidateDecision ? (
              <>
                <section
                  className={`rounded-lg border p-5 shadow-sm ${candidateDecision.tone}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <h3 className="text-base font-bold">
                        {candidateDecision.label}
                      </h3>
                      <p className="mt-1 text-xs font-semibold">
                        비용만이 아니라 실행 상태, schema, downstream, latency를
                        함께 본 판단입니다.
                      </p>
                    </div>
                    <div className="grid gap-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
                      {decisionReasons.map((reason) => (
                        <span
                          key={reason}
                          className="rounded-md border border-current/20 bg-white/70 px-2 py-1 font-semibold"
                        >
                          {reason}
                        </span>
                      ))}
                    </div>
                  </div>
                </section>

                <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="text-sm font-bold">핵심 지표 비교</h3>
                  <div className="mt-4 overflow-auto rounded-lg border border-slate-200">
                    <table
                      aria-label="핵심 지표 비교"
                      className="min-w-full text-left text-xs"
                    >
                      <thead className="bg-slate-50 text-slate-500">
                        <tr>
                          <th className="px-3 py-2 font-bold">항목</th>
                          <th className="px-3 py-2 font-bold">A baseline</th>
                          <th className="px-3 py-2 font-bold">B candidate</th>
                          <th className="px-3 py-2 font-bold">변화</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {comparisonRows.map((row) => (
                          <tr key={row.label}>
                            <td className="px-3 py-2 font-bold text-slate-700">
                              {row.label}
                            </td>
                            <td className="px-3 py-2">{row.baseline}</td>
                            <td className="px-3 py-2">{row.candidate}</td>
                            <td className="px-3 py-2">
                              <div
                                className={`font-bold ${row.change.tone}`}
                                title={row.change.detail}
                              >
                                {row.change.summary}
                              </div>
                            </td>
                          </tr>
                        ))}
                        <tr className="bg-slate-50/80">
                          <td className="px-3 py-3 font-bold text-slate-700">
                            적용
                          </td>
                          <td className="px-3 py-3 text-slate-500">
                            현재 노드 설정 유지
                          </td>
                          <td className="px-3 py-3">
                            <button
                              type="button"
                              disabled={!canApplyCurrentCandidate}
                              onClick={handleApplyCandidate}
                              title={applyCandidateDisabledReason}
                              className="inline-flex h-9 items-center rounded-md bg-emerald-600 px-3 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
                            >
                              {isApplyingCandidate
                                ? '적용 중'
                                : '이 설정으로 노드 적용하기'}
                            </button>
                          </td>
                          <td className="px-3 py-3 text-xs font-semibold text-slate-500">
                            {canApplyCurrentCandidate
                              ? 'B 후보 설정을 현재 노드 draft에 적용합니다.'
                              : applyCandidateDisabledReason}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="text-sm font-bold">출력 품질 비교</h3>
                  <div className="mt-4 grid gap-4 lg:grid-cols-2">
                    <CostOptimizerOutputPreviewPanel
                      title="A baseline 출력"
                      value={activeBaselineOutput}
                      fieldLabels={baselineOutputFieldLabels}
                      usage={
                        selectedHistoryRow || baseline
                          ? {
                              cost: baselineCost,
                              prompt_tokens: activeBaselinePromptTokens,
                              completion_tokens: activeBaselineCompletionTokens,
                              total_tokens: baselineTotalTokens,
                              latency_ms: activeBaselineLatency,
                            }
                          : undefined
                      }
                    />
                    <CostOptimizerOutputPreviewPanel
                      title="B candidate 출력"
                      value={activeCandidateOutput}
                      usage={activeCandidateUsage}
                    />
                  </div>
                </section>
              </>
            ) : null}

            <div className="grid min-h-0 flex-1 gap-4">
              <aside className="min-h-[800px] overflow-y-auto rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <div className="mb-4 flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-emerald-600" />
                  <h3 className="text-sm font-bold">Inspector</h3>
                </div>
                {selectedHistoryRow ? (
                  <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
                    <div className="font-bold">선택한 이전 실험</div>
                    <dl className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      <div>
                        <dt className="text-emerald-700">테스트명</dt>
                        <dd className="font-semibold">
                          {selectedHistoryRow.candidate.name ||
                            '이름 없는 후보'}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-emerald-700">모델</dt>
                        <dd className="font-semibold">
                          {selectedHistoryRow.candidate.model_id || '-'}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-emerald-700">비용/토큰/시간</dt>
                        <dd className="font-semibold">
                          {formatCandidateCost(
                            selectedHistoryRow.candidate.total_cost,
                          )}{' '}
                          ·{' '}
                          {typeof selectedHistoryRow.candidate.total_tokens ===
                          'number'
                            ? formatMetric(
                                selectedHistoryRow.candidate.total_tokens,
                              )
                            : '-'}{' '}
                          ·{' '}
                          {typeof selectedHistoryRow.candidate.latency_ms ===
                          'number'
                            ? formatLatency(
                                selectedHistoryRow.candidate.latency_ms,
                              )
                            : '-'}
                        </dd>
                      </div>
                      <div
                        className={`rounded-md border px-2 py-1 ${schemaStatusToneOf(
                          selectedHistoryRow.candidate.schema_status,
                        )}`}
                      >
                        <dt>Schema 판정</dt>
                        <dd className="font-semibold">
                          {schemaStatusLabelOf(
                            selectedHistoryRow.candidate.schema_status,
                          )}
                        </dd>
                      </div>
                      <div
                        className={`rounded-md border px-2 py-1 ${downstreamStateToneOf(
                          selectedHistoryRow.candidate.downstream_state,
                        )}`}
                      >
                        <dt>후속 노드 판정</dt>
                        <dd className="font-semibold">
                          {downstreamStateLabelOf(
                            selectedHistoryRow.candidate.downstream_state,
                          )}
                        </dd>
                      </div>
                    </dl>
                    <p className="mt-2 leading-relaxed text-emerald-800">
                      이전 실험 이력 API가 제공하는 summary 기준입니다. raw
                      prompt, raw trace, secret 값은 표시하지 않습니다.
                    </p>
                  </div>
                ) : null}
                <div className="mb-4 flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
                  {inspectorTabs.map((tab) => (
                    <button
                      key={tab.value}
                      type="button"
                      aria-pressed={activeInspectorTab === tab.value}
                      onClick={() => setActiveInspectorTab(tab.value)}
                      className={`rounded-md px-2 py-1.5 text-[11px] font-bold transition-colors ${
                        activeInspectorTab === tab.value
                          ? 'bg-white text-emerald-700 shadow-sm'
                          : 'text-slate-500 hover:text-slate-800'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="space-y-3">
                  {activeInspectorTab === 'trace' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        A usage trace
                      </div>
                      <dl className="mt-2 space-y-2 text-xs">
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">모델</dt>
                          <dd className="font-semibold">
                            {activeBaselineModel}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">비용</dt>
                          <dd className="font-semibold">
                            {baseline ? formatCost(baseline.cost) : '-'}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">토큰</dt>
                          <dd className="font-semibold">
                            {baseline
                              ? formatMetric(baseline.total_tokens)
                              : '-'}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">Trace</dt>
                          <dd className="font-semibold">
                            {baseline?.has_trace || baseline?.trace_available
                              ? '있음'
                              : '없음'}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : null}

                  {activeInspectorTab === 'trace' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        B 후보 실행 정보
                      </div>
                      {candidateResult ? (
                        <div className="mt-2 space-y-3">
                          <dl className="space-y-2 text-xs">
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">상태</dt>
                              <dd className="font-semibold">
                                {candidateResult.status}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">비용</dt>
                              <dd className="font-semibold">
                                {formatCandidateCost(candidateTotalCost)}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">토큰</dt>
                              <dd className="font-semibold">
                                {candidateTotalTokens === null
                                  ? '-'
                                  : formatMetric(candidateTotalTokens)}
                              </dd>
                            </div>
                          </dl>
                          <ModelRoutingDecisionDetails
                            output={candidateResult.output}
                            traceMetadata={candidateResult.trace}
                          />
                        </div>
                      ) : (
                        <p className="mt-2 text-xs leading-relaxed text-slate-500">
                          B 실행 후 trace가 표시됩니다.
                        </p>
                      )}
                    </div>
                  ) : null}

                  {activeInspectorTab === 'settings-diff' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        설정 차이
                      </div>
                      <dl className="mt-2 space-y-2 text-xs">
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            모델 차이
                          </dt>
                          <dd className="font-semibold">
                            <div>
                              설정 모델: {activeBaselineModel} →{' '}
                              {candidate.model_id || '-'}
                            </div>
                            {candidate.auto_model_routing ||
                            candidateModelRoutingSummary ? (
                              <div
                                className={
                                  hasCandidateRoutingModelDiff
                                    ? 'mt-1 text-emerald-700'
                                    : 'mt-1 text-slate-600'
                                }
                              >
                                실제 실행 모델: {activeBaselineModel} →{' '}
                                {activeCandidateActualModel}
                              </div>
                            ) : null}
                            {candidateModelRoutingSummary ? (
                              <div className="mt-1 text-[11px] font-medium text-slate-500">
                                규칙:{' '}
                                {candidateModelRoutingSummary.matchedRuleId ||
                                  '-'}{' '}
                                · 근거:{' '}
                                {candidateModelRoutingSummary.reasonCode || '-'}
                              </div>
                            ) : null}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            prompt 차이
                          </dt>
                          <dd className="whitespace-pre-wrap font-semibold">
                            {formatPromptDiffSummary(
                              baselineNodeOptions,
                              candidate,
                            )}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            parameter 차이
                          </dt>
                          <dd className="whitespace-pre-wrap font-semibold">
                            {formatParameterDiffSummary(
                              baselineNodeOptions,
                              candidate,
                            )}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            출력 형식 차이
                          </dt>
                          <dd className="font-semibold">
                            {formatOutputFormatValue(
                              baselineNodeOptions?.output_format,
                            )}{' '}
                            → {candidate.output_format.toUpperCase()}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            비용 차이
                          </dt>
                          <dd className="font-semibold">
                            {formatMetricDiff(
                              baselineCost,
                              candidateTotalCost,
                              formatCost,
                            )}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            토큰 차이
                          </dt>
                          <dd className="font-semibold">
                            {formatMetricDiff(
                              baselineTotalTokens,
                              candidateTotalTokens,
                              (value) => formatMetric(value),
                            )}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            latency 차이
                          </dt>
                          <dd className="font-semibold">
                            {formatMetricDiff(
                              activeBaselineLatency,
                              candidateLatency,
                              formatLatency,
                            )}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : null}

                  {activeInspectorTab === 'trace' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        근거/Trace
                      </div>
                      <dl className="mt-2 space-y-3 text-xs">
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">A 지식 베이스</dt>
                          <dd className="font-semibold">
                            {baselineNodeOptions?.knowledgeBases?.length ?? '-'}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="text-slate-500">B 지식 베이스</dt>
                          <dd className="font-semibold">
                            {candidate.knowledgeBases.length}
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            Schema 검증
                          </dt>
                          <dd className="font-semibold">
                            {activeSchemaStatusLabel}
                          </dd>
                          {selectedHistoryRow ? (
                            <p className="text-[11px] leading-relaxed text-slate-500">
                              이전 실험 이력 API는 schema summary만 제공합니다.
                              상세 오류는 방금 실행한 후보의 compare 응답에서
                              확인할 수 있습니다.
                            </p>
                          ) : candidateSchemaErrors.length > 0 ? (
                            <ul className="space-y-1 text-[11px] leading-relaxed text-red-700">
                              {candidateSchemaErrors.map((error) => (
                                <li key={error}>- {error}</li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            A retrieval summary
                          </dt>
                          <dd>
                            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2 text-[11px] text-slate-700">
                              {formatRetrievalSummary(baselineRetrievalSummary)}
                            </pre>
                          </dd>
                        </div>
                        <div className="grid gap-1">
                          <dt className="font-bold text-slate-500">
                            B retrieval summary
                          </dt>
                          <dd>
                            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2 text-[11px] text-slate-700">
                              {formatRetrievalSummary(
                                candidateRetrievalSummary,
                              )}
                            </pre>
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : null}

                  {activeInspectorTab === 'downstream' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        Downstream 상태
                      </div>
                      <div
                        className={`mt-2 rounded-md border px-3 py-2 text-xs ${activeDownstreamTone}`}
                      >
                        <div className="font-bold">
                          {activeDownstreamLabel}
                        </div>
                        <p className="mt-1 leading-relaxed">
                          {activeDownstreamMessage}
                        </p>
                      </div>
                      <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-600">
                        외부 전송이나 쓰기 작업이 있는 downstream 노드는 자동
                        실행하지 않습니다. Slack 전송, HTTP 요청, DB write는
                        전체 workflow 테스트에서 별도 확인하세요.
                      </p>
                      {!selectedHistoryRow && downstreamCheckedNodeIds.length > 0 ? (
                        <div className="mt-3">
                          <div className="text-[11px] font-bold text-slate-500">
                            검사 노드
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {downstreamCheckedNodeIds.map((checkedNodeId) => (
                              <span
                                key={checkedNodeId}
                                className="rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-semibold text-slate-700"
                              >
                                {checkedNodeId}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      {!selectedHistoryRow && downstreamWarnings.length > 0 ? (
                        <ul className="mt-3 space-y-1 text-[11px] leading-relaxed text-amber-700">
                          {downstreamWarnings.map((warning) => (
                            <li key={warning}>- {warning}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}

                  {activeInspectorTab === 'settings-diff' ? (
                    <div className="rounded-lg border border-slate-200 p-3">
                      <div className="text-xs font-bold text-slate-500">
                        Settings
                      </div>
                      <div className="mt-3 space-y-3 text-xs">
                        <section className="rounded-md bg-slate-50 p-3">
                          <div className="font-bold text-slate-600">
                            기본 설정
                          </div>
                          <dl className="mt-2 space-y-2">
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">출력 형식</dt>
                              <dd className="font-semibold">
                                {candidate.output_format.toUpperCase()}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">Task type</dt>
                              <dd className="font-semibold">
                                {candidate.task_type || '-'}
                              </dd>
                            </div>
                          </dl>
                        </section>

                        <section className="rounded-md bg-slate-50 p-3">
                          <div className="font-bold text-slate-600">
                            고급 설정
                          </div>
                          <dl className="mt-2 space-y-2">
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">max_tokens</dt>
                              <dd className="font-semibold">
                                {candidate.max_tokens}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">temperature</dt>
                              <dd className="font-semibold">
                                {candidate.temperature}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">top_p</dt>
                              <dd className="font-semibold">
                                {candidate.top_p}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">
                                presence_penalty
                              </dt>
                              <dd className="font-semibold">
                                {candidate.presence_penalty}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">
                                frequency_penalty
                              </dt>
                              <dd className="font-semibold">
                                {candidate.frequency_penalty}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">stop</dt>
                              <dd className="font-semibold">
                                {candidate.stop.join(', ') || '-'}
                              </dd>
                            </div>
                          </dl>
                        </section>

                        <section className="rounded-md bg-slate-50 p-3">
                          <div className="font-bold text-slate-600">
                            Knowledge/RAG 설정
                          </div>
                          <dl className="mt-2 space-y-2">
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">Knowledge Base</dt>
                              <dd className="font-semibold">
                                {candidate.knowledgeBases.length}개
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">topK</dt>
                              <dd className="font-semibold">
                                {candidate.topK}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">scoreThreshold</dt>
                              <dd className="font-semibold">
                                {candidate.scoreThreshold.toFixed(2)}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">중복 근거 제거</dt>
                              <dd className="font-semibold">
                                {candidate.dedupeRetrievedContext
                                  ? '켜짐'
                                  : '꺼짐'}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">
                                참조 문서 길이 제한
                              </dt>
                              <dd className="font-semibold">
                                {candidate.retrievedContextMaxChars === null
                                  ? '제한 없음'
                                  : `${candidate.retrievedContextMaxChars.toLocaleString(
                                      'ko-KR',
                                    )}자`}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">검색 문서 압축</dt>
                              <dd className="font-semibold">
                                {compressionLabelOf(
                                  candidate.retrievedContextCompression,
                                )}
                              </dd>
                            </div>
                            <div className="flex justify-between gap-3">
                              <dt className="text-slate-500">
                                답변·검색 문서 어휘 일치도
                              </dt>
                              <dd className="font-semibold">
                                {groundingLabelOf(
                                  candidate.answerGroundingCheck,
                                )}
                              </dd>
                            </div>
                          </dl>
                        </section>
                      </div>
                    </div>
                  ) : null}
                </div>
              </aside>
            </div>

          </div>
        )}
      </section>

      {isApplyDialogOpen && compareResult && candidateResult ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-label="후보 설정 적용 확인"
            className="w-full max-w-xl rounded-xl border border-slate-200 bg-white p-5 shadow-xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-bold text-slate-950">
                  후보 설정 적용 확인
                </h2>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  B 후보 설정 전체를 현재 LLM 노드 draft에 적용합니다.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsApplyDialogOpen(false)}
                className="rounded-md px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-100"
              >
                닫기
              </button>
            </div>

            <dl className="mt-4 grid gap-2 text-xs">
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">변경되는 모델</dt>
                <dd className="font-semibold text-slate-900">
                  {candidate.model_id || '-'}
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">변경되는 prompt</dt>
                <dd className="space-y-1 text-slate-700">
                  <div>system: {candidate.system_prompt || '-'}</div>
                  <div>user: {candidate.user_prompt || '-'}</div>
                  <div>assistant: {candidate.assistant_prompt || '-'}</div>
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">변경되는 parameter</dt>
                <dd className="whitespace-pre-wrap font-semibold text-slate-900">
                  {formatParameterDiffSummary(baselineNodeOptions, candidate)}
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">변경되는 출력 형식</dt>
                <dd className="font-semibold text-slate-900">
                  {candidate.output_format.toUpperCase()}
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">
                  변경되는 JSON schema
                </dt>
                <dd className="whitespace-pre-wrap font-semibold text-slate-900">
                  {formatJsonSchemaSummary(candidate)}
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">
                  변경되는 Knowledge/RAG 설정
                </dt>
                <dd className="whitespace-pre-wrap font-semibold text-slate-900">
                  {formatKnowledgeSummary(candidate)}
                </dd>
              </div>
              <div className="grid grid-cols-[140px_1fr] gap-3 rounded-md bg-slate-50 px-3 py-2">
                <dt className="font-bold text-slate-500">downstream 호환성</dt>
                <dd className="font-semibold text-slate-900">
                  {downstreamLabelOf(compareResult.downstream_compatibility)}
                </dd>
              </div>
            </dl>

            {compareResult.downstream_compatibility?.state === 'warning' ||
            compareResult.downstream_compatibility?.state === 'incompatible' ? (
              <p className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold leading-relaxed text-amber-800">
                검증 가능 상태가 아닙니다. 적용 후 현재 workflow 테스트 실행으로
                최종 확인해야 합니다.
              </p>
            ) : null}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={isApplyingCandidate}
                onClick={() => setIsApplyDialogOpen(false)}
                className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                취소
              </button>
              <button
                type="button"
                disabled={isApplyingCandidate}
                onClick={confirmApplyCandidate}
                className="rounded-md bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
              >
                {isApplyingCandidate ? '적용 중' : '적용 확인'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
