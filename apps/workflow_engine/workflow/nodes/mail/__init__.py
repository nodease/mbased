"""Mail 노드 패키지"""

from apps.workflow_engine.workflow.nodes.mail.acknowledge_node import MailAcknowledgeNode
from apps.workflow_engine.workflow.nodes.mail.entities import (
    EmailProvider,
    GmailDraftNodeData,
    MailAcknowledgeNodeData,
    MailNodeData,
    MailVariable,
)
from apps.workflow_engine.workflow.nodes.mail.gmail_draft_node import GmailDraftNode
from apps.workflow_engine.workflow.nodes.mail.mail_node import MailNode

__all__ = [
    "MailNode",
    "MailNodeData",
    "EmailProvider",
    "MailVariable",
    "GmailDraftNode",
    "GmailDraftNodeData",
    "MailAcknowledgeNode",
    "MailAcknowledgeNodeData",
]
