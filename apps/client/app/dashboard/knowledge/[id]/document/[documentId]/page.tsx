'use client';
import React, { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  FileText,
  Save,
  AlertTriangle,
  Loader2,
  ChevronRight,
  Home,
  Database,
  Calendar,
  RefreshCw,
  Pencil,
  ListTodo,
  CircleHelp,
  ListFilter,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  knowledgeApi,
  JoinConfig,
} from '@/app/features/knowledge/api/knowledgeApi';
import {
  DocumentApiEditConfigSummary,
  DocumentResponse,
} from '@/app/features/knowledge/types/Knowledge';
import { useDocumentProcess } from '@/app/features/knowledge/hooks/useDocumentProcess';
import Link from 'next/link'; // Added for Breadcrumb
// Separated Components
import FileSourceViewer from '@/app/features/knowledge/components/ingestion-views/FileSourceViewer';
import ApiSourceViewer from '@/app/features/knowledge/components/ingestion-views/ApiSourceViewer';
import DbSourceViewer from '@/app/features/knowledge/components/ingestion-views/DbSourceViewer';
import CommonChunkSettings from '@/app/features/knowledge/components/document-settings/CommonChunkSettings';
import ParsingStrategySettings from '@/app/features/knowledge/components/document-settings/ParsingStrategySettings';
import ChunkPreviewList from '@/app/features/knowledge/components/preview/ChunkPreviewList';
import DBConnectionForm from '@/app/features/knowledge/components/create-knowledge-modal/DBConnectionForm';
import { DBConfig } from '@/app/features/knowledge/types/DB';
import {
  connectorApi,
  type ConnectionTestResult,
} from '@/app/features/knowledge/api/connectorApi';
import { useGenericCredential } from '@/app/features/knowledge/hooks/useGenericCredential';
import ColumnAutocomplete from '@/app/features/knowledge/components/document-settings/ColumnAutocomplete';
import {
  ACTIVE_ORGANIZATION_CHANGED_EVENT,
  getStoredActiveOrganizationId,
} from '@/lib/activeOrganization';

// UUID prefix가 있으면 제거, API URL이면 도메인만 추출
const getDisplayFilename = (filename: string): string => {
  // API source: URL이면 도메인만 추출
  if (filename.startsWith('http://') || filename.startsWith('https://')) {
    try {
      const url = new URL(filename);
      return url.hostname;
    } catch {
      return filename;
    }
  }
  // FILE source: UUID prefix 제거
  if (filename.length > 37 && filename[36] === '_') {
    return filename.substring(37);
  }
  // DB source 등: 그대로 반환
  return filename;
};

const isActiveProcessingStatus = (value: string) =>
  value === 'indexing' || value === 'processing';

