from __future__ import annotations

from typing import Any


def filter_chunks_by_selection(
    chunks: list[dict[str, Any]],
    *,
    selection_mode: str | None,
    chunk_range: str | None = None,
    keyword_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Apply the persisted flat-chunk selection contract."""
    if not chunks:
        return []

    if selection_mode is not None and not isinstance(selection_mode, str):
        raise ValueError("invalid selection mode")
    mode = (selection_mode or "all").lower()
    if mode not in {"all", "range", "keyword"}:
        raise ValueError("invalid selection mode")
    if mode == "all":
        return chunks

    if mode == "range" and chunk_range:
        if not isinstance(chunk_range, str):
            raise ValueError("invalid chunk range")
        indices: set[int] = set()
        try:
            for part in (value.strip() for value in chunk_range.split(",")):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    indices.update(range(start, end + 1))
                else:
                    indices.add(int(part))
        except (TypeError, ValueError):
            raise ValueError("invalid chunk range") from None
        return [chunk for index, chunk in enumerate(chunks, start=1) if index in indices]

    if mode == "keyword" and keyword_filter:
        if not isinstance(keyword_filter, str):
            raise ValueError("invalid keyword filter")
        keyword = keyword_filter.lower()
        return [
            chunk
            for chunk in chunks
            if keyword in str(chunk.get("content") or "").lower()
        ]

    return chunks
