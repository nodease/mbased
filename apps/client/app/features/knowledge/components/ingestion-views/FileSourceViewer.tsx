import React, { useEffect, useState } from 'react';
import { ExternalLink, FileText } from 'lucide-react';

import { knowledgeApi } from '../../api/knowledgeApi';

interface FileSourceViewerProps {
  kbId: string;
  documentId: string;
  filename?: string | null;
}

type ContentLoadState =
  | { scopeKey: string; status: 'ready'; objectUrl: string }
  | { scopeKey: string; status: 'error' };

const isPdfFilename = (filename?: string | null): boolean => {
  const normalized = (filename ?? '').trim().toLowerCase();
  const path = normalized.split(/[?#]/)[0];
  return path.endsWith('.pdf');
};

export default function FileSourceViewer({
  kbId,
  documentId,
  filename,
}: FileSourceViewerProps) {
  const normalizedFilename = filename?.trim() ?? '';
  const scopeKey =
    kbId && documentId && normalizedFilename
      ? JSON.stringify([kbId, documentId, normalizedFilename])
      : null;
  const [contentState, setContentState] = useState<ContentLoadState | null>(
    null,
  );

  useEffect(() => {
    if (!scopeKey) return;

    const controller = new AbortController();
    let objectUrl: string | null = null;

    void knowledgeApi
      .getDocumentContent(kbId, documentId, controller.signal)
      .then((content) => {
        if (controller.signal.aborted) return;

        objectUrl = URL.createObjectURL(content);
        setContentState({ scopeKey, status: 'ready', objectUrl });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setContentState({ scopeKey, status: 'error' });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, kbId, scopeKey]);

  if (!kbId || !documentId) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-gray-400 gap-3">
        <FileText className="w-12 h-12 opacity-20" />
        <p>문서를 불러오는 중입니다...</p>
      </div>
    );
  }

  if (!normalizedFilename) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-400">
        <FileText className="h-12 w-12 opacity-20" />
        <p>원본 문서 정보를 확인할 수 없습니다.</p>
      </div>
    );
  }

  if (!contentState || contentState.scopeKey !== scopeKey) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-400">
        <FileText className="h-12 w-12 opacity-20" />
        <p>원본 문서를 불러오는 중입니다...</p>
      </div>
    );
  }

  if (contentState.status === 'error') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-400">
        <FileText className="h-12 w-12 opacity-20" />
        <p>원본 문서를 불러올 수 없습니다.</p>
      </div>
    );
  }

  const contentUrl = contentState.objectUrl;

  if (isPdfFilename(normalizedFilename)) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-2">
        <div className="flex flex-none justify-end">
          <a
            href={contentUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 items-center gap-2 rounded-md border border-gray-200 bg-white px-3 text-sm font-medium text-gray-700 hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            PDF를 새 탭에서 열기
          </a>
        </div>
        <object
          data={contentUrl}
          type="application/pdf"
          className="min-h-0 w-full flex-1 rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800"
          title="Original PDF Document Preview"
        >
          <p className="p-4 text-sm text-gray-600 dark:text-gray-300">
            브라우저에서 PDF 미리보기를 표시할 수 없습니다. 상단 링크로 문서를
            여세요.
          </p>
        </object>
      </div>
    );
  }

  return (
    <iframe
      src={contentUrl}
      sandbox="allow-same-origin"
      className="w-full h-full bg-white rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 dark:bg-gray-800"
      title="Original Document Preview"
    />
  );
}
