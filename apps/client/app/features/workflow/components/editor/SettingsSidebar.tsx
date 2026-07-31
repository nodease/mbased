import React, { useEffect, useState, useMemo } from 'react';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { workflowApi } from '../../api/workflowApi';
import { DeploymentResponse } from '../../types/Deployment';
import { X, Settings, Key, Eye, EyeOff, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { AppAuthSecretControl } from '@/app/features/app/components/AppAuthSecretControl';

// 노드 타입별 자격 증명 필드 정의
const CREDENTIAL_FIELDS: Record<
  string,
  { service: string; keyField: string; name: string }
> = {
  slackPostNode: {
    service: 'Slack',
    keyField: 'authConfig.token',
    name: 'Bot Token',
  },
  githubNode: {
    service: 'GitHub',
    keyField: 'api_token',
    name: 'Personal Access Token',
  },
};

export function SettingsSidebar() {
  const {
    isSettingsOpen,
    toggleSettings,
    activeWorkflowId,
    nodes,
    lastDeployedAt,
  } = useWorkflowStore();

  const [activeTab, setActiveTab] = useState<'deploy' | 'keys'>('deploy');
  const [deployments, setDeployments] = useState<DeploymentResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});

  // 배포 이력 가져오기
  const fetchDeployments = async () => {
    if (!activeWorkflowId) return;
    try {
      setLoading(true);
      const data = await workflowApi.getDeployments(activeWorkflowId);
      // 버전 내림차순 정렬
      const sorted = data.sort((a, b) => b.version - a.version);
      setDeployments(sorted);
    } catch (error) {
      console.error('Failed to fetch deployments:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isSettingsOpen && activeWorkflowId) {
      fetchDeployments();
    }
  }, [isSettingsOpen, activeWorkflowId, lastDeployedAt]);

  // 최신 배포 URL 필터링
  const latestDeployments = useMemo(() => {
    const latestTypes: Record<string, DeploymentResponse> = {};
    deployments.forEach((deploy) => {
      // 성공한 배포만, 그리고 이미 찾은 타입보다 버전이 높으면 갱신 (정렬되어 있으므로 첫번째가 최신)
      if (deploy.is_active && !latestTypes[deploy.type]) {
        latestTypes[deploy.type] = deploy;
      }
    });
    return Object.values(latestTypes);
  }, [deployments]);

  // 외부 연동 키 집계
  const credentials = useMemo(() => {
    const creds: {
      id: string;
      service: string;
      name: string;
      value: string;
    }[] = [];

    nodes.forEach((node) => {
      // 1. 일반 노드 크리덴셜
      const config = CREDENTIAL_FIELDS[node.type || ''];
      if (config) {
        // 중첩 객체 접근 (authConfig.token 등)
        const keys = config.keyField.split('.');
        let value = node.data as any;
        for (const k of keys) {
          value = value?.[k];
        }

        if (value && typeof value === 'string') {
          creds.push({
            id: node.id,
            service: config.service,
            name: config.name,
            value: value,
          });
        }
      }

      // 2. HTTP 노드 (Authorization 헤더 체크)
      if (node.type === 'httpRequestNode') {
        const headers = (node.data as any).headers as Array<{
          key: string;
          value: string;
        }>;
        const authHeader = headers?.find(
          (h) => h.key.toLowerCase() === 'authorization',
        );
        if (authHeader?.value) {
          // Bearer 제거하고 값만 추출 시도, 혹은 전체 표시
          const val = authHeader.value.startsWith('Bearer ')
            ? authHeader.value.slice(7)
            : authHeader.value;
          creds.push({
            id: node.id,
            service: 'HTTP Request',
            name: 'Bearer Token',
            value: val,
          });
        }
      }
    });

    return creds;
  }, [nodes]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('복사되었습니다.');
  };

  const toggleKeyVisibility = (id: string) => {
    setVisibleKeys((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  if (!isSettingsOpen) return null;

  return (
    <div className="absolute top-18 right-2 bottom-2 w-[400px] bg-white border-l border-gray-200 shadow-xl z-50 flex flex-col rounded-xl animate-in slide-in-from-right duration-200">
      {/* 헤더 */}
      <div className="p-4 border-b border-gray-100 flex items-center justify-between bg-white">
        <div className="flex items-center gap-2 text-gray-800">
          <Settings className="w-5 h-5" />
          <h2 className="font-semibold">설정</h2>
        </div>
        <button
          onClick={toggleSettings}
          className="p-1 hover:bg-gray-100 rounded text-gray-500"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* 탭 */}
      <div className="flex border-b border-gray-200">
        <button
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            activeTab === 'deploy'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50/30'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
          }`}
          onClick={() => setActiveTab('deploy')}
        >
          배포 URL
        </button>
        <button
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            activeTab === 'keys'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50/30'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
          }`}
          onClick={() => setActiveTab('keys')}
        >
          외부 연동 키
        </button>
      </div>

      {/* 콘텐츠 */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === 'deploy' && (
          <div className="space-y-4">
            <p className="text-xs text-gray-500 mb-4">
              최근 성공적으로 배포된 각 타입별 URL입니다.
            </p>
            {loading ? (
              <div className="flex justify-center p-8">
                <div className="w-6 h-6 border-2 border-gray-200 border-t-blue-500 rounded-full animate-spin" />
              </div>
            ) : latestDeployments.length === 0 ? (
              <div className="text-center py-10 text-gray-400 text-sm border border-dashed rounded-lg">
                배포 기록이 없습니다.
              </div>
            ) : (
              latestDeployments.map((deploy) => {
                const origin = window.location.origin;

                // REST API
                if (deploy.type === 'api') {
                  const url = `${origin}/api/v1/run/${deploy.url_slug}`;
                  const curlCommand = `curl -X POST ${url} \\
  -H "Authorization: Bearer <APP_SECRET>" \\
  -H "Content-Type: application/json" \\
  -d '{"inputs": {}}'`;

                  return (
                    <div
                      key={deploy.id}
                      className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex flex-col gap-4"
                    >
                      <div className="flex items-center justify-between">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-100 text-purple-700">
                          REST API
                        </span>
                        <span className="text-xs text-gray-500">
                          v{deploy.version}
                        </span>
                      </div>

                      {/* API Endpoint */}
                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-1">
                          API Endpoint URL
                        </div>
                        <div className="flex items-center gap-2 bg-white border border-gray-300 rounded px-2 py-1.5">
                          <div className="flex-1 text-xs text-gray-600 truncate font-mono select-all">
                            {url}
                          </div>
                          <button
                            onClick={() => copyToClipboard(url)}
                            className="p-1 hover:bg-gray-100 rounded text-gray-400 hover:text-gray-600"
                          >
                            <Copy className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <AppAuthSecretControl appId={deploy.app_id} />

                      {/* Test Command */}
                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-1">
                          Test Command (cURL)
                        </div>
                        <div className="relative group">
                          <pre className="text-[10px] grid overflow-x-auto p-3 bg-gray-800 text-gray-100 rounded-lg font-mono whitespace-pre-wrap break-all">
                            {curlCommand}
                          </pre>
                          <button
                            onClick={() => copyToClipboard(curlCommand)}
                            className="absolute top-2 right-2 p-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            <Copy className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                }

                // Web App
                if (deploy.type === 'webapp') {
                  const url = `${origin}/shared/${deploy.url_slug}`;
                  return (
                    <div
                      key={deploy.id}
                      className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex flex-col gap-3"
                    >
                      <div className="flex items-center justify-between">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">
                          WEB APP
                        </span>
                        <span className="text-xs text-gray-500">
                          v{deploy.version}
                        </span>
                      </div>

                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-1">
                          🌐 웹 앱 공유 링크
                        </div>
                        <div className="flex items-center gap-2 bg-white border border-gray-300 rounded px-2 py-1.5">
                          <div
                            className="flex-1 text-xs text-blue-600 truncate font-mono select-all underline cursor-pointer"
                            onClick={() => window.open(url, '_blank')}
                          >
                            {url}
                          </div>
                          <button
                            onClick={() => copyToClipboard(url)}
                            className="p-1 hover:bg-gray-100 rounded text-gray-400 hover:text-gray-600"
                          >
                            <Copy className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                }

                // Public Chatbot / Widget
                if (deploy.type === 'widget' || deploy.type === 'chatbot') {
                  if (!deploy.url_slug) {
                    return null;
                  }
                  const publicUrl = `${origin}/embed/chat/${deploy.url_slug}`;
                  const embedding = deploy.browser_access_policy?.embedding;
                  const embedCode = `<iframe
  src="${publicUrl}"
  width="100%"
  height="600"
  frameborder="0"
></iframe>`;
                  return (
                    <div
                      key={deploy.id}
                      className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex flex-col gap-3"
                    >
                      <div className="flex items-center justify-between">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-200 text-gray-700">
                          {deploy.type === 'widget'
                            ? 'WIDGET'
                            : 'PUBLIC CHATBOT'}
                        </span>
                        <span className="text-xs text-gray-500">
                          v{deploy.version}
                        </span>
                      </div>

                      <div>
                        <div className="mb-1 text-xs font-semibold text-gray-700">
                          직접 링크
                        </div>
                        <div className="flex items-center gap-2 rounded border border-gray-300 bg-white px-2 py-1.5">
                          <div className="min-w-0 flex-1 truncate font-mono text-xs text-blue-600">
                            {publicUrl}
                          </div>
                          <button
                            type="button"
                            title="직접 링크 복사"
                            onClick={() => copyToClipboard(publicUrl)}
                            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="text-xs text-gray-600">
                        {embedding?.enabled
                          ? `허용 origin ${embedding.parent_origins.length}개 · 집행 중`
                          : 'iframe 표시 차단'}
                      </div>

                      {embedding?.enabled && (
                        <div>
                          <div className="mb-1 text-xs font-semibold text-gray-700">
                            웹사이트 임베딩 코드
                          </div>
                          <div className="group relative">
                            <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-gray-800 p-3 font-mono text-[10px] text-gray-100">
                              {embedCode}
                            </pre>
                            <button
                              type="button"
                              title="임베딩 코드 복사"
                              onClick={() => copyToClipboard(embedCode)}
                              className="absolute right-2 top-2 rounded bg-gray-700 p-1.5 text-gray-300 opacity-0 transition-opacity hover:bg-gray-600 group-hover:opacity-100"
                            >
                              <Copy className="h-3 w-3" />
                            </button>
                          </div>
                          <div className="mt-2 space-y-1 font-mono text-[10px] text-gray-500">
                            {embedding.parent_origins.map((parentOrigin) => (
                              <div className="break-all" key={parentOrigin}>
                                {parentOrigin}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                }

                // Fallback for others
                return (
                  <div
                    key={deploy.id}
                    className="p-3 bg-gray-50 rounded-lg border border-gray-200"
                  >
                    <div className="text-xs">Module ID: {deploy.id}</div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {activeTab === 'keys' && (
          <div className="space-y-4">
            <p className="text-xs text-gray-500 mb-4">
              현재 워크플로우 노드에 저장된 외부 서비스 연동 키입니다.
            </p>
            {credentials.length === 0 ? (
              <div className="text-center py-10 text-gray-400 text-sm border border-dashed rounded-lg">
                설정된 연동 키가 없습니다.
              </div>
            ) : (
              credentials.map((cred, idx) => (
                <div
                  key={`${cred.id}-${idx}`}
                  className="p-3 bg-white rounded-lg border border-gray-200 shadow-sm"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center text-gray-600">
                      <Key className="w-4 h-4" />
                    </div>
                    <div>
                      <div className="font-medium text-sm text-gray-900">
                        {cred.service}
                      </div>
                      <div className="text-xs text-gray-500">{cred.name}</div>
                    </div>
                  </div>

                  <div className="relative">
                    <input
                      type={visibleKeys[cred.id] ? 'text' : 'password'}
                      value={cred.value}
                      readOnly
                      className="w-full text-xs font-mono bg-gray-50 border border-gray-200 rounded px-3 py-2 pr-16 focus:outline-none text-gray-600"
                    />
                    <div className="absolute right-1 top-1 flex items-center">
                      <button
                        onClick={() => toggleKeyVisibility(cred.id)}
                        className="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-200 rounded"
                      >
                        {visibleKeys[cred.id] ? (
                          <EyeOff className="w-3.5 h-3.5" />
                        ) : (
                          <Eye className="w-3.5 h-3.5" />
                        )}
                      </button>
                      <button
                        onClick={() => copyToClipboard(cred.value)}
                        className="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-200 rounded"
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
