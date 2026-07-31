import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# === New Schemas ===

class LLMModelResponse(BaseModel):
    id: uuid.UUID
    model_id_for_api_call: str
    name: str
    type: str
    provider_name: str
    context_window: int
    input_price_1k: Optional[float] = None
    output_price_1k: Optional[float] = None
    is_active: bool
    model_metadata: Optional[Dict[str, Any]] = Field(default=None, serialization_alias="metadata")
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class LLMProviderResponse(BaseModel):
    id: uuid.UUID
    name: str # e.g. openai
    description: Optional[str] = None
    type: str # system, custom
    base_url: Optional[str] = None
    auth_type: str
    doc_url: Optional[str] = None
    
    # Models provided by this provider
    models: List[LLMModelResponse] = Field(default_factory=list)
    
    model_config = ConfigDict(from_attributes=True)


class LLMCredentialCreate(BaseModel):
    """
    User credential creation request.
    Existing fields mapped to new schema:
    - alias -> credential_name
    - apiKey -> api_key (to be encrypted)
    """
    provider_id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    credential_name: str
    api_key: str = Field(..., description="Raw API Key")
    # For custom provider override if supported later, otherwise ignored/removed
    # base_url override could be added here if needed for custom generic providers
    
    model_config = ConfigDict(populate_by_name=True)


class LLMCredentialResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    credential_name: str
    config_preview: Optional[str] = None # sk-****
    is_valid: bool
    quota_type: str
    quota_limit: int
    quota_used: int
    created_at: datetime
    updated_at: datetime
    
    # We might want to show which provider it belongs to details?
    # provider_name: str (computed or from relation)

    model_config = ConfigDict(from_attributes=True)


class LLMCredentialOptionResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    credential_name: str
    config_preview: Optional[str] = None
    is_valid: bool

    model_config = ConfigDict(from_attributes=True)


class LLMCredentialModelOptionResponse(BaseModel):
    """
    Agent answer처럼 model과 credential을 함께 선택해야 하는 UI용 옵션.
    model 전용 권한을 만들지 않고 verified credential-model relation과
    credential use 권한을 통과한 조합만 반환한다.
    """

    model: LLMModelResponse
    credential: LLMCredentialOptionResponse
    provider_name: str
    relation_priority: int = 0


class LLMIntentModelProviderResponse(BaseModel):
    provider_name: str
    options: List[LLMCredentialModelOptionResponse] = Field(default_factory=list)
    unavailable_reason: Optional[str] = None


class LLMUsageLogResponse(BaseModel):
    id: uuid.UUID
    prompt_tokens: int
    completion_tokens: int
    total_cost: Optional[float]
    latency_ms: Optional[int]
    status: str
    created_at: datetime
    
    
    model_config = ConfigDict(from_attributes=True)


class LLMTraceItem(BaseModel):
    id: uuid.UUID
    workflow_id: Optional[uuid.UUID] = None
    workflow_run_id: uuid.UUID
    node_id: Optional[str] = None
    model_id: Optional[uuid.UUID] = None
    model_name: Optional[str] = None
    provider: Optional[str] = None
    credential_id: Optional[uuid.UUID] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: Optional[float]
    latency_ms: Optional[int]
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMTraceListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[LLMTraceItem]


class LLMModelPricingUpdate(BaseModel):
    input_price_1k: float = Field(ge=0)
    output_price_1k: float = Field(ge=0)
