from __future__ import annotations

import os
from contextlib import suppress
from urllib.parse import urlsplit

from apps.shared.services.egress_guard import (
    EgressGuardError,
    download_url_to_temp_file,
)
from apps.shared.services.outbound_operation_policy import WORKFLOW_REMOTE_FILE_FETCH
from apps.workflow_engine.application.remote_file import RemoteFileFetchError

_TRANSIENT_REASONS = frozenset(
    {
        "egress.connection_failed",
        "egress.dns_resolution_failed",
        "egress.timeout",
    }
)
_SAFE_SUFFIXES = frozenset({".csv", ".docx", ".md", ".pdf", ".txt", ".xls", ".xlsx"})


def _safe_suffix(url: str) -> str:
    try:
        suffix = os.path.splitext(urlsplit(url).path)[1].lower()
    except ValueError:
        return ".tmp"
    return suffix if suffix in _SAFE_SUFFIXES else ".tmp"


class GuardedRemoteFileFetcher:
    def fetch_to_temp(self, url: str) -> str:
        try:
            return download_url_to_temp_file(
                url,
                suffix=_safe_suffix(url),
                operation_id=WORKFLOW_REMOTE_FILE_FETCH,
            )
        except EgressGuardError as exc:
            partial_path = getattr(exc, "partial_file_path", None)
            if isinstance(partial_path, str) and partial_path:
                with suppress(OSError):
                    os.remove(partial_path)
            if exc.reason_code in _TRANSIENT_REASONS:
                code = "remote_file.connection_failed"
            elif exc.reason_code in {
                "egress.response_too_large",
                "egress.http_status_rejected",
                "egress.unsupported_content_type",
                "egress.compressed_response_not_allowed",
            }:
                code = "remote_file.response_rejected"
            else:
                code = "remote_file.target_denied"
            raise RemoteFileFetchError(code) from None
        except Exception:
            raise RemoteFileFetchError("remote_file.fetch_failed") from None


__all__ = ["GuardedRemoteFileFetcher"]
