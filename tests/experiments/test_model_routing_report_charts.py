from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.model_routing_report_charts import (
    write_economics_chart,
    write_lifecycle_chart,
)


def test_economics_chart_is_written_as_readable_png(tmp_path: Path):
    output = tmp_path / "economics.png"

    write_economics_chart(
        output,
        summaries={
            "high_fixed": {"total_cost": 0.075, "average_duration": 2.0},
            "low_fixed": {"total_cost": 0.005, "average_duration": 2.5},
            "automatic": {"total_cost": 0.071, "average_duration": 2.7},
        },
        quality={
            "automatic_vs_high": {"comparison_average_score": 91.0},
            "low_vs_high": {"comparison_average_score": 82.9},
            "high_reference_score": 89.6,
        },
        assessment={
            "runtime_savings_rate_pct": 5.5,
            "break_even_requests": 2392,
        },
    )

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.width >= 1200
        assert image.height >= 700


def test_lifecycle_chart_visualizes_each_policy_checkpoint(tmp_path: Path):
    output = tmp_path / "lifecycle.png"

    write_lifecycle_chart(
        output,
        checkpoints=[
            {
                "sequence": 0,
                "policy_version": "bootstrap-v1",
                "active_rule_count": 1,
                "validation_spend_usd": 0.08,
                "route_coverage_pct": 25.0,
                "routing_accuracy_pct": 80.0,
            },
            {
                "sequence": 20,
                "policy_version": "adaptive-v2",
                "active_rule_count": 2,
                "validation_spend_usd": 0.12,
                "route_coverage_pct": 45.0,
                "routing_accuracy_pct": 90.0,
            },
        ],
        model_windows=[
            {"start": 1, "end": 10, "models": {"gpt-4.1": 8, "gpt-4.1-mini": 2}},
            {"start": 11, "end": 20, "models": {"gpt-4.1": 5, "gpt-4.1-mini": 5}},
        ],
    )

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.width >= 1200
        assert image.height >= 700
