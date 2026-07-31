import {
  IngestionResponse,
  KnowledgeCreateRequest,
  KnowledgeBaseCreate,
  KnowledgeBaseResponse,
  KnowledgeBaseDetailResponse,
  KnowledgeSafeMetadataResponse,
  DocumentResponse,
  DocumentEditConfigResponse,
  SourceType,
  KnowledgeCollectionAction,
  KnowledgeCollectionRoleBundle,
  KnowledgeCollectionResponse,
  KnowledgeCollectionListResponse,
  KnowledgeCollectionLatestSyncJobResponse,
  KnowledgeCollectionSyncJobResponse,
  KnowledgeCollectionSyncRequestResponse,
  KnowledgeCollectionLLMSelectableItem,
  KnowledgeCollectionLLMSelectableResponse,
  KnowledgeCollectionItemResponse,
  KnowledgeCollectionItemsResponse,
  KnowledgeCollectionLinkCandidate,
  KnowledgeCollectionLinkCandidatesResponse,
  KnowledgeCollectionPermissionResponse,
  KnowledgeCollectionPermissionsResponse,
  KnowledgeCollectionPermissionBulkBundleResponse,
  KnowledgeCollectionVisibility,
  KnowledgeCollectionVisibilityResponse,
  KnowledgeDelegationSubjectsResponse,
  KnowledgeDomainCapabilitiesResponse,
  KnowledgeDomainAction,
  KnowledgeDomainPermissionListResponse,
} from '../types/Knowledge';

export interface JoinConfig {
  enabled: boolean;
  base_table?: string;
  joins?: Array<{
    from_table: string;
    to_table: string;
    from_column: string;
    to_column: string;
  }>;
}

export interface DocumentPreviewRequest {
  chunk_size: number;
  chunk_overlap: number;
  segment_identifier: string;
  remove_urls_emails?: boolean;
  remove_whitespace?: boolean;
  source_type: SourceType;
  strategy?: 'general' | 'llamaparse';
  chunking_mode?: 'flat' | 'hierarchical';
  db_config?: {
    selections: {
      table_name: string;
      columns: string[];
      sensitive_columns?: string[];
    }[];
    selected_items?: Record<string, string[]>;
    sensitive_columns?: Record<string, string[]>;
    aliases?: Record<string, Record<string, string>>;
    template?: string;
    connection_id?: string;
    join_config?: JoinConfig;
  } | null;
  // [추가] 필터링 파라미터
  selection_mode?: 'all' | 'range' | 'keyword';
  chunk_range?: string;
  keyword_filter?: string;
  enable_auto_chunking?: boolean;
}

export interface DocumentSegment {
  created_at: string;
  updated_at?: string;
  content: string;
  token_count: number;
  char_count: number;
}

export interface DocumentPreviewResponse {
  segments: DocumentSegment[];
  total_count: number;
  preview_text_sample: string;
}

export interface AnalyzeResponse {
  filename: string;
  cost_estimate: {
    pages: number;
    credits: number;
    cost_usd: number;
  };
  recommended_strategy: string;
  is_cached: boolean;
}

export interface LLMModelOption {
  id: string;
  model_id_for_api_call: string;
  name: string;
  type: string;
  provider_name: string;
  context_window: number;
  is_active: boolean;
}

export interface LLMCredentialOption {
  id: string;
  provider_id: string;
  organization_id?: string | null;
  credential_name: string;
  config_preview?: string | null;
  is_valid: boolean;
}

export interface LLMAgentAnswerOption {
  model: LLMModelOption;
  credential: LLMCredentialOption;
  provider_name: string;
  relation_priority: number;
}

export interface RAGAgentCitation {
  citation_id: string;
  document_id: string;
  chunk_id?: string | null;
  rank: number;
  score?: number | null;
  filename?: string | null;
  heading?: string | null;
  hierarchy_path?: string[] | null;
  metadata_summary: Record<string, unknown>;
  content_preview?: string | null;
}

