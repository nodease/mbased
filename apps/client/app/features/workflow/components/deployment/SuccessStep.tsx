'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle2, Copy, Clock, Globe } from 'lucide-react';
import type { DeploymentResult } from './types';
import type { DeploymentType } from '../../types/Deployment';
import { formatCronExpression } from './utils';
import {
  AppAuthSecretControl,
  type AppAuthSecretReadiness,
  type IssuedAppAuthSecret,
} from '@/app/features/app/components/AppAuthSecretControl';

interface SuccessStepProps {
  result: DeploymentResult;
  deploymentType: DeploymentType;
  issuedSecret: IssuedAppAuthSecret | null;
  onSecretAvailable: (secret: IssuedAppAuthSecret | null) => void;
  onClose: () => void;
}

export function SuccessStep({
  result,
  deploymentType,
  issuedSecret,
  onSecretAvailable,
  onClose,
}: SuccessStepProps) {
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [testResponse, setTestResponse] = useState<string | null>(null);
  const [sessionTestSecret, setSessionTestSecret] = useState('');
  const [appAuthSecretReadiness, setAppAuthSecretReadiness] =
    useState<AppAuthSecretReadiness>('checking');
  const issuedSecretValue =
    appAuthSecretReadiness === 'ready' ? issuedSecret?.value : null;
  const testAuthSecret = issuedSecretValue ?? sessionTestSecret;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('클립보드에 복사되었습니다!', { duration: 1000 });
  };

  // Generate URLs
  const baseUrl =
    typeof window !== 'undefined'
      ? window.location.origin
      : 'https://moduly-ai.cloud';
  const embeddingPolicy = result.browser_access_policy?.embedding;
  const parentOrigins = embeddingPolicy?.enabled
    ? embeddingPolicy.parent_origins
    : [];
  const iframeCode = result.embedUrl
    ? `<iframe src="${result.embedUrl}" width="100%" height="600" title="Nodease chatbot" frameborder="0"></iframe>`
    : null;
  const API_URL = `${baseUrl}/api/v1/run/${result.url_slug}`;

  // Generate curl example
  const generateCurlExample = (): string => {
    let inputsExample: Record<string, string> = {};

    if (
      result.input_schema &&
      result.input_schema.variables &&
      result.input_schema.variables.length > 0
    ) {
      inputsExample = result.input_schema.variables.reduce(
        (acc, variable) => {
          acc[variable.name] = inputValues[variable.name] || '';
          return acc;
        },
        {} as Record<string, string>,
      );
    }

    const inputsJson = JSON.stringify(inputsExample, null, 2)
      .split('\n')
      .map((line, i) => (i === 0 ? line : `    ${line}`))
      .join('\n');

    const authHeader = `  -H "Authorization: Bearer <APP_SECRET>" \\\n`;

    return `curl -X POST "${API_URL}" \\
  -H "Content-Type: application/json" \\
${authHeader}  -d '{
    "inputs": ${inputsJson}
  }'`;
  };

  // Handle test execution
  const handleTestExecute = async () => {
    if (!testAuthSecret) return;
    setIsLoading(true);
    setTestResponse(null);

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      headers['Authorization'] = `Bearer ${testAuthSecret}`;

      const response = await fetch(`/api/v1/run/${result.url_slug}`, {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify({ inputs: inputValues }),
      });

      const data = await response.json();
      setTestResponse(JSON.stringify(data, null, 2));

      if (response.ok) {
        toast.success('테스트 실행 완료!', { duration: 1500 });
      } else {
        toast.error('API 호출 오류', { duration: 1500 });
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      setTestResponse(JSON.stringify({ error: message }, null, 2));
      toast.error('테스트 실행 실패', { duration: 1500 });
    } finally {
      setIsLoading(false);
    }
  };

  // Webhook trigger detection
  const isWebhookTrigger = deploymentType === 'webhook';

  return (
    <>
      <div className="px-6 py-4 border-b border-gray-200 bg-green-50">
        <div className="flex items-center gap-2 text-green-700">
          <CheckCircle2 className="h-6 w-6" />
          <h2 className="text-xl font-bold">배포 성공 (v{result.version})</h2>
        </div>
        <p className="text-sm text-green-600 mt-1 ml-8">
          워크플로우가 성공적으로 배포되었습니다.
        </p>
      </div>

      <div className="p-6 space-y-6 max-h-[60vh] overflow-y-auto">
        {result.message && (
          <div
            role="status"
            className="whitespace-pre-line rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
          >
            {result.message}
          </div>
        )}

        {/* Web App / Chatbot Share Link */}
        {result.webAppUrl && deploymentType !== 'internal_chatbot' && (
          <div className="border-2 border-blue-200 rounded-lg p-4 bg-blue-50">
            <label className="block text-sm font-semibold text-blue-900 mb-2">
              {deploymentType === 'chatbot'
                ? '공개 챗봇 공유 링크'
                : deploymentType === 'widget'
                  ? '위젯 직접 링크'
                  : '웹 앱 공유 링크'}
            </label>
            <p className="text-xs text-blue-700 mb-3">
              {deploymentType === 'chatbot' || deploymentType === 'widget'
                ? '인증 없이 접근하며 공개 Collection에 연결된 지식만 검색됩니다.'
                : '이 링크를 공유하면 누구나 워크플로우를 사용할 수 있습니다!'}
            </p>
            <div className="flex gap-2">
              <code className="flex-1 p-3 bg-white border border-blue-300 rounded text-sm text-blue-800 font-mono break-all leading-relaxed">
                {result.webAppUrl}
              </code>
              <button
                onClick={() => handleCopy(result.webAppUrl!)}
                className="px-3 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors whitespace-nowrap h-fit"
              >
                복사
              </button>
            </div>
          </div>
        )}

        {deploymentType === 'internal_chatbot' && result.internalRunUrl && (
          <div className="border-2 border-emerald-200 rounded-lg p-4 bg-emerald-50">
            <label className="block text-sm font-semibold text-emerald-900 mb-2">
              사내 인증 실행 링크
            </label>
            <p className="text-xs text-emerald-700 mb-3">
              로그인한 사용자 권한으로 실행됩니다. 사내 private Knowledge/RAG는
              이 링크에서 검증하세요.
            </p>
            <div className="flex gap-2">
              <code className="flex-1 p-3 bg-white border border-emerald-300 rounded text-sm text-emerald-800 font-mono break-all leading-relaxed">
                {result.internalRunUrl}
              </code>
              <a
                href={result.internalRunUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-2 text-sm font-medium text-emerald-700 bg-white border border-emerald-300 hover:bg-emerald-100 rounded transition-colors whitespace-nowrap h-fit"
              >
                열기
              </a>
              <button
                onClick={() => handleCopy(result.internalRunUrl!)}
                className="px-3 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded transition-colors whitespace-nowrap h-fit"
              >
                복사
              </button>
            </div>
          </div>
        )}

        {/* Public Chatbot / Widget Embedding Code */}
        {iframeCode && embeddingPolicy?.enabled && (
          <div className="border-2 border-purple-200 rounded-lg p-4 bg-purple-50">
            <label className="block text-sm font-semibold text-purple-900 mb-2">
              웹사이트 임베딩 코드
            </label>
            <p className="mb-3 text-xs font-medium text-amber-700">
              브라우저 제한 집행 중
            </p>
            <div className="relative">
              <pre className="p-4 bg-gray-900 rounded-lg text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre leading-relaxed border border-gray-700">
                {iframeCode}
              </pre>
              <button
                onClick={() => handleCopy(iframeCode)}
                className="absolute top-2 right-2 px-2 py-1 text-xs font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
              >
                복사
              </button>
            </div>
            <div className="mt-3 rounded-md border border-purple-200 bg-purple-100 p-3">
              <div className="text-xs font-semibold text-purple-900">
                허용 부모 origin
              </div>
              <ul className="mt-2 space-y-1 font-mono text-xs text-purple-800">
                {parentOrigins.map((origin) => (
                  <li className="break-all" key={origin}>
                    {origin}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* Workflow Node Deployment */}
        {result.isWorkflowNode && (
          <div className="border-2 border-indigo-200 rounded-lg p-4 bg-indigo-50">
            <label className="block text-sm font-semibold text-indigo-900 mb-2">
              서브 모듈 배포 완료
            </label>
            <p className="text-xs text-indigo-700 mb-3">
              이 워크플로우는 이제 다른 워크플로우에서 '서브 모듈'로 불러와
              사용할 수 있습니다.
            </p>
            <div className="bg-white p-3 rounded border border-indigo-200 text-sm text-gray-700">
              <p>
                <strong>버전:</strong> {result.version}
              </p>
            </div>
          </div>
        )}

        {/* Schedule Trigger Deployment */}
        {deploymentType === 'schedule' && (
          <div className="border border-gray-200 rounded-xl bg-white overflow-hidden shadow-sm">
            {/* Header */}
            <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-3">
              <div className="p-2 bg-gray-100 rounded-lg text-gray-600">
                <Clock className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-gray-900">
                  스케줄링 활성화됨
                </h3>
                <p className="text-xs text-gray-500">
                  워크플로우가 자동으로 실행됩니다.
                </p>
              </div>
            </div>

            <div className="p-5 space-y-5">
              {/* Primary Info: Natural Language Description */}
              <div>
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider block mb-2">
                  실행 주기
                </span>
                <p className="text-lg font-bold text-gray-900 leading-tight">
                  {result.cronExpression
                    ? formatCronExpression(result.cronExpression)
                    : '주기 설정이 없습니다.'}
                </p>
              </div>

              {/* Meta Info: Timezone */}
              <div>
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider block mb-2">
                  타임존
                </span>
                <div className="flex items-center gap-2 text-gray-700">
                  <Globe className="w-4 h-4 text-gray-400" />
                  <span className="text-sm font-medium">{result.timezone}</span>
                </div>
              </div>

              {/* Secondary Info: Styled Cron Badge */}
              <div className="pt-4 border-t border-gray-100">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-gray-400 font-medium">
                    CRON EXPRESSION
                  </span>
                  <code className="text-[10px] font-mono font-medium text-gray-500 bg-gray-50 px-2 py-1 rounded border border-gray-200 tracking-wide">
                    {result.cronExpression || 'N/A'}
                  </code>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Webhook Trigger Deployment */}
        {isWebhookTrigger && (
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">웹훅 URL</h3>

              <div className="rounded-lg border border-purple-200 bg-purple-50 p-4">
                <label className="mb-2 block text-sm font-semibold text-purple-900">
                  Endpoint
                </label>
                <div className="flex gap-2">
                  <code className="flex-1 break-all rounded border border-purple-300 bg-white p-3 font-mono text-xs">
                    {baseUrl}/api/v1/hooks/{result.url_slug}
                  </code>
                  <button
                    onClick={() =>
                      handleCopy(`${baseUrl}/api/v1/hooks/${result.url_slug}`)
                    }
                    className="flex h-full items-center justify-center rounded border border-purple-200 p-3 text-purple-700 transition-colors hover:bg-purple-100"
                    title="Webhook URL 복사"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {result.appId && (
                <div className="border-l-2 border-gray-200 pl-4">
                  <AppAuthSecretControl
                    appId={result.appId}
                    issuedSecret={issuedSecret}
                    onSecretAvailable={onSecretAvailable}
                    onReadinessChange={setAppAuthSecretReadiness}
                  />
                </div>
              )}
            </div>

            <div className="border-l border-gray-200 pl-6">
              <h3 className="mb-2 text-lg font-semibold text-gray-900">
                연동 조건
              </h3>
              <ul className="space-y-3 border-l-2 border-gray-100 pl-4 text-xs leading-relaxed text-gray-600">
                <li>POST 요청 본문은 최상위 JSON object여야 합니다.</li>
                <li>
                  Secret Key는 URL이나 query parameter가 아니라 Authorization
                  헤더로 전달합니다.
                </li>
                <li>
                  헤더를 설정할 수 없는 외부 서비스는 직접 연결하지 말고 별도
                  adapter를 사용해야 합니다.
                </li>
              </ul>
            </div>
          </div>
        )}

        {/* REST API Deployment (exclude webhooks) */}
        {!result.webAppUrl &&
          !result.embedUrl &&
          !result.isWorkflowNode &&
          deploymentType !== 'internal_chatbot' &&
          deploymentType !== 'schedule' &&
          !isWebhookTrigger && (
            <div className="grid grid-cols-2 gap-6">
              {/* Left Column - API Information */}
              <div className="space-y-4">
                {/* API Endpoint */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    API Endpoint URL
                  </label>
                  <div className="flex gap-2">
                    <code className="flex-1 p-3 bg-gray-50 border border-gray-200 rounded text-xs text-gray-600 font-mono break-all leading-relaxed">
                      {API_URL}
                    </code>
                    <button
                      onClick={() => handleCopy(API_URL)}
                      className="px-3 py-2 text-sm font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded transition-colors whitespace-nowrap h-fit"
                    >
                      복사
                    </button>
                  </div>
                </div>

                {result.appId && (
                  <div className="border-l-2 border-gray-200 pl-4">
                    <AppAuthSecretControl
                      appId={result.appId}
                      issuedSecret={issuedSecret}
                      onSecretAvailable={onSecretAvailable}
                      onReadinessChange={setAppAuthSecretReadiness}
                    />
                  </div>
                )}

                {/* Input Variables Section */}
                {result.input_schema &&
                  result.input_schema.variables &&
                  result.input_schema.variables.length > 0 && (
                    <div>
                      {/* Section Header */}
                      <div className="mb-3">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-gray-700">
                            입력 변수
                          </span>
                          <span className="text-xs text-gray-500">
                            ({result.input_schema.variables.length}개)
                          </span>
                        </div>
                      </div>

                      {/* Scrollable Input Variables Form */}
                      <div className="max-h-[400px] overflow-y-auto pr-2 space-y-4">
                        {result.input_schema.variables.map(
                          (variable, index) => (
                            <div
                              key={index}
                              className="border border-gray-200 rounded-lg p-4 bg-white hover:bg-gray-50 transition-colors"
                            >
                              {/* Variable Info Header */}
                              <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-2">
                                  <code className="text-sm font-mono text-blue-700 font-semibold">
                                    {variable.name}
                                  </code>
                                  <span
                                    className={`text-xs px-2 py-1 rounded font-medium ${
                                      variable.required
                                        ? 'bg-red-100 text-red-700'
                                        : 'bg-green-100 text-green-700'
                                    }`}
                                  >
                                    {variable.required ? '필수' : '선택'}
                                  </span>
                                </div>
                                <div>
                                  <span className="text-xs text-blue-700 bg-blue-100 px-2 py-1 rounded font-medium">
                                    {variable.type}
                                  </span>
                                </div>
                              </div>

                              {/* Input Field */}
                              <input
                                type={
                                  variable.type === 'number' ? 'number' : 'text'
                                }
                                placeholder={`${variable.name} 입력`}
                                value={inputValues[variable.name] || ''}
                                onChange={(e) =>
                                  setInputValues({
                                    ...inputValues,
                                    [variable.name]: e.target.value,
                                  })
                                }
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                              />
                            </div>
                          ),
                        )}
                      </div>
                    </div>
                  )}
              </div>

              {/* Right Column - Test Tools */}
              <div className="space-y-4 border-l border-gray-200 pl-6">
                {/* cURL Example */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Test Command (cURL)
                  </label>
                  <div className="relative">
                    <pre className="p-4 bg-gray-900 rounded-lg text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre leading-relaxed border border-gray-700">
                      {generateCurlExample()}
                    </pre>
                    <button
                      onClick={() => handleCopy(generateCurlExample())}
                      className="absolute top-2 right-2 px-2 py-1 text-xs font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                    >
                      복사
                    </button>
                  </div>
                </div>

                {!issuedSecret && (
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">
                      테스트용 기존 App secret
                    </label>
                    <input
                      type="password"
                      value={sessionTestSecret}
                      onChange={(event) =>
                        setSessionTestSecret(event.target.value)
                      }
                      autoComplete="off"
                      aria-label="테스트용 기존 App secret"
                      placeholder="이 화면에서만 사용"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <p className="mt-1 text-xs text-gray-500">
                      입력값은 브라우저 저장소에 저장하지 않습니다.
                    </p>
                  </div>
                )}

                {/* Test Execution Button */}
                <button
                  onClick={handleTestExecute}
                  disabled={isLoading || !testAuthSecret}
                  className={`w-full py-3 rounded-lg font-semibold text-white transition-colors ${
                    isLoading || !testAuthSecret
                      ? 'bg-gray-400 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  {isLoading
                    ? '실행 중...'
                    : testAuthSecret
                      ? '테스트 실행'
                      : 'Secret 입력 후 테스트'}
                </button>

                {/* Response Result */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    응답 결과
                  </label>
                  {testResponse ? (
                    <div className="relative">
                      <pre className="p-4 bg-gray-900 rounded-lg text-xs text-green-400 font-mono overflow-x-auto whitespace-pre leading-relaxed border border-gray-700 max-h-[250px] overflow-y-auto">
                        {testResponse}
                      </pre>
                      <button
                        onClick={() => handleCopy(testResponse)}
                        className="absolute top-2 right-2 px-2 py-1 text-xs font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                      >
                        복사
                      </button>
                    </div>
                  ) : (
                    <div className="p-4 bg-gray-50 border border-gray-200 rounded-lg min-h-[100px] flex items-center justify-center">
                      <p className="text-sm text-gray-500">
                        테스트 실행 후 결과가 여기에 표시됩니다
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
      </div>

      <div className="px-6 py-4 border-t border-gray-200 flex justify-end">
        <button
          onClick={onClose}
          className="px-4 py-2 text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors font-semibold"
        >
          확인
        </button>
      </div>
    </>
  );
}
