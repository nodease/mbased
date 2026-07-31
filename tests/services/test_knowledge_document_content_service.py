from apps.gateway.services.knowledge_document_content_service import (
    HTML_PREVIEW_CSP,
    KnowledgeDocumentContentService,
    content_disposition_type_for_document,
)


def test_content_disposition_only_allows_safe_inline_types():
    assert content_disposition_type_for_document("manual.pdf", "application/pdf") == "inline"
    assert content_disposition_type_for_document("note.txt", "text/plain") == "inline"
    assert content_disposition_type_for_document("evil.html", "text/html") == "attachment"
    assert content_disposition_type_for_document("sheet.csv", "text/csv") == "attachment"


def test_html_preview_response_sets_defensive_headers():
    response = KnowledgeDocumentContentService()._html_preview_response("<p>safe</p>")

    assert response.headers["content-security-policy"] == HTML_PREVIEW_CSP
    assert response.headers["x-content-type-options"] == "nosniff"


def test_spreadsheet_preview_escapes_cell_html(tmp_path):
    csv_path = tmp_path / "preview.csv"
    csv_path.write_text("name\n<script>alert(1)</script>\n", encoding="utf-8")

    body = KnowledgeDocumentContentService()._spreadsheet_body_html(str(csv_path), ".csv")

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
