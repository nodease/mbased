"""Seed/reset the final demo database state."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from apps.shared.db.base import Base  # noqa: E402
from apps.shared.db.demo_seed import (  # noqa: E402
    DEMO_EMBEDDING_DIMENSION,
    DEMO_EMBEDDING_MODEL,
    DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV,
    DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV,
    demo_summary,
    reset_demo_data,
    reset_test_data,
    seed_demo_data,
    seed_test_data,
    validate_demo_seed_prerequisites,
)
from apps.shared.db.session import SessionLocal, engine  # noqa: E402
from apps.shared.services.llm_client.openai_client import OpenAIClient  # noqa: E402
from apps.shared.services.knowledge_schema_readiness import (  # noqa: E402
    check_knowledge_schema_readiness_with_inspector,
)
from apps.shared.services.alembic_readiness import (  # noqa: E402
    check_alembic_readiness_with_inspector,
)
import apps.shared.db.models  # noqa: E402, F401


OPENAI_API_BASE_URL = "https://api.openai.com/v1"
OPENAI_KEY_VALIDATION_INPUT = "Nodease demo seed credential verification."


REQUIRED_DEMO_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "knowledge_bases": {
        "id",
        "organization_id",
        "name",
        "embedding_model",
        "top_k",
        "similarity_threshold",
        "active_document_version_id",
        "source_identity_id",
        "sync_state",
        "lifecycle_state",
        "user_id",
    },
    "knowledge_collections": {
        "id",
        "organization_id",
        "name",
        "source_identity_id",
        "source_connector_ref",
        "is_system_managed",
        "sync_state",
        "lifecycle_state",
        "safe_metadata",
        "created_by",
    },
    "knowledge_collection_items": {
        "id",
        "organization_id",
        "collection_id",
        "knowledge_base_id",
        "safe_source_path_ref",
        "rank",
        "safe_metadata",
    },
    "documents": {
        "id",
        "knowledge_base_id",
        "filename",
        "file_path",
        "source_type",
        "content_hash",
        "status",
        "chunk_size",
        "chunk_overlap",
        "meta_info",
        "embedding_model",
    },
    "document_versions": {
        "id",
        "organization_id",
        "knowledge_base_id",
        "legacy_document_id",
        "source_identity_id",
        "version_number",
        "status",
        "content_hash",
        "chunking_fingerprint",
        "embedding_model",
        "safe_metadata",
    },
    "document_chunks": {
        "id",
        "document_id",
        "document_version_id",
        "knowledge_base_id",
        "content",
        "embedding",
        "chunk_index",
        "parent_chunk_id",
        "chunk_level",
        "section_path",
        "heading",
        "token_count",
        "metadata",
    },
    "team_knowledge_permissions": {
        "id",
        "grantee_organization_id",
        "team_id",
        "knowledge_base_id",
        "auth_state",
        "assigned_by",
        "assigned_at",
        "options",
        "flags",
    },
    "team_knowledge_collection_permissions": {
        "id",
        "grantee_organization_id",
        "team_id",
        "knowledge_collection_id",
        "permission_action",
        "assigned_by",
        "assigned_at",
        "options",
        "flags",
    },
    "llm_providers": {
        "id",
        "name",
        "type",
        "auth_type",
        "doc_url",
    },
    "llm_models": {
        "id",
        "provider_id",
        "model_id_for_api_call",
        "name",
        "type",
        "context_window",
        "input_price_1k",
        "output_price_1k",
        "is_active",
        "metadata",
    },
}


class DemoSchemaReadinessError(RuntimeError):
    """Raised when an existing local DB schema is stale for demo seed."""


def ensure_schema(*, drop_existing_data: bool = False) -> None:
    """Create local schema objects when running against an empty dev database."""
    if drop_existing_data:
        with engine.begin() as connection:
            # apps.workflow_id와 workflows.app_id는 순환 FK라 SQLAlchemy drop_all이
            # 삭제 순서를 정할 수 없다. 전체 초기화는 로컬 public schema를 재생성한다.
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


def schema_readiness_gaps(
    schema_inspector,
    *,
    required_columns: Mapping[str, set[str]] | None = None,
) -> dict[str, object]:
    """Return missing table/column gaps for the current demo seed contract."""
    required = required_columns or REQUIRED_DEMO_SCHEMA_COLUMNS
    result = check_knowledge_schema_readiness_with_inspector(
        schema_inspector,
        required,
    )
    missing_tables = sorted(result.missing_tables)
    missing_columns = {
        table_name: result.missing_columns[table_name]
        for table_name in sorted(result.missing_columns)
        if table_name not in result.missing_tables
    }

    return {
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "reason": result.reason,
    }


def _alembic_script_directory() -> ScriptDirectory:
    config = Config(str(ROOT_DIR / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT_DIR / "apps" / "shared" / "alembic"),
    )
    return ScriptDirectory.from_config(config)


def alembic_readiness_gaps(
    schema_inspector,
    *,
    code_heads: list[str] | None = None,
    known_revisions: list[str] | None = None,
) -> dict[str, object]:
    script = None
    if code_heads is None or known_revisions is None:
        script = _alembic_script_directory()
    code_heads = code_heads if code_heads is not None else list(script.get_heads())
    known_revisions = (
        known_revisions
        if known_revisions is not None
        else [revision.revision for revision in script.walk_revisions()]
    )
    result = check_alembic_readiness_with_inspector(
        schema_inspector,
        code_heads=code_heads,
        known_revisions=known_revisions,
    )
    return {
        "ready": result.ready,
        "missing_version_table": result.missing_version_table,
        "database_behind": result.database_behind,
        "split_heads": result.split_heads,
        "code_heads": result.code_heads,
        "database_revisions": result.database_revisions,
        "unknown_database_revisions": result.unknown_database_revisions,
        "reason": result.reason,
    }


def _schema_has_gaps(gaps: Mapping[str, object]) -> bool:
    migration = gaps.get("migration")
    if isinstance(migration, Mapping) and not migration.get("ready", True):
        return True
    return bool(
        gaps.get("missing_tables")
        or gaps.get("missing_columns")
        or gaps.get("reason")
    )


def format_schema_readiness_error(gaps: Mapping[str, object]) -> str:
    """Build a user-facing stale schema remediation message."""
    lines = [
        "[ERROR] Local demo DB schema is not ready for the current demo seed.",
        "Base.metadata.create_all() creates missing tables but does not ALTER stale tables.",
        "Seed was stopped before demo data writes so the API does not appear seeded while later failing with 500.",
    ]
    missing_tables = gaps["missing_tables"]
    if missing_tables:
        lines.append("")
        lines.append("Missing tables:")
        for table_name in missing_tables:
            lines.append(f"- {table_name}")

    missing_columns = gaps["missing_columns"]
    if missing_columns:
        lines.append("")
        lines.append("Missing columns:")
        for table_name, columns in missing_columns.items():
            lines.append(f"- {table_name}: {', '.join(columns)}")

    reason = gaps.get("reason")
    if reason:
        lines.append("")
        lines.append(f"Readiness check failed: {reason}")

    migration = gaps.get("migration")
    if isinstance(migration, Mapping) and not migration.get("ready", True):
        lines.append("")
        lines.append("Migration readiness:")
        if migration.get("missing_version_table"):
            lines.append("- alembic_version table is missing.")
        if migration.get("database_behind"):
            code_heads = ", ".join(migration.get("code_heads") or [])
            database_revisions = ", ".join(
                migration.get("database_revisions") or []
            )
            lines.append(
                "- DB revision does not match code head"
                f" (db={database_revisions or 'none'}, code={code_heads or 'none'})."
            )
        if migration.get("split_heads"):
            lines.append(
                "- Alembic migration graph has multiple code heads; merge heads first."
            )
        unknown_database_revisions = migration.get("unknown_database_revisions") or []
        if unknown_database_revisions:
            lines.append(
                "- DB has revisions not found in code: "
                + ", ".join(unknown_database_revisions)
            )
        migration_reason = migration.get("reason")
        if migration_reason:
            lines.append(f"- Migration readiness check failed: {migration_reason}")

    lines.extend(
        [
            "",
            "Resolve with one of the following:",
            "1. Preserve local data and apply migrations:",
            "   apps/gateway/.venv/Scripts/python.exe -m alembic -c apps/shared/alembic.ini upgrade heads",
            "2. Recreate disposable local demo data:",
            "   apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --drop-existing-data --yes",
        ]
    )
    return "\n".join(lines)


def check_demo_schema_readiness() -> None:
    schema_inspector = inspect(engine)
    gaps = schema_readiness_gaps(schema_inspector)
    gaps["migration"] = alembic_readiness_gaps(schema_inspector)
    if _schema_has_gaps(gaps):
        raise DemoSchemaReadinessError(format_schema_readiness_error(gaps))


def resolve_runtime_openai_api_key() -> str:
    """Return the runtime key from the environment or a hidden terminal prompt."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if api_key:
        return api_key

    if not sys.stdin.isatty():
        raise RuntimeError(
            "OPENAI_API_KEY is required in a non-interactive environment. "
            "Set the environment variable or run this command from a terminal."
        )

    try:
        api_key = getpass.getpass("OpenAI API key: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise RuntimeError("OpenAI API key input was cancelled.") from exc
    if not api_key:
        raise RuntimeError("OpenAI API key is required.")

    os.environ["OPENAI_API_KEY"] = api_key
    return api_key


def validate_runtime_openai_api_key(api_key: str) -> bool:
    """Check that the key can create the embedding used by demo RAG search."""
    try:
        client = OpenAIClient(
            model_id=DEMO_EMBEDDING_MODEL,
            credentials={"apiKey": api_key, "baseUrl": OPENAI_API_BASE_URL},
        )
        embedding = client.embed_sync(OPENAI_KEY_VALIDATION_INPUT)
    except Exception:
        return False
    return isinstance(embedding, list) and len(embedding) == DEMO_EMBEDDING_DIMENSION


def prepare_runtime_openai_credential() -> str:
    """Resolve and validate the demo runtime key without logging secret values."""
    api_key = resolve_runtime_openai_api_key()
    if validate_runtime_openai_api_key(api_key):
        print("[OK] OpenAI embedding credential verified.")
        return api_key

    print(
        "[WARN] OpenAI API key could not be verified. "
        "The demo seed may not be able to perform live RAG search.",
        file=sys.stderr,
    )
    if not sys.stdin.isatty():
        raise RuntimeError(
            "OpenAI API key verification requires an interactive confirmation. "
            "Set OPENAI_API_KEY and run the seed from a terminal."
        )

    answer = input("Continue seeding with this key anyway? [Y/n] ").strip().lower()
    if answer in {"n", "no"}:
        raise SystemExit("OpenAI API key verification was not accepted; seed cancelled.")
    return api_key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed Nodease final demo data into the local database."
    )
    parser.add_argument(
        "--profile",
        choices=("demo", "test"),
        default="demo",
        help="Seed profile to apply. demo is final presentation data; test is mutable QA data.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete selected profile seed rows and recreate that profile state.",
    )
    parser.add_argument(
        "--drop-existing-data",
        action="store_true",
        help=(
            "Drop all mapped local tables before recreating the demo seed. "
            "Must be used with --reset --yes."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive operations such as --drop-existing-data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned demo seed summary without touching the database.",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help=(
            "Skip Base.metadata.create_all. Schema readiness is still checked "
            "before demo data is written."
        ),
    )
    parser.add_argument(
        "--regenerate-knowledge-fixture",
        action="store_true",
        help=(
            "Regenerate demo Knowledge chunk/embedding fixture from local legal docs "
            "and OPENAI_API_KEY instead of using the precomputed fixture."
        ),
    )
    parser.add_argument(
        "--enable-runtime-openai-credential",
        action="store_true",
        help=(
            "Validate OPENAI_API_KEY or prompt securely, then seed it as the local "
            "demo runtime OpenAI credential. Use only for disposable demo databases."
        ),
    )
    args = parser.parse_args()
    if args.drop_existing_data and not args.reset:
        parser.error("--drop-existing-data must be used with --reset.")
    if args.drop_existing_data and not args.yes:
        parser.error("--drop-existing-data requires --yes.")
    if args.drop_existing_data and args.skip_schema:
        parser.error("--drop-existing-data cannot be used with --skip-schema.")
    return args


