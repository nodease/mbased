import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.gateway.services import llm_service as llm_service_module
from apps.gateway.services.ingestion.processors.file_processor import FileProcessor
from apps.gateway.services.ingestion.parsers.pdf_parser import (
    ExternalParserEgressUnavailable,
    PdfParser,
)
from apps.gateway.services.ingestion.service import IngestionOrchestrator
from apps.gateway.services.llm_service import (
    LLMCredentialNotAvailableError,
    LLMService,
)


def _credential(*, provider_name="llamaparse", is_valid=True):
    return SimpleNamespace(
        id=uuid4(),
        is_valid=is_valid,
        provider=SimpleNamespace(name=provider_name),
        encrypted_config=json.dumps({"apiKey": "test-only-value"}),
    )


def _patch_candidates(monkeypatch, candidates):
    monkeypatch.setattr(
        LLMService,
        "_list_llamaparse_credentials",
        lambda _db, _organization_id: candidates,
    )


def test_resolver_does_not_select_foreign_credential(monkeypatch):
    subject_id = uuid4()
    organization_id = uuid4()
    foreign_credential = _credential()
    _patch_candidates(monkeypatch, [foreign_credential])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        llm_service_module,
        "get_effective_llm_credential_auth_state",
        lambda *_args, **_kwargs: "none",
    )
    monkeypatch.setattr(
        llm_service_module,
        "record_resource_permission_denied",
        lambda **_kwargs: None,
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )

    assert exc_info.value.reason == "credential_not_available"
    assert str(foreign_credential.id) not in str(exc_info.value)


def test_resolver_records_permission_denial_when_no_candidate_is_usable(monkeypatch):
    subject_id = uuid4()
    organization_id = uuid4()
    credential = _credential()
    recorded = []
    _patch_candidates(monkeypatch, [credential])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        llm_service_module,
        "get_effective_llm_credential_auth_state",
        lambda *_args, **_kwargs: "read",
    )
    monkeypatch.setattr(
        llm_service_module,
        "record_resource_permission_denied",
        lambda **kwargs: recorded.append(kwargs),
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )

    assert exc_info.value.reason == "credential_not_available"
    assert recorded == [
        {
            "user_id": subject_id,
            "resource_type": "llm_credential",
            "resource_id": credential.id,
            "action": "use",
            "effective_auth_state": "read",
            "organization_id": organization_id,
        }
    ]


def test_resolver_does_not_audit_denied_candidate_when_another_is_usable(
    monkeypatch,
):
    subject_id = uuid4()
    organization_id = uuid4()
    denied_credential = _credential()
    usable_credential = _credential()
    recorded = []
    _patch_candidates(monkeypatch, [denied_credential, usable_credential])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda _db, _user_id, credential_id, *_args, **_kwargs: (
            credential_id == usable_credential.id
        ),
    )
    monkeypatch.setattr(
        llm_service_module,
        "get_effective_llm_credential_auth_state",
        lambda *_args, **_kwargs: "read",
    )
    monkeypatch.setattr(
        llm_service_module,
        "record_resource_permission_denied",
        lambda **kwargs: recorded.append(kwargs),
    )

    assert (
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )
        == "test-only-value"
    )
    assert recorded == []


def test_resolver_returns_single_authorized_organization_credential(monkeypatch):
    subject_id = uuid4()
    organization_id = uuid4()
    credential = _credential()
    _patch_candidates(monkeypatch, [credential])
    observed = []

    def has_use_permission(_db, user_id, credential_id, action, *, organization_id):
        observed.append((user_id, credential_id, action, organization_id))
        return True

    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        has_use_permission,
    )

    assert (
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )
        == "test-only-value"
    )
    assert observed == [(subject_id, credential.id, "use", organization_id)]


