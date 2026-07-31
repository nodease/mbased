from typing import TYPE_CHECKING

from .entities import KnowledgeCollectionRef, LLMNodeData

if TYPE_CHECKING:
    from .llm_node import LLMNode


def __getattr__(name: str) -> object:
    """Load worker runtime implementations only when explicitly requested."""

    if name == "LLMNode":
        from .llm_node import LLMNode

        return LLMNode
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["KnowledgeCollectionRef", "LLMNode", "LLMNodeData"]
