'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Bot,
  FileText,
  Loader2,
  Search,
  Send,
} from 'lucide-react';

import {
  knowledgeApi,
  LLMAgentAnswerOption,
  RAGAgentAnswerResponse,
  RAGAgentStreamEvent,
} from '@/app/features/knowledge/api/knowledgeApi';

interface SearchPlaygroundProps {
  knowledgeBaseId: string;
}

type ApiErrorLike = {
  response?: {
    data?: {
      error?: {
        message?: unknown;
      };
    };
  };
};

const getErrorMessage = (error: unknown) => {
  const responseMessage = (error as ApiErrorLike).response?.data?.error?.message;
  if (typeof responseMessage === 'string') return responseMessage;
  if (error instanceof Error) return error.message;
  return '요청 처리 중 오류가 발생했습니다.';
};

const formatPercent = (value?: number | null) => {
  if (typeof value !== 'number') return '-';
  return `${(value * 100).toFixed(1)}%`;
};

const optionKey = (option: LLMAgentAnswerOption) =>
  `${option.model.id}:${option.credential.id}`;

const emptyAgentResponse = (
  knowledgeBaseId: string,
  answerRunId: string,
  correlationId: string,
): RAGAgentAnswerResponse => ({
  answer_run_id: answerRunId,
  correlation_id: correlationId,
  status: 'running',
  answer: '',
  citations: [],
  retrieval_summary: {
    knowledge_base_id: knowledgeBaseId,
    hierarchy_mode: 'auto',
    retrieved_chunk_count: 0,
    document_ids: [],
    citation_ids: [],
    score_summary: {},
    latency_ms: 0,
    raw_content_returned: false,
  },
  usage_summary: {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    total_cost: 0,
    latency_ms: 0,
  },
  policy_result: {},
});

