"""Workflow Worker startup checks for the Mail credential runtime boundary."""

from celery.signals import worker_init, worker_process_init

from apps.shared.services.credential_encryption import (
    require_mail_credential_keyring_ready,
)


@worker_init.connect
def validate_mail_credential_worker_readiness(**kwargs) -> None:
    """Reject a Worker before task consumption when the Mail keyring is invalid."""
    require_mail_credential_keyring_ready()


@worker_process_init.connect
def validate_mail_credential_worker_process(**kwargs) -> None:
    """Recheck the Mail keyring in each Worker child process."""
    require_mail_credential_keyring_ready()
