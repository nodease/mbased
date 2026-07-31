from types import SimpleNamespace

from apps.shared.services.knowledge_owner_backfill import _ineligible_reason


def test_owner_backfill_classification_requires_org_active_owner_and_membership():
    active_owner = SimpleNamespace(deactivated_at=None)
    active_membership = SimpleNamespace(membership_state="active")

    assert (
        _ineligible_reason(
            SimpleNamespace(organization_id=None), active_owner, active_membership
        )
        == "missing_organization"
    )
    assert (
        _ineligible_reason(
            SimpleNamespace(organization_id="org"),
            SimpleNamespace(deactivated_at="timestamp"),
            active_membership,
        )
        == "owner_inactive_or_missing"
    )
    assert (
        _ineligible_reason(
            SimpleNamespace(organization_id="org"), active_owner, None
        )
        == "owner_membership_invalid"
    )
    assert (
        _ineligible_reason(
            SimpleNamespace(organization_id="org"), active_owner, active_membership
        )
        is None
    )
