import type { CostOptimizerDownstreamCompatibility } from '../../types/Api';

export const formatMetric = (value: number, suffix = '') => {
  if (!Number.isFinite(value)) return '-';
  return `${value.toLocaleString('ko-KR')}${suffix}`;
};

export const formatCost = (value: number) => {
  if (!Number.isFinite(value)) return '-';
  return `$${value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;
};

export const formatCandidateCost = (value: number | null | undefined) =>
  value === null || value === undefined ? '비용 계산 불가' : formatCost(value);

export const formatShortId = (value: string | null | undefined) =>
  value ? value.slice(0, 8) : '-';

export const formatLatency = (latencyMs: number) => {
  if (!Number.isFinite(latencyMs)) return '-';
  if (latencyMs < 1000) return `${latencyMs}ms`;
  return `${(latencyMs / 1000).toFixed(1)}s`;
};

export const formatContextDateTime = (value: string | null | undefined) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
};

export const downstreamLabelOf = (
  compatibility: CostOptimizerDownstreamCompatibility | null | undefined,
) => {
  if (!compatibility) return '판정 전';
  if (compatibility.label) return compatibility.label;
  return downstreamStateLabelOf(compatibility.state);
};

export const downstreamStateLabelOf = (state: string | null | undefined) => {
  if (state === 'compatible') return '검증 가능';
  if (state === 'warning') return '주의 필요';
  if (state === 'incompatible') return '검증 불가';
  if (state === 'unknown') return '판정 전';
  return state || '-';
};

export const schemaStatusLabelOf = (status: string | null | undefined) => {
  if (status === 'schema_failed' || status === 'failed') return '실패';
  if (status === 'valid' || status === 'pass') return '통과';
  if (status === 'skipped' || status === 'not_checked') return '검증 안 함';
  return '검증 안 함';
};
