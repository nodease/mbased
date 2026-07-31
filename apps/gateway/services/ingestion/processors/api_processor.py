import logging
from typing import Any, Dict

from apps.gateway.services.ingestion.parsers.json_parser import JsonParser
from apps.shared.services.ingestion.processors.base import (
    BaseProcessor,
    ProcessingResult,
)
from apps.shared.services.egress_guard import (
    EgressGuardError,
    safe_http_request,
)
from apps.shared.services.outbound_operation_policy import KNOWLEDGE_API_FETCH

logger = logging.getLogger(__name__)
_TRANSIENT_EGRESS_REASONS = frozenset(
    {
        "egress.connection_failed",
        "egress.dns_resolution_failed",
        "egress.timeout",
    }
)


def _http_failure_reason(status_code: int) -> str:
    if status_code in {408, 425, 429} or status_code >= 500:
        return "source.temporarily_unavailable"
    return "configuration.invalid"


class ApiProcessor(BaseProcessor):
    """
    [ApiProcessor]
    HTTP API를 호출하여 데이터를 가져오고 텍스트를 추출합니다.
    """

    def process(self, source_config: Dict[str, Any]) -> ProcessingResult:
        """
        source_config: {
            "url": "http://...",
            "method": "GET",
            "headers": {...},
            "body": {...}
        }
        """
        url = source_config.get("url")
        method = source_config.get("method", "GET")
        headers = source_config.get("headers", {})
        body = source_config.get("body")

        # headers가 JSON string일 수 있으므로 파싱
        if isinstance(headers, str):
            try:
                import json

                headers = json.loads(headers)
            except (TypeError, ValueError):
                headers = {}

        # body가 JSON string일 수 있으므로 파싱
        if isinstance(body, str):
            try:
                import json

                body = json.loads(body)
            except (TypeError, ValueError):
                body = None

        if not url:
            return ProcessingResult(
                chunks=[],
                metadata={
                    "error": "No URL provided",
                    "reason_code": "configuration.invalid",
                },
            )

        try:
            response = safe_http_request(
                method=method,
                url=url,
                headers=headers,
                json_body=body,
                operation_id=KNOWLEDGE_API_FETCH,
            )
            if response.status_code >= 400:
                reason_code = _http_failure_reason(response.status_code)
                return ProcessingResult(
                    chunks=[],
                    metadata={
                        "error": "External API returned an error.",
                        "status_code": response.status_code,
                        "reason_code": reason_code,
                    },
                )

            parser = JsonParser()
            try:
                json_data = response.json()
                # 객체 직접 전달
                parsed_blocks = parser.parse("", json_object=json_data)
            except ValueError:
                # JSON이 아닌 경우 텍스트 그대로 사용
                parsed_blocks = [{"text": response.text, "page": 1}]

            chunks = []
            for block in parsed_blocks:
                chunks.append(
                    {
                        "content": block["text"],
                        "metadata": {"source": "api_response", "page": block["page"]},
                    }
                )

            return ProcessingResult(
                chunks=chunks,
                metadata={"source_type": "API", "status_code": response.status_code},
            )

        except EgressGuardError as e:
            reason_code = (
                "source.temporarily_unavailable"
                if e.reason_code in _TRANSIENT_EGRESS_REASONS
                else "configuration.invalid"
            )
            logger.warning(
                "[ApiProcessor] Egress guard denied request: reason_code=%s",
                reason_code,
            )
            return ProcessingResult(
                chunks=[],
                metadata={
                    "error": "Outbound request denied.",
                    "reason_code": reason_code,
                },
            )
        except Exception as e:
            logger.error("[ApiProcessor] Request failed: %s", type(e).__name__)
            return ProcessingResult(
                chunks=[],
                metadata={
                    "error": "Request failed.",
                    "reason_code": "processing.failed",
                },
            )
