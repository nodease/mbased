from typing import Any

SOURCE_TIER_PRIORITY: dict[str, int] = {
    "legal": 100,
    "legal_regulation": 100,
    "contract": 90,
    "company_policy": 80,
    "policy": 80,
    "adr": 70,
    "adr_decision": 70,
    "official_documentation": 60,
    "official_doc": 60,
    "runbook": 50,
    "operational_runbook": 50,
    "semantic_definition": 40,
    "curated_query_corpus": 30,
    "conversation": 10,
    "conversation_or_thread": 10,
}

SOURCE_TIER_POLICY_OFF = "off"
SOURCE_TIER_POLICY_TIE_BREAK = "tie_break"
SOURCE_TIER_POLICIES = {
    SOURCE_TIER_POLICY_OFF,
    SOURCE_TIER_POLICY_TIE_BREAK,
}


def normalize_source_tier_policy(policy: Any) -> str:
    if policy is None:
        return SOURCE_TIER_POLICY_TIE_BREAK
    value = str(policy).strip().lower()
    return value if value in SOURCE_TIER_POLICIES else SOURCE_TIER_POLICY_TIE_BREAK


def source_tier_priority(source_tier: Any) -> int:
    if source_tier is None:
        return 0
    return SOURCE_TIER_PRIORITY.get(str(source_tier).strip().lower(), 0)


def chunk_source_tier_priority(chunk: Any) -> int:
    for metadata_attr in ("metadata_summary", "metadata", "metadata_"):
        metadata = getattr(chunk, metadata_attr, None)
        if isinstance(metadata, dict):
            priority = source_tier_priority(metadata.get("source_tier"))
            if priority:
                return priority

    priority = source_tier_priority(getattr(chunk, "source_tier", None))
    if priority:
        return priority

    document_version = getattr(chunk, "document_version", None)
    return source_tier_priority(getattr(document_version, "source_tier", None))


def retrieval_candidate_source_tier_priority(candidate: dict[str, Any]) -> int:
    chunk = candidate.get("chunk")
    if chunk is not None:
        priority = chunk_source_tier_priority(chunk)
        if priority:
            return priority

    doc = candidate.get("doc")
    metadata = getattr(doc, "meta_info", None)
    if isinstance(metadata, dict):
        return source_tier_priority(metadata.get("source_tier"))
    return 0


def source_tier_tie_break_enabled(policy: Any) -> bool:
    return normalize_source_tier_policy(policy) == SOURCE_TIER_POLICY_TIE_BREAK
