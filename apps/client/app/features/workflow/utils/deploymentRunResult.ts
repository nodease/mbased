import type { DeploymentRunInfoResponse } from '../types/Deployment';
import {
  buildFinalResponsePreview,
  getFinalResponsePreview,
  type FinalResponsePreview,
} from './testExecutionFinalResponse';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const WORKFLOW_CITATION_RESULT_KEY = '__nodease_citations';
const MAX_WORKFLOW_CITATIONS = 8;
const FORBIDDEN_CITATION_KEYS = new Set([
  'knowledge_base_id',
  'collection_id',
  'document_id',
  'chunk_id',
  'filename',
  'path',
  'url',
  'score',
  'rank',
]);
const ALLOWED_CITATION_KEYS = new Set([
  'citation_id',
  'evidence_rank',
  'label',
  'page_number',
  'section',
  'content_preview',
]);

const isLegacyDisplayOutput = (key: string, value: unknown): value is string =>
  !key.startsWith('__nodease_') && typeof value === 'string' && value.trim().length > 0;

const hasControlCharacter = (value: string): boolean =>
  Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint <= 31 || codePoint === 127;
  });

export type WorkflowCitation = {
  citationId: string;
  evidenceRank: number;
  label: string;
  pageNumber?: number;
  section?: string;
  contentPreview?: string;
};

export type WorkflowCitationParseOptions = {
  knownNodeIds?: Iterable<string>;
};

const isSafeCitationText = (value: unknown, maxLength: number): value is string =>
  typeof value === 'string' &&
  value.trim().length > 0 &&
  value.length <= maxLength &&
  !hasControlCharacter(value) &&
  !/https?:\/\//iu.test(value) &&
  !/file:\/\//iu.test(value) &&
  !/(?:[a-z]:\\|\\\\|\/(?:home|users|var|tmp|etc)\/)/iu.test(value) &&
  !/(?:api[_-]?key|token|password|secret|authorization|credential)\s*[:=]/iu.test(
    value,
  );

const parseCitation = (value: unknown): WorkflowCitation | null => {
  if (!isRecord(value)) return null;
  if (
    Object.keys(value).some(
      (key) =>
        FORBIDDEN_CITATION_KEYS.has(key) || !ALLOWED_CITATION_KEYS.has(key),
    )
  ) {
    return null;
  }
  if (
    typeof value.citation_id !== 'string' ||
    !/^evidence-[1-9][0-9]*$/u.test(value.citation_id) ||
    !Number.isInteger(value.evidence_rank) ||
    Number(value.evidence_rank) < 1 ||
    value.citation_id !== `evidence-${Number(value.evidence_rank)}` ||
    !isSafeCitationText(value.label, 255)
  ) {
    return null;
  }
  if (
    value.page_number !== undefined &&
    value.page_number !== null &&
    (!Number.isInteger(value.page_number) || Number(value.page_number) < 1)
  ) {
    return null;
  }
  if (
    value.section !== undefined &&
    value.section !== null &&
    !isSafeCitationText(value.section, 200)
  ) {
    return null;
  }
  if (
    value.content_preview !== undefined &&
    value.content_preview !== null &&
    !isSafeCitationText(value.content_preview, 300)
  ) {
    return null;
  }

  return {
    citationId: value.citation_id,
    evidenceRank: Number(value.evidence_rank),
    label: value.label.trim(),
    ...(value.page_number === undefined || value.page_number === null
      ? {}
      : { pageNumber: Number(value.page_number) }),
    ...(typeof value.section === 'string'
      ? { section: value.section.trim() }
      : {}),
    ...(typeof value.content_preview === 'string'
      ? { contentPreview: value.content_preview.trim() }
      : {}),
  };
};

export const getDeploymentRunCitations = (
  runResponse: unknown,
  options: WorkflowCitationParseOptions = {},
): WorkflowCitation[] => {
  if (
    options.knownNodeIds !== undefined &&
    Array.from(options.knownNodeIds).includes(WORKFLOW_CITATION_RESULT_KEY)
  ) {
    return [];
  }
  const workflowResult =
    isRecord(runResponse) && 'results' in runResponse
      ? runResponse.results
      : runResponse;
  if (!isRecord(workflowResult)) return [];

  const envelope = workflowResult[WORKFLOW_CITATION_RESULT_KEY];
  if (
    !isRecord(envelope) ||
    envelope.version !== 1 ||
    !Array.isArray(envelope.items) ||
    envelope.items.length > MAX_WORKFLOW_CITATIONS
  ) {
    return [];
  }

  return envelope.items
    .map(parseCitation)
    .filter((item): item is WorkflowCitation => item !== null)
    .sort((left, right) => left.evidenceRank - right.evidenceRank);
};

export const getDeploymentRunFinalPreview = (
  deployment: Pick<DeploymentRunInfoResponse, 'output_schema'> | null,
  runResponse: unknown,
): FinalResponsePreview => {
  const workflowResult =
    isRecord(runResponse) && 'results' in runResponse
      ? runResponse.results
      : runResponse;

  if (isRecord(workflowResult)) {
    for (const output of deployment?.output_schema?.outputs || []) {
      if (
        workflowResult[output.variable] !== undefined &&
        workflowResult[output.variable] !== null
      ) {
        return buildFinalResponsePreview(
          workflowResult[output.variable],
          output.label || '워크플로우 최종 출력',
        );
      }
    }

    // Older deployments can have no output schema while still returning one
    // custom-named text output. Preserve that user-visible response path.
    if (!deployment?.output_schema?.outputs?.length) {
      const legacyOutput = Object.entries(workflowResult).find(([key, value]) =>
        isLegacyDisplayOutput(key, value),
      );
      if (legacyOutput) {
        return buildFinalResponsePreview(
          legacyOutput[1],
          '워크플로우 최종 출력',
        );
      }
    }
  }

  return getFinalResponsePreview({
    workflowResult,
    nodeResults: [],
    nodes: [],
  });
};
