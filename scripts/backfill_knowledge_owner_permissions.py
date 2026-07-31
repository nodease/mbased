"""Dry-run or apply the MBA-231 legacy KB owner manager-grant backfill.

Output is restricted to aggregate safe counters. It never prints user, KB,
document, source, connection, or credential values.
"""

from __future__ import annotations

import argparse
import json

from apps.shared.db.session import SessionLocal
from apps.shared.services.knowledge_owner_backfill import (
    backfill_knowledge_owner_manager_permissions,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist eligible manager grants; default is dry-run.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = backfill_knowledge_owner_manager_permissions(db, apply=args.apply)
    finally:
        db.close()
    print(json.dumps(report.safe_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
