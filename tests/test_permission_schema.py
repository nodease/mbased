import pytest
from pydantic import ValidationError
from uuid import uuid4

from apps.shared.schemas.permission import (
    AuditPermissionGrantRequest,
    BulkPermissionGrantRequest,
    KnowledgePermissionGrantRequest,
    LLMPermissionGrantRequest,
    PermissionGrantRequest,
    WorkflowPermissionGrantRequest,
)


@pytest.mark.parametrize(
    "schema",
    [
        PermissionGrantRequest,
        WorkflowPermissionGrantRequest,
        KnowledgePermissionGrantRequest,
        LLMPermissionGrantRequest,
    ],
)
def test_resource_permission_grant_requests_accept_operational_matrix(schema):
    assert schema(auth_state="MANAGER").auth_state == "manager"


@pytest.mark.parametrize(
    "schema",
    [
        PermissionGrantRequest,
        WorkflowPermissionGrantRequest,
        KnowledgePermissionGrantRequest,
        LLMPermissionGrantRequest,
    ],
)
def test_operational_permission_grant_requests_reject_audit_states(schema):
    with pytest.raises(ValidationError):
        schema(auth_state="raw_auditor")


def test_audit_permission_grant_request_accepts_audit_matrix():
    assert AuditPermissionGrantRequest(auth_state="RAW_AUDITOR").auth_state == (
        "raw_auditor"
    )


def test_audit_permission_grant_request_rejects_operational_states():
    with pytest.raises(ValidationError):
        AuditPermissionGrantRequest(auth_state="builder")


def test_bulk_permission_grant_request_accepts_unique_resource_and_grantee_ids():
    resource_ids = [uuid4(), uuid4()]
    grantee_ids = [uuid4(), uuid4()]

    request = BulkPermissionGrantRequest(
        resource_type="workflow",
        resource_ids=resource_ids,
        grantee_type="team",
        grantee_ids=grantee_ids,
        auth_state="BUILDER",
    )

    assert request.resource_ids == resource_ids
    assert request.grantee_ids == grantee_ids
    assert request.auth_state == "builder"


def test_bulk_permission_grant_request_rejects_duplicate_ids():
    duplicate_grantee_id = uuid4()
    with pytest.raises(ValidationError):
        BulkPermissionGrantRequest(
            resource_type="workflow",
            resource_ids=[uuid4(), uuid4()],
            grantee_type="team",
            grantee_ids=[duplicate_grantee_id, duplicate_grantee_id],
            auth_state="viewer",
        )


def test_bulk_permission_grant_request_rejects_more_than_50_pairs():
    with pytest.raises(ValidationError):
        BulkPermissionGrantRequest(
            resource_type="workflow",
            resource_ids=[uuid4() for _ in range(8)],
            grantee_type="team",
            grantee_ids=[uuid4() for _ in range(7)],
            auth_state="viewer",
        )