export default function SearchPlayground({
  knowledgeBaseId,
}: SearchPlaygroundProps) {
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isPreparing, setIsPreparing] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [agentOptions, setAgentOptions] = useState<LLMAgentAnswerOption[]>([]);
  const [selectedOptionKey, setSelectedOptionKey] = useState('');
  const [hierarchyMode, setHierarchyMode] = useState<
    'auto' | 'flat' | 'parent_child'
  >('auto');
  const [response, setResponse] = useState<RAGAgentAnswerResponse | null>(null);

  const selectedOption = useMemo(
    () =>
      agentOptions.find((option) => optionKey(option) === selectedOptionKey) ||
      null,
    [agentOptions, selectedOptionKey],
  );

  useEffect(() => {
    let cancelled = false;

    const loadOptions = async () => {
      setIsPreparing(true);
      setErrorMessage(null);
      try {
        const options = await knowledgeApi.getAgentAnswerOptions();
        if (cancelled) return;
        setAgentOptions(options);
        setSelectedOptionKey((current) =>
          current && options.some((option) => optionKey(option) === current)
            ? current
            : options[0]
              ? optionKey(options[0])
              : '',
        );
      } catch (error) {
        if (!cancelled) setErrorMessage(getErrorMessage(error));
      } finally {
        if (!cancelled) setIsPreparing(false);
      }
    };

    loadOptions();

    return () => {
      cancelled = true;
    };
  }, []);

  const canSubmit =
    query.trim().length > 0 &&
    selectedOption !== null &&
    !isLoading &&
    !isPreparing;

  const handleSearch = async () => {
    if (!canSubmit) return;
    if (!selectedOption) return;

    setIsLoading(true);
    setErrorMessage(null);
    setResponse(null);

    try {
      await knowledgeApi.streamAgentAnswer(
        {
          knowledge_base_id: knowledgeBaseId,
          query: query.trim(),
          generation_model_id: selectedOption.model.id,
          credential_id: selectedOption.credential.id,
          hierarchy_mode: hierarchyMode,
          top_k: 8,
        },
        (event: RAGAgentStreamEvent) => {
          if (event.event === 'error') {
            const reason = String(event.data.reason_code || 'stream.error');
            setErrorMessage(`RAG answer stream failed: ${reason}`);
          }
          setResponse((current) => {
            const answerRunId =
              String(event.data.answer_run_id || current?.answer_run_id || '');
            const correlationId = String(
              event.data.correlation_id || current?.correlation_id || '',
            );
            const base =
              current ||
              emptyAgentResponse(knowledgeBaseId, answerRunId, correlationId);

            if (event.event === 'retrieval.completed') {
              return {
                ...base,
                retrieval_summary:
                  (event.data.retrieval_summary as RAGAgentAnswerResponse['retrieval_summary']) ||
                  base.retrieval_summary,
                citations:
                  (event.data.citations as RAGAgentAnswerResponse['citations']) ||
                  base.citations,
              };
            }
            if (event.event === 'answer.delta') {
              return {
                ...base,
                answer: `${base.answer}${String(event.data.delta || '')}`,
              };
            }
            if (event.event === 'usage') {
              return {
                ...base,
                usage_summary:
                  (event.data.usage_summary as RAGAgentAnswerResponse['usage_summary']) ||
                  base.usage_summary,
              };
            }
            if (event.event === 'summary') {
              return {
                ...base,
                status: String(event.data.status || base.status),
                retrieval_summary:
                  (event.data.retrieval_summary as RAGAgentAnswerResponse['retrieval_summary']) ||
                  base.retrieval_summary,
                policy_result:
                  (event.data.policy_result as Record<string, unknown>) ||
                  base.policy_result,
              };
            }
            if (event.event === 'answer.completed') {
              return {
                ...base,
                status: String(event.data.status || 'completed'),
              };
            }
            if (event.event === 'error') {
              return {
                ...base,
                status: String(event.data.status || 'failed'),
              };
            }
            return base;
          });
        },
      );
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <div className="border-b border-gray-200 bg-gray-50/50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
        <h3 className="flex items-center gap-2 font-semibold text-gray-900 dark:text-white">
          <Bot className="h-5 w-5 text-blue-600" />
          RAG Agent Answer
        </h3>
        <p className="mt-1 text-xs text-gray-500">
          Knowledge Base ID:{' '}
          <span className="rounded bg-gray-100 px-1 py-0.5 font-mono dark:bg-gray-900">
            {knowledgeBaseId}
          </span>
        </p>
      </div>

      <div className="grid gap-3 border-b border-gray-200 p-4 dark:border-gray-700 md:grid-cols-[1fr_160px]">
        <label className="min-w-0 text-xs font-medium text-gray-600 dark:text-gray-300">
          모델 / Credential
          <select
            value={selectedOptionKey}
            onChange={(event) => setSelectedOptionKey(event.target.value)}
            disabled={isPreparing || agentOptions.length === 0}
            className="mt-1 h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 dark:border-gray-600 dark:bg-gray-900 dark:text-white"
          >
            {agentOptions.length === 0 ? (
              <option value="">사용 가능한 조합 없음</option>
            ) : (
              agentOptions.map((option) => (
                <option key={optionKey(option)} value={optionKey(option)}>
                  {option.model.name} ({option.provider_name}) /{' '}
                  {option.credential.credential_name}
                  {option.credential.config_preview
                    ? ` (${option.credential.config_preview})`
                    : ''}
                </option>
              ))
            )}
          </select>
        </label>

        <label className="min-w-0 text-xs font-medium text-gray-600 dark:text-gray-300">
          검색 모드
          <select
            value={hierarchyMode}
            onChange={(event) =>
              setHierarchyMode(
                event.target.value as 'auto' | 'flat' | 'parent_child',
              )
            }
            className="mt-1 h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-white"
          >
            <option value="auto">Auto</option>
            <option value="flat">Flat</option>
            <option value="parent_child">Parent-child</option>
          </select>
        </label>
      </div>

      <div className="min-h-[400px] flex-1 overflow-y-auto p-6">
        {!response && !isLoading && !errorMessage && (
          <div className="flex h-full flex-col items-center justify-center text-gray-400">
            {isPreparing ? (
              <Loader2 className="mb-3 h-8 w-8 animate-spin text-blue-600" />
            ) : (
              <Search className="mb-4 h-12 w-12 opacity-20" />
            )}
            <p className="text-sm">
              {isPreparing
                ? '모델과 credential을 불러오는 중입니다.'
                : '질문을 입력하고 답변을 생성하세요.'}
            </p>
          </div>
        )}

        {errorMessage && (
          <div className="flex items-start gap-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-200">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {isLoading && !response && (
          <div className="flex h-full flex-col items-center justify-center text-blue-600">
            <Loader2 className="mb-2 h-8 w-8 animate-spin" />
            <p className="text-sm font-medium">RAG Agent answer 생성 중...</p>
          </div>
        )}

        {response && (
          <div className="space-y-6">
            <div className="flex gap-4">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-900/30">
                <Bot className="h-5 w-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-gray-900 dark:text-white">
                    AI 답변
                  </p>
                  <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500 dark:bg-gray-900">
                    {response.status}
                  </span>
                </div>
                <div className="whitespace-pre-wrap rounded-md bg-blue-50/60 p-4 text-sm leading-relaxed text-gray-700 dark:bg-blue-900/10 dark:text-gray-300">
                  {response.answer}
                </div>
              </div>
            </div>

            <div className="grid gap-3 border-y border-gray-100 py-4 text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300 md:grid-cols-3">
              <div>
                <span className="block text-gray-400">검색 청크</span>
                <span className="font-medium">
                  {response.retrieval_summary.retrieved_chunk_count}
                </span>
              </div>
              <div>
                <span className="block text-gray-400">토큰</span>
                <span className="font-medium">
                  {response.usage_summary.total_tokens.toLocaleString()}
                </span>
              </div>
              <div>
                <span className="block text-gray-400">비용</span>
                <span className="font-medium">
                  ${response.usage_summary.total_cost.toFixed(6)}
                </span>
              </div>
            </div>

            <div>
              <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                <FileText className="h-4 w-4" />
                참조 문서 ({response.citations.length})
              </h4>
              <div className="grid gap-3">
                {response.citations.map((citation) => (
                  <div
                    key={citation.citation_id}
                    className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm dark:border-gray-700 dark:bg-gray-900"
                  >
                    <div className="mb-2 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <span className="inline-flex max-w-full rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                          <span className="truncate">
                            {citation.filename || citation.document_id}
                          </span>
                        </span>
                        {citation.heading && (
                          <p className="mt-1 truncate text-xs text-gray-500">
                            {citation.heading}
                          </p>
                        )}
                      </div>
                      <span className="shrink-0 text-xs text-gray-400">
                        score {formatPercent(citation.score)}
                      </span>
                    </div>
                    {citation.content_preview && (
                      <p className="line-clamp-3 leading-relaxed text-gray-600 dark:text-gray-400">
                        {citation.content_preview}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
        <div className="relative">
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="지식 베이스에 대해 질문해보세요..."
            className="min-h-[84px] w-full resize-none rounded-md border border-gray-300 bg-white p-3 pr-12 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-white"
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                handleSearch();
              }
            }}
          />
          <button
            type="button"
            onClick={handleSearch}
            disabled={!canSubmit}
            aria-label="답변 생성"
            className="absolute bottom-3 right-3 flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:bg-gray-400"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
