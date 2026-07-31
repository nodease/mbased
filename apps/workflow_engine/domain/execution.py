from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.shared.domain.workflow_node_binding import WorkflowNodeBinding
from apps.workflow_engine.domain.external_effect import ExternalEffectContext


@dataclass(frozen=True)
class NodeExecutionControl:
    execution_id: uuid.UUID
    invocation_path_prefix: tuple[InvocationSegment, ...]
    external_effect_context: ExternalEffectContext | None
    task_deadline: float | None = None
    workflow_node_binding: WorkflowNodeBinding | None = None
    workflow_node_bindings: tuple[WorkflowNodeBinding, ...] = ()
    binding_container_path: tuple[tuple[str, str], ...] = ()
    external_effect_enforced: bool = True
