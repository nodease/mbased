import pytest
from apps.shared.schemas.workflow_citation import (
    MAX_WORKFLOW_CITATIONS,
    WorkflowCitationEnvelope,
    WorkflowCitationItem,
)
from pydantic import ValidationError


def test_workflow_citation_envelope_accepts_only_bounded_safe_shape():
    envelope = WorkflowCitationEnvelope(
        items=[
            WorkflowCitationItem(
                citation_id="evidence-1",
                evidence_rank=1,
                label="휴가 정책",
                page_number=3,
                section="연차 신청",
            )
        ]
    )

    assert envelope.model_dump(mode="json") == {
        "version": 1,
        "items": [
            {
                "citation_id": "evidence-1",
                "evidence_rank": 1,
                "label": "휴가 정책",
                "page_number": 3,
                "section": "연차 신청",
                "content_preview": None,
            }
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"citation_id": "raw-id", "evidence_rank": 1, "label": "문서"},
        {"citation_id": "evidence-1", "evidence_rank": 0, "label": "문서"},
        {
            "citation_id": "evidence-1",
            "evidence_rank": 1,
            "label": "문서",
            "document_id": "hidden",
        },
    ],
)
def test_workflow_citation_item_rejects_identity_and_invalid_rank(payload):
    with pytest.raises(ValidationError):
        WorkflowCitationItem.model_validate(payload)


def test_workflow_citation_envelope_rejects_oversized_result_set():
    items = [
        WorkflowCitationItem(
            citation_id=f"evidence-{index}",
            evidence_rank=index,
            label=f"문서 {index}",
        )
        for index in range(1, MAX_WORKFLOW_CITATIONS + 2)
    ]

    with pytest.raises(ValidationError):
        WorkflowCitationEnvelope(items=items)
