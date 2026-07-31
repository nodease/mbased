from types import SimpleNamespace
from uuid import uuid4

from apps.shared.db.models.organization import Organization
from apps.shared.services import notification_pubsub as module


class _ManagerQuery:
    def __init__(self, rows):
        self.rows = rows
        self.joined = False
        self.filtered = False

    def join(self, *args):
        self.joined = True
        return self

    def filter(self, *args):
        self.filtered = True
        return self

    def all(self):
        return self.rows


class _OrganizationQuery:
    def __init__(self, organization):
        self.organization = organization
        self.filtered = False

    def filter(self, *args):
        self.filtered = True
        return self

    def first(self):
        return self.organization


def test_manager_notification_includes_membership_and_legacy_owner_rows(monkeypatch):
    membership_manager_id = uuid4()
    creator_id = uuid4()
    managed_by_id = uuid4()
    manager_query = _ManagerQuery(
        [SimpleNamespace(user_id=membership_manager_id)]
    )
    organization_query = _OrganizationQuery(
        SimpleNamespace(
            id=uuid4(),
            is_active=True,
            created_by=creator_id,
            managed_by=managed_by_id,
        )
    )

    class Db:
        def query(self, *args):
            if len(args) == 1 and args[0] is Organization:
                return organization_query
            return manager_query

    published = []
    monkeypatch.setattr(
        module,
        "publish_notifications_changed",
        published.append,
    )
    checked_owner_ids = []
    monkeypatch.setattr(
        module,
        "has_organization_manager_permission",
        lambda db, user_id, organization_id: (
            checked_owner_ids.append(user_id) or True
        ),
        raising=False,
    )

    module.publish_notifications_changed_to_organization_managers(
        Db(),
        organization_query.organization.id,
    )

    assert organization_query.filtered is True
    assert manager_query.joined is True
    assert manager_query.filtered is True
    assert checked_owner_ids == [creator_id, managed_by_id]
    assert published == [membership_manager_id, creator_id, managed_by_id]


def test_manager_notification_deduplicates_and_rechecks_owner_fallback(monkeypatch):
    membership_manager_id = uuid4()
    denied_managed_by_id = uuid4()
    manager_query = _ManagerQuery([(membership_manager_id,)])
    organization_query = _OrganizationQuery(
        SimpleNamespace(
            id=uuid4(),
            is_active=True,
            created_by=membership_manager_id,
            managed_by=denied_managed_by_id,
        )
    )

    class Db:
        def query(self, *args):
            if len(args) == 1 and args[0] is Organization:
                return organization_query
            return manager_query

    published = []
    monkeypatch.setattr(module, "publish_notifications_changed", published.append)
    monkeypatch.setattr(
        module,
        "has_organization_manager_permission",
        lambda db, user_id, organization_id: user_id != denied_managed_by_id,
        raising=False,
    )

    module.publish_notifications_changed_to_organization_managers(
        Db(),
        organization_query.organization.id,
    )

    assert published == [membership_manager_id]


def test_manager_recipient_lookup_failure_is_isolated(monkeypatch):
    class Db:
        def query(self, *args):
            raise RuntimeError("database unavailable")

    published = []
    monkeypatch.setattr(
        module,
        "publish_notifications_changed",
        published.append,
    )

    module.publish_notifications_changed_to_organization_managers(
        Db(),
        uuid4(),
    )

    assert published == []


def test_durable_manager_notification_delivery_propagates_failure(monkeypatch):
    class Db:
        def query(self, *args):
            raise RuntimeError("database unavailable")

    try:
        module.deliver_notifications_changed_to_organization_managers(
            Db(),
            uuid4(),
        )
    except RuntimeError as error:
        assert str(error) == "database unavailable"
    else:
        raise AssertionError("durable delivery must expose failures for retry")
