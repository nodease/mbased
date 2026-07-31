from typing import Any

from pydantic import BaseModel, Field

from apps.workflow_engine.workflow.nodes.base.entities import BaseNodeData


class SlackReferencedVariable(BaseModel):
    name: str
    value_selector: list[str]


class SlackPostNodeData(BaseNodeData):
    slackMode: str = "api"
    channel: str | None = None
    message: str = ""
    blocks: Any = None
    attachments: Any = None
    thread_ts: str | None = None
    username: str | None = None
    icon_emoji: str | None = None
    referenced_variables: list[SlackReferencedVariable] = Field(default_factory=list)

    # Legacy HTTP-shaped fields are only read for compatibility validation.
    url: str | None = None
    authConfig: dict[str, Any] = Field(default_factory=dict)
    method: str | None = None
    headers: list[dict[str, Any]] = Field(default_factory=list)
    body: str | None = None
    timeout: int | None = None
    authType: str | None = None
