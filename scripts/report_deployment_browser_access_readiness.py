"""Report active public deployment browser-access rollout readiness.

Output contains aggregate counts and deployment-safe identifiers only. It never
prints parent origins, app or organization identity, graph/config, or secrets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apps.gateway.application.deployment.browser_access_readiness import (  # noqa: E402
    BrowserAccessReadinessCandidate,
    build_browser_access_readiness_report,
)
from apps.shared.db.models.app import App  # noqa: E402
from apps.shared.db.models.workflow_deployment import (  # noqa: E402
    DeploymentType,
    WorkflowDeployment,
)
from apps.shared.db.session import SessionLocal  # noqa: E402
from sqlalchemy import and_  # noqa: E402


def _load_candidates(db) -> list[BrowserAccessReadinessCandidate]:
    rows = (
        db.query(WorkflowDeployment)
        .join(
            App,
            and_(
                App.id == WorkflowDeployment.app_id,
                App.active_deployment_id == WorkflowDeployment.id,
            ),
        )
        .filter(
            WorkflowDeployment.is_active.is_(True),
            WorkflowDeployment.type.in_(
                (DeploymentType.CHATBOT, DeploymentType.WIDGET)
            ),
        )
        .yield_per(500)
    )
    return [
        BrowserAccessReadinessCandidate(
            deployment_id=str(row.id),
            deployment_version=row.version,
            deployment_type=row.type.value,
            browser_access_policy=row.browser_access_policy,
        )
        for row in rows
    ]


def main() -> int:
    db = SessionLocal()
    try:
        report = build_browser_access_readiness_report(_load_candidates(db))
    except Exception as exc:
        print(
            "browser access readiness report failed: "
            f"error_type={type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    finally:
        db.close()

    print(json.dumps(report.safe_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
