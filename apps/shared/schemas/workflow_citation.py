from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WORKFLOW_CITATION_RESULT_KEY = "__nodease_citations"
WORKFLOW_CITATION_VERSION = 1
MAX_WORKFLOW_CITATIONS = 8
MAX_WORKFLOW_CITATION_LABEL_LENGTH = 255
MAX_WORKFLOW_CITATION_SECTION_LENGTH = 200
MAX_WORKFLOW_CITATION_PREVIEW_LENGTH = 300

CitationDisplayMode = Literal["hidden", "basic", "detailed"]


class WorkflowCitationItem(BaseModel):
    """권한 검증이 끝난 evidence의 사용자 응답 전용 표시 정보."""

    model_config = ConfigDict(extra="forbid", strict=True)

    citation_id: str = Field(pattern=r"^evidence-[1-9][0-9]*$")
    evidence_rank: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=MAX_WORKFLOW_CITATION_LABEL_LENGTH)
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_WORKFLOW_CITATION_SECTION_LENGTH,
    )
    content_preview: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_WORKFLOW_CITATION_PREVIEW_LENGTH,
    )


class WorkflowCitationEnvelope(BaseModel):
    """Workflow/Chatbot 최종 응답에만 부착하는 versioned sidecar."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = WORKFLOW_CITATION_VERSION
    items: list[WorkflowCitationItem] = Field(
        default_factory=list,
        max_length=MAX_WORKFLOW_CITATIONS,
    )
