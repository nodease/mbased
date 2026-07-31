from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr


class Position(BaseModel):
    x: float
    y: float


class NodeSchema(BaseModel):
    id: str
    type: str
    position: Position
    data: Dict[str, Any]
    timeout: Optional[int] = None  # [NEW] 노드 실행 타임아웃 (초 단위)

    class Config:
        extra = "allow"


class EdgeSchema(BaseModel):
    id: str
    # source/target은 '어떤 노드'끼리 연결되는지를 나타내고,
    source: str
    target: str
    # sourceHandle/targetHandle은 '그 노드의 어떤 포트'에 연결되는지를 나타냄.
    sourceHandle: Optional[str] = None  # 출발 노드의 특정 출력 포트 ID
    targetHandle: Optional[str] = None  # 도착 노드의 특정 입력 포트 ID

    class Config:
        extra = "allow"


class ViewportSchema(BaseModel):
    x: float
    y: float
    zoom: float


class EnvVariableSchema(BaseModel):
    id: str
    key: str
    value: str
    type: str


class RuntimeVariableSchema(BaseModel):
    id: str
    key: str
    name: str


class WorkflowMutationContext(BaseModel):
    operation_id: UUID
    action: Literal["apply", "revert", "redo"]
    expected_base_graph_hash: str = Field(min_length=64, max_length=64)
    expected_workflow_updated_at: datetime
    catalog_version: Literal[3] = 3


class WorkflowDraftRequest(BaseModel):
    # 하나도 없으면 빈 리스트로 기본값 설정
    nodes: List[NodeSchema] = []
    edges: List[EdgeSchema] = []
    viewport: Optional[ViewportSchema] = None
    features: Optional[Dict[str, Any]] = None
    env_variables: Optional[List[EnvVariableSchema]] = Field(None, alias="envVariables")
    runtime_variables: Optional[List[RuntimeVariableSchema]] = Field(
        None, alias="runtimeVariables"
    )
    expected_graph_hash: str = Field(min_length=64, max_length=64)
    expected_updated_at: datetime
    mutation_context: WorkflowMutationContext | None = None


class WorkflowNodeSecretWriteRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=255)
    node_type: Literal["slackPostNode", "githubNode"]
    parameter_key: Literal["bot_token", "url", "api_token"]
    secret_value: SecretStr = Field(min_length=1, max_length=4096)


class WorkflowNodeSecretWriteResponse(BaseModel):
    secret_reference: str
    configured: Literal[True] = True


class WorkflowCreateRequest(BaseModel):
    """새 워크플로우 생성 요청"""

    app_id: UUID


class WorkflowResponse(BaseModel):
    """워크플로우 응답"""

    id: UUID
    app_id: UUID
    created_at: str
    updated_at: str
