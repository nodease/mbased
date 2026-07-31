'use client';

import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  ArrowLeft,
  Loader2,
  Play,
  RefreshCw,
  SendHorizontal,
  ShieldCheck,
} from 'lucide-react';

import { authApi } from '@/app/features/auth/api/authApi';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { CitationList } from '@/app/features/workflow/components/execution/CitationList';
import { FinalResponseCard } from '@/app/features/workflow/components/execution/FinalResponseCard';
import type {
  DeploymentRunInfoResponse,
  InputVariable,
} from '@/app/features/workflow/types/Deployment';
import {
  getDeploymentRunCitations,
  getDeploymentRunFinalPreview,
} from '@/app/features/workflow/utils/deploymentRunResult';
import {
  claimLoginRedirectPath,
  getCurrentAuthReturnPath,
} from '@/lib/authReturn';

const isCheckboxVariable = (variable: InputVariable) =>
  variable.type === 'boolean' || variable.type === 'checkbox';

const readErrorMessage = (error: unknown) => {
  if (
    typeof error === 'object' &&
    error !== null &&
    'response' in error &&
    typeof (error as { response?: { status?: unknown } }).response?.status ===
      'number'
  ) {
    const response = (error as { response: { status: number } }).response;
    if (response.status === 400 || response.status === 422) {
      return '실행 입력이 올바르지 않습니다.';
    }
    if (response.status === 401) return '로그인이 필요합니다.';
    if (response.status === 403) return '이 배포를 실행할 권한이 없습니다.';
    if (response.status === 404) {
      return '현재 조직에서 실행 가능한 배포를 찾을 수 없습니다.';
    }
    if (response.status === 409) {
      return '배포 상태가 변경되었습니다. 화면을 새로고침해 주세요.';
    }
    if (response.status === 429) {
      return '현재 실행 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.';
    }
    if (response.status === 504) {
      return '배포 실행 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.';
    }
    if (response.status >= 500) {
      return '배포 실행 중 서버 오류가 발생했습니다. 실행 로그를 확인하세요.';
    }
  }

  return '배포 실행에 실패했습니다.';
};

const isUnauthorized = (error: unknown) =>
  typeof error === 'object' &&
  error !== null &&
  'response' in error &&
  (error as { response?: { status?: unknown } }).response?.status === 401;

const defaultValueFor = (variable: InputVariable) =>
  isCheckboxVariable(variable) ? false : '';

const placeholderFor = (variable: InputVariable) =>
  variable.name === 'question' || variable.name === 'message'
    ? '질문을 입력하세요'
    : '';

const resizeChatInput = (input: HTMLTextAreaElement) => {
  input.style.height = 'auto';
  input.style.height = `${Math.max(input.value ? input.scrollHeight : 40, 40)}px`;
};

