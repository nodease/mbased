import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiClientPostMock = vi.hoisted(() => vi.fn());
const activeOrganizationMock = vi.hoisted(() => ({
  ACTIVE_ORGANIZATION_CHANGED_EVENT: 'nodease-active-organization-changed',
  activeOrganizationHeaders: vi.fn(),
  getStoredActiveOrganizationId: vi.fn(),
}));

vi.mock('@/lib/apiClient', () => ({
  apiClient: { post: apiClientPostMock },
}));

vi.mock('@/lib/activeOrganization', () => activeOrganizationMock);

import KnowledgeSearchModal from './index';

const chatModel = {
  id: 'model-option-1',
  model_id_for_api_call: 'gpt-4o-mini',
  name: 'GPT-4o mini',
  type: 'chat',
  provider_name: 'openai',
};

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
};

describe('KnowledgeSearchModal', () => {
  beforeEach(() => {
    activeOrganizationMock.getStoredActiveOrganizationId.mockReturnValue(
      'org-1',
    );
    activeOrganizationMock.activeOrganizationHeaders.mockImplementation(
      (organizationId?: string | null) =>
        organizationId ? { 'X-Organization-Id': organizationId } : {},
    );
    apiClientPostMock.mockResolvedValue({
      data: { answer: '', references: [] },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => [chatModel] }),
    );
    vi.stubGlobal('alert', vi.fn());
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('sends the active organization header with a RAG chat request', async () => {
    render(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000334"
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'AI 답변 (RAG Chat)' }));
    await screen.findByRole('combobox');
    const textarea = screen.getByPlaceholderText(
      '지식 베이스에 대해 질문해보세요...',
    );
    fireEvent.change(textarea, { target: { value: '커밋 컨벤션' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(apiClientPostMock).toHaveBeenCalledWith(
        '/rag/search-test/chat',
        {
          query: '커밋 컨벤션',
          knowledge_base_id: '10200000-0000-0000-0000-000000000334',
          generation_model: 'gpt-4o-mini',
        },
        {
          headers: { 'X-Organization-Id': 'org-1' },
        },
      ),
    );
    expect(fetch).toHaveBeenCalledWith('/api/v1/llm/my-models', {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-Id': 'org-1',
      },
      credentials: 'include',
    });
  });

  it('discards an in-flight search result after the active organization changes', async () => {
    const searchRequest = deferred<{
      data: Array<{
        content: string;
        filename: string;
        similarity_score: number;
      }>;
    }>();
    apiClientPostMock.mockReturnValueOnce(searchRequest.promise);

    render(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000334"
        onClose={vi.fn()}
      />,
    );

    const textarea = screen.getByPlaceholderText('검색어를 입력하세요...');
    fireEvent.change(textarea, { target: { value: '기존 조직 자료' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() => expect(apiClientPostMock).toHaveBeenCalledOnce());

    activeOrganizationMock.getStoredActiveOrganizationId.mockReturnValue(
      'org-2',
    );
    act(() => {
      window.dispatchEvent(
        new Event(activeOrganizationMock.ACTIVE_ORGANIZATION_CHANGED_EVENT),
      );
    });
    await act(async () => {
      searchRequest.resolve({
        data: [
          {
            content: 'OLD_ORGANIZATION_RESULT',
            filename: 'old.pdf',
            similarity_score: 0.99,
          },
        ],
      });
      await searchRequest.promise;
    });

    expect(
      screen.queryByText('OLD_ORGANIZATION_RESULT'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('검색어를 입력하면 관련 문서 청크를 찾습니다.'),
    ).toBeVisible();
  });

  it('discards an in-flight search result after the knowledge base changes', async () => {
    const searchRequest = deferred<{
      data: Array<{
        content: string;
        filename: string;
        similarity_score: number;
      }>;
    }>();
    apiClientPostMock.mockReturnValueOnce(searchRequest.promise);

    const { rerender } = render(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000334"
        onClose={vi.fn()}
      />,
    );
    const textarea = screen.getByPlaceholderText('검색어를 입력하세요...');
    fireEvent.change(textarea, { target: { value: '이전 KB 자료' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() => expect(apiClientPostMock).toHaveBeenCalledOnce());

    rerender(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000335"
        onClose={vi.fn()}
      />,
    );
    await act(async () => {
      searchRequest.resolve({
        data: [
          {
            content: 'OLD_KNOWLEDGE_BASE_RESULT',
            filename: 'old.pdf',
            similarity_score: 0.99,
          },
        ],
      });
      await searchRequest.promise;
    });

    expect(
      screen.queryByText('OLD_KNOWLEDGE_BASE_RESULT'),
    ).not.toBeInTheDocument();
  });

  it('does not start a search with stale organization state', () => {
    vi.mocked(fetch).mockReturnValueOnce(new Promise<Response>(() => {}));
    render(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000334"
        onClose={vi.fn()}
      />,
    );
    const textarea = screen.getByPlaceholderText('검색어를 입력하세요...');
    fireEvent.change(textarea, { target: { value: '조직 전환 직후 검색' } });
    activeOrganizationMock.getStoredActiveOrganizationId.mockReturnValue(
      'org-2',
    );

    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(apiClientPostMock).not.toHaveBeenCalled();
  });

  it('does not submit chat without an available generation model', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    } as Response);

    render(
      <KnowledgeSearchModal
        isOpen
        knowledgeBaseId="10200000-0000-0000-0000-000000000334"
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'AI 답변 (RAG Chat)' }));
    await screen.findByRole('button', { name: '설정 필요' });
    const textarea = screen.getByPlaceholderText(
      '지식 베이스에 대해 질문해보세요...',
    );
    fireEvent.change(textarea, { target: { value: '질문' } });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: '검색 실행' })).toBeDisabled(),
    );
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(apiClientPostMock).not.toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith(
      'AI 답변에 사용할 모델을 먼저 설정해 주세요.',
    );
  });
});
