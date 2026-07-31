from __future__ import annotations

import hashlib
import json
import time
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpRequester,
)
from apps.shared.services.outbound_operation_policy import (
    GITHUB_ISSUE_COMMENT_CREATE,
    GITHUB_PULL_REQUEST_READ,
    require_outbound_operation_profile,
)
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderContractProfile,
    ProviderContractRegistry,
    ProviderInvocationResult,
    ProviderReplayCapability,
    provider_contract_registry,
)


@dataclass(frozen=True)
class GithubCommentRequest:
    token: str
    repo_owner: str
    repo_name: str
    pr_number: int
    comment_body: str


class GithubProviderError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_REPOSITORY_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GITHUB_API_ORIGIN = "https://api.github.com"
_GITHUB_COMMENT_MAX_REQUEST_BYTES = require_outbound_operation_profile(
    GITHUB_ISSUE_COMMENT_CREATE
).policy.max_request_bytes


def _comment_wire_body(comment_body: str) -> bytes:
    return json.dumps(
        {"body": comment_body},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_repository_segment(value: str) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or not _REPOSITORY_SEGMENT.fullmatch(value)
    ):
        raise GithubProviderError("github.request_invalid")
    return value


def _require_pr_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**31:
        raise GithubProviderError("github.request_invalid")
    return value


def _headers(token: str) -> dict[str, str]:
    if not isinstance(token, str) or not token or any(c in token for c in "\r\n\x00"):
        raise GithubProviderError("github.request_invalid")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "moduly",
    }


def _require_comment_request(value: Any) -> GithubCommentRequest:
    if not isinstance(value, GithubCommentRequest) or not isinstance(
        value.comment_body, str
    ):
        raise GithubProviderError("github.request_invalid")
    try:
        comment_body_size = len(value.comment_body.encode("utf-8"))
    except UnicodeEncodeError:
        raise GithubProviderError("github.request_invalid") from None
    if not value.comment_body or comment_body_size > _GITHUB_COMMENT_MAX_REQUEST_BYTES:
        raise GithubProviderError("github.request_invalid")
    try:
        wire_body_size = len(_comment_wire_body(value.comment_body))
    except UnicodeEncodeError:
        raise GithubProviderError("github.request_invalid") from None
    if wire_body_size > _GITHUB_COMMENT_MAX_REQUEST_BYTES:
        raise GithubProviderError("github.request_invalid")
    _headers(value.token)
    _require_repository_segment(value.repo_owner)
    _require_repository_segment(value.repo_name)
    _require_pr_number(value.pr_number)
    return value


def _require_pull_request_projection(value: Any, *, expected_number: int) -> None:
    if not isinstance(value, dict):
        raise GithubProviderError("github.response_invalid")
    number = value.get("number")
    if (
        not isinstance(value.get("title"), str)
        or not isinstance(value.get("state"), str)
        or not isinstance(value.get("diff_url"), str)
        or not value.get("diff_url")
        or (value.get("body") is not None and not isinstance(value.get("body"), str))
        or isinstance(number, bool)
        or number != expected_number
    ):
        raise GithubProviderError("github.response_invalid")


def _require_pull_files_projection(value: Any) -> None:
    if not isinstance(value, list):
        raise GithubProviderError("github.response_invalid")
    for item in value:
        if not isinstance(item, dict):
            raise GithubProviderError("github.response_invalid")
        counts = (item.get("additions"), item.get("deletions"), item.get("changes"))
        if (
            not isinstance(item.get("filename"), str)
            or not isinstance(item.get("status"), str)
            or any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0
                for count in counts
            )
            or (
                item.get("patch") is not None and not isinstance(item.get("patch"), str)
            )
        ):
            raise GithubProviderError("github.response_invalid")


