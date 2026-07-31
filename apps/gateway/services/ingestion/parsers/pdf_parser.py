import logging
from enum import Enum
from typing import Any, Dict, List

import fitz  # PyMuPDF
import pymupdf4llm

from apps.gateway.services.ingestion.parsers.base import BaseParser
from apps.shared.domain.knowledge_document_ingestion import (
    RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
)

logger = logging.getLogger(__name__)


class ParsingStrategy(str, Enum):
    TEXT = "text"
    MIXED = "mixed"
    IMAGE = "image"


class ExternalParserEgressUnavailable(RuntimeError):
    reason_code = RAW_PARSER_EGRESS_UNAVAILABLE_REASON

    def __init__(self) -> None:
        super().__init__("External parser is unavailable.")


class PdfParser(BaseParser):
    """
    [PdfParser]
    PDF 파일을 처리하여 텍스트를 추출하는 파서입니다.

    기능:
    1. PyMuPDF(pymupdf4llm)를 사용한 빠른 마크다운 변환
    2. 승인된 external parser transport가 준비되지 않은 전략의 fail-closed 차단
    3. 파일 성격(이미지 비중 등)에 따른 분석 기능
    """

    def parse(self, source_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        PDF 파싱 메인 메서드

        Args:
            source_path: PDF 파일의 절대 경로
            kwargs:
                - strategy (str): 'general' (기본값). 'llamaparse'는 현재 차단된다.

        Returns:
            [{"text": "...", "page": 1}, ...]
        """
        strategy = kwargs.get("strategy", "general")
        if strategy == "llamaparse":
            raise ExternalParserEgressUnavailable()
        return self._parse_with_pymupdf(source_path)

    def analyze(self, file_path: str) -> Dict[str, Any]:
        """
        PDF 파일의 성격을 분석하여 적절한 처리 전략을 제안합니다.
        (기존 _analyze_pdf_type 로직 이식)
        """
        doc = fitz.open(file_path)
        total_pages = len(doc)

        # 샘플링 (앞3, 중간1, 뒤2)
        sample_indices = set()
        for i in range(min(3, total_pages)):
            sample_indices.add(i)
        if total_pages > 3:
            sample_indices.add(total_pages // 2)
        if total_pages > 1:
            sample_indices.add(total_pages - 1)
        if total_pages > 2:
            sample_indices.add(total_pages - 2)

        text_length = 0
        image_count = 0
        page_count = 0

        for idx in sample_indices:
            if idx >= total_pages:
                continue
            page = doc[idx]
            page_count += 1
            text_length += len(page.get_text().strip())
            image_count += len(page.get_images(full=True))

        doc.close()

        avg_text = text_length / page_count if page_count > 0 else 0
        avg_imgs = image_count / page_count if page_count > 0 else 0

        strategy = "general"
        if avg_text < 50:
            strategy = "llamaparse"  # OCR 필요
        elif avg_imgs > 2:
            strategy = "llamaparse"  # 혼합된 Layout

        return {
            "strategy": strategy,
            "stats": {"avg_text": avg_text, "avg_imgs": avg_imgs},
            "pages": total_pages,
        }

    def _parse_with_pymupdf(self, file_path: str) -> List[Dict[str, Any]]:
        """PyMuPDF4LLM을 사용하여 빠르게 마크다운 텍스트 추출"""
        try:
            md_text_chunks = pymupdf4llm.to_markdown(file_path, page_chunks=True)

            # 구분선(-----)만 있고 실제 텍스트가 없는 경우 감지
            total_content_len = 0
            for chunk in md_text_chunks:
                clean_text = chunk["text"].replace("-", "").strip()
                total_content_len += len(clean_text)

            if total_content_len < 20:  # 텍스트가 거의 없다고 판단
                return self._parse_with_fitz_fallback(file_path)

            results = []
            for index, chunk in enumerate(md_text_chunks):
                text_content = chunk["text"]
                metadata = chunk.get("metadata") or {}

                # pymupdf4llm uses page_number (1-based) in recent releases,
                # while older releases returned page (0-based).
                if metadata.get("page_number") is not None:
                    page_number = int(metadata["page_number"])
                elif metadata.get("page") is not None:
                    page_number = int(metadata["page"]) + 1
                else:
                    # page_chunks preserves document order, so this remains a
                    # useful fallback for metadata-light parser responses.
                    page_number = index + 1
                results.append(
                    {"text": text_content, "page": page_number}
                )
            return results
        except Exception as exc:
            logger.error(
                "[PdfParser] PyMuPDF failed: error_type=%s",
                type(exc).__name__,
            )
            return self._parse_with_fitz_fallback(file_path)

    def _parse_with_fitz_fallback(self, file_path: str) -> List[Dict[str, Any]]:
        """Standard PyMuPDF text extraction as fallback"""
        try:
            doc = fitz.open(file_path)
            results = []
            for i, page in enumerate(doc):
                text = page.get_text()
                # 간단한 정제 (너무 짧은 페이지 제외)
                if len(text.strip()) > 5:
                    results.append({"text": text, "page": i + 1})
            return results
        except Exception as exc:
            logger.error(
                "[PdfParser] Basic fitz extraction failed: error_type=%s",
                type(exc).__name__,
            )
            return []
