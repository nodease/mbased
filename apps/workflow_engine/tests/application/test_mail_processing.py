from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from apps.shared.domain.mail_processing import MailSourceReference
from apps.shared.services.credential_encryption import CredentialEncryptionService
from apps.workflow_engine.application.mail_processing import (
    AcknowledgementAdmission,
    DraftAdmission,
    GmailDraftCreated,
    GmailDraftOutcomeUnknown,
    GmailDraftRejectedBeforeEffect,
    GmailSourceMessage,
    MailProcessingApplicationError,
    MailProcessingApplicationService,
    ProcessingRegistration,
    ProtectedReference,
)


class FakeRepository:
    def __init__(self):
        self.processing_id = uuid.uuid4()
        self.effect_id = uuid.uuid4()
        self.source_reference = None
        self.admission_status = "claimed"
        self.admission_acquired = True
        self.acknowledgement_status = "ack_pending"
        self.acknowledgement_acquired = True
        self.calls = []

    def register_message(self, **kwargs):
        self.calls.append(("register", kwargs))
        self.source_reference = kwargs["source_reference"]
        return self.processing_id

    def load_source_reference(self, **kwargs):
        self.calls.append(("load_source", kwargs))
        return self.source_reference

    def get_processing_credential_id(self, **kwargs):
        self.calls.append(("get_credential", kwargs))
        return self.credential_id

    def claim_draft_effect(self, **kwargs):
        self.calls.append(("claim", kwargs))
        return DraftAdmission(
            effect_id=self.effect_id,
            status=self.admission_status,
            acquired=self.admission_acquired,
        )

    def record_draft_succeeded(self, **kwargs):
        self.calls.append(("draft_succeeded", kwargs))

    def record_draft_failed_before_effect(self, **kwargs):
        self.calls.append(("draft_failed", kwargs))

    def record_draft_outcome_unknown(self, **kwargs):
        self.calls.append(("draft_unknown", kwargs))

    def require_effects_succeeded(self, **kwargs):
        self.calls.append(("require_effects", kwargs))
        return AcknowledgementAdmission(
            status=self.acknowledgement_status,
            acquired=self.acknowledgement_acquired,
        )

    def record_acknowledgement_succeeded(self, **kwargs):
        self.calls.append(("ack_succeeded", kwargs))

    def record_acknowledgement_pending(self, **kwargs):
        self.calls.append(("ack_pending", kwargs))


class FakeGmailProvider:
    def __init__(self, *, failure=None, resolve_failure=None):
        self.failure = failure
        self.resolve_failure = resolve_failure
        self.create_calls = 0
        self.resolve_calls = 0

    def resolve_source_message(self, *, source_reference):
        self.resolve_calls += 1
        if self.resolve_failure:
            raise self.resolve_failure
        assert source_reference.uid == 2
        return GmailSourceMessage(
            message_id="gmail-message",
            thread_id="gmail-thread",
            rfc_message_id="<id@example.com>",
            references=("<parent@example.com>",),
            reply_to="sender@example.com",
            subject="Question",
        )

    def create_reply_draft(self, request):
        self.create_calls += 1
        if self.failure:
            raise self.failure
        return GmailDraftCreated(provider_draft_id="provider-draft")


class FakeAcknowledgement:
    def __init__(self, *, fails=False):
        self.fails = fails
        self.calls = 0

    def acknowledge(self, *, source_reference):
        self.calls += 1
        if self.fails:
            raise OSError("raw provider failure must not escape")


@pytest.fixture
def service_and_repository():
    repository = FakeRepository()
    repository.credential_id = uuid.uuid4()
    encryption = CredentialEncryptionService(
        {"v1": Fernet.generate_key().decode()}, "v1"
    )
    service = MailProcessingApplicationService(
        repository=repository, encryption=encryption
    )
    registration = ProcessingRegistration(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        source_node_id="mail-source",
        credential_id=uuid.uuid4(),
        provider="gmail",
        source=MailSourceReference(
            uid_validity=1, uid=2, message_id="<id@example.com>"
        ),
    )
    repository.credential_id = registration.credential_id
    return service, repository, registration


def _draft_kwargs(repository, registration, provider):
    return {
        "processing_ref": str(repository.processing_id),
        "organization_id": registration.organization_id,
        "workflow_id": registration.workflow_id,
        "deployment_id": registration.deployment_id,
        "credential_id": registration.credential_id,
        "node_id": "gmail-draft",
        "reply_body": "답장 본문",
        "lease_owner_hash": "a" * 64,
        "lease_expires_at": datetime.now(timezone.utc),
        "provider_factory": lambda: provider,
        "authorization_guard": lambda: None,
    }


