from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from apps.gateway.services.deployment_service import DeploymentService


def test_list_deployments_includes_app_slug_without_app_secret() -> None:
    app_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        url_slug="support-bot",
        auth_secret="must-not-be-projected",
    )
    deployment = SimpleNamespace(id=uuid4())

    app_query = MagicMock()
    app_query.filter.return_value.first.return_value = app

    deployment_query = MagicMock()
    deployment_query.filter.return_value = deployment_query
    deployment_query.order_by.return_value = deployment_query
    deployment_query.offset.return_value = deployment_query
    deployment_query.limit.return_value = deployment_query
    deployment_query.all.return_value = [deployment]

    db = MagicMock()
    db.query.side_effect = [app_query, deployment_query]

    result = DeploymentService.list_deployments(db, app_id=app_id)

    assert result == [deployment]
    assert deployment.url_slug == "support-bot"
    assert not hasattr(deployment, "auth_secret")
