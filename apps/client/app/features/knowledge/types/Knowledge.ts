export type SourceType = 'FILE' | 'API' | 'DB';

export interface IngestionResponse {
  knowledge_base_id: string;
  document_id: string;
  status: string;
  message: string;
}

export interface KnowledgeBaseCreate {
  name: string;
  description?: string;
  embedding_model: string;
}

export interface KnowledgeCreateRequest {
  sourceType?: SourceType;
  file?: File;

  // [NEW] S3 Direct Upload Fields
  s3FileUrl?: string;
  s3FileKey?: string;

  apiUrl?: string;
  apiMethod?: string;
  apiHeaders?: string;
  apiBody?: string;
  connectionId?: string;

  name?: string;
  description?: string;
  embeddingModel: string;
  topK: number;
  similarity: number;
  chunkSize: number;
  chunkOverlap: number;
  knowledgeBaseId?: string;
}

export interface KnowledgeBaseResponse {
  id: string;
  organization_id?: string;
  name: string;
  description?: string;
  safe_metadata?: Record<string, unknown>;
  document_count: number;
  created_at: string;
  updated_at?: string;
  source_types?: SourceType[];
  embedding_model: string;
}

export interface DocumentResponse {
  updated_at: string;
  id: string;
  filename: string;
  status:
    | 'pending'
    | 'indexing'
    | 'processing'
    | 'completed'
    | 'failed'
    | 'waiting_for_approval';
  created_at: string;
  error_message?: string;
  chunk_count: number;
  token_count: number;
  chunk_size?: number;

  chunk_overlap?: number;
  parsing_strategy?: 'general' | 'llamaparse';
  source_type?: SourceType;
  meta_info?: {
    progress?: number;
    processing_progress?: number;
    processing_enqueued_at?: string;
    processing_started_at?: string;
    processing_progress_updated_at?: string;
    processing_recovered_from_timeout?: boolean;
    cost_estimate?: {
      pages: number;
      credits: number;
      cost_usd: number;
    };
    strategy?: string;
    remove_urls_emails?: boolean;
    remove_whitespace?: boolean;
    chunking_mode?: 'flat' | 'hierarchical';
    upload_method?: 'backend' | 'direct';
  };
}

export interface DocumentDbJoinConfig {
  enabled: boolean;
  base_table?: string | null;
  joins: Array<{
    from_table: string;
    to_table: string;
    from_column: string;
    to_column: string;
  }>;
}

export interface DocumentDbEditConfig {
  connection_id: string;
  selected_items: Record<string, string[]>;
  sensitive_columns: Record<string, string[]>;
  aliases: Record<string, Record<string, string>>;
  template?: string | null;
  join_config: DocumentDbJoinConfig;
}

export interface DocumentApiEditConfigSummary {
  configured: boolean;
  method: 'GET' | 'POST';
  safe_label: string;
  has_headers: boolean;
  has_body: boolean;
}

export interface DocumentEditConfigResponse {
  editable: boolean;
  safe_reason_code?: 'document.edit_config_unavailable' | null;
  source_type: SourceType;
  chunk_size?: number | null;
  chunk_overlap?: number | null;
  segment_identifier?: string | null;
  remove_urls_emails?: boolean | null;
  remove_whitespace?: boolean | null;
  strategy?: 'general' | 'llamaparse' | null;
  chunking_mode?: 'flat' | 'hierarchical' | null;
  selection_mode?: 'all' | 'range' | 'keyword' | null;
  chunk_range?: string | null;
  keyword_filter?: string | null;
  db_config?: DocumentDbEditConfig | null;
  api_config?: DocumentApiEditConfigSummary | null;
}

export interface KnowledgeBaseDetailResponse extends KnowledgeBaseResponse {
  documents: DocumentResponse[];
  can_edit_settings?: boolean;
  can_manage_safe_metadata?: boolean;
  can_register_initial_document?: boolean;
  can_read?: boolean;
  can_use?: boolean;
  can_write?: boolean;
  can_read_content?: boolean;
  can_manage?: boolean;
}

export interface KnowledgeSafeMetadataResponse {
  safe_metadata: Record<string, unknown>;
  can_manage_safe_metadata: boolean;
}

export type KnowledgeCollectionAction = 'read' | 'route' | 'manage' | 'sync';
export type KnowledgeCollectionRoleBundle =
  | 'viewer'
  | 'workflow_router'
  | 'maintainer'
  | 'sync_operator';
export type KnowledgeCollectionVisibility = 'private' | 'public';
export type KnowledgeDomainAction =
  | 'catalog_manage'
  | 'permission_delegate'
  | 'lifecycle_manage'
  | 'sync_manage';

