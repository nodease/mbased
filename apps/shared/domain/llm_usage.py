"""Shared classification rules for durable LLM usage rows."""

from __future__ import annotations

from typing import Any

AGENT_BUILDER_INTENT_RUNTIME_SURFACE = "agent_builder_intent"


def is_agent_builder_intent_usage(runtime_surface: Any) -> bool:
    return runtime_surface == AGENT_BUILDER_INTENT_RUNTIME_SURFACE


def is_billable_llm_usage(runtime_surface: Any, status: Any) -> bool:
    if not is_agent_builder_intent_usage(runtime_surface):
        return True
    return status == "success"
