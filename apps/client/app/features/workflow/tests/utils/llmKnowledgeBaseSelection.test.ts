import { afterEach, describe, expect, it, vi } from 'vitest';

import { knowledgeApi } from '@/app/features/knowledge/api/knowledgeApi';
import type { KnowledgeBaseDetailResponse } from '@/app/features/knowledge/types/Knowledge';
import {
  fetchEligibleKnowledgeBases,
  fetchEligibleKnowledgeCollections,
  sanitizeSelectedKnowledgeBases,
  sanitizeSelectedKnowledgeCollections,
} from '../../utils/llmKnowledgeBaseSelection';

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    getKnowledgeBases: vi.fn(),
    getLLMSelectableKnowledgeBases: vi.fn(),
    getLLMSelectableKnowledgeCollections: vi.fn(),
    getKnowledgeBase: vi.fn(),
  },
}));

const readyDetail = (
  id: string,
  name: string,
): KnowledgeBaseDetailResponse => ({
  id,
  name,
  description: `${name} 설명`,
  document_count: 1,
  created_at: '2026-07-01T00:00:00Z',
  embedding_model: 'text-embedding-3-small',
  documents: [
    {
      id: `${id}-doc`,
      filename: `${id}.md`,
      status: 'completed',
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
      chunk_count: 2,
      token_count: 40,
    },
  ],
});

describe('llmKnowledgeBaseSelection', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it('uses the permission-scoped LLM selectable API instead of owner-filtered legacy list/detail calls', async () => {
    vi.mocked(
      knowledgeApi.getLLMSelectableKnowledgeBases,
    ).mockResolvedValueOnce([
      readyDetail('kb-a', '사내 정책'),
      readyDetail('kb-b', '개발 규칙'),
    ]);

    const result = await fetchEligibleKnowledgeBases();

    expect(knowledgeApi.getLLMSelectableKnowledgeBases).toHaveBeenCalledTimes(
      1,
    );
    expect(knowledgeApi.getKnowledgeBases).not.toHaveBeenCalled();
    expect(knowledgeApi.getKnowledgeBase).not.toHaveBeenCalled();
    expect(result.bases.map((base) => base.id)).toEqual(['kb-a', 'kb-b']);
    expect(Object.keys(result.detailsById)).toEqual(['kb-a', 'kb-b']);
  });

  it('keeps multiple ready knowledge bases selectable and sanitizes selected names without collapsing to one item', async () => {
    const result = {
      bases: [
        readyDetail('kb-a', '사내 정책'),
        readyDetail('kb-b', '개발 규칙'),
      ],
      detailsById: {},
    };

    expect(
      sanitizeSelectedKnowledgeBases(
        [
          { id: 'kb-a', name: '이전 이름 A' },
          { id: 'kb-b', name: '이전 이름 B' },
        ],
        result.bases,
      ),
    ).toEqual([
      { id: 'kb-a', name: '사내 정책' },
      { id: 'kb-b', name: '개발 규칙' },
    ]);
  });

  it('defensively excludes malformed or not-ready selectable API rows', async () => {
    vi.mocked(
      knowledgeApi.getLLMSelectableKnowledgeBases,
    ).mockResolvedValueOnce([
      readyDetail('kb-ready', '완료 KB'),
      {
        ...readyDetail('kb-pending', '처리 전 KB'),
        documents: [
          {
            id: 'pending-doc',
            filename: 'pending.md',
            status: 'pending',
            created_at: '2026-07-01T00:00:00Z',
            updated_at: '2026-07-01T00:00:00Z',
            chunk_count: 0,
            token_count: 0,
          },
        ],
      },
      {
        ...readyDetail('kb-malformed', '잘못된 KB'),
        documents: 'not-an-array',
      } as never,
    ]);

    const result = await fetchEligibleKnowledgeBases();

    expect(result.bases.map((base) => base.id)).toEqual(['kb-ready']);
    expect(Object.keys(result.detailsById)).toEqual(['kb-ready']);
  });

  it('deduplicates selected knowledge bases, refreshes current names, and preserves missing rows', () => {
    expect(
      sanitizeSelectedKnowledgeBases(
        [
          { id: 'kb-ready', name: '오래된 이름' },
          { id: 'kb-ready', name: '중복 이름' },
          { id: 'kb-transient', name: '일시 장애 KB' },
          { id: 'kb-missing', name: '삭제된 KB' },
        ],
        [
          {
            id: 'kb-ready',
            name: '최신 이름',
            description: 'ready',
            document_count: 1,
            created_at: '2026-07-01T00:00:00Z',
            embedding_model: 'text-embedding-3-small',
          },
        ],
      ),
    ).toEqual([
      { id: 'kb-ready', name: '최신 이름' },
      { id: 'kb-transient', name: '일시 장애 KB' },
      { id: 'kb-missing', name: '삭제된 KB' },
    ]);
  });

  it('uses the route-safe Collection picker and ignores malformed or duplicate rows', async () => {
    const collectionId = '11111111-1111-1111-1111-111111111111';
    vi.mocked(
      knowledgeApi.getLLMSelectableKnowledgeCollections,
    ).mockResolvedValueOnce({
      collections: [
        { id: collectionId, safe_label: '사내 문서' },
        { id: collectionId, safe_label: '중복' },
        { id: 'NOT-A-UUID', safe_label: '잘못된 항목' },
        {
          id: '22222222-2222-2222-2222-222222222222',
          safe_label: '제어\u0000문자',
        },
        {
          id: '33333333-3333-3333-3333-333333333333',
          safe_label: null,
        },
      ],
    });

    const result = await fetchEligibleKnowledgeCollections();

    expect(
      knowledgeApi.getLLMSelectableKnowledgeCollections,
    ).toHaveBeenCalledTimes(1);
    expect(result).toEqual([
      { id: collectionId, safe_label: '사내 문서' },
      {
        id: '33333333-3333-3333-3333-333333333333',
        safe_label: null,
      },
    ]);
  });

  it('refreshes available Collection labels without deleting unavailable references', () => {
    expect(
      sanitizeSelectedKnowledgeCollections(
        [
          {
            id: '11111111-1111-1111-1111-111111111111',
            safeLabel: '이전 이름',
          },
          {
            id: '22222222-2222-2222-2222-222222222222',
            safeLabel: '노출하면 안 되는 저장 이름',
          },
          {
            id: '11111111-1111-1111-1111-111111111111',
            safeLabel: '중복',
          },
        ],
        [
          {
            id: '11111111-1111-1111-1111-111111111111',
            safe_label: '최신 안전 이름',
          },
        ],
      ),
    ).toEqual([
      {
        id: '11111111-1111-1111-1111-111111111111',
        safeLabel: '최신 안전 이름',
      },
      {
        id: '22222222-2222-2222-2222-222222222222',
        safeLabel: '노출하면 안 되는 저장 이름',
      },
    ]);
  });
});