export interface RAGAgentAnswerResponse {
  answer_run_id: string;
  correlation_id: string;
  status: string;
  answer: string;
  citations: RAGAgentCitation[];
  retrieval_summary: {
    knowledge_base_id: string;
    hierarchy_mode: 'auto' | 'flat' | 'parent_child';
    retrieved_chunk_count: number;
    document_ids: string[];
    citation_ids: string[];
    score_summary: Record<string, unknown>;
    latency_ms: number;
    raw_content_returned: boolean;
  };
  usage_summary: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    total_cost: number;
    latency_ms: number;
    model_name?: string | null;
    provider?: string | null;
  };
  policy_result: Record<string, unknown>;
}

export interface RAGAgentStreamEvent {
  event: string;
  data: Record<string, unknown>;
}

// 외부(page.tsx, ..)에서 이 API 모듈을 통해 타입을 직접 import 할 수 있도록 내보냅니다.
export type {
  IngestionResponse,
  KnowledgeCreateRequest,
  KnowledgeBaseCreate,
  KnowledgeBaseResponse,
  KnowledgeBaseDetailResponse,
  KnowledgeCollectionAction,
  KnowledgeCollectionRoleBundle,
  KnowledgeCollectionResponse,
  KnowledgeCollectionListResponse,
  KnowledgeCollectionLatestSyncJobResponse,
  KnowledgeCollectionSyncJobResponse,
  KnowledgeCollectionSyncRequestResponse,
  KnowledgeCollectionLLMSelectableItem,
  KnowledgeCollectionLLMSelectableResponse,
  KnowledgeCollectionItemResponse,
  KnowledgeCollectionItemsResponse,
  KnowledgeCollectionLinkCandidate,
  KnowledgeCollectionLinkCandidatesResponse,
  KnowledgeCollectionPermissionResponse,
  KnowledgeCollectionPermissionsResponse,
  KnowledgeCollectionPermissionBulkBundleResponse,
  KnowledgeCollectionVisibility,
  KnowledgeCollectionVisibilityResponse,
  KnowledgeDelegationSubjectsResponse,
  KnowledgeDomainCapabilitiesResponse,
  KnowledgeDomainAction,
  KnowledgeDomainPermissionListResponse,
};

import {
  activeOrganizationHeaders,
  getStoredActiveOrganizationId,
} from '@/lib/activeOrganization';
import { apiBaseUrl, apiClient } from '@/lib/apiClient';
import { csrfFetch } from '@/lib/csrfToken';

const API_BASE_URL = apiBaseUrl;

// 공통 API 클라이언트 사용
const api = apiClient;

const getHttpStatus = (error: unknown): number | undefined => {
  if (typeof error !== 'object' || error === null) return undefined;
  const response = (error as { response?: { status?: unknown } }).response;
  return typeof response?.status === 'number' ? response.status : undefined;
};

const logKnowledgeApiFailure = (operation: string, error: unknown) => {
  console.warn('[knowledgeApi] request failed', {
    operation,
    status: getHttpStatus(error),
  });
};

const parseSseEvent = (rawEvent: string): RAGAgentStreamEvent | null => {
  const lines = rawEvent.split(/\r?\n/);
  const eventLine = lines.find((line) => line.startsWith('event: '));
  const dataLines = lines.filter((line) => line.startsWith('data: '));
  if (!eventLine || dataLines.length === 0) return null;
  return {
    event: eventLine.slice('event: '.length).trim(),
    data: JSON.parse(
      dataLines.map((line) => line.slice('data: '.length)).join(''),
    ),
  };
};

