"""Workflow runtime exception types."""


class NonRetryableWorkflowError(ValueError):
    """Workflow execution error that should fail immediately without Celery retry."""


class WorkflowNodeConfigurationError(NonRetryableWorkflowError):
    """Invalid workflow-node target, depth, or recursion configuration."""


class ProviderOutcomeUnknownWorkflowError(NonRetryableWorkflowError):
    """A provider may have processed the request, so automatic replay is unsafe."""

    code = "provider_outcome_unknown"
    failure_phase = "outcome_unknown"

    def __init__(self) -> None:
        super().__init__(self.code)