export default function DocumentSettingsPage() {
  const params = useParams();
  const router = useRouter();
  const kbId = params.id as string;
  const documentId = params.documentId as string;

  // 상태 관리
  const [isLoading, setIsLoading] = useState(true);
  const [status, setStatus] = useState<string>(''); // 문서 상태
  const [errorMessage, setErrorMessage] = useState<string | null>(null); // [추가] 에러 메시지 상태
  const [document, setDocument] = useState<DocumentResponse | null>(null);
  const [kbName, setKbName] = useState<string>(''); // KB 이름 상태 추가
  const [selectedDbItems, setSelectedDbItems] = useState<
    Record<string, string[]>
  >({});

  // [신규] LlamaParse 키 확인 Hook
  const { hasKey: hasLlamaParseKey, isLoading: isKeyLoading } =
    useGenericCredential('llamaparse');

  const [sensitiveColumns, setSensitiveColumns] = useState<
    Record<string, string[]>
  >({});
  const [aliases, setAliases] = useState<
    Record<string, Record<string, string>>
  >({});
  const [template, setTemplate] = useState<string>('');

  // 설정 상태
  const [chunkSize, setChunkSize] = useState<number>(1000);
  const [chunkOverlap, setChunkOverlap] = useState<number>(200);
  const [segmentIdentifier, setSegmentIdentifier] = useState<string>('\\n\\n');
  const [removeUrlsEmails, setRemoveUrlsEmails] = useState<boolean>(false);
  const [removeWhitespace, setRemoveWhitespace] = useState<boolean>(true);
  const [parsingStrategy, setParsingStrategy] = useState<
    'general' | 'llamaparse'
  >('general');
  const [chunkingMode, setChunkingMode] = useState<
    'flat' | 'hierarchical'
  >('flat');
  const [apiConfigSummary, setApiConfigSummary] =
    useState<DocumentApiEditConfigSummary | null>(null);
  const [enableAutoChunking, setEnableAutoChunking] = useState<boolean>(true); // 자동 청킹 활성화
  const [joinConfig, setJoinConfig] = useState<JoinConfig | null>(null); // JOIN 설정 상태
  const [canEditDocument, setCanEditDocument] = useState(false);
  const [isEditConfigReady, setIsEditConfigReady] = useState(false);
  const [permissionScope, setPermissionScope] = useState<string | null>(null);
  const [editConfigScope, setEditConfigScope] = useState<string | null>(null);
  const [activeOrganizationId, setActiveOrganizationId] = useState<
    string | null
  >(() => getStoredActiveOrganizationId());
  const fetchGeneration = useRef(0);
  const activeOrganizationRef = useRef<string | null>(activeOrganizationId);
  const activeProcessingScope = useRef<string | null>(null);
  const currentDocumentScope = `${activeOrganizationId ?? 'no-organization'}:${kbId}:${documentId}`;
  const documentScopeRef = useRef(currentDocumentScope);

  useEffect(() => {
    documentScopeRef.current = currentDocumentScope;
  }, [currentDocumentScope]);
  const canEditCurrentDocument =
    canEditDocument && permissionScope === currentDocumentScope;
  const isCurrentEditConfigReady =
    isEditConfigReady && editConfigScope === currentDocumentScope;

  // 작업 불가능 조건: (전략이 llamaparse인데 키가 없으면)
  const isActionDisabled =
    parsingStrategy === 'llamaparse' && !isKeyLoading && !hasLlamaParseKey;
  const isEditActionDisabled =
    isActionDisabled ||
    !canEditCurrentDocument ||
    !isCurrentEditConfigReady;

  // 실시간 진행 상태
  const [progress, setProgress] = useState(0);

  // DB 연결 수정 관련 상태
  const [connectionId, setConnectionId] = useState<string>('');
  const [isEditingConnection, setIsEditingConnection] = useState(false);
  const [formKey, setFormKey] = useState(0); // 폼 강제 리셋용 키
  const [connectionDetails, setConnectionDetails] = useState<DBConfig | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);

  // 범위 선택 관련 상태
  const [selectionMode, setSelectionMode] = useState<
    'all' | 'range' | 'keyword'
  >('all');
  const [rangeStart, setRangeStart] = useState<string>('');
  const [rangeEnd, setRangeEnd] = useState<string>('');
  const [keywordFilter, setKeywordFilter] = useState<string>('');

  useEffect(() => {
    const syncActiveOrganization = () => {
      const nextOrganizationId = getStoredActiveOrganizationId();
      documentScopeRef.current = `${nextOrganizationId ?? 'no-organization'}:${kbId}:${documentId}`;
      if (activeOrganizationRef.current === nextOrganizationId) return;
      activeOrganizationRef.current = nextOrganizationId;
      fetchGeneration.current += 1;
      setActiveOrganizationId(nextOrganizationId);
    };

    syncActiveOrganization();
    window.addEventListener(
      ACTIVE_ORGANIZATION_CHANGED_EVENT,
      syncActiveOrganization,
    );
    return () => {
      window.removeEventListener(
        ACTIVE_ORGANIZATION_CHANGED_EVENT,
        syncActiveOrganization,
      );
    };
  }, [kbId, documentId]);

  // SSE 연결 (Indexing 상태일 때)
  useEffect(() => {
    if (!isActiveProcessingStatus(status) || !documentId) return;
    const streamScope = currentDocumentScope;
    let active = true;
    let url: string;
    try {
      url = knowledgeApi.getProgressUrl(documentId);
    } catch {
      toast.error('활성 조직을 선택한 뒤 문서 상태를 확인해 주세요.');
      return;
    }
    const eventSource = new EventSource(url, { withCredentials: true });
    eventSource.onmessage = (event) => {
      if (!active || documentScopeRef.current !== streamScope) return;
      try {
        const data = JSON.parse(event.data);
        if (data.error && data.status !== 'completed') {
          eventSource.close();
          setProgress(0);
          setStatus('failed');
          toast.error('문서 처리 중 오류가 발생했습니다.');
          return;
        }
        setProgress(data.progress);
        if (data.status === 'completed' || data.progress >= 100) {
          eventSource.close();
          setProgress(100);
          setStatus('completed');
          toast.success('문서 처리가 완료되었습니다!');
        }
        if (data.status === 'failed') {
          eventSource.close();
          setProgress(0);
          setStatus('failed');
          toast.error('문서 처리 중 오류가 발생했습니다.');
        }
      } catch {
        // 잘못된 진행 응답은 현재 문서 상태에 반영하지 않는다.
      }
    };
    eventSource.onerror = () => {
      if (!active || documentScopeRef.current !== streamScope) return;
      if (eventSource.readyState === EventSource.CLOSED) return;
      eventSource.close();
    };
    return () => {
      active = false;
      eventSource.close();
    };
  }, [currentDocumentScope, documentId, status]);
  // 초기 데이터 로드
  useEffect(() => {
    const generation = ++fetchGeneration.current;
    const requestScope = currentDocumentScope;
    let cancelled = false;
    const isStale = () =>
      cancelled ||
      fetchGeneration.current !== generation ||
      documentScopeRef.current !== requestScope;

    setIsLoading(true);
    setDocument(null);
    setKbName('');
    setStatus('');
    setErrorMessage(null);
    setProgress(0);
    setCanEditDocument(false);
    setPermissionScope(null);
    setIsEditConfigReady(false);
    setEditConfigScope(null);
    setApiConfigSummary(null);
    setChunkSize(1000);
    setChunkOverlap(200);
    setSegmentIdentifier('\\n\\n');
    setRemoveUrlsEmails(false);
    setRemoveWhitespace(true);
    setParsingStrategy('general');
    setChunkingMode('flat');
    setEnableAutoChunking(true);
    setSelectedDbItems({});
    setSensitiveColumns({});
    setAliases({});
    setTemplate('');
    setConnectionId('');
    setJoinConfig(null);
    setSelectionMode('all');
    setRangeStart('');
    setRangeEnd('');
    setKeywordFilter('');
    setIsEditingConnection(false);
    setConnectionDetails(null);
    setIsLoadingDetails(false);
    activeProcessingScope.current = null;

    const fetchDocument = async () => {
      try {
        // KB 정보와 문서 정보를 병렬로 조회
        const [kbData, targetDoc] = await Promise.all([
          knowledgeApi.getKnowledgeBase(kbId),
          knowledgeApi.getDocument(kbId, documentId),
        ]);
        if (isStale()) return;

        setKbName(kbData.name);
        const canWrite = kbData.can_write === true;
        setCanEditDocument(canWrite);
        setPermissionScope(requestScope);
        if (targetDoc) {
          setDocument(targetDoc);
          setStatus(targetDoc.status);
          const savedProgress =
            typeof targetDoc.meta_info?.progress === 'number'
              ? targetDoc.meta_info.progress
              : targetDoc.meta_info?.processing_progress;
          if (targetDoc.status === 'completed') {
            setProgress(100);
          } else if (targetDoc.status === 'failed') {
            setProgress(0);
          } else if (typeof savedProgress === 'number') {
            setProgress(savedProgress);
          }
          setErrorMessage(targetDoc.error_message || null); // [추가] 초기 에러 메시지 로드

          if (canWrite) {
            try {
              const editConfig = await knowledgeApi.getDocumentEditConfig(
                kbId,
                documentId,
              );
              if (isStale()) return;
              const sourceType = targetDoc.source_type || 'FILE';
              const hasRequiredSourceConfig =
                (sourceType !== 'DB' || Boolean(editConfig.db_config)) &&
                (sourceType !== 'API' || Boolean(editConfig.api_config));
              if (
                !editConfig.editable ||
                editConfig.source_type !== sourceType ||
                !hasRequiredSourceConfig
              ) {
                toast.warning(
                  '기존 문서 설정을 안전하게 복원할 수 없어 수정 작업을 차단했습니다.',
                );
                return;
              }

              setChunkSize(editConfig.chunk_size ?? 1000);
              setChunkOverlap(editConfig.chunk_overlap ?? 200);
              setSegmentIdentifier(editConfig.segment_identifier ?? '\\n\\n');
              setRemoveUrlsEmails(editConfig.remove_urls_emails ?? false);
              setRemoveWhitespace(editConfig.remove_whitespace ?? true);
              setParsingStrategy(editConfig.strategy ?? 'general');
              setChunkingMode(editConfig.chunking_mode ?? 'flat');
              setSelectionMode(editConfig.selection_mode ?? 'all');
              if (
                editConfig.selection_mode === 'range' &&
                editConfig.chunk_range
              ) {
                const [start, end] = editConfig.chunk_range.split('-', 2);
                setRangeStart(start);
                setRangeEnd(end);
              } else {
                setRangeStart('');
                setRangeEnd('');
              }
              setKeywordFilter(editConfig.keyword_filter ?? '');

              if (editConfig.db_config) {
                setSelectedDbItems(editConfig.db_config.selected_items);
                setSensitiveColumns(editConfig.db_config.sensitive_columns);
                setAliases(editConfig.db_config.aliases);
                setTemplate(editConfig.db_config.template ?? '');
                setConnectionId(editConfig.db_config.connection_id);
                setJoinConfig({
                  enabled: editConfig.db_config.join_config.enabled,
                  ...(editConfig.db_config.join_config.base_table
                    ? {
                        base_table:
                          editConfig.db_config.join_config.base_table,
                      }
                    : {}),
                  joins: editConfig.db_config.join_config.joins,
                });
              }
              setApiConfigSummary(editConfig.api_config ?? null);
              setIsEditConfigReady(true);
              setEditConfigScope(requestScope);
            } catch {
              if (isStale()) return;
              toast.warning(
                '기존 문서 설정을 불러오지 못해 수정 작업을 차단했습니다.',
              );
            }
          }
        } else {
          toast.error('문서를 찾을 수 없습니다.');
          router.push(`/dashboard/knowledge/${kbId}`);
        }
      } catch {
        if (isStale()) return;
        toast.error('문서 정보를 불러오는데 실패했습니다.');
      } finally {
        if (!isStale()) {
          setIsLoading(false);
        }
      }
    };
    if (kbId && documentId) {
      fetchDocument();
    }
    return () => {
      cancelled = true;
    };
  }, [kbId, documentId, router, currentDocumentScope]);
  // useDocumentProcess Hook 사용
  const {
    isAnalyzing,
    analyzingAction,
    isPreviewLoading,
    showCostConfirm,
    setShowCostConfirm,
    analyzeResult,
    setAnalyzeResult,
    setPendingAction,
    previewSegments,
    handleSaveClick,
    handlePreviewClick,
    handleConfirmCost,
  } = useDocumentProcess({
    kbId,
    documentId,
    requestScope: currentDocumentScope,
    isRequestScopeCurrent: (scope) => documentScopeRef.current === scope,
    document,
    setStatus,
    setProgress,
    canEditDocument: canEditCurrentDocument,
    editConfigReady: isCurrentEditConfigReady,
    settings: {
      chunkSize,
      chunkOverlap,
      segmentIdentifier,
      removeUrlsEmails,
      removeWhitespace,
      parsingStrategy,
      chunkingMode,
      selectedDbItems,
      sensitiveColumns,
      aliases,
      template,
      enableAutoChunking,
      joinConfig,
    },
    connectionId: connectionId,
    // 범위 선택
    selectionMode,
    rangeStart,
    rangeEnd,
    keywordFilter,
  });

  const handleStartProcessing = () => handleSaveClick();

  // DB 연결 저장 핸들러
  const handleConnectionRequest = async (
    config: DBConfig,
  ): Promise<Pick<ConnectionTestResult, 'success' | 'retryAfter'>> => {
    if (!canEditCurrentDocument || !isCurrentEditConfigReady) {
      toast.error('기존 문서 설정을 복원한 뒤 다시 시도해 주세요.');
      return { success: false };
    }
    try {
      const newConn = await connectorApi.createConnector(config);
      if (newConn.success && newConn.id) {
        setConnectionId(newConn.id); // ID 업데이트 -> 스키마 새로고침 트리거됨
        toast.success('DB 연결 정보가 업데이트되었습니다.');
        setIsEditingConnection(false); // 폼 닫기
        return { success: true };
      } else {
        toast.error(newConn.message || '연결 실패');
        return { success: false };
      }
    } catch {
      toast.error('DB 연결 정보 업데이트에 실패했습니다.');
      return { success: false };
    }
  };

  const handleEditConnection = async () => {
    if (!canEditCurrentDocument || !isCurrentEditConfigReady) {
      toast.error('기존 문서 설정을 복원한 뒤 다시 시도해 주세요.');
      return;
    }
    if (!connectionId) {
      toast.error('연결 ID가 없습니다.');
      return;
    }

    setIsLoadingDetails(true);
    try {
      const details = await connectorApi.getConnectionDetails(connectionId);
      setConnectionDetails(details);
      setIsEditingConnection(true);
      setFormKey((prev) => prev + 1);
    } catch {
      toast.error('연결 정보를 불러오는데 실패했습니다.');
    } finally {
      setIsLoadingDetails(false);
    }
  };

  // 상태 폴링
  useEffect(() => {
    let intervalId: NodeJS.Timeout | undefined;
    let cancelled = false;
    const pollingScope = currentDocumentScope;
    if (isActiveProcessingStatus(status) || status === 'waiting_for_approval') {
      intervalId = setInterval(async () => {
        try {
          const doc = await knowledgeApi.getDocument(kbId, documentId);
          if (cancelled || documentScopeRef.current !== pollingScope) return;
          setStatus(doc.status);

          if (doc.status === 'waiting_for_approval') {
            clearInterval(intervalId);
            if (doc.meta_info && doc.meta_info.cost_estimate) {
              setAnalyzeResult({
                cost_estimate: doc.meta_info.cost_estimate,
                filename: doc.filename,
                is_cached: false,
                recommended_strategy: 'llamaparse',
              });
              setPendingAction('save');
              setShowCostConfirm(true);
              toast.warning('정밀 파싱을 위해 추가 승인이 필요합니다.');
            }
            return;
          }

          if (doc.status === 'completed' || doc.status === 'failed') {
            clearInterval(intervalId);
            setProgress(doc.status === 'completed' ? 100 : 0);
            // completed/failed 모두 error_message 업데이트 (경고성 메시지 포함)
            setErrorMessage(doc.error_message || null);
            if (doc.status === 'failed') {
              toast.error(doc.error_message || '처리 실패');
            }
          }

          // SSE가 연결된 상태(indexing)에서는 Polling으로 진행률을 덮어쓰지 않음
          if (!isActiveProcessingStatus(doc.status) && doc.meta_info) {
            if (typeof doc.meta_info.progress === 'number') {
              setProgress(doc.meta_info.progress);
            } else if (typeof doc.meta_info.processing_progress === 'number') {
              setProgress(doc.meta_info.processing_progress);
            }
          }
        } catch {
          // 다음 polling tick 또는 SSE 재연결이 상태 확인을 재시도한다.
        }
      }, 2000);
    }
    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [
    status,
    kbId,
    documentId,
    currentDocumentScope,
    setAnalyzeResult,
    setPendingAction,
    setShowCostConfirm,
  ]);

  // 완료 시 자동 이동
  useEffect(() => {
    if (permissionScope !== currentDocumentScope) return;
    if (isActiveProcessingStatus(status)) {
      activeProcessingScope.current = currentDocumentScope;
      return;
    }
    if (
      activeProcessingScope.current === currentDocumentScope &&
      status === 'completed' &&
      progress >= 100
    ) {
      const redirectScope = currentDocumentScope;
      const timer = setTimeout(() => {
        if (documentScopeRef.current === redirectScope) {
          router.push(`/dashboard/knowledge/${kbId}`);
        }
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [
    status,
    progress,
    router,
    kbId,
    permissionScope,
    currentDocumentScope,
  ]);
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>

        {/* CSS import for next/link compatibility if needed, though usually automatic */}
      </div>
    );
  }

  // 벡터화 템플릿 입력 UI 렌더러 (우측 패널용)
  const renderTemplateSection = () => (
    <div className="flex-none h-[35%] border-b border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col">
      {/* 템플릿 헤더 (프리뷰 헤더와 통일) */}
      <div className="px-4 py-3 bg-gray-50 dark:bg-gray-700/30 border-b border-gray-200 dark:border-gray-600 flex justify-between items-center flex-none">
        <h4 className="font-medium text-gray-700 dark:text-gray-200 flex items-center gap-2">
          <Pencil className="w-4 h-4" />
          벡터화 템플릿 작성 (선택사항)
          <div className="relative group ml-1 flex items-center">
            <CircleHelp className="w-4 h-4 text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 cursor-help transition-colors" />
            <div className="absolute left-0 top-6 w-80 p-3 bg-gray-900/95 text-white text-xs rounded-lg shadow-xl backdrop-blur-sm z-50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none leading-relaxed border border-white/10">
              DB의 row 데이터를 하나의 완성된 문장으로 정의해 주세요.
              <br />잘 가공된 템플릿은 검색 효율을 높이고, AI가 더 똑똑하고
              자연스럽게 답변하는 밑거름이 됩니다.
            </div>
          </div>
        </h4>
      </div>

      <div className="p-4 bg-white dark:bg-gray-800 h-full flex flex-col overflow-y-auto">
        <ColumnAutocomplete
          selectedColumns={selectedDbItems}
          value={template}
          onChange={setTemplate}
          placeholder="예: {{mock_inventory.name}} 상품은 현재 {{mock_inventory.quantity}}개 남아있으며, 정상가는 {{mock_inventory.price}}원입니다."
          className="w-full flex-1 text-sm border border-gray-300 dark:border-gray-600 rounded px-3 py-2 bg-white dark:bg-gray-800 text-gray-900 dark:text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none font-mono overflow-y-auto"
        />
      </div>
    </div>
  );

  // 중앙 패널 렌더러
  const renderCenterPanel = () => {
    if (!document) return null;
    if (
      (document.source_type === 'DB' || document.source_type === 'API') &&
      !isCurrentEditConfigReady
    ) {
      return (
        <div className="flex h-full items-center justify-center p-6 text-center text-sm text-gray-600 dark:text-gray-300">
          이 소스의 설정은 수정 권한과 안전한 설정 복원이 확인된 경우에만
          표시됩니다.
        </div>
      );
    }
    switch (document.source_type) {
      case 'DB':
        return (
          <div className="flex flex-col h-full">
            {isEditingConnection ? (
              <div className="flex-1 overflow-y-auto px-1 py-2 pb-20">
                <div className="bg-white p-4 rounded-lg border border-gray-200 shadow-sm mb-4">
                  <div className="flex justify-between items-center mb-4">
                    <h4 className="text-sm font-semibold text-gray-900">
                      DB 연결 정보 수정
                    </h4>
                    <button
                      onClick={() => {
                        setIsEditingConnection(false);
                        setConnectionDetails(null); // 상세 정보 초기화
                      }}
                      className="text-gray-500 hover:text-gray-700 text-xs"
                    >
                      취소
                    </button>
                  </div>
                  <DBConnectionForm
                    key={formKey} // 폼 초기화
                    onChange={() => {}}
                    onTestConnection={handleConnectionRequest}
                    initialConfig={connectionDetails ?? undefined}
                  />
                </div>
              </div>
            ) : (
              <div className="flex-1 min-h-0 relative">
                <div className="absolute inset-0 overflow-y-auto px-1">
                  <DbSourceViewer
                    connectionId={connectionId} // 업데이트된 ID 사용
                    selectedDbItems={selectedDbItems}
                    onChange={setSelectedDbItems}
                    sensitiveColumns={sensitiveColumns}
                    onSensitiveColumnsChange={setSensitiveColumns}
                    aliases={aliases}
                    onAliasesChange={setAliases}
                    onEditConnection={handleEditConnection}
                    isEditingLoading={isLoadingDetails}
                    enableAutoChunking={enableAutoChunking}
                    onEnableAutoChunkingChange={setEnableAutoChunking}
                    onJoinConfigChange={setJoinConfig}
                  />
                </div>
              </div>
            )}

            {/* 템플릿 UI 제거됨 (우측 패널로 이동) */}
          </div>
        );
      case 'API':
        return <ApiSourceViewer apiConfig={apiConfigSummary} />;
      default: // FILE
        return (
          <FileSourceViewer
            kbId={kbId}
            documentId={documentId}
            filename={document?.filename}
          />
        );
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="flex-none bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        {/* Breadcrumb - Top Bar */}
        <div className="px-6 py-2 border-b border-gray-50 dark:border-gray-700/50 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <Link
            href="/dashboard"
            className="hover:text-blue-600 flex items-center gap-1"
          >
            <Home className="w-3 h-3" />
            <span>홈</span>
          </Link>
          <ChevronRight className="w-3 h-3 text-gray-300" />
          <Link href="/dashboard/knowledge" className="hover:text-blue-600">
            지식 관리
          </Link>
          <ChevronRight className="w-3 h-3 text-gray-300" />
          <Link
            href={`/dashboard/knowledge/${kbId}`}
            className="hover:text-blue-600 max-w-[150px] truncate"
            title={kbName}
          >
            {kbName || '지식 베이스'}
          </Link>
          <ChevronRight className="w-3 h-3 text-gray-300" />
          <span
            className="text-gray-900 dark:text-white font-medium max-w-[150px] truncate"
            title={
              document?.filename
                ? getDisplayFilename(document.filename)
                : undefined
            }
          >
            {document?.filename
              ? getDisplayFilename(document.filename)
              : '문서'}
          </span>
        </div>

        {/* Main Title Area */}
        <div className="px-6 py-5 flex items-start justify-between">
          <div className="flex items-start gap-4">
            <div
              className={`p-3 rounded-xl mt-1 ${
                document?.source_type === 'DB'
                  ? 'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400'
                  : 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400'
              }`}
            >
              {document?.source_type === 'DB' ? (
                <Database className="w-8 h-8" />
              ) : (
                <FileText className="w-8 h-8" />
              )}
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-2 leading-tight">
                {document?.filename
                  ? getDisplayFilename(document.filename)
                  : '문서 설정'}
              </h1>

              {/* Metadata Badges */}
              <div className="flex items-center gap-3 text-sm">
                <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-md bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 text-xs font-medium">
                  {document?.source_type === 'API' && (
                    <>
                      <RefreshCw className="w-3 h-3" /> API Source
                    </>
                  )}
                  {document?.source_type === 'DB' && (
                    <>
                      <Database className="w-3 h-3" /> Database Source
                    </>
                  )}
                  {(!document?.source_type ||
                    document?.source_type === 'FILE') && (
                    <>
                      <FileText className="w-3 h-3" /> File Source
                    </>
                  )}
                </div>
                {document?.created_at && (
                  <div className="flex items-center gap-1.5 text-gray-500 dark:text-gray-400 text-xs">
                    <Calendar className="w-3 h-3" />
                    {new Date(document.created_at).toLocaleDateString('ko-KR')}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* 에러/경고 메시지 */}
            {(status === 'failed' || errorMessage) && (
              <div className="relative group mr-2 cursor-help flex items-center">
                <div
                  className={`px-3 py-2 text-sm rounded-lg border flex items-center gap-2 ${
                    status === 'failed'
                      ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800'
                      : 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-800'
                  }`}
                >
                  <AlertTriangle className="w-4 h-4" />
                  <span className="font-medium max-w-[200px] truncate">
                    {errorMessage || '처리 실패'}
                  </span>
                </div>
                <div className="absolute top-full right-0 mt-2 w-max max-w-[400px] p-3 bg-gray-900 text-white text-xs rounded-lg shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
                  {errorMessage || '처리 실패'}
                </div>
              </div>
            )}

            {/* 진행률 표시 */}
            {isActiveProcessingStatus(status) && (
              <div className="flex flex-col items-end mr-4 min-w-[120px]">
                <div className="flex items-center gap-2 mb-1">
                  <Loader2 className="w-3 h-3 animate-spin text-blue-600" />
                  <span className="text-blue-600 font-bold text-sm tracking-tight">
                    {Math.round(progress)}%
                  </span>
                </div>
                <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-600 transition-all duration-300"
                    style={{ width: `${Math.round(progress)}%` }}
                  />
                </div>
              </div>
            )}

            <button
              onClick={handleStartProcessing}
              disabled={
                isEditActionDisabled ||
                isAnalyzing ||
                status === 'completed' ||
                isActiveProcessingStatus(status)
              }
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl transition-all shadow-sm hover:shadow-md disabled:opacity-50 disabled:cursor-not-allowed font-medium"
            >
              {analyzingAction === 'save' || isActiveProcessingStatus(status) ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {isActiveProcessingStatus(status)
                ? '처리 중...'
                : status === 'pending'
                  ? '처리 시작'
                  : status === 'completed'
                    ? '처리 완료됨'
                    : '처리 시작'}
            </button>
          </div>
        </div>
      </header>
      {/* Main Layout (3 Columns) */}
      <div className="flex-1 flex overflow-hidden">
        {/* 1. Left Panel: Settings - DB가 아닐 때만 표시 */}
        {document?.source_type !== 'DB' && (
          <div className="w-80 relative bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
            {/* 스크롤 가능한 콘텐츠 영역 */}
            <div className="flex-1 overflow-y-auto p-6 pb-24">
              {/* FILE일 때만 파싱 전략 노출 */}
              {(document?.source_type === 'FILE' || !document?.source_type) && (
                <ParsingStrategySettings
                  strategy={parsingStrategy}
                  setStrategy={setParsingStrategy}
                  hasKey={hasLlamaParseKey}
                />
              )}
              <CommonChunkSettings
                chunkSize={chunkSize}
                setChunkSize={setChunkSize}
                chunkOverlap={chunkOverlap}
                setChunkOverlap={setChunkOverlap}
                segmentIdentifier={segmentIdentifier}
                setSegmentIdentifier={setSegmentIdentifier}
                removeWhitespace={removeWhitespace}
                setRemoveWhitespace={setRemoveWhitespace}
                removeUrlsEmails={removeUrlsEmails}
                setRemoveUrlsEmails={setRemoveUrlsEmails}
              />

              {/* 범위 선택 UI */}
              <div className="mt-6">
                <div className="flex items-center gap-2 text-gray-900 dark:text-white font-medium py-1.5 px-3 -mx-3 bg-gray-50 dark:bg-gray-700/30 rounded-lg text-sm mb-3">
                  <ListFilter className="w-4 h-4" />
                  <h4>청크 선택 범위</h4>
                </div>

                {/* 모드 선택 라디오 버튼 */}
                <div className="space-y-2 mb-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="all"
                      checked={selectionMode === 'all'}
                      onChange={(e) => setSelectionMode(e.target.value as any)}
                      className="w-4 h-4 text-indigo-600"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">
                      전체 선택 (기본)
                    </span>
                  </label>

                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="range"
                      checked={selectionMode === 'range'}
                      onChange={(e) => setSelectionMode(e.target.value as any)}
                      className="w-4 h-4 text-indigo-600"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">
                      청크 범위 지정
                    </span>
                  </label>

                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="keyword"
                      checked={selectionMode === 'keyword'}
                      onChange={(e) => setSelectionMode(e.target.value as any)}
                      className="w-4 h-4 text-indigo-600"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">
                      키워드 검색
                    </span>
                  </label>
                </div>

                {/* 조건부 입력 폼 */}
                {selectionMode === 'range' && (
                  <div>
                    <label className="block text-xs text-gray-600 dark:text-gray-400 mb-2">
                      처리할 청크 번호 범위
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        value={rangeStart}
                        onChange={(e) => setRangeStart(e.target.value)}
                        onKeyDown={(e) => {
                          if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                            e.preventDefault();
                          }
                        }}
                        placeholder="시작"
                        min={1}
                        className="w-20 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm text-center"
                      />
                      <span className="text-gray-400">~</span>
                      <input
                        type="number"
                        value={rangeEnd}
                        onChange={(e) => setRangeEnd(e.target.value)}
                        onKeyDown={(e) => {
                          if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                            e.preventDefault();
                          }
                        }}
                        placeholder="끝"
                        min={1}
                        className="w-20 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm text-center"
                      />
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      예: 1번부터 100번까지만 처리
                    </p>
                  </div>
                )}

                {selectionMode === 'keyword' && (
                  <div>
                    <input
                      type="text"
                      value={keywordFilter}
                      onChange={(e) => setKeywordFilter(e.target.value)}
                      placeholder="검색할 키워드 입력"
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      대소문자 구분 없이 검색
                    </p>
                  </div>
                )}
              </div>
            </div>
            {/* 하단 고정 버튼 영역 */}
            <div className="absolute bottom-0 left-0 right-0 z-10 p-4 bg-gradient-to-t from-white via-white/80 to-transparent dark:from-gray-800 dark:via-gray-800/80 dark:to-transparent">
              <button
                onClick={handlePreviewClick}
                disabled={
                  isEditActionDisabled || isPreviewLoading || isAnalyzing
                }
                className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed font-medium"
              >
                {isPreviewLoading || analyzingAction === 'preview' ? (
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                결과 미리보기
              </button>
            </div>
          </div>
        )}
        {/* 2. Center Panel: Original Document View */}
        <div className="flex-1 bg-gray-100 dark:bg-gray-900/50 overflow-hidden flex flex-col border-r border-gray-200 dark:border-gray-700">
          <div className="px-6 py-3 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
            <h3 className="font-medium text-gray-700 dark:text-gray-200 flex items-center gap-2">
              <ListTodo className="w-4 h-4" />
              {document?.source_type === 'API'
                ? 'API 데이터 원본 확인'
                : document?.source_type === 'DB'
                  ? '테이블 및 컬럼 선택'
                  : '원본 문서 확인'}
            </h3>
          </div>
          <div className="flex-1 w-full h-full p-4">{renderCenterPanel()}</div>
        </div>
        {/* 3. Right Panel: Preview Results */}
        <div className="flex-1 min-w-0 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 overflow-hidden">
          {document?.source_type === 'DB' ? (
            <div className="flex flex-col h-full">
              {renderTemplateSection()}
              <div className="flex-1 min-h-0 overflow-hidden relative">
                <div className="absolute inset-0">
                  <ChunkPreviewList
                    previewSegments={previewSegments}
                    isLoading={isPreviewLoading}
                    headerButton={
                      <button
                        onClick={handlePreviewClick}
                        disabled={
                          isEditActionDisabled ||
                          isPreviewLoading ||
                          isAnalyzing
                        }
                        className="px-3 py-1.5 text-xs font-medium bg-white border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-gray-700 dark:text-gray-300 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                      >
                        {isPreviewLoading || analyzingAction === 'preview' ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="w-3.5 h-3.5" />
                        )}
                        {isPreviewLoading ? '분석 중...' : '미리보기'}
                      </button>
                    }
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col h-full">
              {parsingStrategy === 'llamaparse' && (
                <div className="flex-none px-4 py-3 bg-amber-50 border-b border-amber-100 flex items-start gap-3">
                  <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                  <div className="text-xs text-amber-800 leading-relaxed">
                    <p className="font-semibold mb-0.5">미리보기 제한 안내</p>
                    <p>
                      빠른 속도를 위해{' '}
                      <span className="font-bold underline">첫 5페이지</span>만
                      분석하여 보여줍니다.
                      <br />
                      실제 처리(저장) 시에는 전체 문서가 정상적으로
                      인덱싱됩니다.
                    </p>
                  </div>
                </div>
              )}
              <div className="flex-1 min-h-0">
                <ChunkPreviewList
                  previewSegments={previewSegments}
                  isLoading={isPreviewLoading}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 비용 승인 모달 */}
      {showCostConfirm && analyzeResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg p-6 max-w-md w-full mx-4 shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              💰 비용 승인 필요
            </h3>
            <p className="text-gray-600 dark:text-gray-300 mb-6">
              이 기능은 유료 기능입니다.
              <br />
              <span className="block mt-2 p-3 bg-gray-50 dark:bg-gray-700 rounded border border-gray-200 dark:border-gray-600 text-sm">
                파일: <strong>{analyzeResult.filename.substring(37)}</strong>
                <br />
                예상 결제 포인트:{' '}
                <strong className="text-amber-600">
                  {analyzeResult.cost_estimate.credits} P
                </strong>
              </span>
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowCostConfirm(false)}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700 rounded-lg transition-colors"
              >
                취소
              </button>
              <button
                onClick={handleConfirmCost}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium shadow-sm"
              >
                승인 및 진행
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