def main() -> None:
    args = parse_args()
    if args.regenerate_knowledge_fixture:
        os.environ[DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV] = "1"
    if args.enable_runtime_openai_credential:
        os.environ[DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV] = "1"
    summary = demo_summary(args.profile)

    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    if args.profile == "demo":
        if args.enable_runtime_openai_credential:
            prepare_runtime_openai_credential()
        validate_demo_seed_prerequisites()

    if not args.skip_schema:
        ensure_schema(drop_existing_data=args.drop_existing_data)

    if args.profile == "demo":
        try:
            check_demo_schema_readiness()
        except DemoSchemaReadinessError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc

    db = SessionLocal()
    try:
        if args.drop_existing_data:
            if args.profile == "test":
                seed_test_data(db)
            else:
                seed_demo_data(db)
            print(
                f"[OK] Existing local DB data dropped and {args.profile} profile recreated."
            )
        elif args.reset:
            if args.profile == "test":
                reset_test_data(db)
            else:
                reset_demo_data(db)
            print(f"[OK] {args.profile} profile reset complete.")
        else:
            if args.profile == "test":
                seed_test_data(db)
            else:
                seed_demo_data(db)
            print(f"[OK] {args.profile} profile seed upsert complete.")

        print(f"- profile: {summary['profile']}")
        print(f"- organization: {summary['organization']}")
        print("- password: 123123")
        print("- accounts:")
        for email in summary["users"]:
            print(f"  - {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
