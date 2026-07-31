from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from apps.shared.domain.mail_credential import (
    MailNodeCredentialBoundaryError,
    MailProcessingGraphBoundaryError,
    validate_mail_node_credential_boundary,
    validate_mail_processing_graph_contract,
    validate_mail_processing_node_boundary,
)
from apps.shared.domain.slack_delivery import (
    SlackGraphBoundaryError,
    validate_slack_graph_boundary,
)
from apps.shared.domain.workflow_graph import (
    WorkflowGraphValidationError,
    validate_workflow_graph,
)

from .models import NodeCatalogSnapshot, PreflightAudience, PreflightStatus
from .ports import DeploymentPreflightRepository

MANAGED_NODE_TYPES = frozenset(
    {"mailNode", "gmailDraftNode", "mailAcknowledgeNode", "slackPostNode"}
)
RUNTIME_AUTHORITATIVE_NODE_TYPES = frozenset(
    {"llmNode", "httpRequestNode", "githubNode"}
)
EXTERNAL_SIDE_EFFECTS = frozenset({"external_read", "external_write"})
AUXILIARY_NODE_TYPES = frozenset({"note"})


@dataclass(frozen=True)
class NodeConfigurationIssue:
    node_id: str | None
    node_type: str
    severity: PreflightStatus
    reason_code: str
    permission_resource_id: uuid.UUID | None = None
    permission_effective_auth_state: str | None = None


