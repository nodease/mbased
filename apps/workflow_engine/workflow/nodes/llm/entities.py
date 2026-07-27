from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_COLLECTION_REFERENCES,
    MAX_RUNTIME_DIRECT_KB_REFERENCES,
)
from apps.shared.domain.workflow_knowledge_references import (
    parse_llm_knowledge_references,
)
from apps.shared.schemas.workflow_citation import CitationDisplayMode
from apps.workflow_engine.workflow.nodes.base.entities import BaseNodeData


class LLMVariable(BaseModel):
    """
    LLM 프롬프트에서 사용될 변수 정의 (Template 노드와 동일한 구조)
    """

    name: str = Field(..., description="프롬프트 변수명 (예: username)")
    value_selector: List[str] = Field(
        ..., description="값을 가져올 경로 [node_id, variable_key]"
    )


class KnowledgeBaseRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class KnowledgeCollectionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    safeLabel: Optional[str] = None


EvidenceSufficiencyPolicy = Literal["minimum_evidence", "strict_citation"]
RAGFailurePolicy = Literal["safe_no_result", "fail_node"]
SourceTierPolicy = Literal["tie_break", "off"]
QueryRewriteMode = Literal["off", "template", "llm_assisted"]
MAX_RAG_RETRIEVAL_KBS = MAX_RUNTIME_DIRECT_KB_REFERENCES
MAX_RAG_COLLECTIONS = MAX_RUNTIME_COLLECTION_REFERENCES
MAX_RAG_CHUNKS_PER_KB = 8
MAX_RAG_QUERY_REWRITE_TEMPLATE_LENGTH = 512


