from sqlalchemy import ForeignKeyConstraint

from apps.shared.db.models.agent_builder import AgentBuilderDraft, AgentBuilderSession
from apps.shared.db.models.cost_optimizer import (
    CostOptimizerExperiment,
    CostOptimizerRecommendationVerification,
)
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.mail_processing import MailMessageProcessing
from apps.shared.db.models.model_routing_cohort import (
    LLMNodeModelRoutingCohort,
    LLMNodeModelRoutingObservation,
    LLMNodeModelRoutingValidationBatch,
    LLMNodeModelRoutingValidationBudgetMonth,
)
from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingPolicyRunEvent,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.team import TeamWorkflowPermission, UserWorkflowPermission
from apps.shared.db.models.workflow_run import WorkflowRun


def _foreign_key_targets(model, column_name: str) -> set[str]:
    return {
        foreign_key.target_fullname
        for foreign_key in model.__table__.c[column_name].foreign_keys
    }


def test_retained_history_resource_ids_are_not_lifecycle_foreign_keys():
    detached_columns = (
        (WorkflowRun, "workflow_id"),
        (WorkflowRun, "app_id"),
        (WorkflowRun, "deployment_id"),
        (LLMUsageLog, "workflow_id"),
        (CostOptimizerExperiment, "workflow_id"),
        (CostOptimizerExperiment, "app_id"),
        (CostOptimizerRecommendationVerification, "workflow_id"),
        (MailMessageProcessing, "workflow_id"),
        (MailMessageProcessing, "deployment_id"),
        (LLMNodeModelRoutingPolicyUpdate, "policy_id"),
        (LLMNodeModelRoutingPolicyRunEvent, "policy_id"),
        (LLMNodeModelRoutingCohort, "policy_id"),
        (LLMNodeModelRoutingObservation, "policy_id"),
        (LLMNodeModelRoutingValidationBatch, "policy_id"),
        (LLMNodeModelRoutingValidationBudgetMonth, "policy_id"),
    )

    for model, column_name in detached_columns:
        assert _foreign_key_targets(model, column_name) == set()


def test_workflow_permissions_remain_active_cascade_configuration():
    team_workflow_fk = next(
        iter(TeamWorkflowPermission.__table__.c.workflow_id.foreign_keys)
    )
    assert team_workflow_fk.target_fullname == "workflows.id"
    assert team_workflow_fk.ondelete == "CASCADE"

    user_workflow_fk = next(
        constraint
        for constraint in UserWorkflowPermission.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_user_workflow_permissions_workflow_org"
    )
    assert user_workflow_fk.ondelete == "CASCADE"


def test_agent_builder_references_still_detach_with_set_null():
    for model in (AgentBuilderSession, AgentBuilderDraft):
        assert next(iter(model.__table__.c.app_id.foreign_keys)).ondelete == "SET NULL"
        assert (
            next(iter(model.__table__.c.workflow_id.foreign_keys)).ondelete
            == "SET NULL"
        )
