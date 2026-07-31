from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.shared.services.rag_hierarchy import DEFAULT_PARENT_TARGET_SIZE
from langchain_text_splitters import RecursiveCharacterTextSplitter

CANONICAL_HIERARCHY_METADATA_KEYS = {
    "parent_chunk_id",
    "chunk_level",
    "section_path",
    "heading",
}


@dataclass(frozen=True)
class HierarchicalChunkerConfig:
    child_chunk_size: int
    child_chunk_overlap: int
    segment_identifier: str = "\n\n"
    parent_target_size: int = DEFAULT_PARENT_TARGET_SIZE
    parent_chunk_overlap: int = 0


class HierarchicalChunker:
    """FILE/API text block을 parent routing chunk와 child evidence chunk로 나눈다."""

    def __init__(self, config: HierarchicalChunkerConfig):
        self.config = config
        separators = self._separators(config.segment_identifier)
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.parent_target_size,
            chunk_overlap=config.parent_chunk_overlap,
            separators=separators,
            keep_separator=True,
        )
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.child_chunk_size,
            chunk_overlap=config.child_chunk_overlap,
            separators=separators,
            keep_separator=True,
        )

    @staticmethod
    def _separators(segment_identifier: str | None) -> list[str]:
        separators = ["\n\n", "\n", ".", " ", ""]
        if segment_identifier:
            identifier = str(segment_identifier).replace("\\n", "\n")
            if identifier not in separators:
                separators.insert(0, identifier)
        return separators

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        return {
            key: value
            for key, value in dict(metadata or {}).items()
            if key not in CANONICAL_HIERARCHY_METADATA_KEYS
        }

    @staticmethod
    def _section_path(metadata: dict[str, Any], fallback_index: int) -> list[str]:
        value = metadata.get("section_path")
        if isinstance(value, list):
            path = [str(item).strip() for item in value if str(item).strip()]
            if path:
                return path
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in value.split("/") if part.strip()]

        heading = metadata.get("heading") or metadata.get("title")
        if heading:
            return [str(heading).strip()]
        return [f"Section {fallback_index}"]

    @staticmethod
    def _heading(metadata: dict[str, Any], section_path: list[str]) -> str | None:
        heading = metadata.get("heading") or metadata.get("title")
        if heading:
            return str(heading).strip()
        return section_path[-1] if section_path else None

    def build(self, raw_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        parent_index = 0

        for block_index, block in enumerate(raw_blocks, start=1):
            content = str(block.get("content") or "").strip()
            if not content:
                continue

            original_metadata = dict(block.get("metadata") or {})
            base_section_path = self._section_path(original_metadata, block_index)
            heading = self._heading(original_metadata, base_section_path)
            safe_metadata = self._safe_metadata(original_metadata)
            parent_splits = [
                split.strip()
                for split in self.parent_splitter.split_text(content)
                if split.strip()
            ]

            for parent_content in parent_splits:
                parent_ref = f"parent-{parent_index}"
                section_path = list(base_section_path)

                child_splits = [
                    split.strip()
                    for split in self.child_splitter.split_text(parent_content)
                    if split.strip()
                ]
                if not child_splits:
                    continue

                chunks.append(
                    {
                        "content": parent_content,
                        "metadata": safe_metadata.copy(),
                        "chunk_level": "parent",
                        "local_ref": parent_ref,
                        "section_path": section_path,
                        "heading": heading,
                    }
                )

                for child_content in child_splits:
                    chunks.append(
                        {
                            "content": child_content,
                            "metadata": safe_metadata.copy(),
                            "chunk_level": "child",
                            "parent_ref": parent_ref,
                            "section_path": section_path,
                            "heading": heading,
                        }
                    )
                parent_index += 1

        return chunks


def filter_hierarchical_chunks(
    chunks: list[dict[str, Any]],
    *,
    selection_mode: str,
    keyword_filter: str | None = None,
) -> list[dict[str, Any]]:
    mode = str(selection_mode or "all").lower()
    if mode == "all":
        return chunks
    if mode != "keyword" or not keyword_filter:
        return chunks

    keyword = keyword_filter.lower()
    selected_parent_refs = {
        chunk.get("parent_ref")
        for chunk in chunks
        if chunk.get("chunk_level") == "child"
        and keyword in str(chunk.get("content") or "").lower()
    }
    if not selected_parent_refs:
        return []

    return [
        chunk
        for chunk in chunks
        if (
            chunk.get("chunk_level") == "parent"
            and chunk.get("local_ref") in selected_parent_refs
        )
        or (
            chunk.get("chunk_level") == "child"
            and chunk.get("parent_ref") in selected_parent_refs
            and keyword in str(chunk.get("content") or "").lower()
        )
    ]
