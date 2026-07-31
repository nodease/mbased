'use client';

import type { CostOptimizerPreviewFieldLabels } from './costOptimizerPreviewLabels';

type PreviewValue =
  | string
  | number
  | boolean
  | null
  | PreviewValue[]
  | { [key: string]: PreviewValue };

interface CostOptimizerPreviewViewerProps {
  value: unknown;
  emptyText?: string;
  className?: string;
  fieldLabels?: CostOptimizerPreviewFieldLabels;
}

interface CostOptimizerOutputPreviewPanelProps {
  title: string;
  value: unknown;
  usage?: unknown;
  emptyText?: string;
  fieldLabels?: CostOptimizerPreviewFieldLabels;
}

const keyLabelMap: Record<string, string> = {
  completion_tokens: '응답 토큰',
  cost: '비용',
  input_tokens: '입력 토큰',
  latency_ms: '실행 시간',
  output_tokens: '응답 토큰',
  prompt_tokens: '프롬프트 토큰',
  total_cost: '총 비용',
  total_tokens: '전체 토큰',
};

const formatPreviewKey = (
  key: string,
  fieldLabels?: CostOptimizerPreviewFieldLabels,
) => fieldLabels?.[key] || keyLabelMap[key] || key;
const metricKeys = new Set([
  'cost',
  'total_cost',
  'input_tokens',
  'output_tokens',
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'latency_ms',
]);
const usageKeys = new Set([
  'input_tokens',
  'output_tokens',
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'latency_ms',
]);

const stripJsonCodeFence = (value: string) => {
  const trimmed = value.trim();
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match ? match[1].trim() : value;
};

const parseJsonLikeString = (value: string): unknown => {
  const trimmed = stripJsonCodeFence(value).trim();
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
};

const isRecord = (value: PreviewValue): value is Record<string, PreviewValue> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isMetricKey = (key: string) => metricKeys.has(key);
const isUsageKey = (key: string) => usageKeys.has(key);

const mergeUsageValues = (
  output: PreviewValue,
  usage: unknown,
): Record<string, PreviewValue> => {
  const normalizedUsage = normalizeCostOptimizerPreview(usage);
  const merged: Record<string, PreviewValue> = {};

  if (isRecord(output)) {
    if ('usage' in output && isRecord(output.usage)) {
      Object.assign(merged, output.usage);
    }
    for (const [key, value] of Object.entries(output)) {
      if (isUsageKey(key)) {
        merged[key] = value;
      }
    }
  }

  if (isRecord(normalizedUsage)) {
    Object.assign(merged, normalizedUsage);
  }

  return merged;
};

const outputContentOf = (output: PreviewValue): PreviewValue => {
  if (!isRecord(output)) return output;

  if ('text' in output) {
    const text = output.text;
    if (typeof text === 'string') {
      return normalizeCostOptimizerPreview(text);
    }
    return text;
  }

  return Object.fromEntries(
    Object.entries(output).filter(
      ([key]) => key !== 'usage' && !isMetricKey(key),
    ),
  );
};

const rootMetricsOf = (output: PreviewValue) => {
  if (!isRecord(output)) return {};
  return Object.fromEntries(
    Object.entries(output).filter(([key]) => isMetricKey(key)),
  );
};

