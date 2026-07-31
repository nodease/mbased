import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow import Workflow


class LLMNodeVersion(Base):
    __tablename__ = "llm_node_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    node_id: Mapped[str] = mapped_column(Text, nullable=False)

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    parent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    source_workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_model_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assistant_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    referenced_variables: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    knowledge_bases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    context_variable: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    top_k: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    output_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    retrieval_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    tool_config: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    app: Mapped["App"] = relationship("App", back_populates="llm_node_versions")
    source_workflow: Mapped[Optional["Workflow"]] = relationship("Workflow")
    parent_version: Mapped[Optional["LLMNodeVersion"]] = relationship(
        "LLMNodeVersion",
        remote_side=[app_id, node_id, id],
        foreign_keys=[app_id, node_id, parent_version_id],
        back_populates="child_versions",
        overlaps="app,llm_node_versions",
    )
    child_versions: Mapped[list["LLMNodeVersion"]] = relationship(
        "LLMNodeVersion",
        foreign_keys=[app_id, node_id, parent_version_id],
        back_populates="parent_version",
        overlaps="app,llm_node_versions",
    )

    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "node_id",
            "version_number",
            name="uq_llm_node_versions_app_node_version",
        ),
        UniqueConstraint(
            "app_id",
            "node_id",
            "id",
            name="uq_llm_node_versions_app_node_id",
        ),
        ForeignKeyConstraint(
            ["app_id", "node_id", "parent_version_id"],
            [
                "llm_node_versions.app_id",
                "llm_node_versions.node_id",
                "llm_node_versions.id",
            ],
            name="fk_llm_node_versions_parent_same_node",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_llm_node_versions_version_number_positive",
        ),
        CheckConstraint(
            "top_k IS NULL OR top_k > 0",
            name="ck_llm_node_versions_top_k_positive",
        ),
        CheckConstraint(
            "score_threshold IS NULL OR (score_threshold >= 0 AND score_threshold <= 1)",
            name="ck_llm_node_versions_score_threshold_range",
        ),
    )
