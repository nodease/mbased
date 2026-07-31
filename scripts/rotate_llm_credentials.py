"""Bounded plaintext backfill and key rotation for LLM credentials."""

from __future__ import annotations

import argparse
import json

from sqlalchemy.exc import SQLAlchemyError

from apps.shared.db.session import SessionLocal
from apps.shared.services.llm_credential_config import (
    LLMCredentialConfigError,
    get_llm_credential_config_service,
)
from apps.shared.services.llm_credential_rotation import (
    LLMCredentialRotationError,
    LLMCredentialRotationService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill or rotate LLM credential encryption in bounded batches."
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=10)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether rows still require rotation.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    db = SessionLocal()
    try:
        service = LLMCredentialRotationService(
            db,
            config_service=get_llm_credential_config_service(),
        )
        if args.check:
            pending = service.pending_count()
            print(
                json.dumps(
                    {
                        "status": "ready" if pending == 0 else "pending",
                        "pending": pending,
                    }
                )
            )
            return 0 if pending == 0 else 1

        result = service.rotate(
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
        print(
            json.dumps(
                {
                    "status": "complete" if result.pending == 0 else "partial",
                    "processed": result.processed,
                    "pending": result.pending,
                    "batches": result.batches,
                }
            )
        )
        return 0 if result.pending == 0 else 2
    except (
        LLMCredentialConfigError,
        LLMCredentialRotationError,
        SQLAlchemyError,
        ValueError,
    ):
        print(json.dumps({"status": "failed"}))
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
