"""LLM output format을 provider prompt와 routing feature에 동일하게 반영한다."""

from __future__ import annotations

import json
from typing import Any


JSON_OUTPUT_SCHEMA_SYSTEM_INSTRUCTION_PREFIX = (
    "응답은 반드시 아래 json schema를 만족하는 json object 하나만 반환하세요."
)


def build_json_output_schema_instruction(
    output_format: Any,
    *,
    force_json_object: bool = False,
) -> str | None:
    if not force_json_object and not isinstance(output_format, dict):
        return None
    if isinstance(output_format, dict) and output_format.get("type") != "json":
        return None

    schema = output_format.get("schema") if isinstance(output_format, dict) else None
    if not isinstance(schema, dict) or not schema:
        return (
            "응답은 반드시 json object 하나만 반환하세요. "
            "설명 문장, markdown, code fence는 포함하지 마세요."
        )

    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    return (
        f"{JSON_OUTPUT_SCHEMA_SYSTEM_INSTRUCTION_PREFIX}\n"
        "설명 문장, markdown, code fence는 포함하지 마세요.\n\n"
        f"json schema:\n{schema_text}"
    )


def response_format_requires_json_instruction(response_format: Any) -> bool:
    if not isinstance(response_format, dict):
        return False
    return response_format.get("type") in {"json_object", "json_schema"}
