import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  processDocument,
  analyzeDocument,
  confirmDocumentParsing,
  toastError,
} = vi.hoisted(() => ({
  processDocument: vi.fn(),
  analyzeDocument: vi.fn(),
  confirmDocumentParsing: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    analyzeDocument,
    confirmDocumentParsing,
    previewDocumentChunking: vi.fn(),
    processDocument,
  },
}));

vi.mock('sonner', () => ({
  toast: {
    error: toastError,
    success: vi.fn(),
  },
}));

import { useDocumentProcess } from './useDocumentProcess';

const props = {
  kbId: 'kb-1',
  documentId: 'document-1',
  document: {
    id: 'document-1',
    filename: 'API source',
    status: 'pending' as const,
    created_at: '2026-07-13T00:00:00Z',
    updated_at: '2026-07-13T00:00:00Z',
    chunk_count: 0,
    token_count: 0,
    source_type: 'API' as const,
  },
  setStatus: vi.fn(),
  setProgress: vi.fn(),
  canEditDocument: true,
  editConfigReady: true,
  settings: {
    chunkSize: 800,
    chunkOverlap: 80,
    segmentIdentifier: '\\n\\n',
    removeUrlsEmails: false,
    removeWhitespace: true,
    parsingStrategy: 'general' as const,
    chunkingMode: 'flat' as const,
    selectedDbItems: {},
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  processDocument.mockResolvedValue({
    status: 'processing',
    message: 'processing',
  });
});

describe('useDocumentProcess edit configuration gate', () => {
  it('does not process when the write-scoped configuration was not hydrated', () => {
    const { result } = renderHook(() =>
      useDocumentProcess({ ...props, editConfigReady: false }),
    );

    act(() => result.current.handleSaveClick());

    expect(processDocument).not.toHaveBeenCalled();
    expect(analyzeDocument).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(
      '기존 문서 설정을 불러온 뒤 다시 시도해 주세요.',
    );
  });

  it('reprocesses a hydrated API source without sending raw source configuration', async () => {
    const { result } = renderHook(() => useDocumentProcess(props));

    act(() => result.current.handleSaveClick());

    await waitFor(() => expect(processDocument).toHaveBeenCalledOnce());
    expect(processDocument).toHaveBeenCalledWith(
      'kb-1',
      'document-1',
      expect.objectContaining({
        source_type: 'API',
        chunking_mode: 'flat',
        db_config: null,
      }),
    );
    const request = processDocument.mock.calls[0][2];
    expect(request).not.toHaveProperty('api_config');
  });

  it('does not enter indexing state when the processing request fails', async () => {
    processDocument.mockRejectedValueOnce(new Error('INTERNAL_SENTINEL'));
    const setStatus = vi.fn();
    const setProgress = vi.fn();
    const { result } = renderHook(() =>
      useDocumentProcess({ ...props, setStatus, setProgress }),
    );

    act(() => result.current.handleSaveClick());

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('저장에 실패했습니다.'),
    );
    expect(setStatus).not.toHaveBeenCalled();
    expect(setProgress).not.toHaveBeenCalled();
  });

  it('enters indexing only after waiting approval is confirmed successfully', async () => {
    const setStatus = vi.fn();
    const setProgress = vi.fn();
    const waitingDocument = {
      ...props.document,
      status: 'waiting_for_approval' as const,
    };
    confirmDocumentParsing.mockRejectedValueOnce(
      new Error('INTERNAL_SENTINEL'),
    );
    const { result } = renderHook(() =>
      useDocumentProcess({
        ...props,
        document: waitingDocument,
        setStatus,
        setProgress,
      }),
    );

    await act(async () => result.current.handleConfirmCost());

    expect(toastError).toHaveBeenCalledWith('처리 재개 실패');
    expect(setStatus).not.toHaveBeenCalled();
    expect(setProgress).not.toHaveBeenCalled();
  });

  it('discards a processing response after the organization scope changes', async () => {
    let resolveProcessing!: (value: { status: string; message: string }) => void;
    processDocument.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProcessing = resolve;
      }),
    );
    const setStatus = vi.fn();
    const setProgress = vi.fn();
    const { result, rerender } = renderHook(
      ({ requestScope }) =>
        useDocumentProcess({
          ...props,
          requestScope,
          setStatus,
          setProgress,
        }),
      { initialProps: { requestScope: 'org-1:kb-1:document-1' } },
    );

    act(() => result.current.handleSaveClick());
    await waitFor(() => expect(processDocument).toHaveBeenCalledOnce());
    rerender({ requestScope: 'org-2:kb-1:document-1' });
    await act(async () => {
      resolveProcessing({ status: 'processing', message: 'processing' });
    });

    expect(setStatus).not.toHaveBeenCalled();
    expect(setProgress).not.toHaveBeenCalled();
  });

  it('does not start processing when the external organization scope is already stale', () => {
    const { result } = renderHook(() =>
      useDocumentProcess({
        ...props,
        requestScope: 'org-1:kb-1:document-1',
        isRequestScopeCurrent: () => false,
      }),
    );

    act(() => result.current.handleSaveClick());

    expect(processDocument).not.toHaveBeenCalled();
    expect(analyzeDocument).not.toHaveBeenCalled();
  });
});
