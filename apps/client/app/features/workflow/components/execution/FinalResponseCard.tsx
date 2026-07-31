import { useId } from 'react';
import { MessageSquare } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { FinalResponsePreview } from '../../utils/testExecutionFinalResponse';

export function FinalResponseCard({
  preview,
  expandContent = false,
  renderMarkdown = false,
  appearance = 'default',
}: {
  preview: FinalResponsePreview;
  expandContent?: boolean;
  renderMarkdown?: boolean;
  appearance?: 'default' | 'chat';
}) {
  const titleId = useId();
  const isChatAppearance = appearance === 'chat';
  const responseContainerClassName = `mt-3 rounded-md border bg-white p-3 ${
    isChatAppearance
      ? 'col-span-2 border-slate-200 text-slate-900'
      : 'border-emerald-200 dark:border-emerald-800 dark:bg-emerald-950/40'
  }${expandContent ? '' : ' max-h-56 overflow-y-auto'}`;

  return (
    <section
      aria-labelledby={titleId}
      className={
        isChatAppearance
          ? 'w-full'
          : 'rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-800 dark:bg-emerald-900/20'
      }
    >
      <div
        className={
          isChatAppearance
            ? 'grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3'
            : 'flex items-start gap-3'
        }
      >
        <div
          className={
            isChatAppearance
              ? 'rounded-md bg-slate-100 p-2 text-slate-600'
              : 'rounded-md bg-emerald-100 p-2 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-200'
          }
        >
          <MessageSquare className="h-4 w-4" />
        </div>
        <div className={isChatAppearance ? 'contents' : 'min-w-0 flex-1'}>
          <div className="flex flex-wrap items-center gap-2">
            <h3
              id={titleId}
              className={`text-sm font-semibold ${
                isChatAppearance
                  ? 'text-slate-950'
                  : 'text-emerald-950 dark:text-emerald-50'
              }`}
            >
              최종 응답
            </h3>
            <span
              className={`rounded-full border px-2 py-0.5 text-xs ${
                isChatAppearance
                  ? 'border-slate-200 bg-slate-50 text-slate-600'
                  : 'border-emerald-200 bg-white text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200'
              }`}
            >
              {preview.sourceLabel}
            </span>
          </div>

          {preview.isEmpty ? (
            <p
              className={`mt-3 text-sm ${
                isChatAppearance
                  ? 'col-span-2 text-slate-600'
                  : 'text-emerald-700 dark:text-emerald-200'
              }`}
            >
              최종 사용자에게 표시할 응답이 비어 있습니다.
            </p>
          ) : preview.kind === 'json' ? (
            <div className={responseContainerClassName}>
              {preview.items.length > 0 ? (
                <dl className="space-y-2">
                  {preview.items.map((item) => (
                    <div key={item.label} className="min-w-0">
                      <dt
                        className={`text-xs font-semibold ${
                          isChatAppearance
                            ? 'text-slate-600'
                            : 'text-emerald-700 dark:text-emerald-200'
                        }`}
                      >
                        {item.label}
                      </dt>
                      <dd
                        className={`mt-0.5 whitespace-pre-wrap break-words text-sm ${
                          isChatAppearance
                            ? 'text-gray-900'
                            : 'text-gray-900 dark:text-gray-100'
                        }`}
                      >
                        {item.value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p
                  className={`text-sm ${
                    isChatAppearance
                      ? 'text-slate-600'
                      : 'text-emerald-700 dark:text-emerald-200'
                  }`}
                >
                  표시 가능한 응답 필드가 없습니다.
                </p>
              )}
            </div>
          ) : renderMarkdown ? (
            <div className={`${responseContainerClassName} text-sm leading-6`}>
              <ReactMarkdown
                disallowedElements={['img']}
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="mb-4 mt-6 text-2xl font-bold first:mt-0">
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="mb-3 mt-6 text-xl font-bold first:mt-0">
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="mb-2 mt-5 text-lg font-bold first:mt-0">
                      {children}
                    </h3>
                  ),
                  p: ({ children }) => (
                    <p className="mb-4 break-words last:mb-0">{children}</p>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-4 list-disc space-y-2 pl-7 last:mb-0">
                      {children}
                    </ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-4 list-decimal space-y-2 pl-7 last:mb-0">
                      {children}
                    </ol>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote
                      className={`my-4 border-l-4 pl-4 text-slate-600 ${
                        isChatAppearance
                          ? 'border-slate-300'
                          : 'border-emerald-300'
                      }`}
                    >
                      {children}
                    </blockquote>
                  ),
                  code: ({ children }) => (
                    <code className="break-words rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.9em] text-slate-900">
                      {children}
                    </code>
                  ),
                  a: ({ children, href }) => (
                    <a
                      href={href}
                      className={`font-semibold underline underline-offset-4 ${
                        isChatAppearance ? 'text-blue-600' : 'text-emerald-700'
                      }`}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {children}
                    </a>
                  ),
                  table: ({ children }) => (
                    <table className="my-4 w-full border-collapse text-left">
                      {children}
                    </table>
                  ),
                  th: ({ children }) => (
                    <th className="border border-slate-300 bg-slate-50 px-3 py-2 font-semibold">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-slate-300 px-3 py-2">
                      {children}
                    </td>
                  ),
                }}
              >
                {preview.text}
              </ReactMarkdown>
            </div>
          ) : (
            <div className={responseContainerClassName}>
              <p
                className={`whitespace-pre-wrap break-words text-sm leading-6 ${
                  isChatAppearance
                    ? 'text-gray-900'
                    : 'text-gray-900 dark:text-gray-100'
                }`}
              >
                {preview.text}
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
