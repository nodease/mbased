import uuid

import pytest
from apps.shared.services.tracing.policy import TracePolicyService


def test_global_policy_scope_rejects_scope_id():
    with pytest.raises(ValueError, match="global_scope_must_not_have_scope_id"):
        TracePolicyService.validate_policy_scope("global", uuid.uuid4())


def test_app_policy_scope_requires_scope_id():
    with pytest.raises(ValueError, match="app_scope_requires_scope_id"):
        TracePolicyService.validate_policy_scope("app", None)


def test_organization_policy_scope_is_not_supported_for_management_api():
    with pytest.raises(ValueError, match="organization_scope_policy_not_supported"):
        TracePolicyService.validate_policy_scope("organization", uuid.uuid4())


def test_app_policy_scope_accepts_uuid():
    app_id = uuid.uuid4()

    assert TracePolicyService.validate_policy_scope("app", str(app_id)) == app_id
