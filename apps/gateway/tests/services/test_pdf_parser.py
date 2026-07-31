from apps.gateway.services.ingestion.parsers.pdf_parser import PdfParser


def test_pymupdf_parser_uses_current_page_number_metadata(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.services.ingestion.parsers.pdf_parser.pymupdf4llm.to_markdown",
        lambda _file_path, page_chunks: [
            {
                "text": "First page contains enough extracted text.",
                "metadata": {"page_number": 1},
            },
            {
                "text": "Second page contains enough extracted text.",
                "metadata": {"page_number": 2},
            },
        ],
    )

    assert PdfParser()._parse_with_pymupdf("sample.pdf") == [
        {"text": "First page contains enough extracted text.", "page": 1},
        {"text": "Second page contains enough extracted text.", "page": 2},
    ]


def test_pymupdf_parser_supports_legacy_zero_based_page_metadata(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.services.ingestion.parsers.pdf_parser.pymupdf4llm.to_markdown",
        lambda _file_path, page_chunks: [
            {
                "text": "First page contains enough extracted text.",
                "metadata": {"page": 0},
            },
        ],
    )

    assert PdfParser()._parse_with_pymupdf("sample.pdf") == [
        {"text": "First page contains enough extracted text.", "page": 1},
    ]


def test_pymupdf_parser_falls_back_to_chunk_order_without_page_metadata(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.services.ingestion.parsers.pdf_parser.pymupdf4llm.to_markdown",
        lambda _file_path, page_chunks: [
            {"text": "First page contains enough extracted text."},
            {"text": "Second page contains enough extracted text."},
        ],
    )

    assert PdfParser()._parse_with_pymupdf("sample.pdf") == [
        {"text": "First page contains enough extracted text.", "page": 1},
        {"text": "Second page contains enough extracted text.", "page": 2},
    ]