class LLMNodeData(BaseNodeData):
    """
    개요: LLM 노드에서 사용할 설정/입력값 정의.
    - 필드 정의
    - validation 메서드 정의 (인스턴스)
    """

    provider: Optional[str] = None
    model_id: str
    fallback_model_id: Optional[str] = None
    auto_model_routing: bool = Field(
        default=False, description="LLM 모델 자동 라우팅 사용 여부"
    )
    model_routing_policy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="실행 시점 자동 모델 라우팅 active policy safe snapshot",
    )
    model_routing_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="도메인 키워드 없이 런타임 라우팅 rule 평가에 사용할 명시적 노드 분류 힌트",
    )
    model_routing_task_description: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Judge-first 자동 라우팅에 사용하는 사용자 정의 노드 작업 설명",
    )
    task_type: str = Field(default="generate", description="LLM 노드 작업 유형")
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    assistant_prompt: Optional[str] = None
    referenced_variables: List[LLMVariable] = Field(default_factory=list)
    context_variable: Optional[str] = None
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="LLM API 파라미터 (temperature, top_p, max_tokens 등)",
    )
    output_format: Optional[Dict[str, Any]] = Field(
        default=None,
        description="LLM 출력 형식 설정 (text/json 및 JSON schema)",
    )

    # LLM node RAG 옵션은 실행 시점 execution subject 기준으로 다시 검증한다.
    knowledgeBases: List[KnowledgeBaseRef] = Field(
        default_factory=list,
        description="검색할 지식 베이스 목록",
    )
    knowledgeCollections: List[KnowledgeCollectionRef] = Field(
        default_factory=list,
        description="실행 시점에 멤버십을 해석할 지식 컬렉션 목록",
    )
    scoreThreshold: float = Field(default=0.3, description="유사도 점수 임계값")
    topK: int = Field(default=5, description="상위 K개 문서 반환")
    dedupeRetrievedContext: bool = Field(
        default=False, description="검색된 문서 조각의 중복 근거 제거 여부"
    )
    retrievedContextMaxChars: Optional[int] = Field(
        default=None,
        ge=1,
        description="검색으로 주입되는 Knowledge/RAG context 최대 글자 수",
    )
    retrievedContextCompression: str = Field(
        default="off",
        pattern="^(off|light|strong)$",
        description="검색 문서 압축 강도",
    )
    answerGroundingCheck: str = Field(
        default="basic",
        pattern="^(off|basic|strict)$",
        description="답변과 검색 문서의 어휘 일치도 metadata 기록 수준",
    )
    citationDisplayMode: CitationDisplayMode = Field(
        default="hidden",
        description="최종 사용자 응답의 권한 안전 Citation 표시 수준",
    )
    evidenceSufficiencyPolicy: EvidenceSufficiencyPolicy = Field(
        default="minimum_evidence",
        description="RAG 근거 충분성 정책",
    )
    ragFailurePolicy: RAGFailurePolicy = Field(
        default="safe_no_result",
        description="근거 부족 시 LLM 호출을 막고 안전 응답 또는 노드 실패로 닫는 정책",
    )
    sourceTierPolicy: SourceTierPolicy = Field(
        default="tie_break",
        description="권한 통과 evidence 안에서 source_tier를 동점 정렬 힌트로 사용할지 결정",
    )
    queryRewriteMode: QueryRewriteMode = Field(
        default="off",
        description="RAG 검색 query rewrite 방식. llm_assisted는 별도 gate 전까지 비활성",
    )
    queryRewriteTemplate: Optional[str] = Field(
        default=None,
        description="template rewrite에서 사용할 safe query template",
    )

    def _active_routing_model_id(self) -> Optional[str]:
        if not self.auto_model_routing or not isinstance(
            self.model_routing_policy, dict
        ):
            return None

        active_policy = self.model_routing_policy.get("active_policy")
        if not isinstance(active_policy, dict):
            return None

        rules = active_policy.get("rules")
        if isinstance(rules, list) and rules:
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                selected_model = str(rule.get("selected_model_id") or "").strip()
                if selected_model:
                    return selected_model

        default_model = str(active_policy.get("default_model_id") or "").strip()
        return default_model or None

    def validate(self) -> None:
        # 모델은 필수
        if not self.model_id or not self.model_id.strip():
            if not self._active_routing_model_id():
                raise ValueError("모델을 선택하세요.")
        # Provider is now inferred from model_id via LLMService
        if self.fallback_model_id is not None:
            stripped_fallback = self.fallback_model_id.strip()
            self.fallback_model_id = stripped_fallback or None
        if self.fallback_model_id == self.model_id:
            self.fallback_model_id = None

        prompts = [self.system_prompt, self.user_prompt, self.assistant_prompt]
        if all(not p for p in prompts):
            raise ValueError("system/user/assistant 프롬프트 중 최소 1개는 필요합니다.")

        # referenced_variables 정리: 불완전한 변수(이름/selector 없음)는 무시
        cleaned_vars = []
        for var in self.referenced_variables:
            name = (var.name or "").strip()
            selector = var.value_selector or []
            # 이름과 selector가 모두 비어있으면 무시
            if not name and (not selector or len(selector) < 2):
                continue
            # 이름은 있지만 selector가 불완전하면 무시
            if name and (not selector or len(selector) < 2):
                continue
            cleaned_vars.append(var)
        self.referenced_variables = cleaned_vars

        if self.context_variable is not None:
            stripped_context = self.context_variable.strip()
            if not stripped_context:
                self.context_variable = None
            else:
                self.context_variable = stripped_context

        if self.topK < 1:
            self.topK = 1
        if self.topK > MAX_RAG_CHUNKS_PER_KB:
            self.topK = MAX_RAG_CHUNKS_PER_KB
        parse_llm_knowledge_references(
            {
                "knowledgeBases": [
                    reference.model_dump(mode="python")
                    for reference in self.knowledgeBases
                ],
                "knowledgeCollections": [
                    reference.model_dump(mode="python")
                    for reference in self.knowledgeCollections
                ],
            }
        )

        if self.queryRewriteMode == "llm_assisted":
            raise ValueError("llm_assisted query rewrite는 아직 사용할 수 없습니다.")
        if self.queryRewriteTemplate is not None:
            stripped_template = self.queryRewriteTemplate.strip()
            if not stripped_template:
                self.queryRewriteTemplate = None
            elif len(stripped_template) > MAX_RAG_QUERY_REWRITE_TEMPLATE_LENGTH:
                raise ValueError("query rewrite template이 너무 깁니다.")
            else:
                self.queryRewriteTemplate = stripped_template
