import type { Node } from '../types/Workflow';

type NodeResultLike = {
  nodeId: string;
  nodeType?: string;
  output?: unknown;
};

export type FinalResponsePreview =
  | {
      kind: 'text';
      text: string;
      isEmpty: boolean;
      sourceLabel: string;
    }
  | {
      kind: 'json';
      items: Array<{ label: string; value: string }>;
      text: string;
      isEmpty: boolean;
      sourceLabel: string;
    };

const TEXT_KEYS = [
  'text',
  'message',
  'answer',
  'content',
  'response',
  'reply',
  'output',
];
const WORKFLOW_OUTPUT_KEYS = ['output', 'response', 'answer', 'content', 'message'];
const POLICY_KEYS = ['policy_message', 'policyMessage', 'blocked_reason'];
const SENSITIVE_KEY_PATTERNS = [
  /credential/i,
  /secret/i,
  /token/i,
  /api[_-]?key/i,
  /password/i,
  /raw[_-]?trace/i,
  /trace[_-]?payload/i,
  /raw[_-]?payload/i,
  /source[_-]?(title|path|url)/i,
  /hidden[_-]?kb/i,
  /knowledge[_-]?base[_-]?id/i,
  /\bkb[_-]?id\b/i,
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isSensitiveKey = (key: string) =>
  SENSITIVE_KEY_PATTERNS.some((pattern) => pattern.test(key));

const isPrimitiveDisplayValue = (value: unknown) =>
  typeof value === 'string' ||
  typeof value === 'number' ||
  typeof value === 'boolean';

const toDisplayText = (value: unknown): string => {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (value === null || value === undefined) return '';
  return JSON.stringify(value, null, 2);
};

const pickFirstText = (
  source: Record<string, unknown>,
  keys: string[],
): unknown => {
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) {
      return source[key];
    }
  }
  return undefined;
};

const readWorkflowFinishOutput = (workflowResult: unknown): unknown => {
  if (!isRecord(workflowResult)) return workflowResult;
  return pickFirstText(workflowResult, WORKFLOW_OUTPUT_KEYS);
};

const isNodeExecutionContext = (value: unknown, nodes: Node[]) => {
  if (!isRecord(value)) return false;

  const keys = Object.keys(value);
  if (keys.length === 0) return false;

  const nodeIds = new Set(nodes.map((node) => node.id));
  return keys.every((key) => nodeIds.has(key));
};

const readNodeDisplayName = (node: Node | undefined, fallback: string) => {
  const data = node?.data as { title?: unknown; name?: unknown } | undefined;
  const title = typeof data?.title === 'string' ? data.title.trim() : '';
  if (title) return title;
  const name = typeof data?.name === 'string' ? data.name.trim() : '';
  return name || fallback;
};

const findNode = (nodes: Node[], nodeId: string) =>
  nodes.find((node) => node.id === nodeId);

const isAnswerLikeNode = (node: Node | undefined, result: NodeResultLike) => {
  const type = `${node?.type || result.nodeType || ''}`.toLowerCase();
  return type.includes('answer') || type.includes('response');
};

const isLlmLikeNode = (node: Node | undefined, result: NodeResultLike) => {
  const type = `${node?.type || result.nodeType || ''}`.toLowerCase();
  return type.includes('llm');
};

const readNodeOutputCandidate = (output: unknown) => {
  if (!isRecord(output)) return output;
  return pickFirstText(output, TEXT_KEYS) ?? output;
};

const findNodeCandidate = (
  nodes: Node[],
  nodeResults: NodeResultLike[],
  predicate: (node: Node | undefined, result: NodeResultLike) => boolean,
) => {
  for (let index = nodeResults.length - 1; index >= 0; index -= 1) {
    const result = nodeResults[index];
    const node = findNode(nodes, result.nodeId);
    if (!predicate(node, result)) continue;
    return {
      value: readNodeOutputCandidate(result.output),
      sourceLabel: readNodeDisplayName(node, result.nodeId),
    };
  }
  return undefined;
};

const summarizeValue = (value: unknown): string => {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value === null) return 'null';
  if (Array.isArray(value)) return `${value.length}개 항목`;
  if (isRecord(value)) return `${Object.keys(value).length}개 필드`;
  return '';
};

const buildJsonPreviewItems = (value: unknown) => {
  if (Array.isArray(value)) {
    return value
      .map((item, index) => ({
        label: `항목 ${index + 1}`,
        value: summarizeValue(item),
      }))
      .filter((item) => item.value.length > 0)
      .slice(0, 6);
  }

  if (!isRecord(value)) return [];

  return Object.entries(value)
    .filter(([key]) => !isSensitiveKey(key))
    .map(([key, item]) => ({
      label: key,
      value: summarizeValue(item),
    }))
    .filter((item) => item.value.length > 0)
    .slice(0, 6);
};

export const buildFinalResponsePreview = (
  value: unknown,
  sourceLabel: string,
): FinalResponsePreview => {
  if (isRecord(value) || Array.isArray(value)) {
    const policyValue = isRecord(value)
      ? pickFirstText(value, POLICY_KEYS)
      : undefined;
    const directValue = isRecord(value)
      ? pickFirstText(value, TEXT_KEYS)
      : undefined;
    const policyText = isPrimitiveDisplayValue(policyValue)
      ? toDisplayText(policyValue)
      : '';
    const directText = isPrimitiveDisplayValue(directValue)
      ? toDisplayText(directValue)
      : '';
    if (policyText || directText) {
      return {
        kind: 'text',
        text: policyText || directText,
        isEmpty: !(policyText || directText),
        sourceLabel,
      };
    }

    const jsonValue =
      directValue && (isRecord(directValue) || Array.isArray(directValue))
        ? directValue
        : value;
    const items = buildJsonPreviewItems(jsonValue);
    return {
      kind: 'json',
      items,
      text: items.map((item) => `${item.label}: ${item.value}`).join('\n'),
      isEmpty: items.length === 0,
      sourceLabel,
    };
  }

  const text = toDisplayText(value);
  return {
    kind: 'text',
    text,
    isEmpty: text.length === 0,
    sourceLabel,
  };
};

export const getFinalResponsePreview = ({
  workflowResult,
  nodeResults,
  nodes,
}: {
  workflowResult: unknown;
  nodeResults: NodeResultLike[];
  nodes: Node[];
}): FinalResponsePreview => {
  const workflowOutput = readWorkflowFinishOutput(workflowResult);
  if (
    workflowOutput !== undefined &&
    workflowOutput !== null &&
    !isNodeExecutionContext(workflowOutput, nodes)
  ) {
    return buildFinalResponsePreview(workflowOutput, '워크플로우 최종 출력');
  }

  const answerCandidate = findNodeCandidate(nodes, nodeResults, isAnswerLikeNode);
  if (answerCandidate) {
    return buildFinalResponsePreview(answerCandidate.value, answerCandidate.sourceLabel);
  }

  const llmCandidate = findNodeCandidate(nodes, nodeResults, isLlmLikeNode);
  if (llmCandidate) {
    return buildFinalResponsePreview(llmCandidate.value, llmCandidate.sourceLabel);
  }

  return buildFinalResponsePreview('', '실행 결과');
};

export const shouldShowFinalResponseCard = ({
  hasExecutionResult,
  error,
}: {
  hasExecutionResult: boolean;
  error?: string | null;
}) => hasExecutionResult && !error;
