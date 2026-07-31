from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.services.credential_encryption import CredentialEncryptionError
from apps.shared.services.workflow_node_secret_service import (
    WorkflowNodeSecretService,
)


def test_store_node_secret_checks_scope_and_returns_only_reference(monkeypatch) -> None:
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    user_id = uuid4()
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = workflow
    reference = f"workflow-node-secret://{uuid4()}"
    create_reference = Mock(return_value=reference)

    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        WorkflowNodeSecretService,
        "create_reference",
        create_reference,
    )
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.get_workflow_node_secret_encryption_service",
        lambda: object(),
        raising=False,
    )

    result = WorkflowService.store_node_secret(
        db,
        workflow_id=str(workflow.id),
        active_organization_id=workflow.organization_id,
        user_id=user_id,
        node_id="slack-1",
        node_type="slackPostNode",
        parameter_key="bot_token",
        secret_value="synthetic-input-value",
    )

    assert result == {"secret_reference": reference, "configured": True}
    assert "synthetic-input-value" not in str(result)
    create_reference.assert_called_once()
    call = create_reference.call_args.kwargs
    assert call["workflow_id"] == workflow.id
    assert call["organization_id"] == workflow.organization_id
    assert call["user_id"] == user_id
    assert call["secret_value"] == "synthetic-input-value"
    db.commit.assert_called_once_with()


def test_store_node_secret_returns_safe_503_when_keyring_is_unavailable(
    monkeypatch,
) -> None:
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = workflow
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )

    def unavailable_keyring():
        raise CredentialEncryptionError("synthetic unavailable keyring")

    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.get_workflow_node_secret_encryption_service",
        unavailable_keyring,
    )

    with pytest.raises(HTTPException) as captured:
        WorkflowService.store_node_secret(
            db,
            workflow_id=str(workflow.id),
            active_organization_id=workflow.organization_id,
            user_id=uuid4(),
            node_id="slack-1",
            node_type="slackPostNode",
            parameter_key="bot_token",
            secret_value="synthetic-input-value",
        )

    assert captured.value.status_code == 503
    assert captured.value.detail == "workflow.node_secret_storage_unavailable"
    assert "synthetic-input-value" not in str(captured.value.detail)
    db.rollback.assert_called_once_with()


def test_store_node_secret_rejects_invalid_slack_webhook_before_encryption(
    monkeypatch,
) -> None:
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = workflow
    create_reference = Mock()
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        WorkflowNodeSecretService,
        "create_reference",
        create_reference,
    )

    with pytest.raises(HTTPException) as captured:
        WorkflowService.store_node_secret(
            db,
            workflow_id=str(workflow.id),
            active_organization_id=workflow.organization_id,
            user_id=uuid4(),
            node_id="slack-1",
            node_type="slackPostNode",
            parameter_key="url",
            secret_value="https://example.com/not-a-slack-webhook",
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "workflow.node_secret_invalid"
    create_reference.assert_not_called()
    db.rollback.assert_called_once_with()


def test_store_node_secret_rejects_workflow_outside_active_organization(
    monkeypatch,
) -> None:
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = workflow
    create_reference = Mock()
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        WorkflowNodeSecretService,
        "create_reference",
        create_reference,
    )

    with pytest.raises(HTTPException) as captured:
        WorkflowService.store_node_secret(
            db,
            workflow_id=str(workflow.id),
            active_organization_id=uuid4(),
            user_id=uuid4(),
            node_id="slack-1",
            node_type="slackPostNode",
            parameter_key="bot_token",
            secret_value="synthetic-input-value",
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == "Workflow not found"
    create_reference.assert_not_called()
    db.commit.assert_not_called()
