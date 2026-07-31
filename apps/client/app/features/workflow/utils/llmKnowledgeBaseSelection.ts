import {
  knowledgeApi,
  KnowledgeBaseDetailResponse,
  KnowledgeBaseResponse,
  KnowledgeCollectionLLMSelectableItem,
} from '@/app/features/knowledge/api/knowledgeApi';
import type {
  KnowledgeBaseNodeReference,
  KnowledgeCollectionNodeReference,
} from '@/app/features/workflow/types/Nodes';

/**
 * Workflow Builder의 LLM Knowledge picker 전용 유틸입니다.
 * 관리 화면의 Collection projection과 runtime resolver 결과는 사용하지 않습니다.
 */
export const MAX_CONFIGURED_KNOWLEDGE_REFERENCES = 20;

type EligibleKnowledgeBasesResult = {
  bases: KnowledgeBaseResponse[];
  detailsById: Record<string, KnowledgeBaseDetailResponse>;
};

const CANONICAL_UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const isSafeDisplay = (value: string) =>
  value.length <= 255 &&
  !Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127;
  });

const getCompletedCount = (detail: KnowledgeBaseDetailResponse) => {
  const documents = Array.isArray(detail.documents) ? detail.documents : [];
  return documents.filter(
    (doc) => doc.status === 'completed' && (doc.chunk_count || 0) > 0,
  ).length;
};

/** Gateway가 권한과 retrieval readiness를 적용한 direct KB 후보를 가져옵니다. */
export const fetchEligibleKnowledgeBases =
  async (): Promise<EligibleKnowledgeBasesResult> => {
    const details = await knowledgeApi.getLLMSelectableKnowledgeBases();
    const detailsById: Record<string, KnowledgeBaseDetailResponse> = {};
    const eligibleBases: KnowledgeBaseResponse[] = [];

    if (!Array.isArray(details)) return { bases: [], detailsById };

    details.forEach((detail) => {
      if (
        !detail ||
        typeof detail !== 'object' ||
        typeof detail.id !== 'string' ||
        typeof detail.name !== 'string'
      ) {
        return;
      }
      const completedCount = getCompletedCount(detail);
      if (completedCount > 0 && !detailsById[detail.id]) {
        eligibleBases.push(detail);
        detailsById[detail.id] = detail;
      }
    });

    return { bases: eligibleBases, detailsById };
  };

const isSelectableCollection = (
  value: unknown,
): value is KnowledgeCollectionLLMSelectableItem => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  if (!CANONICAL_UUID_PATTERN.test(String(item.id ?? ''))) return false;
  if (item.safe_label !== undefined && item.safe_label !== null) {
    if (
      typeof item.safe_label !== 'string' ||
      !isSafeDisplay(item.safe_label)
    ) {
      return false;
    }
  }
  return Object.keys(item).every((key) => key === 'id' || key === 'safe_label');
};

/** Gateway의 route-safe 최소 projection만 수용하고 malformed row는 무시합니다. */
export const fetchEligibleKnowledgeCollections = async () => {
  const response = await knowledgeApi.getLLMSelectableKnowledgeCollections();
  const rawCollections = Array.isArray(response?.collections)
    ? response.collections
    : [];
  const seen = new Set<string>();
  return rawCollections.filter((item) => {
    if (!isSelectableCollection(item) || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
};

/**
 * 저장 참조를 자동 삭제하지 않고, 현재 후보에 있는 display snapshot만 갱신합니다.
 * 중복은 첫 항목을 유지합니다.
 */
export const sanitizeSelectedKnowledgeBases = (
  selected: KnowledgeBaseNodeReference[],
  eligibleBases: KnowledgeBaseResponse[],
) => {
  const nameById = new Map(eligibleBases.map((kb) => [kb.id, kb.name]));
  const seen = new Set<string>();
  return selected.flatMap((kb) => {
    if (seen.has(kb.id)) return [];
    seen.add(kb.id);
    return [{ id: kb.id, name: nameById.get(kb.id) ?? kb.name }];
  });
};

export const sanitizeSelectedKnowledgeCollections = (
  selected: KnowledgeCollectionNodeReference[],
  eligibleCollections: KnowledgeCollectionLLMSelectableItem[],
) => {
  const labelById = new Map(
    eligibleCollections.map((collection) => [
      collection.id,
      collection.safe_label ?? undefined,
    ]),
  );
  const availableIds = new Set(
    eligibleCollections.map((collection) => collection.id),
  );
  const seen = new Set<string>();
  return selected.flatMap((collection) => {
    if (seen.has(collection.id)) return [];
    seen.add(collection.id);
    if (!availableIds.has(collection.id)) return [collection];
    const safeLabel = labelById.get(collection.id);
    return [
      safeLabel === undefined
        ? { id: collection.id }
        : { id: collection.id, safeLabel },
    ];
  });
};

export const isSameKnowledgeSelection = (
  a: KnowledgeBaseNodeReference[],
  b: KnowledgeBaseNodeReference[],
) =>
  a.length === b.length &&
  a.every(
    (item, index) => item.id === b[index]?.id && item.name === b[index]?.name,
  );

export const isSameKnowledgeCollectionSelection = (
  a: KnowledgeCollectionNodeReference[],
  b: KnowledgeCollectionNodeReference[],
) =>
  a.length === b.length &&
  a.every(
    (item, index) =>
      item.id === b[index]?.id && item.safeLabel === b[index]?.safeLabel,
  );