export const knowledgeApi = {
  // [NEW] S3 Presigned URL 요청
  getPresignedUploadUrl: async (
    filename: string,
    contentType: string,
    knowledgeBaseId?: string,
  ): Promise<{
    upload_url: string;
    s3_key: string;
    method: string;
    use_backend_proxy?: boolean;
  }> => {
    const response = await api.post('/rag/upload/presigned-url', {
      filename,
      content_type: contentType,
      ...(knowledgeBaseId ? { knowledgeBaseId } : {}),
    });
    return response.data;
  },

  // S3 직접 업로드
  uploadToS3: async (
    presignedUrl: string,
    file: File,
    contentType: string,
    // onProgress?: (progress: number) => void,
  ): Promise<void> => {
    // fetch API 사용 (axios는 CORS preflight 이슈 가능성)
    const response = await fetch(presignedUrl, {
      method: 'PUT',
      headers: {
        'Content-Type': contentType,
      },
      body: file,
    });

    if (!response.ok) {
      throw new Error(
        `S3 upload failed: ${response.status} ${response.statusText}`,
      );
    }
  },

  // 지식 베이스 생성 (빈 KB)
  createKnowledgeBase: async (
    data: KnowledgeBaseCreate,
  ): Promise<KnowledgeBaseResponse> => {
    const response = await api.post('/knowledge', data);
    return response.data;
  },

  // 지식 생성 및 파일 업로드
  uploadKnowledgeBase: async (
    data: KnowledgeCreateRequest,
  ): Promise<IngestionResponse> => {
    const formData = new FormData();

    // [NEW] S3 직접 업로드 정보 (있으면 추가)
    if (data.s3FileUrl) formData.append('s3FileUrl', data.s3FileUrl);
    if (data.s3FileKey) formData.append('s3FileKey', data.s3FileKey);

    // [기존] 파일 직접 전송
    if (data.file) formData.append('file', data.file);

    if (data.sourceType) formData.append('sourceType', data.sourceType);
    if (data.apiUrl) formData.append('apiUrl', data.apiUrl);
    if (data.apiMethod) formData.append('apiMethod', data.apiMethod);
    if (data.apiHeaders) formData.append('apiHeaders', data.apiHeaders);
    if (data.apiBody) formData.append('apiBody', data.apiBody);
    if (data.connectionId) formData.append('connectionId', data.connectionId);
    if (data.name) formData.append('name', data.name);
    if (data.description) formData.append('description', data.description);
    formData.append('embeddingModel', data.embeddingModel);
    formData.append('topK', data.topK.toString());
    formData.append('similarity', data.similarity.toString());
    formData.append('chunkSize', data.chunkSize.toString());
    formData.append('chunkOverlap', data.chunkOverlap.toString());
    if (data.knowledgeBaseId)
      formData.append('knowledgeBaseId', data.knowledgeBaseId);
    try {
      const response = await api.post('/rag/upload', formData);
      return response.data;
    } catch (error) {
      logKnowledgeApiFailure('uploadKnowledgeBase', error);
      throw error;
    }
  },

  // 지식 목록 조회
  getKnowledgeBases: async (): Promise<KnowledgeBaseResponse[]> => {
    try {
      const response = await api.get('/knowledge');
      return response.data;
    } catch (error) {
      logKnowledgeApiFailure('getKnowledgeBases', error);
      throw error;
    }
  },

  // LLM 노드 RAG picker에서 선택 가능한 지식 목록 조회
  getLLMSelectableKnowledgeBases: async (): Promise<
    KnowledgeBaseDetailResponse[]
  > => {
    try {
      const response = await api.get('/knowledge/llm-selectable');
      return response.data;
    } catch (error) {
      logKnowledgeApiFailure('getLLMSelectableKnowledgeBases', error);
      throw error;
    }
  },

  // LLM 노드에서 route 권한으로 선택 가능한 Collection의 최소 projection 조회
  getLLMSelectableKnowledgeCollections:
    async (): Promise<KnowledgeCollectionLLMSelectableResponse> => {
      try {
        const response = await api.get('/knowledge/llm-selectable-collections');
        return response.data;
      } catch (error) {
        logKnowledgeApiFailure('getLLMSelectableKnowledgeCollections', error);
        throw error;
      }
    },

  // 지식 상세 조회
  getKnowledgeBase: async (
    id: string,
  ): Promise<KnowledgeBaseDetailResponse> => {
    const response = await api.get(`/knowledge/${id}`);
    return response.data;
  },

  // 단일 문서 상세 조회
  getDocument: async (
    kbId: string,
    documentId: string,
  ): Promise<DocumentResponse> => {
    const response = await api.get(
      `/knowledge/${kbId}/documents/${documentId}`,
    );
    return response.data;
  },

  getDocumentContent: async (
    kbId: string,
    documentId: string,
    signal?: AbortSignal,
  ): Promise<Blob> => {
    const organizationId = getStoredActiveOrganizationId();
    if (!organizationId) {
      throw new Error('Active organization is required for document content.');
    }

    const response = await api.get<Blob>(
      `/knowledge/${kbId}/documents/${documentId}/content`,
      {
        headers: activeOrganizationHeaders(organizationId),
        responseType: 'blob',
        signal,
      },
    );
    return response.data;
  },

  getDocumentEditConfig: async (
    kbId: string,
    documentId: string,
  ): Promise<DocumentEditConfigResponse> => {
    const response = await api.get(
      `/knowledge/${kbId}/documents/${documentId}/edit-config`,
    );
    return response.data;
  },

  // 지식 수정, 재인덱싱
  updateKnowledgeBase: async (
    id: string,
    data: {
      name?: string;
      description?: string;
      embedding_model?: string;
    },
  ): Promise<KnowledgeBaseResponse> => {
    const response = await api.patch(`/knowledge/${id}`, data);
    return response.data;
  },

  updateKnowledgeSafeMetadata: async (
    id: string,
    data: {
      safe_label?: string;
      kb_safe_description?: string;
      kb_safe_topics?: string[];
    },
  ): Promise<KnowledgeSafeMetadataResponse> => {
    const response = await api.patch(`/knowledge/${id}/safe-metadata`, data);
    return response.data;
  },

  archiveKnowledgeBase: async (id: string): Promise<void> => {
    await api.post(`/knowledge/${id}/archive`);
  },

  restoreKnowledgeBase: async (id: string): Promise<void> => {
    await api.post(`/knowledge/${id}/restore`);
  },

  hardDeleteKnowledgeBase: async (id: string): Promise<void> => {
    await api.delete(`/knowledge/${id}`, {
      params: { acknowledged_hard_delete: true },
    });
  },

  getKnowledgeCollectionsResponse: async (params?: {
    lifecycle_state?: 'active' | 'archived' | 'deleted';
    visibility?: KnowledgeCollectionVisibility;
    system_managed?: boolean;
    limit?: number;
  }): Promise<KnowledgeCollectionListResponse> => {
    const response = await api.get<KnowledgeCollectionListResponse>(
      '/knowledge/collections',
      { params },
    );
    return response.data;
  },

  getKnowledgeCollections: async (params?: {
    lifecycle_state?: 'active' | 'archived' | 'deleted';
    visibility?: KnowledgeCollectionVisibility;
    system_managed?: boolean;
    limit?: number;
  }): Promise<KnowledgeCollectionResponse[]> => {
    const response = await knowledgeApi.getKnowledgeCollectionsResponse(params);
    return response.collections;
  },

  createKnowledgeCollection: async (data: {
    name: string;
    description?: string | null;
    safe_metadata?: Record<string, unknown>;
  }): Promise<KnowledgeCollectionResponse> => {
    const response = await api.post('/knowledge/collections', data);
    return response.data;
  },

  requestKnowledgeCollectionSync: async (
    id: string,
    idempotencyKey: string,
  ): Promise<KnowledgeCollectionSyncRequestResponse> => {
    const response = await api.post(
      `/knowledge/collections/${id}/sync-jobs`,
      {},
      { headers: { 'Idempotency-Key': idempotencyKey } },
    );
    return response.data;
  },

  getLatestKnowledgeCollectionSyncJob: async (
    id: string,
  ): Promise<KnowledgeCollectionLatestSyncJobResponse> => {
    const response = await api.get(
      `/knowledge/collections/${id}/sync-jobs/latest`,
    );
    return response.data;
  },

  getKnowledgeCollectionSyncJob: async (
    id: string,
    jobId: string,
  ): Promise<KnowledgeCollectionSyncJobResponse> => {
    const response = await api.get(
      `/knowledge/collections/${id}/sync-jobs/${jobId}`,
    );
    return response.data;
  },

  getKnowledgeDomainCapabilities:
    async (): Promise<KnowledgeDomainCapabilitiesResponse> => {
      const response = await api.get('/knowledge/domain-capabilities');
      return response.data;
    },

  getKnowledgeDomainPermissions:
    async (): Promise<KnowledgeDomainPermissionListResponse> => {
      const response = await api.get('/knowledge/domain-permissions');
      return response.data;
    },

  getKnowledgeDomainDelegationSubjects: async (params: {
    subject_type: 'team' | 'user';
    query?: string;
    cursor?: string;
    limit?: number;
  }): Promise<KnowledgeDelegationSubjectsResponse> => {
    const response = await api.get('/knowledge/domain-delegation-subjects', {
      params,
    });
    return response.data;
  },

  grantKnowledgeDomainPermission: async (data: {
    subject_type: 'team' | 'user';
    subject_id: string;
    permission_action: KnowledgeDomainAction;
    expires_at?: string | null;
  }): Promise<void> => {
    const subjectPath = data.subject_type === 'team' ? 'teams' : 'users';
    await api.put(
      `/knowledge/domain-permissions/${subjectPath}/${data.subject_id}/${data.permission_action}`,
      { expires_at: data.expires_at ?? null },
    );
  },

  revokeKnowledgeDomainPermission: async (data: {
    subject_type: 'team' | 'user';
    subject_id: string;
    permission_action: KnowledgeDomainAction;
  }): Promise<void> => {
    const subjectPath = data.subject_type === 'team' ? 'teams' : 'users';
    await api.delete(
      `/knowledge/domain-permissions/${subjectPath}/${data.subject_id}/${data.permission_action}`,
    );
  },

  updateKnowledgeCollection: async (
    id: string,
    data: {
      name?: string;
      description?: string | null;
      safe_metadata?: Record<string, unknown>;
    },
  ): Promise<KnowledgeCollectionResponse> => {
    const response = await api.patch(`/knowledge/collections/${id}`, data);
    return response.data;
  },

  archiveKnowledgeCollection: async (id: string): Promise<void> => {
    await api.delete(`/knowledge/collections/${id}`);
  },

  restoreKnowledgeCollection: async (id: string): Promise<void> => {
    await api.post(`/knowledge/collections/${id}/restore`);
  },

  getKnowledgeCollectionItems: async (
    id: string,
  ): Promise<KnowledgeCollectionItemsResponse> => {
    const response = await api.get(`/knowledge/collections/${id}/items`);
    return response.data;
  },

  linkKnowledgeCollectionItem: async (
    id: string,
    data: {
      knowledge_base_id: string;
      acknowledged_public_runtime_exposure?: boolean;
    },
  ): Promise<KnowledgeCollectionItemsResponse> => {
    const response = await api.post(`/knowledge/collections/${id}/items`, data);
    return response.data;
  },

  unlinkKnowledgeCollectionItem: async (
    id: string,
    itemId: string,
    acknowledgedPublicRuntimeExposure = false,
  ): Promise<void> => {
    await api.delete(`/knowledge/collections/${id}/items/${itemId}`, {
      params: {
        acknowledged_public_runtime_exposure: acknowledgedPublicRuntimeExposure,
      },
    });
  },

  reorderKnowledgeCollectionItems: async (
    id: string,
    items: { item_id: string; rank: number }[],
    expectedOrderRevision: string,
    acknowledgedPublicRuntimeExposure = false,
  ): Promise<KnowledgeCollectionItemsResponse> => {
    const response = await api.patch(
      `/knowledge/collections/${id}/items/reorder`,
      {
        items,
        expected_order_revision: expectedOrderRevision,
        acknowledged_public_runtime_exposure: acknowledgedPublicRuntimeExposure,
      },
    );
    return response.data;
  },

  getKnowledgeCollectionLinkCandidates: async (
    id: string,
  ): Promise<KnowledgeCollectionLinkCandidatesResponse> => {
    const response = await api.get(
      `/knowledge/collections/${id}/link-candidates`,
    );
    return response.data;
  },

  getKnowledgeCollectionPermissions: async (
    id: string,
  ): Promise<KnowledgeCollectionPermissionsResponse> => {
    const response = await api.get(`/knowledge/collections/${id}/permissions`);
    return response.data;
  },

  getKnowledgeCollectionDelegationSubjects: async (
    id: string,
    params: {
      subject_type: 'team' | 'user';
      query?: string;
      cursor?: string;
      limit?: number;
    },
  ): Promise<KnowledgeDelegationSubjectsResponse> => {
    const response = await api.get(
      `/knowledge/collections/${id}/delegation-subjects`,
      { params },
    );
    return response.data;
  },

  grantKnowledgeCollectionPermission: async (
    id: string,
    data: {
      subject_type: 'team' | 'user';
      subject_id: string;
      permission_action: KnowledgeCollectionAction;
    },
  ): Promise<KnowledgeCollectionPermissionsResponse> => {
    const response = await api.post(
      `/knowledge/collections/${id}/permissions`,
      data,
    );
    return response.data;
  },

  grantKnowledgeCollectionPermissionBundle: async (
    id: string,
    data: {
      subject_type: 'team' | 'user';
      subject_id: string;
      role_bundle: KnowledgeCollectionRoleBundle;
    },
  ): Promise<KnowledgeCollectionPermissionsResponse> => {
    const response = await api.post(
      `/knowledge/collections/${id}/permissions/bundles`,
      data,
    );
    return response.data;
  },

  revokeKnowledgeCollectionPermissionBundle: async (
    id: string,
    data: {
      subject_type: 'team' | 'user';
      subject_id: string;
      role_bundle: KnowledgeCollectionRoleBundle;
    },
  ): Promise<void> => {
    await api.post(
      `/knowledge/collections/${id}/permissions/bundles/revoke`,
      data,
    );
  },

  mutateKnowledgeCollectionPermissionBundles: async (data: {
    collection_ids: string[];
    operation: 'grant' | 'revoke';
    subject_type: 'team' | 'user';
    subject_id: string;
    role_bundle: KnowledgeCollectionRoleBundle;
  }): Promise<KnowledgeCollectionPermissionBulkBundleResponse> => {
    const response = await api.post(
      '/knowledge/collection-permissions/bulk-bundles',
      data,
    );
    return response.data;
  },

  revokeKnowledgeCollectionPermission: async (
    id: string,
    permissionId: string,
  ): Promise<void> => {
    await api.delete(
      `/knowledge/collections/${id}/permissions/${permissionId}`,
    );
  },

  updateKnowledgeCollectionVisibility: async (
    id: string,
    data: {
      visibility: KnowledgeCollectionVisibility;
      acknowledged_public_runtime_exposure: boolean;
    },
  ): Promise<KnowledgeCollectionVisibilityResponse> => {
    const response = await api.post(
      `/knowledge/collections/${id}/visibility`,
      data,
    );
    return response.data;
  },

  // 문서 파싱 승인 (LlamaParse 비용 발생 등)
  confirmDocumentParsing: async (
    documentId: string,
    strategy: 'llamaparse' | 'general' = 'llamaparse',
  ): Promise<any> => {
    // 쿼리 파라미터로 strategy 전달
    const response = await api.post(
      `/rag/document/${documentId}/confirm?strategy=${strategy}`,
    );
    return response.data;
  },

  // 문서 분석 (비용 예측)
  analyzeDocument: async (documentId: string): Promise<AnalyzeResponse> => {
    const response = await api.post(`/rag/document/${documentId}/analyze`);
    return response.data;
  },

  deleteDocument: async (documentId: string) => {
    const response = await api.delete(`/rag/document/${documentId}`);
    return response.data;
  },

  // 문서 청킹 미리보기
  previewDocumentChunking: async (
    kbId: string,
    documentId: string,
    data: DocumentPreviewRequest,
  ): Promise<DocumentPreviewResponse> => {
    const response = await api.post(
      `/knowledge/${kbId}/documents/${documentId}/preview`,
      data,
    );
    return response.data;
  },

  // 문서 처리 시작 (설정 저장 및 백그라운드 작업 트리거)
  processDocument: async (
    kbId: string,
    documentId: string,
    data: DocumentPreviewRequest,
  ): Promise<{ status: string; message: string }> => {
    const response = await api.post(
      `/knowledge/${kbId}/documents/${documentId}/process`,
      data,
    );
    return response.data;
  },

  syncDocument: async (
    kbId: string,
    documentId: string,
  ): Promise<{ status: string; message: string }> => {
    const response = await api.post(
      `/knowledge/${kbId}/documents/${documentId}/sync`,
    );
    return response.data;
  },

  // SSE 연결을 위한 URL 반환
  getProgressUrl: (documentId: string): string => {
    const organizationId = getStoredActiveOrganizationId();
    if (!organizationId) {
      throw new Error('Active organization is required for document progress.');
    }
    return `${API_BASE_URL}/rag/document/${documentId}/progress?organizationId=${encodeURIComponent(
      organizationId,
    )}`;
  },

  // API Proxy Preview
  proxyApiPreview: async (data: {
    url: string;
    method: string;
    headers?: any;
    body?: any;
  }): Promise<any> => {
    const response = await api.post('/rag/proxy/preview', data);
    return response.data;
  },

  getAvailableModels: async (): Promise<LLMModelOption[]> => {
    const response = await api.get('/llm/my-models');
    return response.data;
  },

  getCredentials: async (): Promise<LLMCredentialOption[]> => {
    const response = await api.get('/llm/credentials');
    return response.data;
  },

  getAgentAnswerOptions: async (): Promise<LLMAgentAnswerOption[]> => {
    const response = await api.get('/llm/agent-answer-options');
    return response.data;
  },

  askAgentAnswer: async (data: {
    knowledge_base_id: string;
    query: string;
    generation_model_id: string;
    credential_id: string;
    hierarchy_mode?: 'auto' | 'flat' | 'parent_child';
    top_k?: number;
  }): Promise<RAGAgentAnswerResponse> => {
    const response = await api.post('/rag/agent/answer', data);
    return response.data;
  },

  streamAgentAnswer: async (
    data: {
      knowledge_base_id: string;
      query: string;
      generation_model_id: string;
      credential_id: string;
      hierarchy_mode?: 'auto' | 'flat' | 'parent_child';
      top_k?: number;
    },
    onEvent: (event: RAGAgentStreamEvent) => void,
  ): Promise<void> => {
    const response = await csrfFetch(
      `${API_BASE_URL}/rag/agent/answer/stream`,
      {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...activeOrganizationHeaders(getStoredActiveOrganizationId()),
        },
        body: JSON.stringify(data),
      },
    );

    if (!response.ok) {
      let message = `요청 실패 (${response.status})`;
      try {
        const body = await response.json();
        message = body?.error?.message || body?.detail || message;
      } catch {
        // Sanitized fallback message is enough for UI display.
      }
      throw new Error(message);
    }

    if (!response.body) {
      throw new Error('스트리밍 응답을 읽을 수 없습니다.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() || '';

      for (const rawEvent of events) {
        const parsed = parseSseEvent(rawEvent.trim());
        if (parsed) {
          onEvent(parsed);
          if (parsed.event === 'error') {
            const reason = String(parsed.data.reason_code || 'stream.error');
            throw new Error(`RAG answer stream failed: ${reason}`);
          }
        }
      }
    }

    buffer += decoder.decode();

    if (buffer.trim()) {
      const parsed = parseSseEvent(buffer.trim());
      if (parsed) {
        onEvent(parsed);
        if (parsed.event === 'error') {
          const reason = String(parsed.data.reason_code || 'stream.error');
          throw new Error(`RAG answer stream failed: ${reason}`);
        }
      }
    }
  },
};
