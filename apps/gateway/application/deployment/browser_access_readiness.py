from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable

from .browser_access_errors import BrowserAccessPolicyError
from .browser_access_policy import normalize_browser_access_policy

READINESS_CONTRACT_VERSION = "deployment_browser_access_readiness.v1"
POLICY_STATES = ("legacy_null", "malformed", "disabled", "enabled")


@dataclass(frozen=True)
class BrowserAccessReadinessCandidate:
    deployment_id: str
    deployment_version: int
    deployment_type: str
    browser_access_policy: object


@dataclass(frozen=True)
class BrowserAccessReadinessItem:
    deployment_id: str
    deployment_version: int
    deployment_type: str
    policy_state: str

    def safe_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "deployment_version": self.deployment_version,
            "deployment_type": self.deployment_type,
            "policy_state": self.policy_state,
        }


@dataclass(frozen=True)
class BrowserAccessReadinessReport:
    items: tuple[BrowserAccessReadinessItem, ...]

    def safe_dict(self) -> dict:
        counts = Counter(item.policy_state for item in self.items)
        return {
            "contract_version": READINESS_CONTRACT_VERSION,
            "counts": {
                "total": len(self.items),
                **{state: counts[state] for state in POLICY_STATES},
            },
            "deployments": [item.safe_dict() for item in self.items],
        }


def build_browser_access_readiness_report(
    candidates: Iterable[BrowserAccessReadinessCandidate],
) -> BrowserAccessReadinessReport:
    items = [
        BrowserAccessReadinessItem(
            deployment_id=candidate.deployment_id,
            deployment_version=candidate.deployment_version,
            deployment_type=candidate.deployment_type,
            policy_state=_policy_state(candidate),
        )
        for candidate in candidates
    ]
    items.sort(key=lambda item: (item.deployment_type, item.deployment_id))
    return BrowserAccessReadinessReport(items=tuple(items))


def _policy_state(candidate: BrowserAccessReadinessCandidate) -> str:
    if candidate.browser_access_policy is None:
        return "legacy_null"
    if not isinstance(candidate.browser_access_policy, Mapping):
        return "malformed"
    try:
        policy = normalize_browser_access_policy(
            candidate.deployment_type,
            candidate.browser_access_policy,
            environment="production",
        )
    except BrowserAccessPolicyError:
        return "malformed"
    if policy is None:
        return "malformed"
    return "enabled" if policy.embedding.enabled else "disabled"