class NodeConfigurationEvaluator:
    MAX_LOOP_DEPTH = 16

    def __init__(
        self,
        repository: DeploymentPreflightRepository,
        *,
        organization_id: uuid.UUID | None,
        principal_id: uuid.UUID | None,
        node_catalog_by_type: Mapping[str, NodeCatalogSnapshot],
    ) -> None:
        self.repository = repository
        self.organization_id = organization_id
        self.principal_id = principal_id
        self.node_catalog_by_type = dict(node_catalog_by_type)

    def evaluate(
        self,
        graph_snapshot: dict,
        *,
        audience: PreflightAudience,
    ) -> list[NodeConfigurationIssue]:
        nodes, graph_issue = self._flatten_nodes(graph_snapshot)
        if graph_issue is not None:
            return [graph_issue]

        issues = self._catalog_issues(nodes)
        if any(issue.reason_code == "workflow_graph_invalid" for issue in issues):
            return self._dedupe(issues)

        mail_nodes = [
            node
            for node in nodes
            if str(node.get("type") or "")
            in {"mailNode", "gmailDraftNode", "mailAcknowledgeNode"}
        ]
        if audience == "anonymous_public":
            issues.extend(
                self._issue(node, "mail_execution_subject_required")
                for node in mail_nodes
            )
        issues.extend(self._mail_issues(graph_snapshot, mail_nodes))
        if audience == "workflow_node_inherited":
            issues.extend(
                self._issue(
                    node,
                    "mail_execution_subject_inherited",
                    severity="warning",
                )
                for node in mail_nodes
            )

        issues.extend(self._slack_issues(graph_snapshot, nodes))
        return self._dedupe(issues)

    def _catalog_issues(
        self,
        nodes: list[Mapping[str, Any]],
    ) -> list[NodeConfigurationIssue]:
        issues: list[NodeConfigurationIssue] = []
        for node in nodes:
            node_type = str(node.get("type") or "")
            if node_type in AUXILIARY_NODE_TYPES:
                continue
            definition = self.node_catalog_by_type.get(node_type)
            if definition is None:
                issues.append(self._issue(node, "workflow_graph_invalid"))
                continue
            if not definition.implemented:
                issues.append(
                    self._issue(node, "node_configuration_validator_unavailable")
                )
                continue
            if definition.side_effect not in EXTERNAL_SIDE_EFFECTS:
                continue
            if node_type in MANAGED_NODE_TYPES:
                continue
            if node_type in RUNTIME_AUTHORITATIVE_NODE_TYPES:
                continue
            issues.append(self._issue(node, "node_configuration_validator_unavailable"))
        return issues

    def _mail_issues(
        self,
        graph_snapshot: dict,
        mail_nodes: list[Mapping[str, Any]],
    ) -> list[NodeConfigurationIssue]:
        issues: list[NodeConfigurationIssue] = []
        credential_ids: set[uuid.UUID] = set()
        parsed_credentials: dict[int, uuid.UUID] = {}

        for node in mail_nodes:
            node_type = str(node.get("type") or "")
            data = node.get("data")
            if not isinstance(data, Mapping):
                issues.append(self._issue(node, "node_configuration_invalid"))
                continue
            if data.get("configuration_state") == "unresolved":
                issues.append(self._issue(node, "node_configuration_unresolved"))
            elif data.get("configuration_state") not in (None, "resolved"):
                issues.append(self._issue(node, "node_configuration_invalid"))

            try:
                if node_type == "mailNode":
                    validate_mail_node_credential_boundary(data)
                else:
                    validate_mail_processing_node_boundary(
                        node_type,
                        data,
                        allow_unresolved=False,
                    )
            except MailNodeCredentialBoundaryError:
                reason = (
                    "node_configuration_unresolved"
                    if self._mail_node_is_unresolved(node_type, data)
                    else "node_configuration_invalid"
                )
                issues.append(self._issue(node, reason))
                if reason == "node_configuration_invalid":
                    continue

            if node_type == "mailAcknowledgeNode":
                continue
            raw_credential_id = data.get("credential_id")
            if raw_credential_id is None:
                reason = (
                    "node_configuration_unresolved"
                    if self._mail_node_is_unresolved(node_type, data)
                    else "node_configuration_invalid"
                )
                issues.append(self._issue(node, reason))
                continue
            if raw_credential_id == "":
                issues.append(self._issue(node, "node_configuration_invalid"))
                continue
            credential_id = self._uuid_or_none(raw_credential_id)
            if credential_id is None:
                issues.append(self._issue(node, "node_configuration_invalid"))
                continue
            parsed_credentials[id(node)] = credential_id
            credential_ids.add(credential_id)

        snapshots = (
            self.repository.get_mail_credential_snapshots(
                credential_ids,
                self.organization_id,
                self.principal_id,
            )
            if credential_ids
            else {}
        )
        for node in mail_nodes:
            credential_id = parsed_credentials.get(id(node))
            if credential_id is None:
                continue
            snapshot = snapshots.get(credential_id)
            if snapshot is None:
                issues.append(self._issue(node, "mail_credential_unavailable"))
                continue
            if self.principal_id is not None and not snapshot.usable_by_principal:
                issues.append(
                    self._issue(
                        node,
                        "mail_credential_unavailable",
                        permission_resource_id=credential_id,
                        permission_effective_auth_state=(snapshot.effective_auth_state),
                    )
                )
                continue
            if node.get("type") == "gmailDraftNode" and (
                snapshot.provider != "gmail" or snapshot.auth_type != "oauth2"
            ):
                issues.append(self._issue(node, "node_configuration_invalid"))

        try:
            validate_mail_processing_graph_contract(
                graph_snapshot,
                require_resolved=True,
            )
        except MailProcessingGraphBoundaryError as exc:
            target = next(
                (
                    node
                    for node in mail_nodes
                    if str(node.get("id") or "") == str(exc.node_id or "")
                ),
                mail_nodes[0] if mail_nodes else None,
            )
            if target is not None:
                data = target.get("data")
                reason = (
                    "node_configuration_unresolved"
                    if isinstance(data, Mapping)
                    and self._mail_node_is_unresolved(
                        str(target.get("type") or ""), data
                    )
                    else "node_configuration_invalid"
                )
                issues.append(self._issue(target, reason))
        return issues

    def _slack_issues(
        self,
        graph_snapshot: dict,
        nodes: list[Mapping[str, Any]],
    ) -> list[NodeConfigurationIssue]:
        slack_nodes = [node for node in nodes if node.get("type") == "slackPostNode"]
        issues: list[NodeConfigurationIssue] = []
        for node in slack_nodes:
            try:
                validate_slack_graph_boundary(
                    [node],
                    require_resolved=True,
                    allow_legacy_selectors=True,
                )
            except SlackGraphBoundaryError:
                data = node.get("data")
                reason = (
                    "node_configuration_unresolved"
                    if isinstance(data, Mapping)
                    and self._slack_node_is_unresolved(data)
                    else "node_configuration_invalid"
                )
                issues.append(self._issue(node, reason))

        try:
            validate_slack_graph_boundary(
                graph_snapshot.get("nodes", []),
                require_resolved=False,
                allow_legacy_selectors=False,
            )
        except SlackGraphBoundaryError:
            if slack_nodes and not any(
                issue.reason_code == "node_configuration_invalid" for issue in issues
            ):
                issues.append(self._issue(slack_nodes[0], "node_configuration_invalid"))
        return issues

    def _flatten_nodes(
        self,
        graph_snapshot: Any,
    ) -> tuple[list[Mapping[str, Any]], NodeConfigurationIssue | None]:
        try:
            validate_workflow_graph(graph_snapshot)
        except WorkflowGraphValidationError:
            return [], self._graph_issue()
        pending: list[tuple[Mapping[str, Any], int]] = [(graph_snapshot, 0)]
        result: list[Mapping[str, Any]] = []
        while pending:
            graph, depth = pending.pop()
            if depth > self.MAX_LOOP_DEPTH:
                return [], self._graph_issue()
            nodes = graph.get("nodes")
            if not isinstance(nodes, list):
                return [], self._graph_issue()
            for node in nodes:
                data = node.get("data")
                result.append(node)
                subgraph = data.get("subGraph") if isinstance(data, Mapping) else None
                if subgraph is not None:
                    if not isinstance(subgraph, Mapping):
                        return [], self._graph_issue(node)
                    pending.append((subgraph, depth + 1))
        return result, None

    @staticmethod
    def _mail_node_is_unresolved(node_type: str, data: Mapping[str, Any]) -> bool:
        if node_type == "mailNode":
            if not (
                data.get("credential_id") is None
                and (
                    data.get("configuration_state") == "unresolved"
                    or "configuration_state" not in data
                )
            ):
                return False
            completed = dict(data)
            completed["credential_id"] = str(uuid.UUID(int=0))
            completed["configuration_state"] = "resolved"
            try:
                validate_mail_node_credential_boundary(completed)
            except MailNodeCredentialBoundaryError:
                return False
            return True

        if node_type == "gmailDraftNode":
            has_missing_field = (
                data.get("credential_id") is None
                or data.get("processing_ref_selector") in (None, [])
                or data.get("reply_body_selector") in (None, [])
            )
            if not (
                has_missing_field and data.get("configuration_state") == "unresolved"
            ):
                return False
            completed = dict(data)
            if completed.get("credential_id") is None:
                completed["credential_id"] = str(uuid.UUID(int=0))
            completed["configuration_state"] = "resolved"
            if completed.get("processing_ref_selector") in (None, []):
                completed["processing_ref_selector"] = ["mail", "processing_ref"]
            if completed.get("reply_body_selector") in (None, []):
                completed["reply_body_selector"] = ["llm", "result"]
        elif node_type == "mailAcknowledgeNode":
            has_missing_field = data.get("processing_ref_selector") in (
                None,
                [],
            ) or data.get("required_effect_ref_selectors") in (None, [])
            if not has_missing_field:
                return False
            completed = dict(data)
            if completed.get("processing_ref_selector") in (None, []):
                completed["processing_ref_selector"] = ["mail", "processing_ref"]
            if completed.get("required_effect_ref_selectors") in (None, []):
                completed["required_effect_ref_selectors"] = [["draft", "draft_ref"]]
        else:
            return False

        try:
            validate_mail_processing_node_boundary(
                node_type,
                completed,
                allow_unresolved=False,
            )
        except MailNodeCredentialBoundaryError:
            return False
        return True

    @staticmethod
    def _slack_node_is_unresolved(data: Mapping[str, Any]) -> bool:
        mode = data.get("slackMode", "api")
        completed = dict(data)
        has_missing_field = False
        if completed.get("configuration_state") == "unresolved":
            completed["configuration_state"] = "resolved"
        if completed.get("channel_resolution_state") == "unresolved":
            completed["channel_resolution_state"] = "resolved"
        if mode == "api":
            auth_config = data.get("authConfig")
            token = (
                auth_config.get("token") if isinstance(auth_config, Mapping) else None
            )
            if not token and isinstance(auth_config, Mapping):
                completed["authConfig"] = {
                    **auth_config,
                    "token": "configuration-ready",
                }
                has_missing_field = True
            if not data.get("channel"):
                completed["channel"] = "configuration-ready"
                has_missing_field = True
        elif mode == "webhook":
            if not data.get("url"):
                completed["url"] = (
                    "https://hooks.slack.com/services/T00000000/B00000000/"
                    "configuration-ready"
                )
                has_missing_field = True
        else:
            return False

        candidates = [(completed, has_missing_field)]
        message = completed.get("message")
        if message is None or (isinstance(message, str) and not message.strip()):
            with_payload = dict(completed)
            with_payload["message"] = "configuration-ready"
            candidates.append((with_payload, True))

        for candidate, candidate_has_missing_field in candidates:
            try:
                validate_slack_graph_boundary(
                    [{"id": "slack", "type": "slackPostNode", "data": candidate}],
                    require_resolved=True,
                    allow_legacy_selectors=True,
                )
            except SlackGraphBoundaryError:
                continue
            return candidate_has_missing_field
        return False

    @staticmethod
    def _issue(
        node: Mapping[str, Any],
        reason_code: str,
        *,
        severity: PreflightStatus = "blocked",
        permission_resource_id: uuid.UUID | None = None,
        permission_effective_auth_state: str | None = None,
    ) -> NodeConfigurationIssue:
        return NodeConfigurationIssue(
            node_id=str(node.get("id"))[:128] if node.get("id") is not None else None,
            node_type=str(node.get("type") or "unknown")[:64],
            severity=severity,
            reason_code=reason_code,
            permission_resource_id=permission_resource_id,
            permission_effective_auth_state=permission_effective_auth_state,
        )

    @staticmethod
    def _graph_issue(
        node: Mapping[str, Any] | None = None,
    ) -> NodeConfigurationIssue:
        return NodeConfigurationIssue(
            node_id=(
                str(node.get("id"))[:128]
                if isinstance(node, Mapping) and node.get("id") is not None
                else None
            ),
            node_type=(
                str(node.get("type") or "unknown")[:64]
                if isinstance(node, Mapping)
                else "unknown"
            ),
            severity="blocked",
            reason_code="workflow_graph_invalid",
        )

    @staticmethod
    def _dedupe(
        issues: Iterable[NodeConfigurationIssue],
    ) -> list[NodeConfigurationIssue]:
        result: list[NodeConfigurationIssue] = []
        seen: set[
            tuple[
                str | None,
                str,
                PreflightStatus,
                str,
                uuid.UUID | None,
                str | None,
            ]
        ] = set()
        for issue in issues:
            key = (
                issue.node_id,
                issue.node_type,
                issue.severity,
                issue.reason_code,
                issue.permission_resource_id,
                issue.permission_effective_auth_state,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result

    @staticmethod
    def _uuid_or_none(value: object) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None