class GithubReadProvider:
    def __init__(self, *, requester: OperationHttpRequester | None = None) -> None:
        self._requester = requester or OperationHttpRequester()

    def get_pull_request(
        self,
        *,
        token: str,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        owner = _require_repository_segment(repo_owner)
        name = _require_repository_segment(repo_name)
        number = _require_pr_number(pr_number)
        headers = _headers(token)
        base_url = f"{_GITHUB_API_ORIGIN}/repos/{owner}/{name}/pulls/{number}"
        pr = self._read_json(url=base_url, headers=headers)
        files = self._read_json(url=f"{base_url}/files", headers=headers)
        _require_pull_request_projection(pr, expected_number=number)
        _require_pull_files_projection(files)
        return pr, files

    def _read_json(self, *, url: str, headers: dict[str, str]) -> Any:
        try:
            response = self._requester.request(
                operation_id=GITHUB_PULL_REQUEST_READ,
                approved_endpoint=_GITHUB_API_ORIGIN,
                method="GET",
                url=url,
                headers=headers,
            )
        except OperationHttpFailure:
            raise GithubProviderError("github.unavailable") from None
        if response.status_code >= 400:
            raise GithubProviderError("github.provider_rejected")
        try:
            return response.json()
        except (TypeError, ValueError):
            raise GithubProviderError("github.response_invalid") from None


class GithubCommentEffectAdapter:
    _REQUEST_SEMANTICS = frozenset({"github.issue_comment.request.v1"})
    _RESPONSE_SEMANTICS = frozenset({"github.issue_comment.response.v1"})

    def __init__(
        self,
        *,
        contracts: ProviderContractRegistry | None = None,
        active_profile: ProviderContractProfile | None = None,
        historical_profiles: Iterable[ProviderContractProfile] = (),
        requester: OperationHttpRequester | None = None,
    ) -> None:
        provider = "github"
        operation = "github.issue_comment.create"
        historical_profiles = tuple(historical_profiles)
        if contracts is not None and (
            active_profile is not None or historical_profiles
        ):
            raise ValueError("GitHub adapter contract sources cannot be mixed")
        if contracts is None and (active_profile is not None or historical_profiles):
            active_profile = active_profile or provider_contract_registry().active(
                provider,
                operation,
            )
            contracts = ProviderContractRegistry(
                (active_profile, *historical_profiles),
                active_versions={
                    (provider, operation): active_profile.contract_version
                },
            )
        contracts = contracts or provider_contract_registry()
        self._profile = contracts.active(provider, operation)
        profiles = contracts.profiles_for(provider, operation)
        if any(
            profile.provider != provider or profile.operation != operation
            for profile in profiles
        ):
            raise ValueError("GitHub adapter profile does not match its operation")
        self._profiles_by_version: dict[str, ProviderContractProfile] = {}
        for profile in profiles:
            if profile.request_semantics not in self._REQUEST_SEMANTICS:
                raise ValueError("unsupported GitHub request semantics")
            if profile.response_semantics not in self._RESPONSE_SEMANTICS:
                raise ValueError("unsupported GitHub response semantics")
            if profile.replay_projection_semantics is not None:
                raise ValueError("unsupported GitHub replay projection semantics")
            existing = self._profiles_by_version.get(profile.contract_version)
            if existing is not None and existing != profile:
                raise ValueError("GitHub contract version has conflicting definitions")
            self._profiles_by_version[profile.contract_version] = profile
        self.trace_metadata: dict[str, Any] = {}
        self._requester = requester or OperationHttpRequester()

    @property
    def profile(self) -> ProviderContractProfile:
        return self._profile

    def _require_known_profile(
        self,
        profile: ProviderContractProfile,
    ) -> ProviderContractProfile:
        known = self._profiles_by_version.get(profile.contract_version)
        if known != profile:
            raise ValueError("unsupported GitHub provider profile")
        return known

    def prepare_effect(
        self,
        payload: Any,
        *,
        profile: ProviderContractProfile | None = None,
    ) -> PreparedEffectRequest:
        profile = profile or self.profile
        profile = self._require_known_profile(profile)
        if profile.request_semantics != "github.issue_comment.request.v1":
            raise ValueError("unsupported GitHub request semantics")
        try:
            _require_comment_request(payload)
        except GithubProviderError:
            raise ValueError("invalid GitHub comment request") from None
        canonical = json.dumps(
            {
                "token": payload.token,
                "repo_owner": payload.repo_owner,
                "repo_name": payload.repo_name,
                "pr_number": payload.pr_number,
                "comment_body": payload.comment_body,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return PreparedEffectRequest(
            request=payload,
            effect_input_digest=hashlib.sha256(canonical).hexdigest(),
            profile=profile,
        )

    def finalize_provider_call(
        self,
        prepared: PreparedEffectRequest,
        idempotency_key: str | None,
    ) -> PreparedProviderCall:
        profile = prepared.profile or self.profile
        profile = self._require_known_profile(profile)
        if (
            profile.provider_replay is ProviderReplayCapability.SUPPORTED
            or idempotency_key is not None
        ):
            raise ValueError("GitHub comment contract does not support a system key")
        return PreparedProviderCall(
            request=prepared.request,
            idempotency_key=None,
            profile=profile,
        )

    def invoke_effect(self, call: PreparedProviderCall) -> ProviderInvocationResult:
        profile = self._require_known_profile(call.profile)
        if profile.response_semantics != "github.issue_comment.response.v1":
            raise ValueError("unsupported GitHub response semantics")
        request = call.request
        started = time.perf_counter()
        try:
            request = _require_comment_request(request)
        except GithubProviderError:
            self._set_trace(request, None, started)
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="invalid_prepared_request",
                retry_before_effect=False,
            ) from None
        url = f"{_GITHUB_API_ORIGIN}/repos/{request.repo_owner}/{request.repo_name}/issues/{request.pr_number}/comments"
        headers = _headers(request.token)
        try:
            response = self._requester.request(
                operation_id=GITHUB_ISSUE_COMMENT_CREATE,
                approved_endpoint=_GITHUB_API_ORIGIN,
                method="POST",
                url=url,
                headers=headers,
                json_body={"body": request.comment_body},
            )
        except OperationHttpFailure as exc:
            self._set_trace(request, None, started)
            if exc.phase is not OperationHttpFailurePhase.BEFORE_SEND:
                raise EffectInvocationFailure(
                    outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                    error_code="response_lost",
                ) from None
            retry_before_effect = exc.reason_code == "egress.connection_failed"
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code=(
                    "timeout" if retry_before_effect else "invalid_prepared_request"
                ),
                retry_before_effect=retry_before_effect,
            ) from None

        self._set_trace(request, response, started)
        if response.status_code in {401, 403, 404, 410, 422}:
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="provider_rejected_request",
                provider_status_code=response.status_code,
                retry_before_effect=False,
            )
        if response.status_code != 201:
            raise EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="unexpected_provider_status",
                provider_status_code=response.status_code,
            )
        try:
            comment = response.json()
            comment_id = comment["id"]
            comment_url = comment["html_url"]
            comment_body = comment["body"]
            if (
                isinstance(comment_id, bool)
                or not isinstance(comment_id, int)
                or not isinstance(comment_url, str)
                or not comment_url
                or not isinstance(comment_body, str)
            ):
                raise TypeError("invalid GitHub comment response")
            output = {
                "comment_id": comment_id,
                "comment_url": comment_url,
                "comment_body": comment_body,
            }
        except (ValueError, KeyError, TypeError):
            raise EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="response_malformed",
                provider_status_code=response.status_code,
            ) from None
        return ProviderInvocationResult(
            output, provider_status_code=response.status_code
        )

    def create_comment(self, request: GithubCommentRequest) -> dict[str, Any]:
        try:
            prepared = self.prepare_effect(request)
            result = self.invoke_effect(self.finalize_provider_call(prepared, None))
        except (EffectInvocationFailure, ValueError):
            raise GithubProviderError("github.comment_failed") from None
        return dict(result.output)

    def _set_trace(
        self,
        request: Any,
        response: Any,
        started: float,
    ) -> None:
        self.trace_metadata = {
            "http": {
                "method": "POST",
                "status_code": getattr(response, "status_code", None),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "request_size": (
                    len(_comment_wire_body(request.comment_body))
                    if isinstance(getattr(request, "comment_body", None), str)
                    else None
                ),
                "response_size": len(getattr(response, "content", b"") or b""),
            }
        }

    def replay_projection(
        self,
        output: Any,
        *,
        profile: ProviderContractProfile,
    ) -> Any:
        self._require_known_profile(profile)
        raise ValueError("GitHub contract does not support replay projection")


__all__ = [
    "GithubCommentEffectAdapter",
    "GithubCommentRequest",
    "GithubProviderError",
    "GithubReadProvider",
]
