import { act, render, screen, waitFor } from '@testing-library/react';
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

const getDocumentContentMock = vi.hoisted(() => vi.fn());

vi.mock('../../api/knowledgeApi', () => ({
  knowledgeApi: {
    getDocumentContent: getDocumentContentMock,
  },
}));

import FileSourceViewer from './FileSourceViewer';

const CONTENT_URL = 'blob:http://localhost/document-content';
const createObjectURLMock = vi.fn(() => CONTENT_URL);
const revokeObjectURLMock = vi.fn();
const originalCreateObjectURL = Object.getOwnPropertyDescriptor(
  URL,
  'createObjectURL',
);
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(
  URL,
  'revokeObjectURL',
);

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: createObjectURLMock,
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: revokeObjectURLMock,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

afterAll(() => {
  if (originalCreateObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', originalCreateObjectURL);
  } else {
    Reflect.deleteProperty(URL, 'createObjectURL');
  }
  if (originalRevokeObjectURL) {
    Object.defineProperty(URL, 'revokeObjectURL', originalRevokeObjectURL);
  } else {
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  }
});

describe('FileSourceViewer', () => {
  it('renders a loading state until both resource identifiers are available', () => {
    const { container } = render(
      <FileSourceViewer
        kbId=""
        documentId="document-1"
        filename="policy.pdf"
      />,
    );

    expect(screen.getByText('문서를 불러오는 중입니다...')).toBeInTheDocument();
    expect(container.querySelector('object')).not.toBeInTheDocument();
    expect(container.querySelector('iframe')).not.toBeInTheDocument();
    expect(getDocumentContentMock).not.toHaveBeenCalled();
  });

  it('does not request content when the document filename is unavailable', () => {
    const { container } = render(
      <FileSourceViewer kbId="kb-1" documentId="document-1" />,
    );

    expect(
      screen.getByText('원본 문서 정보를 확인할 수 없습니다.'),
    ).toBeInTheDocument();
    expect(container.querySelector('object')).not.toBeInTheDocument();
    expect(container.querySelector('iframe')).not.toBeInTheDocument();
    expect(getDocumentContentMock).not.toHaveBeenCalled();
  });

  it('uses an authorized blob for the PDF viewer and safe new-tab fallback', async () => {
    const content = new Blob(['pdf'], { type: 'application/pdf' });
    getDocumentContentMock.mockResolvedValueOnce(content);
    const { container, unmount } = render(
      <FileSourceViewer
        kbId="kb-1"
        documentId="document-1"
        filename="Policy.PDF?version=2#page=1"
      />,
    );

    expect(
      screen.getByText('원본 문서를 불러오는 중입니다...'),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(container.querySelector('object')).not.toBeNull(),
    );

    const object = container.querySelector('object');
    expect(object).toHaveAttribute('data', CONTENT_URL);
    expect(object).toHaveAttribute('type', 'application/pdf');
    expect(object).not.toHaveAttribute('sandbox');
    expect(container.querySelector('iframe')).not.toBeInTheDocument();

    const openLink = screen.getByRole('link', {
      name: 'PDF를 새 탭에서 열기',
    });
    expect(openLink).toHaveAttribute('href', CONTENT_URL);
    expect(openLink).toHaveAttribute('target', '_blank');
    expect(openLink).toHaveAttribute('rel', 'noopener noreferrer');
    expect(getDocumentContentMock).toHaveBeenCalledWith(
      'kb-1',
      'document-1',
      expect.any(AbortSignal),
    );
    expect(createObjectURLMock).toHaveBeenCalledWith(content);

    unmount();
    expect(revokeObjectURLMock).toHaveBeenCalledWith(CONTENT_URL);
  });

  it('keeps non-PDF content in a scriptless sandbox without download permission', async () => {
    getDocumentContentMock.mockResolvedValueOnce(
      new Blob(['document'], {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }),
    );
    const { container } = render(
      <FileSourceViewer
        kbId="kb-1"
        documentId="document-1"
        filename="policy.docx"
      />,
    );

    await waitFor(() =>
      expect(container.querySelector('iframe')).not.toBeNull(),
    );
    const iframe = container.querySelector('iframe');
    expect(iframe).toHaveAttribute('src', CONTENT_URL);
    expect(iframe).toHaveAttribute('sandbox', 'allow-same-origin');
    expect(iframe?.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(iframe?.getAttribute('sandbox')).not.toContain('allow-downloads');
    expect(container.querySelector('object')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'PDF를 새 탭에서 열기' }),
    ).not.toBeInTheDocument();
  });

  it('shows a safe error without exposing the request failure', async () => {
    getDocumentContentMock.mockRejectedValueOnce(
      new Error('sensitive upstream failure'),
    );

    const { container } = render(
      <FileSourceViewer
        kbId="kb-1"
        documentId="document-1"
        filename="policy.pdf"
      />,
    );

    expect(
      await screen.findByText('원본 문서를 불러올 수 없습니다.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/sensitive upstream failure/i),
    ).not.toBeInTheDocument();
    expect(container.querySelector('object')).not.toBeInTheDocument();
    expect(container.querySelector('iframe')).not.toBeInTheDocument();
    expect(createObjectURLMock).not.toHaveBeenCalled();
  });

  it('aborts the previous scope and ignores its late content', async () => {
    let resolvePreviousContent: (content: Blob) => void = () => undefined;
    const previousContent = new Promise<Blob>((resolve) => {
      resolvePreviousContent = resolve;
    });
    const currentContent = new Blob(['current'], { type: 'application/pdf' });
    getDocumentContentMock
      .mockReturnValueOnce(previousContent)
      .mockResolvedValueOnce(currentContent);
    const { container, rerender } = render(
      <FileSourceViewer
        kbId="kb-1"
        documentId="document-1"
        filename="old.pdf"
      />,
    );
    const previousSignal = getDocumentContentMock.mock
      .calls[0][2] as AbortSignal;

    rerender(
      <FileSourceViewer
        kbId="kb-1"
        documentId="document-2"
        filename="current.pdf"
      />,
    );

    expect(previousSignal.aborted).toBe(true);
    await waitFor(() =>
      expect(container.querySelector('object')).toHaveAttribute(
        'data',
        CONTENT_URL,
      ),
    );

    await act(async () => {
      resolvePreviousContent(
        new Blob(['previous'], { type: 'application/pdf' }),
      );
      await previousContent;
    });

    expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    expect(createObjectURLMock).toHaveBeenCalledWith(currentContent);
  });
});
