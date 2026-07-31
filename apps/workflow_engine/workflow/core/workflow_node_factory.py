from typing import Dict

from apps.shared.domain.mail_credential import (
    MAIL_NODE_UI_METADATA_FIELDS,
    validate_mail_node_credential_boundary,
    validate_mail_processing_node_boundary,
)
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowKnowledgeReferenceError,
    parse_llm_knowledge_references,
)
from apps.shared.schemas.workflow import NodeSchema
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError
from apps.workflow_engine.workflow.nodes.answer import AnswerNode, AnswerNodeData
from apps.workflow_engine.workflow.nodes.base.node import Node
from apps.workflow_engine.workflow.nodes.code import CodeNode, CodeNodeData
from apps.workflow_engine.workflow.nodes.condition import (
    ConditionNode,
    ConditionNodeData,
)
from apps.workflow_engine.workflow.nodes.file_extraction import (
    FileExtractionNode,
    FileExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.github import GithubNode, GithubNodeData
from apps.workflow_engine.workflow.nodes.http import (
    HttpRequestNode,
    HttpRequestNodeData,
)
from apps.workflow_engine.workflow.nodes.llm import LLMNode, LLMNodeData
from apps.workflow_engine.workflow.nodes.loop import LoopNode, LoopNodeData
from apps.workflow_engine.workflow.nodes.mail import (
    GmailDraftNode,
    GmailDraftNodeData,
    MailAcknowledgeNode,
    MailAcknowledgeNodeData,
    MailNode,
    MailNodeData,
)
from apps.workflow_engine.workflow.nodes.schedule import (
    ScheduleTriggerNode,
    ScheduleTriggerNodeData,
)
from apps.workflow_engine.workflow.nodes.slack import SlackPostNode, SlackPostNodeData
from apps.workflow_engine.workflow.nodes.start import StartNode, StartNodeData
from apps.workflow_engine.workflow.nodes.template.entities import TemplateNodeData
from apps.workflow_engine.workflow.nodes.template.template_node import TemplateNode
from apps.workflow_engine.workflow.nodes.variable_extraction import (
    VariableExtractionNode,
    VariableExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.webhook import (
    WebhookTriggerNode,
    WebhookTriggerNodeData,
)
from apps.workflow_engine.workflow.nodes.workflow.entities import WorkflowNodeData
from apps.workflow_engine.workflow.nodes.workflow.workflow_node import WorkflowNode


class NodeFactory:
    """
    NodeSchema를 실제 Node 인스턴스로 변환하는 Factory 클래스
    새로운 노드 타입 추가 시 NODE_REGISTRY에 등록만 하면 됨
    """

    # 노드 타입 → (NodeClass, DataClass) 매핑
    # 프론트엔드 React Flow 타입명과 일치해야 함
    NODE_REGISTRY: Dict[str, tuple] = {
        "startNode": (StartNode, StartNodeData),
        "webhookTrigger": (WebhookTriggerNode, WebhookTriggerNodeData),
        "scheduleTrigger": (ScheduleTriggerNode, ScheduleTriggerNodeData),
        "answerNode": (AnswerNode, AnswerNodeData),
        "codeNode": (CodeNode, CodeNodeData),
        "conditionNode": (ConditionNode, ConditionNodeData),
        "llmNode": (LLMNode, LLMNodeData),
        "httpRequestNode": (HttpRequestNode, HttpRequestNodeData),
        "slackPostNode": (SlackPostNode, SlackPostNodeData),
        "githubNode": (GithubNode, GithubNodeData),
        "mailNode": (MailNode, MailNodeData),
        "gmailDraftNode": (GmailDraftNode, GmailDraftNodeData),
        "mailAcknowledgeNode": (MailAcknowledgeNode, MailAcknowledgeNodeData),
        "templateNode": (TemplateNode, TemplateNodeData),
        "workflowNode": (WorkflowNode, WorkflowNodeData),
        "fileExtractionNode": (FileExtractionNode, FileExtractionNodeData),
        "variableExtractionNode": (VariableExtractionNode, VariableExtractionNodeData),
        "loopNode": (LoopNode, LoopNodeData),
    }

    @staticmethod
    def create(
        schema: NodeSchema,
        context: Dict = None,
        runtime_dependencies: WorkflowRuntimeDependencies | None = None,
    ) -> Node:
        """
        NodeSchema로부터 적절한 Node 인스턴스를 생성

        Args:
            schema: 노드 스키마 (타입, 데이터 등 포함)
            context: 실행 컨텍스트 (user_id 등)

        Returns:
            생성된 Node 인스턴스

        Raises:
            NotImplementedError: 등록되지 않은 노드 타입일 때
        """
        if schema.type not in NodeFactory.NODE_REGISTRY:
            raise NotImplementedError(
                f"Node type '{schema.type}' is not implemented yet. "
                f"Available types: {list(NodeFactory.NODE_REGISTRY.keys())}"
            )

        runtime_data = schema.data
        if schema.type == "mailNode":
            validate_mail_node_credential_boundary(schema.data)
            runtime_data = {
                key: value
                for key, value in schema.data.items()
                if key not in MAIL_NODE_UI_METADATA_FIELDS
            }
        elif schema.type in {"gmailDraftNode", "mailAcknowledgeNode"}:
            validate_mail_processing_node_boundary(schema.type, schema.data)
            runtime_data = {
                key: value
                for key, value in schema.data.items()
                if key not in MAIL_NODE_UI_METADATA_FIELDS
            }
        elif schema.type == "llmNode":
            try:
                parse_llm_knowledge_references(schema.data)
            except WorkflowKnowledgeReferenceError as exc:
                raise NonRetryableWorkflowError(exc.reason_code) from exc

        NodeClass, DataClass = NodeFactory.NODE_REGISTRY[schema.type]
        data = DataClass(**runtime_data)
        node = NodeClass(schema.id, data, execution_context=context)
        if runtime_dependencies is not None and isinstance(
            node, (WorkflowNode, LoopNode)
        ):
            node.bind_runtime_dependencies(runtime_dependencies)
        if schema.type == "llmNode" and runtime_dependencies is not None:
            resolver = runtime_dependencies.knowledge_runtime_candidate_resolver
            if resolver is not None:
                node.bind_knowledge_runtime_candidate_resolver(resolver)
            provider_runtime = runtime_dependencies.provider_execution_runtime
            if provider_runtime is not None:
                node.bind_provider_execution_runtime(provider_runtime)
            usage_recorder = runtime_dependencies.provider_usage_recorder
            if usage_recorder is not None:
                node.bind_provider_usage_recorder(usage_recorder)
            query_runtime = runtime_dependencies.query_embedding_runtime
            if query_runtime is not None:
                node.bind_query_embedding_runtime(query_runtime)
        if schema.type == "fileExtractionNode" and runtime_dependencies is not None:
            fetcher = runtime_dependencies.remote_file_fetcher
            if fetcher is not None:
                node.bind_remote_file_fetcher(fetcher)
        node.runtime_node_type = schema.type
        return node
