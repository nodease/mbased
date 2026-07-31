"""Run the Agent Builder intent extractor against a permission-aware live model.

The report intentionally excludes prompts, credentials, provider payloads, and
workflow identifiers. This runner is not part of deterministic unit tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from apps.gateway.services.agent_builder_intent_service import (
    AgentBuilderIntentExtractionError,
    LLMAgentBuilderIntentExtractor,
)
from apps.gateway.services.llm_service import LLMService
from apps.shared.db.session import SessionLocal


DEFAULT_DATASET = Path(__file__).parent / "datasets" / "agent_builder_intent_v1.json"


class _CountingClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls = 0

    def invoke_sync(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._client.invoke_sync(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _required_uuid(name: str) -> uuid.UUID:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid UUID in environment variable: {name}") from exc


def _ratio(matches: int, total: int) -> float:
    return matches / total if total else 1.0


def run(dataset_path: Path) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    user_id = _required_uuid("NODEASE_EVAL_USER_ID")
    organization_id = _required_uuid("NODEASE_EVAL_ORGANIZATION_ID")
    credential_id = _required_uuid("NODEASE_EVAL_CREDENTIAL_ID")
    model_id = _required_uuid("NODEASE_EVAL_MODEL_ID")

    totals = {
        "request_type": 0,
        "draft_mode": 0,
        "ordered_capabilities": 0,
        "target": 0,
        "placement": 0,
        "target_cases": 0,
        "integration_actions": 0,
        "integration_cases": 0,
        "invalid": 0,
        "repair_attempted": 0,
        "repair_succeeded": 0,
    }
    results: list[dict[str, Any]] = []

    db = SessionLocal()
    try:
        for case in cases:
            counted: _CountingClient | None = None

            def runtime_loader(**kwargs: Any) -> Any:
                nonlocal counted
                runtime = LLMService.get_wizard_client_for_selection(**kwargs)
                counted = _CountingClient(runtime.client)
                return replace(runtime, client=counted)

            extractor = LLMAgentBuilderIntentExtractor(
                db=db,
                user_id=user_id,
                organization_id=organization_id,
                credential_id=credential_id,
                model_id=model_id,
                runtime_loader=runtime_loader,
            )
            expected = case["expected"]
            try:
                actual = extractor.extract(
                    safe_message=case["message"],
                    workflow_context=case["workflow_context"],
                )
            except AgentBuilderIntentExtractionError as exc:
                totals["invalid"] += 1
                if counted and counted.calls == 2:
                    totals["repair_attempted"] += 1
                results.append(
                    {"id": case["id"], "valid": False, "error": type(exc).__name__}
                )
                continue

            calls = counted.calls if counted else 0
            if calls == 2:
                totals["repair_attempted"] += 1
                totals["repair_succeeded"] += 1
            edit = actual.edit
            actual_values = {
                "request_type": actual.request_type,
                "draft_mode": actual.draft_mode,
                "ordered_capabilities": actual.ordered_capabilities,
                "target_reference_type": (
                    edit.target_reference_type if edit is not None else None
                ),
                "placement": edit.placement if edit is not None else None,
                "integration_actions": [
                    f"{action.provider}.{action.resource}.{action.operation}"
                    for action in actual.integration_actions
                ],
            }
            for field in ("request_type", "draft_mode", "ordered_capabilities"):
                totals[field] += int(actual_values[field] == expected[field])
            if "target_reference_type" in expected:
                totals["target_cases"] += 1
                totals["target"] += int(
                    actual_values["target_reference_type"]
                    == expected["target_reference_type"]
                )
                totals["placement"] += int(
                    actual_values["placement"] == expected["placement"]
                )
            if "integration_actions" in expected:
                totals["integration_cases"] += 1
                totals["integration_actions"] += int(
                    actual_values["integration_actions"]
                    == expected["integration_actions"]
                )
            results.append(
                {
                    "id": case["id"],
                    "valid": True,
                    "calls": calls,
                    "expected": expected,
                    "actual": actual_values,
                }
            )
    finally:
        db.close()

    count = len(cases)
    metrics = {
        "request_type_accuracy": _ratio(totals["request_type"], count),
        "draft_mode_accuracy": _ratio(totals["draft_mode"], count),
        "ordered_capability_exact_match": _ratio(
            totals["ordered_capabilities"], count
        ),
        "target_accuracy": _ratio(totals["target"], totals["target_cases"]),
        "placement_accuracy": _ratio(
            totals["placement"], totals["target_cases"]
        ),
        "integration_action_exact_match": _ratio(
            totals["integration_actions"], totals["integration_cases"]
        ),
        "invalid_output_rate": _ratio(totals["invalid"], count),
        "repair_success_rate": _ratio(
            totals["repair_succeeded"], totals["repair_attempted"]
        ),
        "sample_count": count,
        "repair_attempt_count": totals["repair_attempted"],
    }
    return {"dataset": dataset_path.name, "metrics": metrics, "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--request-type-threshold", type=float, default=0.9)
    parser.add_argument("--draft-mode-threshold", type=float, default=0.9)
    parser.add_argument("--capability-threshold", type=float, default=0.8)
    parser.add_argument("--target-threshold", type=float, default=0.85)
    parser.add_argument("--integration-threshold", type=float, default=1.0)
    parser.add_argument("--max-invalid-rate", type=float, default=0.05)
    args = parser.parse_args()

    report = run(args.dataset)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    metrics = report["metrics"]
    passed = (
        metrics["request_type_accuracy"] >= args.request_type_threshold
        and metrics["draft_mode_accuracy"] >= args.draft_mode_threshold
        and metrics["ordered_capability_exact_match"] >= args.capability_threshold
        and metrics["target_accuracy"] >= args.target_threshold
        and metrics["placement_accuracy"] >= args.target_threshold
        and metrics["integration_action_exact_match"]
        >= args.integration_threshold
        and metrics["invalid_output_rate"] <= args.max_invalid_rate
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