const coerceInputValue = (variable: InputVariable, value: unknown) => {
  if (variable.type === 'number') {
    if (value === '' || value === null || value === undefined) return undefined;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (isCheckboxVariable(variable)) return Boolean(value);
  return typeof value === 'string' ? value : String(value ?? '');
};

const makeConversationId = () => {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
};

type ConversationTurn = {
  id: string;
  question: string;
  response: unknown;
};

export default function AuthenticatedDeploymentRunPage() {
  const { push, replace } = useRouter();
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const workflowId = params.id;
  const deploymentId = searchParams.get('deploymentId') || '';
  const [deployment, setDeployment] =
    useState<DeploymentRunInfoResponse | null>(null);
  const [currentUserName, setCurrentUserName] = useState('');
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [conversationId] = useState(makeConversationId);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [runError, setRunError] = useState('');
  const [runResult, setRunResult] = useState<unknown>(null);
  const [conversationTurns, setConversationTurns] = useState<
    ConversationTurn[]
  >([]);
  const [pendingQuestion, setPendingQuestion] = useState('');
  const conversationRegionRef = useRef<HTMLElement>(null);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);

  const variables = useMemo(
    () => deployment?.input_schema?.variables || [],
    [deployment?.input_schema?.variables],
  );
  const finalPreview = useMemo(
    () => getDeploymentRunFinalPreview(deployment, runResult),
    [deployment, runResult],
  );
  const finalCitations = useMemo(
    () => getDeploymentRunCitations(runResult),
    [runResult],
  );
  const chatVariable = useMemo(() => {
    if (
      (deployment?.type !== 'chatbot' &&
        deployment?.type !== 'internal_chatbot') ||
      variables.length !== 1 ||
      isCheckboxVariable(variables[0]) ||
      variables[0].type === 'number'
    ) {
      return null;
    }
    return variables[0];
  }, [deployment?.type, variables]);

  useEffect(() => {
    const region = conversationRegionRef.current;
    if (region) region.scrollTop = region.scrollHeight;
  }, [conversationTurns, pendingQuestion]);

  useEffect(() => {
    if (chatInputRef.current) resizeChatInput(chatInputRef.current);
  }, [chatVariable, inputs]);

  useEffect(() => {
    let active = true;

    authApi
      .me()
      .then((userInfo) => {
        if (active) setCurrentUserName(userInfo.user?.name?.trim() || '');
      })
      .catch(() => {
        if (active) setCurrentUserName('');
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    setLoadError('');

    if (!deploymentId) {
      setLoadError('실행할 배포 ID가 없습니다.');
      setIsLoading(false);
      return () => {
        active = false;
      };
    }

    workflowApi
      .getDeploymentRunInfo(deploymentId)
      .then((nextDeployment) => {
        if (!active) return;
        if (nextDeployment.workflow_id !== workflowId) {
          setDeployment(null);
          setInputs({});
          setLoadError('요청한 workflow와 배포 정보가 일치하지 않습니다.');
          return;
        }
        setDeployment(nextDeployment);
        setConversationTurns([]);
        setPendingQuestion('');
        const nextInputs: Record<string, unknown> = {};
        for (const variable of nextDeployment.input_schema?.variables || []) {
          nextInputs[variable.name] = defaultValueFor(variable);
        }
        setInputs(nextInputs);
      })
      .catch((error) => {
        if (!active) return;
        if (isUnauthorized(error)) {
          const redirectPath = claimLoginRedirectPath(
            getCurrentAuthReturnPath(),
          );
          if (redirectPath) replace(redirectPath);
          return;
        }
        setLoadError(readErrorMessage(error));
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [deploymentId, replace, workflowId]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!deploymentId || isRunning) return;

    setRunError('');
    setRunResult(null);
    setIsRunning(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const variable of variables) {
        const value = inputs[variable.name];
        if (
          variable.required &&
          (value === '' || value === null || value === undefined)
        ) {
          throw new Error(
            `${variable.label || variable.name} 값을 입력하세요.`,
          );
        }
        const coerced = coerceInputValue(variable, value);
        if (coerced !== undefined) payload[variable.name] = coerced;
      }

      const usesConversationControl =
        deployment?.type === 'chatbot' ||
        deployment?.type === 'internal_chatbot';

      const question = chatVariable
        ? String(payload[chatVariable.name] ?? '')
        : '';
      if (chatVariable) setPendingQuestion(question);

      const response = await workflowApi.runDeployment(
        deploymentId,
        payload,
        usesConversationControl ? conversationId : undefined,
      );
      setRunResult(response);
      if (chatVariable) {
        setConversationTurns((current) => [
          ...current,
          {
            id: `${Date.now()}-${current.length}`,
            question,
            response,
          },
        ]);
        setInputs((current) => ({
          ...current,
          [chatVariable.name]: defaultValueFor(chatVariable),
        }));
      }
    } catch (error) {
      setRunError(
        error instanceof Error && !('response' in error)
          ? error.message
          : readErrorMessage(error),
      );
    } finally {
      setPendingQuestion('');
      setIsRunning(false);
    }
  };

  const handleChatInputKeyDown = (
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229
    ) {
      return;
    }

    event.preventDefault();
    if (isRunning || loadError || event.currentTarget.value.trim() === '') {
      return;
    }
    event.currentTarget.form?.requestSubmit();
  };

  const title = deployment?.name || '배포된 워크플로우 실행';

  return (
    <main className="h-full overflow-y-auto bg-slate-50 px-6 py-8 text-slate-900">
      <div className="mx-auto flex max-w-5xl flex-col gap-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <button
              type="button"
              onClick={() => push('/dashboard')}
              className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-slate-900"
            >
              <ArrowLeft className="h-4 w-4" />
              대시보드로 돌아가기
            </button>
            <p className="text-xs font-semibold text-slate-500">
              내부 배포 실행
            </p>
            <h1 className="mt-2 truncate text-2xl font-bold text-slate-950">
              {title}
            </h1>
          </div>
          <span className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-slate-600">
            <ShieldCheck className="h-4 w-4" />
            <span className="flex flex-col leading-tight">
              {currentUserName && (
                <span className="text-sm font-semibold text-slate-900">
                  {currentUserName}
                </span>
              )}
              <span className="text-xs font-semibold">
                사용자 권한 적용
              </span>
            </span>
          </span>
        </header>

        {loadError && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">
            {loadError}
          </div>
        )}

        {isLoading ? (
          <div className="flex min-h-56 items-center justify-center rounded-lg border border-slate-200 bg-white">
            <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
          </div>
        ) : chatVariable ? (
          <section className="flex h-[calc(100vh-13rem)] min-h-[520px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <section
              ref={conversationRegionRef}
              aria-label="대화 내용"
              className="flex flex-1 flex-col gap-7 overflow-y-auto px-4 py-8 sm:px-8"
            >
              {conversationTurns.length === 0 && !pendingQuestion ? (
                <div className="my-auto text-center">
                  <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                    <ShieldCheck className="h-5 w-5" />
                  </div>
                  <h2 className="mt-4 text-lg font-semibold text-slate-950">
                    무엇을 도와드릴까요?
                  </h2>
                  <p className="mt-2 text-sm text-slate-500">
                    현재 로그인한 사용자의 권한 안에서 답변합니다.
                  </p>
                </div>
              ) : (
                conversationTurns.map((turn) => (
                  <div key={turn.id} className="flex flex-col gap-4">
                    <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-slate-100 px-4 py-3 text-sm leading-6 text-slate-900">
                      <p className="whitespace-pre-wrap break-words">
                        {turn.question}
                      </p>
                    </div>
                    <div className="max-w-[92%]">
                      <FinalResponseCard
                        appearance="chat"
                        expandContent
                        renderMarkdown
                        preview={getDeploymentRunFinalPreview(
                          deployment,
                          turn.response,
                        )}
                      />
                      <CitationList
                        appearance="chat"
                        items={getDeploymentRunCitations(turn.response)}
                      />
                    </div>
                  </div>
                ))
              )}

              {pendingQuestion && (
                <div className="flex flex-col gap-4" aria-live="polite">
                  <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-slate-100 px-4 py-3 text-sm leading-6 text-slate-900">
                    <p className="whitespace-pre-wrap break-words">
                      {pendingQuestion}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 px-1 text-sm text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    답변을 만들고 있습니다.
                  </div>
                </div>
              )}
            </section>

            <div className="border-t border-slate-200 bg-slate-50/80 p-3 sm:p-5">
              {runError && (
                <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{runError}</span>
                </div>
              )}
              <form
                onSubmit={handleSubmit}
                className="flex items-center gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100"
              >
                <label className="flex min-w-0 flex-1 items-center">
                  <span className="sr-only">
                    {chatVariable.label || chatVariable.name}
                  </span>
                  <textarea
                    ref={chatInputRef}
                    value={String(inputs[chatVariable.name] ?? '')}
                    onChange={(event) => {
                      resizeChatInput(event.currentTarget);
                      setInputs((current) => ({
                        ...current,
                        [chatVariable.name]: event.target.value,
                      }));
                    }}
                    onKeyDown={handleChatInputKeyDown}
                    rows={1}
                    disabled={isRunning || Boolean(loadError)}
                    placeholder="질문을 입력하세요"
                    className="min-h-10 w-full resize-none overflow-hidden bg-transparent px-3 py-2.5 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
                  />
                </label>
                <button
                  type="submit"
                  aria-label="전송"
                  disabled={
                    isRunning ||
                    Boolean(loadError) ||
                    String(inputs[chatVariable.name] ?? '').trim() === ''
                  }
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {isRunning ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <SendHorizontal className="h-4 w-4" />
                  )}
                </button>
              </form>
              <p className="mt-2 text-center text-xs text-slate-400">
                Enter로 전송 · Shift+Enter로 줄바꿈 · 중요한 내용은 연결된 사내
                문서에서 다시 확인하세요.
              </p>
            </div>
          </section>
        ) : (
          <section className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <form
              onSubmit={handleSubmit}
              className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-slate-950">
                  실행 입력
                </h2>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">
                  {workflowId}
                </span>
              </div>

              <div className="mt-5 flex flex-col gap-4">
                {variables.length === 0 ? (
                  <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
                    이 배포는 입력 변수가 없습니다.
                  </p>
                ) : (
                  variables.map((variable) => (
                    <label key={variable.name} className="block">
                      <span className="text-sm font-semibold text-slate-700">
                        {variable.label || variable.name}
                        {variable.required && (
                          <span className="ml-1 text-red-500">*</span>
                        )}
                      </span>
                      {isCheckboxVariable(variable) ? (
                        <input
                          type="checkbox"
                          checked={Boolean(inputs[variable.name])}
                          onChange={(event) =>
                            setInputs((current) => ({
                              ...current,
                              [variable.name]: event.target.checked,
                            }))
                          }
                          className="mt-2 h-5 w-5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                        />
                      ) : variable.type === 'paragraph' ? (
                        <textarea
                          value={String(inputs[variable.name] ?? '')}
                          onChange={(event) =>
                            setInputs((current) => ({
                              ...current,
                              [variable.name]: event.target.value,
                            }))
                          }
                          rows={5}
                          placeholder={placeholderFor(variable)}
                          className="mt-2 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                        />
                      ) : (
                        <input
                          type={variable.type === 'number' ? 'number' : 'text'}
                          value={String(inputs[variable.name] ?? '')}
                          placeholder={placeholderFor(variable)}
                          onChange={(event) =>
                            setInputs((current) => ({
                              ...current,
                              [variable.name]: event.target.value,
                            }))
                          }
                          className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                        />
                      )}
                    </label>
                  ))
                )}
              </div>

              <button
                type="submit"
                disabled={isRunning || Boolean(loadError)}
                className="mt-5 inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {isRunning ? '실행 중' : '실행'}
              </button>
            </form>

            <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-slate-950">
                  사용자 응답
                </h2>
                {runResult !== null && (
                  <button
                    type="button"
                    onClick={() => setRunResult(null)}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-slate-900"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    초기화
                  </button>
                )}
              </div>

              {runError ? (
                <div className="mt-5 flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="font-semibold">{runError}</span>
                </div>
              ) : runResult !== null ? (
                <div className="mt-5">
                  <FinalResponseCard preview={finalPreview} />
                  <CitationList items={finalCitations} />
                </div>
              ) : (
                <div className="mt-5 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">
                  실행하면 최종 사용자가 받는 응답이 이 영역에 표시됩니다.
                </div>
              )}
            </section>
          </section>
        )}
      </div>
    </main>
  );
}
