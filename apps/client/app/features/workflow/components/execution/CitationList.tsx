import { BookOpenText } from 'lucide-react';

import type { WorkflowCitation } from '../../utils/deploymentRunResult';

export function CitationList({
  items,
  appearance = 'default',
}: {
  items: WorkflowCitation[];
  appearance?: 'default' | 'chat';
}) {
  if (items.length === 0) return null;
  const isChatAppearance = appearance === 'chat';

  return (
    <details
      className={`group mt-3 border-t border-gray-200 pt-3 ${
        isChatAppearance ? '' : 'dark:border-gray-700'
      }`}
    >
      <summary
        className={`flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
          isChatAppearance ? '' : 'dark:text-gray-200'
        }`}
      >
        <BookOpenText aria-hidden="true" className="h-4 w-4 shrink-0" />
        <span>참조 문서 ({items.length})</span>
        <span
          aria-hidden="true"
          className="ml-auto text-xs text-gray-500 group-open:hidden"
        >
          펼치기
        </span>
        <span
          aria-hidden="true"
          className="ml-auto hidden text-xs text-gray-500 group-open:inline"
        >
          접기
        </span>
      </summary>
      <ol aria-label="답변 참조 문서" className="mt-3 space-y-3">
        {items.map((item) => (
          <li
            key={item.citationId}
            className={`min-w-0 border-l-2 border-indigo-200 pl-3 ${
              isChatAppearance ? '' : 'dark:border-indigo-700'
            }`}
          >
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
              <span
                className={`text-xs font-semibold text-indigo-700 ${
                  isChatAppearance ? '' : 'dark:text-indigo-300'
                }`}
              >
                {item.evidenceRank}
              </span>
              <span
                className={`min-w-0 break-words text-sm font-medium text-gray-900 ${
                  isChatAppearance ? '' : 'dark:text-gray-100'
                }`}
              >
                {item.label}
              </span>
              {item.pageNumber ? (
                <span
                  className={`text-xs text-gray-500 ${
                    isChatAppearance ? '' : 'dark:text-gray-400'
                  }`}
                >
                  {item.pageNumber}쪽
                </span>
              ) : null}
            </div>
            {item.section ? (
              <p
                className={`mt-1 break-words text-xs text-gray-600 ${
                  isChatAppearance ? '' : 'dark:text-gray-300'
                }`}
              >
                {item.section}
              </p>
            ) : null}
            {item.contentPreview ? (
              <p
                className={`mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-gray-600 ${
                  isChatAppearance ? '' : 'dark:text-gray-300'
                }`}
              >
                {item.contentPreview}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}