export interface KnowledgeCollectionResponse {
  id: string;
  organization_id: string;
  name: string;
  description?: string | null;
  is_system_managed: boolean;
  sync_state: string;
  lifecycle_state: 'active' | 'archived' | 'deleted';
  visibility: KnowledgeCollectionVisibility;
  linked_kb_count_bucket: string;
  active_kb_count_bucket: string;
  can_read: boolean;
  can_route: boolean;
  can_manage: boolean;
  can_sync: boolean;
  sync_supported: boolean;
  safe_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeCollectionListResponse {
  collections: KnowledgeCollectionResponse[];
  can_create_collection: boolean;
  can_change_public_visibility: boolean;
}

export type KnowledgeCollectionSyncJobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'partially_failed'
  | 'failed'
  | 'cancelled';

export type KnowledgeCollectionSyncProgress =
  | 'none'
  | 'started'
  | 'progressing'
  | 'most'
  | 'complete';

export interface KnowledgeCollectionSyncJobResponse {
  job_id: string;
  collection_id: string;
  status: KnowledgeCollectionSyncJobStatus;
  progress: KnowledgeCollectionSyncProgress;
  safe_reason_code?: string | null;
  retryable: boolean;
  requested_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface KnowledgeCollectionSyncRequestResponse {
  job: KnowledgeCollectionSyncJobResponse;
  reused: boolean;
  dispatch_deferred: boolean;
}

export interface KnowledgeCollectionLatestSyncJobResponse {
  job?: KnowledgeCollectionSyncJobResponse | null;
}

export interface KnowledgeCollectionLLMSelectableItem {
  id: string;
  safe_label?: string | null;
}

export interface KnowledgeCollectionLLMSelectableResponse {
  collections: KnowledgeCollectionLLMSelectableItem[];
}

export interface KnowledgeCollectionItemResponse {
  item_id: string;
  knowledge_base_id: string;
  safe_label?: string | null;
  lifecycle_state: string;
  sync_state: string;
  rank: number;
  can_manage_kb: boolean;
  can_use_kb: boolean;
}

export interface KnowledgeCollectionItemsResponse {
  items: KnowledgeCollectionItemResponse[];
  order_revision: string;
  reorder_supported: boolean;
  safe_reason_code?: 'item_reorder_limit_exceeded' | null;
}

export interface KnowledgeCollectionLinkCandidate {
  knowledge_base_id: string;
  safe_label?: string | null;
  disabled: boolean;
  safe_reason_code?: string | null;
}

export interface KnowledgeCollectionLinkCandidatesResponse {
  candidates: KnowledgeCollectionLinkCandidate[];
}

export interface KnowledgeCollectionPermissionResponse {
  permission_id: string;
  subject_type: 'team' | 'user';
  subject_id: string;
  subject_safe_label?: string | null;
  permission_action: KnowledgeCollectionAction;
}

export interface KnowledgeCollectionPermissionsResponse {
  permissions: KnowledgeCollectionPermissionResponse[];
}

export interface KnowledgeDelegationSubject {
  subject_type: 'team' | 'user';
  subject_id: string;
  subject_safe_label: string;
}

export interface KnowledgeDelegationSubjectsResponse {
  subjects: KnowledgeDelegationSubject[];
  next_cursor?: string | null;
}

export interface KnowledgeCollectionPermissionBulkBundleResponse {
  operation: 'grant' | 'revoke';
  subject_type: 'team' | 'user';
  role_bundle: KnowledgeCollectionRoleBundle;
  target_count_bucket: '0' | '1' | '2-10' | '11-50';
  changed_count_bucket: '0' | '1' | '2-10' | '11-50';
  unchanged_count_bucket: '0' | '1' | '2-10' | '11-50';
}

export interface KnowledgeDomainCapabilitiesResponse {
  actions: KnowledgeDomainAction[];
  can_manage_domain_permissions: boolean;
  can_create_collection: boolean;
  can_delegate_permissions: boolean;
  can_manage_lifecycle: boolean;
  can_manage_sync: boolean;
  can_change_public_visibility: boolean;
}

export interface KnowledgeDomainPermissionResponse {
  permission_id: string;
  subject_type: 'team' | 'user';
  subject_id: string;
  subject_safe_label: string;
  permission_action: KnowledgeDomainAction;
  assigned_at: string;
  expires_at?: string | null;
  is_expired: boolean;
}

export interface KnowledgeDomainPermissionListResponse {
  permissions: KnowledgeDomainPermissionResponse[];
}

export interface KnowledgeCollectionVisibilityResponse {
  collection: KnowledgeCollectionResponse;
  public_runtime_effect: string;
  linked_kb_count_bucket: string;
  active_kb_count_bucket: string;
  sensitive_content_warning: string;
}
