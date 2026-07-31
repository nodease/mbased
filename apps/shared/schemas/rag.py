import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CLASSIFICATION_VALUES = {"public", "internal", "confidential", "pii"}
SOURCE_TYPE_VALUES = {"FILE", "API", "DB"}
TAG_FILTER_MODES = {"contains_any", "contains_all"}
HierarchyMode = Literal["auto", "flat", "parent_child"]
ChunkingMode = Literal["flat", "hierarchical"]
EvidenceSufficiencyPolicy = Literal["minimum_evidence", "strict_citation"]
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
CORRELATION_ID_SECRET_PATTERNS = (
    re.compile(r"(?:^|[-_:])(?:sk|pk|rk|api)[-_][A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
)


def _normalize_str_list(values: list[Any] | None) -> list[str] | None:
    if values is None:
        return None
    normalized = [str(value).strip() for value in values]
    normalized = [value for value in normalized if value]
    if not normalized:
        raise ValueError("filter values must not be empty")
    return list(dict.fromkeys(normalized))


def _normalize_classification_values(values: list[str] | None) -> list[str] | None:
    normalized = _normalize_str_list(values)
    if normalized is None:
        return None
    result = [value.lower() for value in normalized]
    invalid = sorted(set(result) - CLASSIFICATION_VALUES)
    if invalid:
        raise ValueError(f"unsupported classification value: {', '.join(invalid)}")
    return result


def _normalize_source_type_values(values: list[str] | None) -> list[str] | None:
    normalized = _normalize_str_list(values)
    if normalized is None:
        return None
    result = [value.upper() for value in normalized]
    invalid = sorted(set(result) - SOURCE_TYPE_VALUES)
    if invalid:
        raise ValueError(f"unsupported source_type value: {', '.join(invalid)}")
    return result


class TagFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["contains_any", "contains_all"] = "contains_any"
    values: List[str] = Field(min_length=1, max_length=20)

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[str]) -> list[str]:
        normalized = _normalize_str_list(values)
        if normalized is None:
            raise ValueError("tag values are required")
        result = [value.lower() for value in normalized]
        if any(len(value) > 64 for value in result):
            raise ValueError("tag values must be 64 characters or fewer")
        return list(dict.fromkeys(result))


class MetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Optional[List[str]] = None
    tags: Optional[TagFilter] = None
    source_type: Optional[List[str]] = None
    effective_at: Optional[datetime] = None

    @field_validator("classification")
    @classmethod
    def validate_classification(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_classification_values(values)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_source_type_values(values)


# --- Dev A ---
class IngestionResponse(BaseModel):
    knowledge_base_id: UUID
    document_id: UUID
    status: str
    message: str


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    organization_id: Optional[UUID] = None
    name: str
    description: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    document_count: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None  # 문서 최종 업데이트 시간
    source_types: List[str] = []  # 포함된 소스 타입 목록
    embedding_model: str


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: Optional[str] = None
    embedding_model: str = "text-embedding-3-small"


class KnowledgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    description: Optional[str] = None
    embedding_model: Optional[str] = None


class KnowledgeSafeMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    safe_label: Optional[str] = None
    kb_safe_description: Optional[str] = None
    kb_safe_topics: Optional[List[str]] = None


class KnowledgeSafeMetadataResponse(BaseModel):
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    can_manage_safe_metadata: bool = True


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    error_message: Optional[str] = None
    chunk_count: int = 0
    token_count: int = 0  # 추후 구현
    chunk_size: int = 1000
    chunk_overlap: int = 200
    source_type: str = "FILE"
    meta_info: Optional[dict] = None


class DocumentDbJoinEdgeEditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    to_table: str
    from_column: str
    to_column: str


class DocumentDbJoinEditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_table: Optional[str] = None
    joins: List[DocumentDbJoinEdgeEditConfig] = Field(default_factory=list)


class DocumentDbEditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    selected_items: Dict[str, List[str]] = Field(default_factory=dict)
    sensitive_columns: Dict[str, List[str]] = Field(default_factory=dict)
    aliases: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    template: Optional[str] = None
    join_config: DocumentDbJoinEditConfig = Field(
        default_factory=DocumentDbJoinEditConfig
    )


class DocumentApiEditConfigSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    method: Literal["GET", "POST"]
    safe_label: str
    has_headers: bool
    has_body: bool


class DocumentEditConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    editable: bool
    safe_reason_code: Optional[Literal["document.edit_config_unavailable"]] = None
    source_type: Literal["FILE", "API", "DB"]
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    segment_identifier: Optional[str] = None
    remove_urls_emails: Optional[bool] = None
    remove_whitespace: Optional[bool] = None
    strategy: Optional[Literal["general", "llamaparse"]] = None
    chunking_mode: Optional[ChunkingMode] = None
    selection_mode: Optional[Literal["all", "range", "keyword"]] = None
    chunk_range: Optional[str] = None
    keyword_filter: Optional[str] = None
    db_config: Optional[DocumentDbEditConfig] = None
    api_config: Optional[DocumentApiEditConfigSummary] = None


class KnowledgeBaseDetailResponse(KnowledgeBaseResponse):
    documents: List[DocumentResponse]
    can_edit_settings: bool = True
    can_manage_safe_metadata: bool = True
    can_register_initial_document: bool = False
    can_read: bool = True
    can_use: bool = False
    can_write: bool = False
    can_read_content: bool = False
    can_manage: bool = False


# --- Retrieval Schemas (Dev B) ---
class SearchQuery(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    knowledge_base_id: Optional[UUID] = None  # 특정 KB 검색 시 사용
    generation_model: Optional[str] = "gpt-4o"  # 답변 생성에 사용할 모델 ID
    metadata_filter: Optional[MetadataFilter] = None
    classification_filter: Optional[List[str]] = None
    tags: Optional[TagFilter] = None
    source_type: Optional[List[str]] = None
    effective_at: Optional[datetime] = None
    hierarchy_mode: HierarchyMode = "auto"

    @field_validator("classification_filter")
    @classmethod
    def validate_classification_filter(
        cls, values: list[str] | None
    ) -> list[str] | None:
        return _normalize_classification_values(values)

    @field_validator("source_type")
    @classmethod
    def validate_source_type_filter(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_source_type_values(values)

    @model_validator(mode="after")
    def reject_duplicate_metadata_shortcuts(self) -> "SearchQuery":
        if self.metadata_filter is None:
            return self

        duplicates = []
        shortcut_pairs = {
            "classification": self.classification_filter,
            "tags": self.tags,
            "source_type": self.source_type,
            "effective_at": self.effective_at,
        }
        for key, shortcut_value in shortcut_pairs.items():
            metadata_value = getattr(self.metadata_filter, key)
            if shortcut_value is not None and metadata_value is not None:
                duplicates.append(key)
        if duplicates:
            keys = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate metadata filter shortcut: {keys}")
        return self


class ChunkPreview(BaseModel):
    chunk_id: Optional[UUID] = None
    parent_chunk_id: Optional[UUID] = None
    content: str
    document_id: UUID
    filename: str
    page_number: Optional[int] = None
    similarity_score: float
    score: Optional[float] = None
    rank: Optional[int] = None
    token_count: Optional[int] = None
    metadata_summary: Optional[Dict[str, Any]] = None
    hierarchy_path: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class RAGResponse(BaseModel):
    answer: str
    references: List[ChunkPreview]  # Metadata for UI source linking


class RAGCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    document_id: UUID
    chunk_id: Optional[UUID] = None
    rank: int
    score: Optional[float] = None
    filename: Optional[str] = None
    heading: Optional[str] = None
    hierarchy_path: Optional[List[str]] = None
    metadata_summary: Dict[str, Any] = Field(default_factory=dict)
    content_preview: Optional[str] = Field(default=None, max_length=300)


class RAGRetrievalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    hierarchy_mode: HierarchyMode
    retrieved_chunk_count: int = 0
    document_ids: List[UUID] = Field(default_factory=list)
    citation_ids: List[str] = Field(default_factory=list)
    score_summary: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    raw_content_returned: bool = False
    evidence_sufficient: bool = True
    insufficiency_reason: Optional[str] = None
    partial_result: bool = False
    source_tier_used: Dict[str, Any] = Field(default_factory=dict)


class RAGUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    latency_ms: int = 0
    model_name: Optional[str] = None
    provider: Optional[str] = None


class RAGAnswerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_length: int = 0
    cited_document_count: int = 0
    citation_ids: List[str] = Field(default_factory=list)
    policy_result: Dict[str, Any] = Field(default_factory=dict)
    completion_status: str


class RAGAgentAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    query: str = Field(min_length=1)
    metadata_filter: Optional[MetadataFilter] = None
    classification_filter: Optional[List[str]] = None
    tags: Optional[TagFilter] = None
    source_type: Optional[List[str]] = None
    effective_at: Optional[datetime] = None
    hierarchy_mode: HierarchyMode = "auto"
    top_k: int = 8
    generation_model_id: UUID
    credential_id: UUID
    correlation_id: Optional[str] = None
    evidence_sufficiency_policy: EvidenceSufficiencyPolicy = "minimum_evidence"

    @field_validator("classification_filter")
    @classmethod
    def validate_classification_filter(
        cls, values: list[str] | None
    ) -> list[str] | None:
        return _normalize_classification_values(values)

    @field_validator("source_type")
    @classmethod
    def validate_source_type_filter(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_source_type_values(values)

    @model_validator(mode="after")
    def reject_duplicate_metadata_shortcuts(self) -> "RAGAgentAnswerRequest":
        if self.metadata_filter is None:
            return self

        duplicates = []
        shortcut_pairs = {
            "classification": self.classification_filter,
            "tags": self.tags,
            "source_type": self.source_type,
            "effective_at": self.effective_at,
        }
        for key, shortcut_value in shortcut_pairs.items():
            metadata_value = getattr(self.metadata_filter, key)
            if shortcut_value is not None and metadata_value is not None:
                duplicates.append(key)
        if duplicates:
            keys = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate metadata filter shortcut: {keys}")
        return self


class RAGAgentAnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_run_id: UUID
    correlation_id: str
    status: str
    answer: str
    citations: List[RAGCitation] = Field(default_factory=list)
    retrieval_summary: RAGRetrievalSummary
    usage_summary: RAGUsageSummary
    policy_result: Dict[str, Any] = Field(default_factory=dict)


class RAGAgentSSEEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    data: Dict[str, Any] = Field(default_factory=dict)


class DocumentPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chunk_size: int = 500
    chunk_overlap: int = 50
    segment_identifier: str = "\n\n"
    remove_urls_emails: bool = False
    remove_whitespace: bool = True
    strategy: str = "general"  # "general" or "llamaparse"
    source_type: str = "FILE"  # FILE or API
    chunking_mode: ChunkingMode = Field(default="flat", alias="chunkingMode")
    db_config: Optional[Dict[str, Any]] = None
    # 필터링 파라미터 추가
    selection_mode: str = "all"  # 'all', 'range', 'keyword'
    chunk_range: Optional[str] = None
    keyword_filter: Optional[str] = None

    @field_validator("source_type")
    @classmethod
    def validate_preview_source_type(cls, value: str) -> str:
        source_type = str(value or "FILE").upper()
        if source_type not in SOURCE_TYPE_VALUES:
            raise ValueError(f"unsupported source_type value: {source_type}")
        return source_type

    @field_validator("selection_mode")
    @classmethod
    def validate_selection_mode(cls, value: str) -> str:
        selection_mode = str(value or "all").lower()
        if selection_mode not in {"all", "range", "keyword"}:
            raise ValueError("selection_mode must be all, range, or keyword")
        return selection_mode


class DocumentProcessRequest(DocumentPreviewRequest):
    # PreviewRequest와 동일한 필드를 사용 (상속)
    pass


class DocumentSegment(BaseModel):
    content: str
    token_count: int
    char_count: int


class DocumentAnalyzeResponse(BaseModel):
    filename: str
    cost_estimate: dict  # { "pages": int, "credits": int, "cost_usd": float }
    recommended_strategy: str = "general"
    is_cached: bool = False


class DocumentPreviewResponse(BaseModel):
    segments: List[DocumentSegment]
    total_count: int
    preview_text_sample: str = ""


# --- API Proxy Schema ---
class ApiPreviewRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None