def test_registration_protects_source_reference(service_and_repository):
    service, repository, registration = service_and_repository
    assert service.register_message(registration) == str(repository.processing_id)
    stored = repository.calls[0][1]
    assert stored["message_identity_hash"]
    assert "<id@example.com>" not in stored["source_reference"].ciphertext


def test_successful_draft_is_recorded_once_with_opaque_ref(service_and_repository):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider()
    result = service.create_gmail_reply_draft(
        **_draft_kwargs(repository, registration, provider)
    )
    assert result == {
        "status": "succeeded",
        "draft_ref": str(repository.effect_id),
        "processing_ref": str(repository.processing_id),
    }
    assert provider.create_calls == 1
    succeeded = next(call for call in repository.calls if call[0] == "draft_succeeded")
    protected = succeeded[1]["draft_reference"]
    assert isinstance(protected, ProtectedReference)
    assert "provider-draft" not in protected.ciphertext


def test_duplicate_succeeded_admission_never_calls_provider(service_and_repository):
    service, repository, registration = service_and_repository
    repository.admission_status = "succeeded"
    provider = FakeGmailProvider()
    kwargs = _draft_kwargs(repository, registration, provider)
    provider_factory = MagicMock(return_value=provider)
    kwargs["provider_factory"] = provider_factory
    result = service.create_gmail_reply_draft(**kwargs)
    assert result["draft_ref"] == str(repository.effect_id)
    provider_factory.assert_not_called()
    assert provider.create_calls == 0


def test_active_claim_owned_by_another_execution_never_calls_provider(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    repository.admission_acquired = False
    provider = FakeGmailProvider()

    with pytest.raises(MailProcessingApplicationError) as exc_info:
        service.create_gmail_reply_draft(
            **_draft_kwargs(repository, registration, provider)
        )

    assert exc_info.value.reason_code == "mail.draft_effect_in_progress"
    assert provider.create_calls == 0


def test_application_clock_sets_deterministic_default_lease(service_and_repository):
    _service, repository, registration = service_and_repository
    fixed_now = datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc)
    encryption = CredentialEncryptionService(
        {"v1": Fernet.generate_key().decode()},
        "v1",
    )
    service = MailProcessingApplicationService(
        repository=repository,
        encryption=encryption,
        clock=lambda: fixed_now,
    )
    service.register_message(registration)
    kwargs = _draft_kwargs(repository, registration, FakeGmailProvider())
    kwargs["lease_expires_at"] = None

    service.create_gmail_reply_draft(**kwargs)

    claim = next(call for call in repository.calls if call[0] == "claim")
    assert claim[1]["lease_expires_at"].isoformat() == "2026-07-12T03:05:00+00:00"


def test_outcome_unknown_is_recorded_and_not_reclassified(service_and_repository):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider(
        failure=GmailDraftOutcomeUnknown("mail.draft_outcome_unknown")
    )
    with pytest.raises(GmailDraftOutcomeUnknown):
        service.create_gmail_reply_draft(
            **_draft_kwargs(repository, registration, provider)
        )
    assert provider.create_calls == 1
    assert [name for name, _ in repository.calls].count("draft_unknown") == 1


