"""배포 후 운영 실행과 모델 라우팅 policy refresh를 연결하는 lifecycle helper."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


OPERATIONAL_TRIGGER_MODES = {"api", "webhook", "scheduler", "app"}


@dataclass(frozen=True)
class PolicyRunEventOutcome:
    should_enqueue_refresh: bool


class ModelRoutingPolicyLifecycleService:
    """DB 저장소가 event를 만들었는지에 따라 policy state만 전이한다."""

    REFRESH_REQUEST_LEASE = timedelta(minutes=10)

    @staticmethod
    def has_pending_refresh_request(policy: Any) -> bool:
        """broker 재시도 시 다시 발행해도 되는 auto refresh 요청인지 확인한다."""
        return bool(
            getattr(policy, "enabled", False)
            and getattr(policy, "refresh_requested_at", None) is not None
            and str(getattr(policy, "status", "") or "").lower() == "refreshing"
        )

    @staticmethod
    def is_eligible_operational_run(run: Any) -> bool:
        deployment_id = getattr(run, "deployment_id", None)
        trigger_mode = getattr(run, "trigger_mode", None)
        status = getattr(run, "status", None)
        normalized_trigger = str(
            getattr(trigger_mode, "value", trigger_mode) or ""
        ).lower()
        normalized_status = str(getattr(status, "value", status) or "").lower()
        return (
            bool(deployment_id)
            and normalized_trigger in OPERATIONAL_TRIGGER_MODES
            and normalized_status in {"success", "failed"}
        )

    @staticmethod
    def apply_run_event(
        policy: Any,
        *,
        event_was_created: bool,
        performance_changed: bool | None = None,
        now: datetime | None = None,
    ) -> PolicyRunEventOutcome:
        if not event_was_created or not bool(getattr(policy, "enabled", False)):
            return PolicyRunEventOutcome(should_enqueue_refresh=False)

        now = now or datetime.now(timezone.utc)
        policy.eligible_runs_since_last_refresh = (
            int(getattr(policy, "eligible_runs_since_last_refresh", 0) or 0) + 1
        )
        requested_at = getattr(policy, "refresh_requested_at", None)
        if requested_at is not None:
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=timezone.utc)
            is_refreshing = (
                str(getattr(policy, "status", "") or "").lower() == "refreshing"
            )
            if (
                is_refreshing
                and now - requested_at
                >= ModelRoutingPolicyLifecycleService.REFRESH_REQUEST_LEASE
            ):
                policy.refresh_requested_at = now
                return PolicyRunEventOutcome(should_enqueue_refresh=True)
            return PolicyRunEventOutcome(should_enqueue_refresh=False)
        if performance_changed is None:
            # 직접 lifecycle helper를 쓰는 기존 호환 경로는 횟수 기준을 유지한다.
            # 운영 run 저장소는 성적 변화 여부를 명시해 이 분기를 사용하지 않는다.
            threshold = max(
                5,
                min(100, int(getattr(policy, "refresh_every_runs", 20) or 20)),
            )
            if policy.eligible_runs_since_last_refresh < threshold:
                return PolicyRunEventOutcome(should_enqueue_refresh=False)
        elif not performance_changed:
            return PolicyRunEventOutcome(should_enqueue_refresh=False)

        policy.status = "refreshing"
        policy.refresh_requested_at = now
        return PolicyRunEventOutcome(should_enqueue_refresh=True)

    @staticmethod
    def apply_refresh_result(
        policy: Any,
        *,
        status: str,
        proposed_policy: dict[str, Any],
        policy_version: str | None,
    ) -> None:
        policy.last_refresh_result = status
        if status == "applied":
            policy.active_policy = proposed_policy
            policy.pending_policy = None
            policy.policy_version = policy_version
            policy.status = "active"
            return

        if status == "pending_review":
            policy.pending_policy = proposed_policy
            policy.status = "pending_review"
            return

        # kept_current와 failed는 기존 active policy를 그대로 실행한다.
        policy.status = (
            "active" if getattr(policy, "active_policy", None) else "collecting"
        )

    @staticmethod
    def complete_refresh_cycle(
        policy: Any,
        *,
        eligible_runs_since_last_refresh: int,
    ) -> None:
        """Replay/Judge 검증이 끝난 뒤에만 refresh lease를 해제한다.

        검증 중 완료된 운영 run은 다음 주기의 표본이므로 버리지 않는다. 호출자는
        refresh 요청 시각 이후에 생성된 run event 수를 계산해 전달한다.
        """
        policy.refresh_requested_at = None
        policy.eligible_runs_since_last_refresh = max(
            0,
            int(eligible_runs_since_last_refresh or 0),
        )
