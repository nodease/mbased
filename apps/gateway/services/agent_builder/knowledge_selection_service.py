from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
)
from apps.gateway.application.agent_builder.knowledge_timing import (
    KnowledgeTimingResolver,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.gateway.services.knowledge_rag_recommendation_service import (
    knowledge_base_recommendation_handle,
    knowledge_collection_selection_handle,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.workflow import Workflow
from apps.shared.schemas.agent_builder import (
    AgentBuilderKnowledgeSelectionRequest,
    AgentBuilderKnowledgeSelectionResponse,
    AgentBuilderParameterGroup,
    AgentBuilderStructuredRequest,
    GraphMutationSafeEnvelope,
)
from apps.shared.services.permissions import has_workflow_permission


class KnowledgeSelectionService:
    def __init__(
        self,
        db: Session,
        *,
        user_id: UUID,
        organization_id: UUID,
        binding_materializer: Callable[..., dict[str, Any]],
        no_knowledge_candidate_id: str,
        before_graph_builder: Callable[..., dict[str, Any]] | None = None,
        repository: AgentBuilderRepository | None = None,
        knowledge_base_handle_resolver: Callable[[UUID], str] | None = None,
        knowledge_collection_handle_resolver: Callable[[UUID], str] | None = None,
        knowledge_selection_refresher: Callable[
            [AgentBuilderStructuredRequest], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.binding_materializer = binding_materializer
        self.before_graph_builder = before_graph_builder
        self.no_knowledge_candidate_id = no_knowledge_candidate_id
        self.repository = repository or AgentBuilderRepository()
        self.knowledge_selection_refresher = knowledge_selection_refresher
        self.knowledge_base_handle_resolver = (
            knowledge_base_handle_resolver
            or (
                lambda resource_id: knowledge_base_recommendation_handle(
                    self.organization_id,
                    resource_id,
                )
            )
        )
        self.knowledge_collection_handle_resolver = (
            knowledge_collection_handle_resolver
            or (
                lambda resource_id: knowledge_collection_selection_handle(
                    self.organization_id,
                    resource_id,
                )
            )
        )

    def _refresh_knowledge_resolution(
        self,
        *,
        request_row: AgentBuilderRequest,
        structured: AgentBuilderStructuredRequest,
        resolution_id: str,
    ) -> bool:
        if self.knowledge_selection_refresher is None:
            return False
        try:
            refreshed = self.knowledge_selection_refresher(structured)
        except Exception:
            return False
        knowledge_selection = refreshed.get("knowledge_selection")
        if not isinstance(knowledge_selection, dict):
            return False
        payload = dict(request_row.response_payload or {})
        current = payload.get("knowledge_resolution")
        if not isinstance(current, dict):
            return False
        if str(current.get("resolution_id") or "") != resolution_id:
            return False
        updated_resolution = dict(current)
        updated_resolution.update(
            {
                "candidates": [],
                "collections": list(knowledge_selection.get("collections") or []),
                "ungrouped_kbs": list(
                    knowledge_selection.get("ungrouped_kbs") or []
                ),
                "selected": [],
                "selected_collection_handles": [],
                "selected_kb_handles": [],
                "selection_status": None,
            }
        )
        payload["knowledge_resolution"] = updated_resolution
        issued_handle_bindings = refreshed.get(
            "_issued_knowledge_handle_bindings"
        )
        if isinstance(issued_handle_bindings, dict):
            payload["_issued_knowledge_handle_bindings"] = {
                "resolution_id": resolution_id,
                **issued_handle_bindings,
            }
        stored_resolutions = []
        for item in payload.get("knowledge_resolutions") or []:
            if (
                isinstance(item, dict)
                and str(item.get("resolution_id") or "") == resolution_id
                and item.get("status") == "unapplied"
            ):
                stored_resolutions.append(
                    {
                        **item,
                        "selected_candidate_ids": [],
                        "selected_collection_handles": [],
                        "selected_kb_handles": [],
                        "selection_invalidated": True,
                    }
                )
            else:
                stored_resolutions.append(item)
        payload["knowledge_resolutions"] = stored_resolutions
        request_row.response_payload = payload
        self.db.commit()
        return True

    def _raise_stale_knowledge_selection(
        self,
        *,
        request_row: AgentBuilderRequest,
        structured: AgentBuilderStructuredRequest,
        resolution_id: str,
    ) -> None:
        if self._refresh_knowledge_resolution(
            request_row=request_row,
            structured=structured,
            resolution_id=resolution_id,
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "knowledge_selection_stale"},
            )
        raise HTTPException(status_code=422, detail="catalog_validation_failed")

    @staticmethod
    def _target_node_id(
        *,
        payload: dict[str, Any],
        request_row: AgentBuilderRequest,
        workflow: Workflow,
        target_step_id: str,
    ) -> str:
        direct = dict(payload.get("safe_step_node_ids") or {})
        if direct.get(target_step_id):
            return str(direct[target_step_id])
        for raw_group in payload.get("parameter_groups") or []:
            if not isinstance(raw_group, dict):
                continue
            group = AgentBuilderParameterGroup.model_validate(raw_group)
            task = next(
                (task for task in group.tasks if task.step_id == target_step_id),
                None,
            )
            if task is not None:
                return task.node_id
        affected = {
            str(node_id)
            for envelope in payload.get("operation_envelopes") or []
            if isinstance(envelope, dict)
            for node_id in envelope.get("affected_node_ids") or []
        }
        candidates = [
            str(node.get("id"))
            for node in (workflow.graph or {}).get("nodes") or []
            if node.get("type") == "llmNode" and str(node.get("id")) in affected
        ]
        if len(candidates) == 1:
            return candidates[0]
        raise HTTPException(status_code=422, detail="catalog_validation_failed")

    @staticmethod
    def _candidate_options_for_resolution(
        payload: dict[str, Any],
        resolution_id: str,
    ) -> list[dict[str, Any]]:
        direct_resolution = payload.get("knowledge_resolution")
        if not isinstance(direct_resolution, dict):
            return []
        direct_resolution_id = direct_resolution.get("resolution_id")
        direct_candidates = (
            direct_resolution.get("candidates")
            if isinstance(direct_resolution.get("candidates"), list)
            else []
        )
        return [
            option
            for option in direct_candidates
            if isinstance(option, dict)
            and (
                str(option.get("resolution_id")) == resolution_id
                or (
                    option.get("resolution_id") is None
                    and direct_resolution_id is not None
                    and str(direct_resolution_id) == resolution_id
                )
            )
        ]

    @staticmethod
    def _direct_resolution_matches(
        payload: dict[str, Any],
        resolution_id: str,
    ) -> bool:
        direct_resolution = payload.get("knowledge_resolution")
        if not isinstance(direct_resolution, dict):
            return False
        if direct_resolution.get("resolution_id") is not None:
            return str(direct_resolution.get("resolution_id")) == resolution_id
        return bool(
            KnowledgeSelectionService._candidate_options_for_resolution(
                payload,
                resolution_id,
            )
        )

    @staticmethod
    def _hierarchy_handles(payload: dict[str, Any]) -> tuple[set[str], set[str]]:
        resolution = payload.get("knowledge_resolution")
        if not isinstance(resolution, dict):
            return set(), set()
        collection_handles: set[str] = set()
        kb_handles: set[str] = set()
        for raw_collection in resolution.get("collections") or []:
            if not isinstance(raw_collection, dict):
                continue
            handle = raw_collection.get("collection_handle")
            if isinstance(handle, str) and handle:
                collection_handles.add(handle)
            for raw_child in raw_collection.get("children") or []:
                if isinstance(raw_child, dict):
                    kb_handle = raw_child.get("kb_handle")
                    if isinstance(kb_handle, str) and kb_handle:
                        kb_handles.add(kb_handle)
        for raw_kb in resolution.get("ungrouped_kbs") or []:
            if isinstance(raw_kb, dict):
                kb_handle = raw_kb.get("kb_handle")
                if isinstance(kb_handle, str) and kb_handle:
                    kb_handles.add(kb_handle)
        return collection_handles, kb_handles

    @staticmethod
    def _issued_handle_bindings(
        payload: dict[str, Any],
        resolution_id: str,
    ) -> dict[str, dict[str, str]]:
        raw = payload.get("_issued_knowledge_handle_bindings")
        if not isinstance(raw, dict) or str(raw.get("resolution_id") or "") != (
            resolution_id
        ):
            return {}
        result: dict[str, dict[str, str]] = {}
        for binding_type in ("knowledge_bases", "collections"):
            values = raw.get(binding_type)
            if not isinstance(values, dict):
                continue
            result[binding_type] = {
                str(handle): str(resource_id)
                for handle, resource_id in values.items()
                if isinstance(handle, str) and handle and resource_id
            }
        return result

    @staticmethod
    def _placement_for_resolution(
        structured: AgentBuilderStructuredRequest,
        *,
        payload: dict[str, Any],
        resolution_id: str,
        options: list[dict[str, Any]],
    ):
        requirement_ids = {
            str(option.get("requirement_id"))
            for option in options
            if option.get("requirement_id")
        }
        direct_resolution = payload.get("knowledge_resolution")
        if isinstance(direct_resolution, dict) and direct_resolution.get(
            "requirement_id"
        ):
            requirement_ids.add(str(direct_resolution["requirement_id"]))
        pending_target_step = next(
            (
                item.target_step_ref
                for item in structured.pending_resolution
                if item.slot_type == "knowledge_base"
                and item.resolution_id == resolution_id
            ),
            None,
        )
        if pending_target_step:
            requirement_ids.update(
                requirement.requirement_id
                for requirement in structured.knowledge_requirements
                if requirement.target_step_ref == pending_target_step
            )
        if requirement_ids:
            matched = next(
                (
                    item
                    for item in structured.knowledge_placements
                    if item.requirement_id in requirement_ids
                ),
                None,
            )
            if matched is not None:
                return matched
        if pending_target_step:
            matched = next(
                (
                    item
                    for item in structured.knowledge_placements
                    if pending_target_step
                    in {
                        item.target_step_id,
                        item.knowledge_step_id,
                        item.upstream_step_id,
                        item.downstream_step_id,
                    }
                ),
                None,
            )
            if matched is not None:
                return matched
        if len(structured.knowledge_placements) == 1:
            return structured.knowledge_placements[0]
        return None

    def select(
        self,
        session_id: UUID,
        selection: AgentBuilderKnowledgeSelectionRequest,
    ) -> AgentBuilderKnowledgeSelectionResponse:
        session = (
            self.db.query(AgentBuilderSession)
            .filter(
                AgentBuilderSession.id == session_id,
                AgentBuilderSession.user_id == self.user_id,
                AgentBuilderSession.organization_id == self.organization_id,
            )
            .first()
        )
        if (
            session is None
            or session.protocol_version != "direct_edit_v1"
            or session.workflow_id is None
        ):
            raise HTTPException(status_code=404, detail="resource_not_found")
        workflow = (
            self.db.query(Workflow)
            .filter(
                Workflow.id == session.workflow_id,
                Workflow.organization_id == self.organization_id,
            )
            .first()
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
        if not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "write",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="permission_denied")

        rows = (
            self.db.query(AgentBuilderRequest)
            .filter(AgentBuilderRequest.session_id == session.id)
            .order_by(AgentBuilderRequest.created_at.desc())
            .with_for_update()
            .all()
        )
        request_row = None
        structured = None
        placement = None
        options: list[dict[str, Any]] = []
        for candidate in rows:
            payload = candidate.response_payload or {}
            candidate_options = self._candidate_options_for_resolution(
                payload,
                selection.resolution_id,
            )
            if not candidate_options and not self._direct_resolution_matches(
                payload,
                selection.resolution_id,
            ):
                continue
            try:
                candidate_structured = AgentBuilderStructuredRequest.model_validate(
                    candidate.structured_request
                )
            except Exception:
                continue
            candidate_placement = self._placement_for_resolution(
                candidate_structured,
                payload=payload,
                resolution_id=selection.resolution_id,
                options=candidate_options,
            )
            if candidate_placement is None:
                continue
            request_row = candidate
            structured = candidate_structured
            placement = candidate_placement
            options = candidate_options
            break
        if request_row is None or structured is None or placement is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
        if placement.target_step_id is None:
            raise HTTPException(status_code=422, detail="catalog_validation_failed")

        payload = request_row.response_payload or {}
        structural_envelopes = [
            envelope
            for envelope in payload.get("operation_envelopes") or []
            if isinstance(envelope, dict)
            and envelope.get("kind")
            in {"initial_graph", "graph_edit", "replace_workflow"}
        ]
        if placement.timing == "after_graph":
            if (
                not structural_envelopes
                or structural_envelopes[-1].get("status") != "acknowledged"
            ):
                raise HTTPException(status_code=409, detail="stale_graph")
        elif structural_envelopes:
            raise HTTPException(status_code=409, detail="stale_graph")

        option_keys = {
            (
                str(option.get("candidate_id")),
                str(option.get("requirement_id")),
            )
            for option in options
        }
        allowed_collection_handles, allowed_kb_handles = self._hierarchy_handles(
            payload
        )
        for candidate in selection.selected_candidates:
            requirement_id = candidate.requirement_id or placement.requirement_id
            if (candidate.candidate_id, requirement_id) not in option_keys:
                raise HTTPException(status_code=422, detail="catalog_validation_failed")
            if candidate.resolution_id not in {None, selection.resolution_id}:
                raise HTTPException(status_code=422, detail="catalog_validation_failed")

        if selection.editor_target_node_id is not None:
            if placement.timing != "after_graph":
                raise HTTPException(
                    status_code=422,
                    detail="catalog_validation_failed",
                )
            canonical_target_node_id = self._target_node_id(
                payload=payload,
                request_row=request_row,
                workflow=workflow,
                target_step_id=placement.target_step_id,
            )
            if selection.editor_target_node_id != canonical_target_node_id:
                raise HTTPException(
                    status_code=422,
                    detail="catalog_validation_failed",
                )

        hierarchy_collection_handles = {
            *selection.selected_collection_handles,
            *(
                self.knowledge_collection_handle_resolver(resource_id)
                for resource_id in selection.selected_knowledge_collection_ids
            ),
        }
        hierarchy_kb_handles = {
            *selection.selected_kb_handles,
            *(
                self.knowledge_base_handle_resolver(resource_id)
                for resource_id in selection.selected_knowledge_base_ids
            ),
        }
        selected_collection_handles = sorted(hierarchy_collection_handles)
        selected_kb_handles = sorted(
            {
                *hierarchy_kb_handles,
                *(candidate.candidate_id for candidate in selection.selected_candidates),
            }
        )
        selected_candidate_ids = sorted(
            {*selected_collection_handles, *selected_kb_handles}
        )
        is_editor_selection = selection.editor_target_node_id is not None
        issued_handle_bindings = self._issued_handle_bindings(
            payload,
            selection.resolution_id,
        )
        if is_editor_selection:
            issued_handle_bindings = {
                "knowledge_bases": {
                    self.knowledge_base_handle_resolver(resource_id): str(resource_id)
                    for resource_id in selection.selected_knowledge_base_ids
                },
                "collections": {
                    self.knowledge_collection_handle_resolver(resource_id): str(
                        resource_id
                    )
                    for resource_id in selection.selected_knowledge_collection_ids
                },
            }
        existing_resolution = self.repository.find_knowledge_resolution(
            request_row,
            selection.resolution_id,
        )
        if (
            existing_resolution is not None
            and existing_resolution.get("status") != "unapplied"
        ):
            raise HTTPException(
                status_code=409,
                detail="knowledge_resolution_already_submitted",
            )
        if not is_editor_selection and (
            any(
                handle not in allowed_collection_handles
                for handle in hierarchy_collection_handles
            )
            or any(
                handle not in allowed_kb_handles
                for handle in hierarchy_kb_handles
            )
        ):
            self._raise_stale_knowledge_selection(
                request_row=request_row,
                structured=structured,
                resolution_id=selection.resolution_id,
            )

        if existing_resolution is not None:
            stored_collection_handles = existing_resolution.get(
                "selected_collection_handles"
            )
            stored_kb_handles = existing_resolution.get("selected_kb_handles")
            selection_differs = (
                list(stored_collection_handles or [])
                != selected_collection_handles
                or list(stored_kb_handles or []) != selected_kb_handles
                if stored_collection_handles is not None
                or stored_kb_handles is not None
                else existing_resolution.get("selected_candidate_ids")
                != selected_candidate_ids
            )
            if selection_differs and not existing_resolution.get(
                "selection_invalidated"
            ):
                raise HTTPException(status_code=409, detail="task_conflict")

        candidate_handles = set(selected_kb_handles)
        if not candidate_handles and not selected_collection_handles:
            recommendation = {
                "status": "ready",
                "bindings": [],
                "collections": [],
                "warnings": [],
            }
        elif selection.selected_candidates and not (
            selection.selected_kb_handles or selection.selected_collection_handles
        ):
            recommendation = self.binding_materializer(
                structured,
                selected_candidate_handles=candidate_handles,
                issued_handle_bindings=issued_handle_bindings,
            )
        else:
            recommendation = self.binding_materializer(
                structured,
                selected_kb_handles=candidate_handles,
                selected_collection_handles=set(selected_collection_handles),
                issued_handle_bindings=issued_handle_bindings,
            )
        if recommendation.get("status") != "ready":
            if not is_editor_selection:
                self._raise_stale_knowledge_selection(
                    request_row=request_row,
                    structured=structured,
                    resolution_id=selection.resolution_id,
                )
            raise HTTPException(status_code=422, detail="catalog_validation_failed")
        knowledge_base_refs = [
            {
                "id": str(binding["knowledge_base_id"]),
                "name": str(binding["name"]),
            }
            for binding in recommendation.get("bindings") or []
            if binding.get("knowledge_base_id") and binding.get("name")
        ]
        knowledge_collection_refs = [
            {
                "id": str(binding["knowledge_collection_id"]),
                "name": str(binding["name"]),
            }
            for binding in recommendation.get("collections") or []
            if binding.get("knowledge_collection_id") and binding.get("name")
        ]
        if placement.timing == "before_graph":
            if self.before_graph_builder is None:
                raise HTTPException(
                    status_code=422,
                    detail="catalog_validation_failed",
                )
            try:
                issued = self.before_graph_builder(
                    structured=structured,
                    workflow=workflow,
                    placement=placement,
                    bindings=list(recommendation.get("bindings") or []),
                    collection_bindings=list(
                        recommendation.get("collections") or []
                    ),
                    resolution_id=selection.resolution_id,
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="catalog_validation_failed",
                ) from exc
            mutation = issued["mutation"]
            parameter_group = issued.get("parameter_group")
            if parameter_group is not None:
                self.repository.store_parameter_group(request_row, parameter_group)
            updated_payload = dict(request_row.response_payload or {})
            updated_payload["safe_step_node_ids"] = dict(
                issued.get("step_node_ids") or {}
            )
            request_row.response_payload = updated_payload
        else:
            target_node_id = self._target_node_id(
                payload=payload,
                request_row=request_row,
                workflow=workflow,
                target_step_id=placement.target_step_id,
            )
            mutation = KnowledgeTimingResolver().build_after_graph_mutation(
                operation_id=uuid4(),
                workflow_id=workflow.id,
                graph=workflow.graph or {"nodes": [], "edges": []},
                workflow_updated_at=workflow.updated_at,
                target_node_id=target_node_id,
                selected_knowledge_bases=knowledge_base_refs,
                selected_knowledge_collections=knowledge_collection_refs,
                resolution_id=selection.resolution_id,
            )
        self.repository.store_envelope(
            request_row, GraphMutationSafeEnvelope.from_mutation(mutation)
        )
        self.repository.store_knowledge_resolution(
            request_row,
            resolution_id=selection.resolution_id,
            operation_id=mutation.operation_id,
            timing=placement.timing,
            selected_candidate_ids=selected_candidate_ids,
            selected_collection_handles=selected_collection_handles,
            selected_kb_handles=selected_kb_handles,
        )
        add_action_audit(
            self.db,
            AuditAction.AGENT_BUILDER_KNOWLEDGE_MUTATION_ISSUED,
            self.user_id,
            "workflow",
            workflow.id,
            organization_id=self.organization_id,
            metadata={
                "session_id": str(session.id),
                "resolution_id": selection.resolution_id,
                "operation_id": str(mutation.operation_id),
                "selected_collection_count": len(selected_collection_handles),
                "selected_kb_count": len(selected_kb_handles),
            },
        )
        self.db.commit()
        return AgentBuilderKnowledgeSelectionResponse(
            resolution_id=selection.resolution_id,
            selected_candidates=selection.selected_candidates,
            selected_collection_handles=selected_collection_handles,
            selected_kb_handles=selected_kb_handles,
            graph_mutation=mutation,
        )
