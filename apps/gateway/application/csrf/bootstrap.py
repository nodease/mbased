from __future__ import annotations

from collections.abc import Mapping, Sequence

from apps.gateway.application.csrf.token import CsrfValidationReason


CSRF_BOOTSTRAP_HEADER_NAME = "X-CSRF-Bootstrap"
CSRF_BOOTSTRAP_HEADER_VALUE = "1"
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "same-site"})


def validate_csrf_bootstrap_request(
    headers: Mapping[str, str],
    *,
    allowed_origins: Sequence[str],
) -> CsrfValidationReason | None:
    """Validate that a browser script, rather than an ambient GET, requested a token."""
    if (
        headers.get(CSRF_BOOTSTRAP_HEADER_NAME)
        != CSRF_BOOTSTRAP_HEADER_VALUE
    ):
        return CsrfValidationReason.FETCH_METADATA_INVALID

    fetch_site = headers.get("sec-fetch-site")
    normalized_fetch_site = fetch_site.lower() if fetch_site is not None else None
    if (
        normalized_fetch_site is not None
        and normalized_fetch_site not in _ALLOWED_FETCH_SITES
    ):
        return CsrfValidationReason.FETCH_METADATA_INVALID

    origin = headers.get("origin")
    if origin is not None:
        if origin not in frozenset(allowed_origins):
            return CsrfValidationReason.ORIGIN_INVALID
    elif normalized_fetch_site != "same-origin":
        # Same-origin safe GETs may omit Origin. Cross-origin requests must
        # present an exact allowlisted Origin after the custom-header preflight.
        return CsrfValidationReason.ORIGIN_INVALID

    return None
