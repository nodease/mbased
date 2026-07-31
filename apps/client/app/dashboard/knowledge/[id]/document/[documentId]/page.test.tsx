import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const push = vi.fn();
  return {
    params: { id: 'kb-1', documentId: 'document-1' } as Record<string, string>,
    push,
    router: { push },
    getKnowledgeBase: vi.fn(),
    getDocument: vi.fn(),
    getDocumentEditConfig: vi.fn(),
    toastError: vi.fn(),
    activeOrganizationId: 'org-1' as string | null,
  };
});

vi.mock('next/navigation', () => ({
  useParams: () => mocks.params,
  useRouter: () => mocks.router,
}));

vi.mock('next/link', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('sonner', () => ({
  toast: {
    error: mocks.toastError,
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    getKnowledgeBase: mocks.getKnowledgeBase,
    getDocument: mocks.getDocument,
    getDocumentEditConfig: mocks.getDocumentEditConfig,
    getProgressUrl: vi.fn(),
  },
}));

vi.mock('@/app/features/knowledge/hooks/useDocumentProcess', () => ({
  useDocumentProcess: vi.fn(),
}));

vi.mock('@/lib/activeOrganization', () => ({
  ACTIVE_ORGANIZATION_CHANGED_EVENT: 'nodease-active-organization-changed',
  getStoredActiveOrganizationId: () => mocks.activeOrganizationId,
}));

vi.mock('@/app/features/knowledge/hooks/useGenericCredential', () => ({
  useGenericCredential: () => ({ hasKey: true, isLoading: false }),
}));

vi.mock('@/app/features/knowledge/api/connectorApi', () => ({
  connectorApi: {},
}));

vi.mock(
  '@/app/features/knowledge/components/ingestion-views/FileSourceViewer',
  () => ({
    default: ({ filename }: { filename?: string | null }) => (
      <div data-testid="file-source" data-filename={filename ?? ''}>
        file source
      </div>
    ),
  }),
);
vi.mock(
  '@/app/features/knowledge/components/ingestion-views/ApiSourceViewer',
  () => ({ default: () => <div>api source</div> }),
);
vi.mock(
  '@/app/features/knowledge/components/ingestion-views/DbSourceViewer',
  () => ({ default: () => <div>db source</div> }),
);
vi.mock(
  '@/app/features/knowledge/components/document-settings/CommonChunkSettings',
  () => ({
    default: ({ chunkSize }: { chunkSize: number }) => (
      <div data-testid="chunk-size">{chunkSize}</div>
    ),
  }),
);
vi.mock(
  '@/app/features/knowledge/components/document-settings/ParsingStrategySettings',
  () => ({ default: () => <div>parsing settings</div> }),
);
vi.mock(
  '@/app/features/knowledge/components/preview/ChunkPreviewList',
  () => ({ default: () => <div>chunk preview</div> }),
);
vi.mock(
  '@/app/features/knowledge/components/create-knowledge-modal/DBConnectionForm',
  () => ({ default: () => <div>connection form</div> }),
);
vi.mock(
  '@/app/features/knowledge/components/document-settings/ColumnAutocomplete',
  () => ({ default: () => <div>column autocomplete</div> }),
);

import DocumentSettingsPage from './page';
import { useDocumentProcess } from '@/app/features/knowledge/hooks/useDocumentProcess';

const mockedUseDocumentProcess = vi.mocked(useDocumentProcess);
type DocumentProcessResult = ReturnType<typeof useDocumentProcess>;

const createDocumentProcessResult = (
  handleSaveClick: DocumentProcessResult['handleSaveClick'] = vi.fn(),
): DocumentProcessResult => ({
  isAnalyzing: false,
  analyzingAction: null,
  isPreviewLoading: false,
  showCostConfirm: false,
  setShowCostConfirm: vi.fn(),
  analyzeResult: null,
  setAnalyzeResult: vi.fn(),
  setPendingAction: vi.fn(),
  previewSegments: [],
  setPreviewSegments: vi.fn(),
  handleSaveClick,
  handlePreviewClick: vi.fn(),
  handleConfirmCost: vi.fn(),
});

const knowledgeBase = {
  id: 'kb-1',
  name: 'Knowledge Base',
  can_write: true,
};

const documentResponse = (id: string) => ({
  id,
  filename: `${id}.pdf`,
  status: 'pending',
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
  chunk_count: 0,
  token_count: 0,
  source_type: 'FILE',
  meta_info: {},
});

const editConfig = (chunkSize: number) => ({
  editable: true,
  source_type: 'FILE',
  chunk_size: chunkSize,
  chunk_overlap: 20,
  segment_identifier: '\\n\\n',
  remove_urls_emails: false,
  remove_whitespace: true,
  strategy: 'general',
  chunking_mode: 'flat',
  selection_mode: 'all',
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
};

class FakeEventSource {
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  static reset() {
    FakeEventSource.instances = [];
  }
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.params = { id: 'kb-1', documentId: 'document-1' };
  mocks.activeOrganizationId = 'org-1';
  mocks.getKnowledgeBase.mockResolvedValue(knowledgeBase);
  mocks.getDocumentEditConfig.mockResolvedValue(editConfig(1000));
  mockedUseDocumentProcess.mockReturnValue(createDocumentProcessResult());
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('DocumentSettingsPage request scoping', () => {
  it('keeps an already completed document open for preview', async () => {
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    mocks.getDocument.mockResolvedValue({
      ...documentResponse('document-1'),
      status: 'completed',
    });
    mocks.getDocumentEditConfig.mockResolvedValue(editConfig(1000));

    render(<DocumentSettingsPage />);

    await waitFor(() => {
      expect(screen.getByTestId('file-source')).toHaveAttribute(
        'data-filename',
        'document-1.pdf',
      );
    });
    expect(
      timeoutSpy.mock.calls.some(([, delay]) => delay === 3000),
    ).toBe(false);
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('schedules the existing redirect after observed processing completes', async () => {
    FakeEventSource.reset();
    vi.stubGlobal('EventSource', FakeEventSource);
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    mocks.getDocument.mockResolvedValue({
      ...documentResponse('document-1'),
      status: 'processing',
      meta_info: { progress: 50 },
    });
    mocks.getDocumentEditConfig.mockResolvedValue(editConfig(1000));

    render(<DocumentSettingsPage />);

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
    const [eventSource] = FakeEventSource.instances;
    act(() => {
      eventSource.onmessage?.({
        data: JSON.stringify({ status: 'completed', progress: 100 }),
      });
    });

    await waitFor(() => {
      expect(
        timeoutSpy.mock.calls.some(([, delay]) => delay === 3000),
      ).toBe(true);
    });
  });

  it('ignores a late response from the previously selected document', async () => {
    const firstDocument = deferred<ReturnType<typeof documentResponse>>();
    mocks.getDocument.mockImplementation(
      (_kbId: string, documentId: string) =>
        documentId === 'document-1'
          ? firstDocument.promise
          : Promise.resolve(documentResponse('document-2')),
    );
    mocks.getDocumentEditConfig.mockImplementation(
      (_kbId: string, documentId: string) =>
        Promise.resolve(editConfig(documentId === 'document-2' ? 2222 : 1111)),
    );

    const { rerender } = render(<DocumentSettingsPage />);
    mocks.params = { id: 'kb-1', documentId: 'document-2' };
    rerender(<DocumentSettingsPage />);

    await waitFor(() => {
      expect(screen.getByTestId('chunk-size')).toHaveTextContent('2222');
      expect(screen.getByTestId('file-source')).toHaveAttribute(
        'data-filename',
        'document-2.pdf',
      );
      expect(
        screen.getByRole('button', { name: '처리 시작' }),
      ).toBeEnabled();
    });

    await act(async () => {
      firstDocument.resolve(documentResponse('document-1'));
      await firstDocument.promise;
    });

    expect(screen.getByTestId('chunk-size')).toHaveTextContent('2222');
    expect(screen.getByTestId('file-source')).toHaveAttribute(
      'data-filename',
      'document-2.pdf',
    );
    expect(mocks.getDocumentEditConfig).not.toHaveBeenCalledWith(
      'kb-1',
      'document-1',
    );
  });

  it('ignores a late response from the previously active organization', async () => {
    const firstOrganizationDocument = deferred<
      ReturnType<typeof documentResponse>
    >();
    let documentReads = 0;
    mocks.getDocument.mockImplementation(() => {
      documentReads += 1;
      if (documentReads === 1) return firstOrganizationDocument.promise;
      return Promise.resolve({
        ...documentResponse('document-1'),
        filename: 'organization-2.pdf',
      });
    });
    mocks.getDocumentEditConfig.mockResolvedValue(editConfig(2222));

    render(<DocumentSettingsPage />);
    await waitFor(() => expect(mocks.getDocument).toHaveBeenCalledOnce());

    mocks.activeOrganizationId = 'org-2';
    act(() => {
      window.dispatchEvent(new Event('nodease-active-organization-changed'));
    });
    await waitFor(() => {
      expect(screen.getByTestId('file-source')).toHaveAttribute(
        'data-filename',
        'organization-2.pdf',
      );
      expect(screen.getByTestId('chunk-size')).toHaveTextContent('2222');
    });

    await act(async () => {
      firstOrganizationDocument.resolve({
        ...documentResponse('document-1'),
        filename: 'organization-1-private.pdf',
      });
      await firstOrganizationDocument.promise;
    });

    expect(screen.getByTestId('file-source')).toHaveAttribute(
      'data-filename',
      'organization-2.pdf',
    );
    expect(
      screen.queryByRole('heading', { name: 'organization-1-private.pdf' }),
    ).not.toBeInTheDocument();
  });

  it('resets the edit gate when the next document request fails', async () => {
    mocks.getDocument.mockImplementation(
      (_kbId: string, documentId: string) =>
        documentId === 'document-1'
          ? Promise.resolve(documentResponse(documentId))
          : Promise.reject(new Error('request failed')),
    );
    mocks.getDocumentEditConfig.mockResolvedValue(editConfig(1111));

    const { rerender } = render(<DocumentSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId('chunk-size')).toHaveTextContent('1111');
      expect(
        screen.getByRole('button', { name: '처리 시작' }),
      ).toBeEnabled();
    });

    mocks.params = { id: 'kb-1', documentId: 'document-2' };
    rerender(<DocumentSettingsPage />);

    await waitFor(() => {
      expect(mocks.toastError).toHaveBeenCalledWith(
        '문서 정보를 불러오는데 실패했습니다.',
      );
      expect(
        screen.getByRole('button', { name: '처리 시작' }),
      ).toBeDisabled();
    });
    expect(screen.getByTestId('chunk-size')).toHaveTextContent('1000');
  });

  it('ignores a progress event from the document left behind after navigation', async () => {
    FakeEventSource.reset();
    vi.stubGlobal('EventSource', FakeEventSource);
    mocks.getDocument.mockImplementation(
      (_kbId: string, documentId: string) =>
        Promise.resolve({
          ...documentResponse(documentId),
          status: documentId === 'document-1' ? 'processing' : 'pending',
        }),
    );

    const { rerender } = render(<DocumentSettingsPage />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const [oldEventSource] = FakeEventSource.instances;

    mocks.params = { id: 'kb-1', documentId: 'document-2' };
    rerender(<DocumentSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId('file-source')).toHaveAttribute(
        'data-filename',
        'document-2.pdf',
      );
      expect(screen.getByRole('button', { name: '처리 시작' })).toBeEnabled();
    });

    act(() => {
      oldEventSource.onmessage?.({
        data: JSON.stringify({ status: 'completed', progress: 100 }),
      });
    });

    expect(screen.getByRole('button', { name: '처리 시작' })).toBeEnabled();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('ignores a late polling response from the document left behind', async () => {
    vi.useFakeTimers();
    const oldPollingRequest = deferred<ReturnType<typeof documentResponse>>();
    let firstDocumentReads = 0;
    mocks.getDocument.mockImplementation(
      (_kbId: string, documentId: string) => {
        if (documentId === 'document-2') {
          return Promise.resolve(documentResponse(documentId));
        }
        firstDocumentReads += 1;
        if (firstDocumentReads === 1) {
          return Promise.resolve({
            ...documentResponse(documentId),
            status: 'waiting_for_approval',
          });
        }
        return oldPollingRequest.promise;
      },
    );

    const { rerender } = render(<DocumentSettingsPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('file-source')).toHaveAttribute(
      'data-filename',
      'document-1.pdf',
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(firstDocumentReads).toBe(2);

    mocks.params = { id: 'kb-1', documentId: 'document-2' };
    rerender(<DocumentSettingsPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => {
      oldPollingRequest.resolve({
        ...documentResponse('document-1'),
        status: 'completed',
      });
      await oldPollingRequest.promise;
    });

    expect(screen.getByTestId('file-source')).toHaveAttribute(
      'data-filename',
      'document-2.pdf',
    );
    expect(screen.getByRole('button', { name: '처리 시작' })).toBeEnabled();
    expect(mocks.push).not.toHaveBeenCalled();
  });
});


describe('DocumentSettingsPage completion redirect', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.reset();
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  it('keeps an already-completed document settings page open', async () => {
    mocks.getDocument.mockResolvedValue({
      ...documentResponse('doc-1'),
      filename: 'guide.md',
      status: 'completed',
    });

    await act(async () => {
      render(<DocumentSettingsPage />);
    });

    expect(screen.getByRole('heading', { name: 'guide.md' })).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('returns to the knowledge base after this page starts processing and it completes', async () => {
    let processProps: Parameters<typeof useDocumentProcess>[0] | undefined;
    const handleSaveClick = vi.fn();
    mocks.getDocument.mockResolvedValue({
      ...documentResponse('doc-1'),
      filename: 'guide.md',
    });
    mockedUseDocumentProcess.mockImplementation((props) => {
      processProps = props;
      return createDocumentProcessResult(handleSaveClick);
    });

    await act(async () => {
      render(<DocumentSettingsPage />);
    });

    expect(screen.getByRole('heading', { name: 'guide.md' })).toBeVisible();
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '처리 시작' }));
    });
    expect(handleSaveClick).toHaveBeenCalledOnce();

    act(() => {
      processProps?.setStatus('indexing');
    });
    expect(screen.getByRole('button', { name: '처리 중...' })).toBeDisabled();
    act(() => {
      processProps?.setProgress(100);
      processProps?.setStatus('completed');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(mocks.push).toHaveBeenCalledWith('/dashboard/knowledge/kb-1');
  });

  it('does not redirect when a processing attempt never enters an active state', async () => {
    mocks.getDocument.mockResolvedValue({
      ...documentResponse('doc-1'),
      filename: 'guide.md',
    });
    mockedUseDocumentProcess.mockImplementation((props) =>
      createDocumentProcessResult(() => {
        props.setStatus('completed');
        props.setProgress(100);
      }),
    );

    await act(async () => {
      render(<DocumentSettingsPage />);
    });
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '처리 시작' }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(mocks.push).not.toHaveBeenCalled();
  });
});
