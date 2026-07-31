"""독립 Requirement Judge 검토 결과를 합의 기준으로 정리한다."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Iterable, Mapping


AXES = ("task_complexity", "decision_impact", "evidence_synthesis")


def build_consensus_report(
    *,
    cases: Iterable[Mapping[str, Any]],
    review_a: Mapping[str, Any],
    review_b: Mapping[str, Any],
    review_c: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A/B blind review와 필요한 C adjudication을 안전한 합의 결과로 만든다."""

    case_ids = _case_ids(cases)
    labels_a = _labels_by_case(review_a, case_ids)
    labels_b = _labels_by_case(review_b, case_ids)
    labels_c = _labels_by_case(review_c, case_ids, required=False)
    disagreement_ids = [
        case_id
        for case_id in case_ids
        if _requirements(labels_a[case_id]) != _requirements(labels_b[case_id])
    ]
    needs_adjudication = [
        case_id for case_id in disagreement_ids if case_id not in labels_c
    ]
    consensus: list[dict[str, Any]] = []
    for case_id in case_ids:
        a = _requirements(labels_a[case_id])
        b = _requirements(labels_b[case_id])
        if a == b:
            consensus.append(_consensus_row(case_id, a, "high"))
            continue
        if case_id in needs_adjudication:
            continue
        c = _requirements(labels_c[case_id])
        values_by_axis = {axis: [a[axis], b[axis], c[axis]] for axis in AXES}
        has_large_gap = any(max(values) - min(values) >= 2 for values in values_by_axis.values())
        has_no_axis_majority = any(
            max(Counter(values).values()) == 1 for values in values_by_axis.values()
        )
        requirements = {
            axis: int(median(values)) for axis, values in values_by_axis.items()
        }
        consensus.append(
            _consensus_row(
                case_id,
                requirements,
                "low" if has_large_gap or has_no_axis_majority else "medium",
            )
        )
    return {
        "rubric_version": _rubric_version(review_a, review_b, review_c),
        "agreement": _agreement(labels_a, labels_b, case_ids),
        "needs_adjudication_case_ids": needs_adjudication,
        "consensus": consensus,
        "eligible_case_count": sum(
            1 for row in consensus if row["included_in_accuracy"]
        ),
    }


def _case_ids(cases: Iterable[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in result:
            raise ValueError("cases must contain unique case_id values")
        result.append(case_id)
    if not result:
        raise ValueError("at least one case is required")
    return result


def _labels_by_case(
    review: Mapping[str, Any] | None,
    case_ids: list[str],
    *,
    required: bool = True,
) -> dict[str, Mapping[str, Any]]:
    if review is None:
        return {}
    raw_labels = review.get("labels")
    if not isinstance(raw_labels, list):
        raise ValueError("review labels must be a list")
    allowed = set(case_ids)
    result: dict[str, Mapping[str, Any]] = {}
    for label in raw_labels:
        if not isinstance(label, Mapping):
            raise ValueError("review label must be an object")
        case_id = str(label.get("case_id") or "").strip()
        if case_id not in allowed or case_id in result:
            raise ValueError("review has an unknown or duplicate case_id")
        _requirements(label)
        result[case_id] = label
    if required and set(result) != allowed:
        raise ValueError("review must label every case")
    return result


def _requirements(label: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for axis in AXES:
        value = label.get(axis)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
            raise ValueError(f"invalid {axis}")
        result[axis] = value
    return result


def _consensus_row(
    case_id: str,
    requirements: Mapping[str, int],
    strength: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "task_requirements": dict(requirements),
        "consensus_strength": strength,
        "included_in_accuracy": strength != "low",
    }


def _agreement(
    labels_a: Mapping[str, Mapping[str, Any]],
    labels_b: Mapping[str, Mapping[str, Any]],
    case_ids: list[str],
) -> dict[str, Any]:
    axis_rates = {
        axis: sum(
            _requirements(labels_a[case_id])[axis] == _requirements(labels_b[case_id])[axis]
            for case_id in case_ids
        )
        / len(case_ids)
        for axis in AXES
    }
    exact_rate = sum(
        _requirements(labels_a[case_id]) == _requirements(labels_b[case_id])
        for case_id in case_ids
    ) / len(case_ids)
    return {
        "exact_triplet_rate": exact_rate,
        "axis_agreement_rates": axis_rates,
        "axis_quadratic_weighted_kappa": {
            axis: _quadratic_weighted_kappa(
                [_requirements(labels_a[case_id])[axis] for case_id in case_ids],
                [_requirements(labels_b[case_id])[axis] for case_id in case_ids],
            )
            for axis in AXES
        },
    }


def _quadratic_weighted_kappa(left: list[int], right: list[int]) -> float:
    observed = sum(((a - b) / 3) ** 2 for a, b in zip(left, right)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[a] / len(left)) * (right_counts[b] / len(right)) * ((a - b) / 3) ** 2
        for a in range(4)
        for b in range(4)
    )
    if expected == 0:
        return 1.0
    return 1.0 - observed / expected


def _rubric_version(*reviews: Mapping[str, Any] | None) -> str | None:
    versions = {
        str(review.get("rubric_version") or "").strip()
        for review in reviews
        if review is not None and str(review.get("rubric_version") or "").strip()
    }
    if len(versions) > 1:
        raise ValueError("reviews must use the same rubric_version")
    return next(iter(versions), None)
