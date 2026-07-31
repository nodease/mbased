"""Workflow Worker startup checks for the LLM credential keyring."""

from celery.signals import worker_init, worker_process_init

from apps.shared.services.llm_credential_config import (
    require_llm_credential_keyring_ready,
)


@worker_init.connect
def validate_llm_credential_worker_readiness(**kwargs) -> None:
    """Reject a Worker before task consumption when the LLM keyring is invalid."""
    require_llm_credential_keyring_ready()


@worker_process_init.connect
def validate_llm_credential_worker_process(**kwargs) -> None:
    """Recheck the LLM keyring in each Worker child process."""
    require_llm_credential_keyring_ready()
