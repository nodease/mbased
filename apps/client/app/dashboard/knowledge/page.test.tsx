import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePage from './page';
import { knowledgeApi } from '@/app/features/knowledge/api/knowledgeApi';

const { routerPush } = vi.hoisted(() => ({
  routerPush: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    getKnowledgeBases: vi.fn(),
  },
}));

vi.mock('@/app/features/knowledge/components/create-knowledge-modal', () => ({
  default: () => null,
}));

const mockedKnowledgeApi = vi.mocked(knowledgeApi);

describe('KnowledgePage failure state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows a retryable load failure instead of an empty list', async () => {
    mockedKnowledgeApi.getKnowledgeBases
      .mockRejectedValueOnce({
        response: { status: 500, data: { detail: 'raw detail' } },
      })
      .mockResolvedValueOnce([
        {
          id: 'kb-1',
          name: '사내 문서',
          description: '온보딩 자료',
          document_count: 1,
          created_at: '2026-07-08T00:00:00Z',
          updated_at: '2026-07-08T00:00:00Z',
          source_types: ['FILE'],
          embedding_model: 'text-embedding-3-small',
        },
      ]);

    render(<KnowledgePage />);

    const title = screen.getByRole('heading', {
      level: 1,
      name: '지식 관리',
    });
    expect(title.firstElementChild).toHaveClass('lucide-book-open');

    expect(
      await screen.findByText('지식 베이스 목록을 불러오지 못했습니다.'),
    ).toBeVisible();
    expect(
      screen.queryByText('등록된 지식 베이스가 없습니다.'),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));

    expect(await screen.findByText('사내 문서')).toBeVisible();
    await waitFor(() => {
      expect(mockedKnowledgeApi.getKnowledgeBases).toHaveBeenCalledTimes(2);
    });
  });
});