const formatMetricValue = (key: string, value: PreviewValue) => {
  if (value === null) return '-';
  if (typeof value === 'number') {
    if (key.includes('cost')) {
      return `$${value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;
    }
    if (key === 'latency_ms') return `${value}ms`;
    return value.toLocaleString('ko-KR');
  }
  return valueToCopyText(value);
};

const MetricGrid = ({
  title,
  values,
  tone = 'slate',
  labelPrefix = '',
}: {
  title: string;
  values: Record<string, PreviewValue>;
  tone?: 'blue' | 'slate';
  labelPrefix?: string;
}) => {
  const entries = Object.entries(values);
  if (entries.length === 0) return null;

  const itemClassName =
    tone === 'blue'
      ? 'rounded-md border border-blue-100 bg-blue-50 px-3 py-2'
      : 'rounded-md border border-slate-100 bg-slate-50 px-3 py-2';
  const labelClassName =
    tone === 'blue'
      ? 'text-[10px] font-bold uppercase tracking-wide text-blue-500'
      : 'text-[10px] font-bold uppercase tracking-wide text-slate-500';
  const valueClassName =
    tone === 'blue'
      ? 'mt-1 font-mono text-xs font-semibold text-blue-800'
      : 'mt-1 font-mono text-xs font-semibold text-slate-800';

  return (
    <section className="grid gap-2">
      <div className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
        {title}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {entries.map(([key, metricValue]) => (
          <div key={key} className={itemClassName}>
            <div className={labelClassName}>
              {labelPrefix
                ? `${labelPrefix} ${formatPreviewKey(key)}`
                : formatPreviewKey(key)}
            </div>
            <div className={valueClassName}>
              {formatMetricValue(key, metricValue)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

const normalizeCostOptimizerPreview = (
  value: unknown,
  depth = 0,
): PreviewValue => {
  if (depth > 8) return String(value);
  if (value === null || value === undefined) return null;
  if (
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = parseJsonLikeString(value);
    if (parsed !== value) {
      return normalizeCostOptimizerPreview(parsed, depth + 1);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeCostOptimizerPreview(item, depth + 1));
  }
  if (typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        normalizeCostOptimizerPreview(item, depth + 1),
      ]),
    );
  }

  return String(value);
};

const valueToCopyText = (value: PreviewValue): string => {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
};

const PreviewLeaf = ({ value }: { value: string | number | boolean | null }) => {
  if (value === null) {
    return <span className="text-slate-400">null</span>;
  }
  if (typeof value === 'boolean') {
    return (
      <span
        className={
          value ? 'font-semibold text-emerald-700' : 'font-semibold text-red-600'
        }
      >
        {String(value)}
      </span>
    );
  }
  if (typeof value === 'number') {
    return <span className="font-mono text-blue-700">{value}</span>;
  }

  return (
    <span className="whitespace-pre-wrap break-words text-slate-800">
      {value}
    </span>
  );
};

const PreviewNode = ({
  value,
  level = 0,
  fieldLabels,
}: {
  value: PreviewValue;
  level?: number;
  fieldLabels?: CostOptimizerPreviewFieldLabels;
}) => {
  if (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return <PreviewLeaf value={value} />;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-slate-400">[]</span>;
    }
    return (
      <span className="grid gap-2">
        {value.map((item, index) => (
          <span
            key={`${level}-${index}`}
            className="grid gap-1 rounded-md border border-slate-100 bg-white/70 px-2 py-1.5"
          >
            <span className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
              {index}
            </span>
            <PreviewNode
              value={item}
              level={level + 1}
              fieldLabels={fieldLabels}
            />
          </span>
        ))}
      </span>
    );
  }

  const entries = Object.entries(value);
  if (entries.length === 0) {
    return <span className="text-slate-400">{'{}'}</span>;
  }

  return (
    <span className="grid gap-2">
      {entries.map(([key, item]) => (
        <span
          key={`${level}-${key}`}
          className="grid gap-1 rounded-md border border-slate-100 bg-white/70 px-2 py-1.5"
        >
          <span className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
            {formatPreviewKey(key, fieldLabels)}
          </span>
          <PreviewNode
            value={item}
            level={level + 1}
            fieldLabels={fieldLabels}
          />
        </span>
      ))}
    </span>
  );
};

export function CostOptimizerPreviewViewer({
  value,
  emptyText = '-',
  className = '',
  fieldLabels,
}: CostOptimizerPreviewViewerProps) {
  const normalized = normalizeCostOptimizerPreview(value);
  const isEmpty =
    normalized === null ||
    (typeof normalized === 'string' && normalized.trim().length === 0);

  if (isEmpty) {
    return <span className={className}>{emptyText}</span>;
  }

  return (
    <span
      className={`block whitespace-normal break-words rounded-md bg-slate-50 p-3 text-xs leading-relaxed text-slate-800 ${className}`}
      title={valueToCopyText(normalized)}
    >
      <PreviewNode value={normalized} fieldLabels={fieldLabels} />
    </span>
  );
}

export function CostOptimizerOutputPreviewPanel({
  title,
  value,
  usage,
  emptyText = '출력 없음',
  fieldLabels,
}: CostOptimizerOutputPreviewPanelProps) {
  const normalized = normalizeCostOptimizerPreview(value);
  const isEmpty =
    normalized === null ||
    (typeof normalized === 'string' && normalized.trim().length === 0);
  const content = outputContentOf(normalized);
  const rootMetrics = rootMetricsOf(normalized);
  const usageMetrics = mergeUsageValues(normalized, usage);
  const usageLabelPrefix = title.startsWith('A ')
    ? 'A'
    : title.startsWith('B ')
      ? 'B'
      : '';

  return (
    <div className="min-h-0 rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-4 py-3">
        <h4 className="text-xs font-bold text-slate-700">{title}</h4>
      </div>
      <div className="grid max-h-96 gap-4 overflow-auto p-4">
        <MetricGrid title="Metric" values={rootMetrics} tone="blue" />

        <section className="grid gap-2">
          <div className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
            Output
          </div>
          {isEmpty ? (
            <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-500">
              {emptyText}
            </div>
          ) : (
            <CostOptimizerPreviewViewer
              value={content}
              emptyText={emptyText}
              className="border border-slate-100 bg-slate-50"
              fieldLabels={fieldLabels}
            />
          )}
        </section>

        <MetricGrid
          title="Usage"
          values={usageMetrics}
          labelPrefix={usageLabelPrefix}
        />
      </div>
    </div>
  );
}