def test_unexpected_provider_error_fails_safe_as_outcome_unknown(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider(failure=OSError("secret-bearing raw error"))
    with pytest.raises(GmailDraftOutcomeUnknown) as exc_info:
        service.create_gmail_reply_draft(
            **_draft_kwargs(repository, registration, provider)
        )
    assert str(exc_info.value) == "mail.draft_outcome_unknown"


def test_source_resolution_failure_is_safe_before_effect(service_and_repository):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider(resolve_failure=OSError("raw lookup error"))
    with pytest.raises(MailProcessingApplicationError) as exc_info:
        service.create_gmail_reply_draft(
            **_draft_kwargs(repository, registration, provider)
        )
    assert exc_info.value.reason_code == "mail.source_resolution_failed"
    assert provider.create_calls == 0
    assert "draft_failed" in [name for name, _ in repository.calls]


def test_authorization_revocation_before_provider_call_is_failed_before_effect(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider()
    kwargs = _draft_kwargs(repository, registration, provider)
    kwargs["authorization_guard"] = lambda: (_ for _ in ()).throw(
        PermissionError("raw authorization detail")
    )

    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        service.create_gmail_reply_draft(**kwargs)

    assert exc_info.value.reason_code == "mail.credential_not_available"
    assert exc_info.value.__cause__ is None
    assert provider.resolve_calls == 0
    assert provider.create_calls == 0
    failed = next(call for call in repository.calls if call[0] == "draft_failed")
    assert failed[1]["safe_reason_code"] == "mail.credential_not_available"


def test_authorization_is_rechecked_after_source_lookup_before_mutation(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    provider = FakeGmailProvider()
    guard_calls = 0

    def authorization_guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise PermissionError("revoked after source lookup")

    kwargs = _draft_kwargs(repository, registration, provider)
    kwargs["authorization_guard"] = authorization_guard

    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        service.create_gmail_reply_draft(**kwargs)

    assert exc_info.value.reason_code == "mail.credential_not_available"
    assert provider.resolve_calls == 1
    assert provider.create_calls == 0


def test_acknowledgement_requires_effect_refs(service_and_repository):
    service, repository, registration = service_and_repository
    with pytest.raises(MailProcessingApplicationError) as exc_info:
        service.acknowledge_message(
            processing_ref=str(repository.processing_id),
            effect_refs=[],
            organization_id=registration.organization_id,
            workflow_id=registration.workflow_id,
            deployment_id=registration.deployment_id,
            acknowledgement_factory=lambda _credential_id: FakeAcknowledgement(),
            required_effect_contract_hash="a" * 64,
            authorization_guard=lambda _credential_id: None,
            lease_owner_hash="b" * 64,
        )
    assert exc_info.value.reason_code == "mail.required_effects_missing"


def test_processing_credential_lookup_preserves_deployment_scope(
    service_and_repository,
):
    service, repository, registration = service_and_repository

    credential_id = service.processing_credential_id(
        processing_ref=str(repository.processing_id),
        organization_id=registration.organization_id,
        workflow_id=registration.workflow_id,
        deployment_id=registration.deployment_id,
    )

    assert credential_id == registration.credential_id
    lookup = next(call for call in repository.calls if call[0] == "get_credential")
    assert lookup[1]["deployment_id"] == registration.deployment_id


def test_ack_failure_retries_only_acknowledgement(service_and_repository):
    service, repository, registration = service_and_repository
    service.register_message(registration)
    acknowledgement = FakeAcknowledgement(fails=True)
    with pytest.raises(MailProcessingApplicationError) as exc_info:
        service.acknowledge_message(
            processing_ref=str(repository.processing_id),
            effect_refs=[str(repository.effect_id)],
            organization_id=registration.organization_id,
            workflow_id=registration.workflow_id,
            deployment_id=registration.deployment_id,
            acknowledgement_factory=lambda _credential_id: acknowledgement,
            required_effect_contract_hash="a" * 64,
            authorization_guard=lambda _credential_id: None,
            lease_owner_hash="b" * 64,
        )
    assert exc_info.value.reason_code == "mail.acknowledgement_failed"
    assert acknowledgement.calls == 1
    assert "ack_pending" in [name for name, _ in repository.calls]


def test_completed_acknowledgement_reuses_terminal_result_without_provider_call(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    repository.acknowledgement_status = "succeeded"
    repository.acknowledgement_acquired = False
    acknowledgement = FakeAcknowledgement()
    acknowledgement_factory = MagicMock(return_value=acknowledgement)
    authorization_guard = MagicMock()

    result = service.acknowledge_message(
        processing_ref=str(repository.processing_id),
        effect_refs=[str(repository.effect_id)],
        organization_id=registration.organization_id,
        workflow_id=registration.workflow_id,
        deployment_id=registration.deployment_id,
        acknowledgement_factory=acknowledgement_factory,
        required_effect_contract_hash="a" * 64,
        authorization_guard=authorization_guard,
        lease_owner_hash="b" * 64,
    )

    assert result["status"] == "succeeded"
    acknowledgement_factory.assert_not_called()
    authorization_guard.assert_not_called()
    assert acknowledgement.calls == 0


def test_active_acknowledgement_admission_blocks_duplicate_provider_call(
    service_and_repository,
):
    service, repository, registration = service_and_repository
    repository.acknowledgement_acquired = False
    acknowledgement = FakeAcknowledgement()

    with pytest.raises(MailProcessingApplicationError) as exc_info:
        service.acknowledge_message(
            processing_ref=str(repository.processing_id),
            effect_refs=[str(repository.effect_id)],
            organization_id=registration.organization_id,
            workflow_id=registration.workflow_id,
            deployment_id=registration.deployment_id,
            acknowledgement_factory=lambda _credential_id: acknowledgement,
            required_effect_contract_hash="a" * 64,
            authorization_guard=lambda _credential_id: None,
            lease_owner_hash="b" * 64,
        )

    assert exc_info.value.reason_code == "mail.acknowledgement_in_progress"
    assert acknowledgement.calls == 0