@pytest.mark.parametrize(
    "credential",
    [
        _credential(is_valid=False),
        _credential(provider_name="other-provider"),
    ],
)
def test_resolver_rejects_invalid_or_incompatible_credential(monkeypatch, credential):
    _patch_candidates(monkeypatch, [credential])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=uuid4(), organization_id=uuid4()
        )

    assert exc_info.value.reason == "credential_not_available"


def test_resolver_rejects_missing_execution_context_before_query(monkeypatch):
    monkeypatch.setattr(
        LLMService,
        "_list_llamaparse_credentials",
        lambda *_args, **_kwargs: pytest.fail("candidate query must not run"),
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=None, organization_id=uuid4()
        )

    assert exc_info.value.reason == "credential_context_missing"


def test_resolver_rejects_permission_loss_and_ambiguous_candidates(monkeypatch):
    subject_id = uuid4()
    organization_id = uuid4()
    first = _credential()
    second = _credential()
    _patch_candidates(monkeypatch, [first, second])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )

    assert exc_info.value.reason == "credential_selection_ambiguous"


def test_resolver_sanitizes_malformed_config(monkeypatch):
    subject_id = uuid4()
    organization_id = uuid4()
    credential = _credential()
    credential.encrypted_config = "not-json-with-opaque-material"
    _patch_candidates(monkeypatch, [credential])
    monkeypatch.setattr(
        llm_service_module,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        LLMService.resolve_llamaparse_api_key(
            object(), user_id=subject_id, organization_id=organization_id
        )

    assert "opaque-material" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.reason == "credential_not_available"


def test_file_processor_blocks_external_parser_before_fetch_or_credential_lookup(
    monkeypatch,
):
    monkeypatch.setattr(
        LLMService,
        "resolve_llamaparse_api_key",
        lambda *_args, **_kwargs: pytest.fail("credential lookup must not run"),
    )
    monkeypatch.setattr(
        FileProcessor,
        "_download_file",
        lambda *_args, **_kwargs: pytest.fail("source fetch must not run"),
    )
    result = FileProcessor(
        object(),
        user_id=uuid4(),
        organization_id=uuid4(),
    ).process(
        {
            "file_path": "https://source.example.invalid/document.pdf",
            "strategy": "llamaparse",
        }
    )

    assert result.chunks == []
    assert result.metadata == {
        "error": "External parser is unavailable.",
        "reason_code": "knowledge.raw_parser_egress_unavailable",
    }


def test_pdf_parser_blocks_external_strategy_without_sdk_or_local_fallback(monkeypatch):
    parser = PdfParser()
    monkeypatch.setattr(
        parser,
        "_parse_with_pymupdf",
        lambda _path: pytest.fail("external parser must not fall back locally"),
    )

    with pytest.raises(ExternalParserEgressUnavailable) as exc_info:
        parser.parse(
            "document.pdf",
            strategy="llamaparse",
            api_key="synthetic-test-value",
        )

    assert exc_info.value.reason_code == "knowledge.raw_parser_egress_unavailable"
    assert "synthetic-test-value" not in str(exc_info.value)


def test_ingestion_orchestrator_preserves_organization_context_for_processor(
    monkeypatch,
):
    subject_id = uuid4()
    organization_id = uuid4()
    observed = []

    class Processor:
        def process(self, source_config):
            assert source_config == {"file_path": "document.pdf"}
            return SimpleNamespace(metadata={}, chunks=[])

    monkeypatch.setattr(
        "apps.gateway.services.ingestion.service.IngestionFactory.get_processor",
        lambda *args: observed.append(args) or Processor(),
    )
    orchestrator = IngestionOrchestrator(
        object(),
        user_id=subject_id,
        organization_id=organization_id,
    )
    monkeypatch.setattr(
        orchestrator,
        "_build_config",
        lambda _document: {"file_path": "document.pdf"},
    )

    assert orchestrator._extract_raw_blocks(SimpleNamespace(source_type="FILE")) == []
    assert observed == [("FILE", orchestrator.db, subject_id, organization_id)]
