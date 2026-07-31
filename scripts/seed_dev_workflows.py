"""Seed local workflow examples for frontend/backend integration testing."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def ensure_schema() -> None:
    from sqlalchemy import text

    from apps.shared.db.base import Base
    from apps.shared.db.session import engine

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


def main() -> None:
    from apps.shared.db.seed import seed_dev_workflow_examples
    from apps.shared.db.session import SessionLocal

    ensure_schema()
    db = SessionLocal()
    try:
        seed_dev_workflow_examples(db)
        print("✅ Dev workflow examples seeded.")
        print("- user: dev@moduly.app / dev-password")
        print("- workflows:")
        print("  - [DEV] 템플릿 응답 워크플로우")
        print("  - [DEV] LLM 문의 의도 분석")
        print("  - [DEV] 우선순위 분기 워크플로우")
    finally:
        db.close()


if __name__ == "__main__":
    main()
