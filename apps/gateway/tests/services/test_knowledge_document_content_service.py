from types import SimpleNamespace

from fastapi.responses import FileResponse, StreamingResponse

from apps.gateway.services import knowledge_document_content_service as content_module
from apps.gateway.services.knowledge_document_content_service import (
    KnowledgeDocumentContentService,
)
from apps.shared.db.models.knowledge import Document


def _document(*, filename: str, file_path: str) -> Document:
    return Document(
        filename=filename,
        file_path=file_path,
        source_type="FILE",
    )


def test_local_pdf_response_is_inline_and_disables_content_sniffing(tmp_path):
    pdf_path = tmp_path / "policy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    response = KnowledgeDocumentContentService().build_content_response(
        _document(filename="policy.pdf", file_path=str(pdf_path))
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")


def test_external_pdf_response_disables_content_sniffing(monkeypatch):
    monkeypatch.setattr(
        content_module,
        "safe_http_request",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200,
            content=b"%PDF-1.4\n",
        ),
    )

    response = KnowledgeDocumentContentService().build_content_response(
        _document(
            filename="policy.pdf",
            file_path="https://documents.example.test/policy.pdf",
        )
    )

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")
