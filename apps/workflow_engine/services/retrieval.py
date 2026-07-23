import logging
import os
import re

from gevent.monkey import get_original
from sqlalchemy import and_, bindparam, or_, select
from sqlalchemy.orm import Session, aliased

from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
)
from apps.shared.db.models.llm import LLMCredential, LLMModel, LLMProvider
from apps.shared.schemas.rag import ChunkPreview, RAGResponse
from apps.shared.services.rag_filters import (
    NormalizedMetadataFilter,
    bind_keyword_filter_params,
    build_keyword_filter_clause,
    build_sqlalchemy_filter_conditions,
)
from apps.shared.services.rag_hierarchy import (
    HIERARCHY_MODE_PARENT_CHILD,
    child_pool_limit,
    normalize_hierarchy_mode,
    parent_candidate_limit,
)
from apps.shared.services.rag_source_tier import (
    normalize_source_tier_policy,
    retrieval_candidate_source_tier_priority,
    source_tier_tie_break_enabled,
)
from apps.shared.services.retrieval_embedding_model_projection import (
    EmbeddingModelBinding,
)
from apps.shared.services.retrieval_metadata import (
    chunk_metadata as build_chunk_metadata,
    hierarchy_path as build_hierarchy_path,
    metadata_summary as build_metadata_summary,
)
from apps.workflow_engine.services.llm_service import LLMService
from apps.workflow_engine.utils.encryption import encryption_manager

logger = logging.getLogger(__name__)

RAG_RERANK_ENABLED_ENV = "RAG_CROSS_ENCODER_RERANK_ENABLED"
RAG_RERANK_MODEL_ENV = "RAG_CROSS_ENCODER_MODEL"
DEFAULT_RAG_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-12-v2"
_allocate_native_lock = get_original("_thread", "allocate_lock")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class RetrievalService:
    _cross_encoder_model = None
    _cross_encoder_model_name = None
    _cross_encoder_model_lock = _allocate_native_lock()

    def __init__(self, db: Session, user_id, organization_id=None):
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.llm_client = None

    @classmethod
    def _is_cross_encoder_rerank_enabled(cls) -> bool:
        return _env_flag(RAG_RERANK_ENABLED_ENV, default=False)

    @classmethod
    def _get_cross_encoder_model(cls):
        from sentence_transformers import CrossEncoder

        model_name = os.getenv(RAG_RERANK_MODEL_ENV, DEFAULT_RAG_RERANK_MODEL).strip()
        if not model_name:
            model_name = DEFAULT_RAG_RERANK_MODEL

        if (
            cls._cross_encoder_model is not None
            and cls._cross_encoder_model_name == model_name
        ):
            return cls._cross_encoder_model

        with cls._cross_encoder_model_lock:
            if (
                cls._cross_encoder_model is None
                or cls._cross_encoder_model_name != model_name
            ):
                model = CrossEncoder(model_name, max_length=512)
                cls._cross_encoder_model = model
                cls._cross_encoder_model_name = model_name
        return cls._cross_encoder_model

    def _get_efficient_rewrite_model(self) -> str:
        """
        사용자의 credential을 확인하여 가장 효율적인(가성비) 모델을 반환합니다.
        Fallback: gpt-4o-mini
        """
        try:
            credential_query = self.db.query(LLMCredential).filter(
                LLMCredential.user_id == self.user_id,
                LLMCredential.is_valid,
            )
            if self.organization_id is not None:
                credential_query = credential_query.filter(
                    LLMCredential.organization_id == self.organization_id
                )
            credentials = credential_query.all()

            if not credentials:
                return "gpt-4o-mini"

            cred_map = {c.provider_id: c for c in credentials}
            providers = (
                self.db.query(LLMProvider)
                .filter(LLMProvider.id.in_(cred_map.keys()))
                .all()
            )

            provider_map = {p.id: p.name.lower() for p in providers}
            available_providers = set(provider_map.values())

            preferred_order = ["openai", "anthropic", "google"]

            for pref in preferred_order:
                if pref in available_providers:
                    model = LLMService.EFFICIENT_MODELS.get(pref)
                    if model:
                        return model

            for prov_name in available_providers:
                model = LLMService.EFFICIENT_MODELS.get(prov_name)
                if model:
                    return model

            return "gpt-4o-mini"

        except Exception as e:
            logger.warning(f"[Model Selection Error] Using default: {e}")
            return "gpt-4o-mini"

    async def _rewrite_query(self, query: str) -> str:
        """
        LLM을 사용하여 검색 쿼리를 최적화합니다. (Query Rewriting, 비동기)
        """
        try:
            rewrite_model_id = self._get_efficient_rewrite_model()
            client = LLMService.get_client_for_user(
                self.db,
                self.user_id,
                rewrite_model_id,
                organization_id=self.organization_id,
            )

            system_prompt = (
                "You are a search optimization expert. Rewrite the user's query to maximize relevance for a vector search engine.\n"
                "Rules:\n"
                "1. Detect the language of the original query and rewrite in the SAME language.\n"
                "2. Include synonyms and specific keywords.\n"
                "3. Remove unnecessary stopwords.\n"
                "4. Keep technical terms and proper nouns in their original language (usually English) if they are critical for search.\n"
                "5. Do NOT distort the original intent.\n"
                "6. Output ONLY the rewritten query."
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Original Query: {query}"},
            ]
            response = await client.invoke(messages, max_tokens=200)
            rewritten_query = response["choices"][0]["message"]["content"].strip()

            if rewritten_query.startswith('"') and rewritten_query.endswith('"'):
                rewritten_query = rewritten_query[1:-1]

            return rewritten_query

        except Exception as e:
            logger.error(f"Failed to rewrite query: {e}")
            return query

    async def _generate_multi_queries(
        self, query: str, num_variations: int = 3
    ) -> list[str]:
        """
        Multi-Query Expansion: LLM을 사용하여 원본 질문의 다양한 변형을 생성합니다. (비동기)
        """
        try:
            rewrite_model_id = self._get_efficient_rewrite_model()
            client = LLMService.get_client_for_user(
                self.db,
                self.user_id,
                rewrite_model_id,
                organization_id=self.organization_id,
            )

            system_prompt = (
                f"You are an expert research assistant. Generate {num_variations} different search queries that would help find information to answer the user's question.\n"
                "Each query should approach the question from a different angle or focus on different aspects.\n"
                "Rules:\n"
                "1. Each query should be in the same language as the original question.\n"
                "2. Focus on different entities, concepts, or relationships.\n"
                "3. Keep queries short and search-friendly (3-8 words each).\n"
                f"4. Output ONLY the queries, one per line, numbered 1-{num_variations}."
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Original Question: {query}"},
            ]
            response = await client.invoke(messages, max_tokens=500)
            content = response["choices"][0]["message"]["content"].strip()

            queries = []
            for line in content.split("\n"):
                line = line.strip()
                if line and any(
                    line.startswith(f"{i}.") or line.startswith(f"{i})")
                    for i in range(1, num_variations + 1)
                ):
                    query_text = line.lstrip("0123456789.)").strip()
                    if query_text:
                        queries.append(query_text)

            if len(queries) < num_variations:
                queries.append(await self._rewrite_query(query))

            return queries[:num_variations]

        except Exception as e:
            logger.error(f"[Multi-Query] Falling back to single query: {e}")
            return [await self._rewrite_query(query)]

    def _vector_search(
        self,
        query_vector: list,
        knowledge_base_id: str,
        top_k: int,
        metadata_filter: NormalizedMetadataFilter | None = None,
        *,
        chunk_levels: tuple[str | None, ...] | None = None,
        parent_ids: list | None = None,
        chunk_metadata_fallback: bool = True,
    ):
        if parent_ids is not None and not parent_ids:
            return []
        distance_col = DocumentChunk.embedding.cosine_distance(query_vector).label(
            "distance"
        )
        conditions = [
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "completed",
            self._retrieval_visible_chunk_condition(),
        ]
        conditions.extend(
            build_sqlalchemy_filter_conditions(
                metadata_filter,
                chunk_metadata_fallback=chunk_metadata_fallback,
            )
        )
        conditions.extend(self._chunk_level_conditions(chunk_levels))
        if parent_ids is not None:
            conditions.append(DocumentChunk.parent_chunk_id.in_(parent_ids))
        stmt = (
            select(DocumentChunk, Document, distance_col)
            .join(Document)
            .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.knowledge_base_id)
            .outerjoin(DocumentVersion, DocumentChunk.document_version_id == DocumentVersion.id)
            .where(*conditions)
            .order_by(distance_col)
            .limit(top_k)
        )
        return self.db.execute(stmt).all()

    def _keyword_search(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        metadata_filter: NormalizedMetadataFilter | None = None,
        *,
        chunk_levels: tuple[str | None, ...] | None = None,
        parent_ids: list | None = None,
        chunk_metadata_fallback: bool = True,
    ):
        from sqlalchemy import text

        if parent_ids is not None and not parent_ids:
            return []
        filter_clause = build_keyword_filter_clause(
            metadata_filter,
            chunk_metadata_fallback=chunk_metadata_fallback,
        )
        filter_sql = "".join(
            f"\n              AND {fragment}" for fragment in filter_clause.fragments
        )
        level_sql, level_params, level_expanding = self._keyword_level_filter(
            chunk_levels
        )
        parent_sql = ""
        if parent_ids is not None:
            parent_sql = "\n              AND dc.parent_chunk_id IN :parent_ids"
            level_params["parent_ids"] = list(parent_ids)
            level_expanding.append("parent_ids")
        stmt = text(f"""
            SELECT dc.id, dc.content, dc.metadata, dc.document_id, d.filename,
                   d.meta_info, d.source_type,
                   dc.parent_chunk_id, dc.chunk_level, dc.section_path, dc.heading,
                   dc.token_count, dv.source_tier,
                   ts_rank(
                       to_tsvector('english', dc.content || ' ' || COALESCE(CAST(dc.metadata->'keywords' AS TEXT), '')),
                       websearch_to_tsquery('english', :query)
                   ) as rank
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            JOIN knowledge_bases kb ON dc.knowledge_base_id = kb.id
            LEFT JOIN document_versions dv ON dc.document_version_id = dv.id
            WHERE dc.knowledge_base_id = :kb_id
              AND d.status = 'completed'
              AND (
                  (
                      kb.active_document_version_id IS NULL
                      AND dc.document_version_id IS NULL
                  )
                  OR (
                      kb.active_document_version_id = dc.document_version_id
                      AND dv.status = 'ready'
                  )
              )
              AND to_tsvector('english', dc.content || ' ' || COALESCE(CAST(dc.metadata->'keywords' AS TEXT), '')) @@ websearch_to_tsquery('english', :query)
              {filter_sql}
              {level_sql}
              {parent_sql}
            ORDER BY rank DESC
            LIMIT :top_k
        """)
        stmt = bind_keyword_filter_params(stmt, filter_clause)
        for param_name in level_expanding:
            stmt = stmt.bindparams(bindparam(param_name, expanding=True))
        params = {"query": query, "kb_id": knowledge_base_id, "top_k": top_k}
        params.update(filter_clause.params)
        params.update(level_params)
        return self.db.execute(stmt, params).fetchall()

    @staticmethod
    def _retrieval_visible_chunk_condition():
        return or_(
            and_(
                KnowledgeBase.active_document_version_id.is_(None),
                DocumentChunk.document_version_id.is_(None),
            ),
            and_(
                KnowledgeBase.active_document_version_id
                == DocumentChunk.document_version_id,
                DocumentVersion.status == "ready",
            ),
        )

    def _chunk_level_conditions(
        self, chunk_levels: tuple[str | None, ...] | None
    ) -> list:
        if chunk_levels is None:
            return []
        levels = list(chunk_levels)
        conditions = []
        non_null_levels = [level for level in levels if level is not None]
        if None in levels and non_null_levels:
            conditions.append(
                or_(
                    DocumentChunk.chunk_level.is_(None),
                    DocumentChunk.chunk_level.in_(non_null_levels),
                )
            )
        elif None in levels:
            conditions.append(DocumentChunk.chunk_level.is_(None))
        elif non_null_levels:
            conditions.append(DocumentChunk.chunk_level.in_(non_null_levels))
        return conditions

    def _keyword_level_filter(
        self, chunk_levels: tuple[str | None, ...] | None
    ) -> tuple[str, dict, list[str]]:
        if chunk_levels is None:
            return "", {}, []
        levels = list(chunk_levels)
        non_null_levels = [level for level in levels if level is not None]
        params = {}
        expanding = []
        if None in levels and non_null_levels:
            params["chunk_levels"] = non_null_levels
            expanding.append("chunk_levels")
            return (
                "\n              AND (dc.chunk_level IS NULL OR dc.chunk_level IN :chunk_levels)",
                params,
                expanding,
            )
        if None in levels:
            return "\n              AND dc.chunk_level IS NULL", params, expanding
        params["chunk_levels"] = non_null_levels
        expanding.append("chunk_levels")
        return "\n              AND dc.chunk_level IN :chunk_levels", params, expanding

    def _has_valid_hierarchy(self, knowledge_base_id: str) -> bool:
        parent = aliased(DocumentChunk)
        child_version = aliased(DocumentVersion)
        parent_version = aliased(DocumentVersion)
        return (
            self.db.query(DocumentChunk.id)
            .join(parent, DocumentChunk.parent_chunk_id == parent.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.knowledge_base_id)
            .outerjoin(child_version, DocumentChunk.document_version_id == child_version.id)
            .outerjoin(parent_version, parent.document_version_id == parent_version.id)
            .filter(
                DocumentChunk.knowledge_base_id == knowledge_base_id,
                DocumentChunk.chunk_level == "child",
                parent.chunk_level == "parent",
                DocumentChunk.document_id == parent.document_id,
                DocumentChunk.knowledge_base_id == parent.knowledge_base_id,
                Document.status == "completed",
                or_(
                    and_(
                        KnowledgeBase.active_document_version_id.is_(None),
                        DocumentChunk.document_version_id.is_(None),
                    ),
                    and_(
                        KnowledgeBase.active_document_version_id
                        == DocumentChunk.document_version_id,
                        child_version.status == "ready",
                    ),
                ),
                or_(
                    and_(
                        KnowledgeBase.active_document_version_id.is_(None),
                        parent.document_version_id.is_(None),
                    ),
                    and_(
                        KnowledgeBase.active_document_version_id
                        == parent.document_version_id,
                        parent_version.status == "ready",
                    ),
                ),
            )
            .first()
            is not None
        )

    @staticmethod
    def _search_method(
        *,
        use_hierarchy: bool,
        hybrid_search: bool,
        use_multi_query: bool = False,
        use_rerank: bool = False,
    ) -> str:
        if use_hierarchy:
            method = "hierarchical_hybrid" if hybrid_search else "hierarchical"
        elif hybrid_search:
            method = "hybrid"
        elif use_multi_query:
            method = "multi_query"
        else:
            method = "vector"
        rerank_requested = use_rerank and _env_flag(
            RAG_RERANK_ENABLED_ENV,
            default=False,
        )
        return f"{method}+rerank" if rerank_requested else method

    def _rrf_fusion(
        self,
        vector_results,
        keyword_results,
        k=60,
        *,
        source_tier_policy: str = "tie_break",
    ):
        """
        Reciprocal Rank Fusion
        Score = 1 / (k + rank)
        """
        fused_scores = {}

        for rank, (chunk, doc, distance) in enumerate(vector_results):
            doc_id = str(chunk.id)
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {
                    "score": 0,
                    "chunk": chunk,
                    "doc": doc,
                    "vector_rank": rank,
                    "similarity": 1 - distance,
                }
            fused_scores[doc_id]["score"] += 1.0 / (k + rank + 1)

        for rank, row in enumerate(keyword_results):
            doc_id = str(row[0])
            if doc_id not in fused_scores:

                class DummyChunk:
                    def __init__(
                        self,
                        c_id,
                        content,
                        metadata,
                        doc_meta_info,
                        doc_source_type,
                        parent_chunk_id,
                        chunk_level,
                        section_path,
                        heading,
                        token_count,
                        source_tier,
                    ):
                        self.id = c_id
                        self.content = content
                        self.metadata_ = metadata
                        self.parent_chunk_id = parent_chunk_id
                        self.chunk_level = chunk_level
                        self.section_path = section_path
                        self.heading = heading
                        self.token_count = token_count
                        self.source_tier = source_tier

                class DummyDoc:
                    def __init__(self, d_id, filename, meta_info, source_type):
                        self.id = d_id
                        self.filename = filename
                        self.meta_info = meta_info or {}
                        self.source_type = source_type

                chunk = DummyChunk(
                    row[0],
                    row[1],
                    row[2],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                )
                doc = DummyDoc(row[3], row[4], row[5], row[6])
                fused_scores[doc_id] = {
                    "score": 0,
                    "chunk": chunk,
                    "doc": doc,
                    "keyword_rank": rank,
                }

            fused_scores[doc_id]["score"] += 1.0 / (k + rank + 1)

        sorted_results = sorted(
            fused_scores.values(),
            key=lambda x: (
                x["score"],
                self._candidate_source_tier_priority(x, source_tier_policy),
            ),
            reverse=True,
        )
        return sorted_results

    def _rerank(
        self,
        query: str,
        candidates: list,
        top_k: int,
        *,
        source_tier_policy: str = "tie_break",
    ):
        """
        Cross-Encoder Reranking using MS-MARCO based model.
        """
        if not candidates:
            return candidates

        if not self._is_cross_encoder_rerank_enabled():
            logger.debug(
                "CrossEncoder reranker disabled; falling back to original order"
            )
            return candidates[:top_k]

        try:
            model = self._get_cross_encoder_model()
            pairs = [
                (query, self._decrypt_content(item["chunk"].content))
                for item in candidates
            ]
            scores = model.predict(pairs)

            for i, item in enumerate(candidates):
                item["rerank_score"] = float(scores[i])

            reranked = sorted(
                candidates,
                key=lambda x: (
                    x["rerank_score"],
                    self._candidate_source_tier_priority(x, source_tier_policy),
                ),
                reverse=True,
            )
            return reranked[:top_k]

        except ImportError as exc:
            logger.warning(
                "Reranker dependency unavailable; falling back to original order: %s",
                exc,
            )
            return candidates[:top_k]
        except Exception:
            logger.warning(
                "Reranking failed; falling back to original order",
                exc_info=True,
            )
            return candidates[:top_k]

    async def search_documents(
        self,
        query: str,
        knowledge_base_id: str = None,
        top_k: int = 5,
        threshold: float = 0.15,
        use_rewrite: bool = False,
        hybrid_search: bool = True,
        use_rerank: bool = True,
        use_multi_query: bool = False,
        metadata_filter: NormalizedMetadataFilter | None = None,
        hierarchy_mode: str = "auto",
        source_tier_policy: str = "tie_break",
    ) -> list[ChunkPreview]:
        """
        [Public API] Hybrid Search (Vector + Keyword) with optional Multi-Query and Reranking (비동기)
        """
        if not knowledge_base_id:
            logger.error("Missing knowledge_base_id")
            return []
        hierarchy_mode = normalize_hierarchy_mode(hierarchy_mode)
        source_tier_policy = normalize_source_tier_policy(source_tier_policy)

        if use_multi_query:
            queries = await self._generate_multi_queries(query, num_variations=3)
        elif use_rewrite:
            queries = [await self._rewrite_query(query)]
        else:
            queries = [query]

        all_candidates = {}

        try:
            kb = (
                self.db.query(KnowledgeBase)
                .filter(KnowledgeBase.id == knowledge_base_id)
                .first()
            )
            if not kb or not kb.embedding_model:
                return []

            model_info = (
                self.db.query(LLMModel)
                .filter(LLMModel.model_id_for_api_call == kb.embedding_model)
                .first()
            )
            if model_info and model_info.type != "embedding":
                return []

            has_hierarchy = self._has_valid_hierarchy(knowledge_base_id)
            if hierarchy_mode == HIERARCHY_MODE_PARENT_CHILD and not has_hierarchy:
                raise ValueError("hierarchy_unavailable")
            use_hierarchy = (
                hierarchy_mode in {"auto", HIERARCHY_MODE_PARENT_CHILD}
                and has_hierarchy
            )

            embed_client = LLMService.get_client_for_user(
                self.db,
                self.user_id,
                kb.embedding_model,
                organization_id=self.organization_id,
            )

            for i, q in enumerate(queries):
                query_vector = await embed_client.embed(q)
                if use_hierarchy:
                    parent_vector_results = self._vector_search(
                        query_vector,
                        knowledge_base_id,
                        parent_candidate_limit(top_k),
                        metadata_filter=metadata_filter,
                        chunk_levels=("parent",),
                        chunk_metadata_fallback=False,
                    )
                    if hybrid_search:
                        parent_keyword_results = self._keyword_search(
                            q,
                            knowledge_base_id,
                            parent_candidate_limit(top_k),
                            metadata_filter=metadata_filter,
                            chunk_levels=("parent",),
                            chunk_metadata_fallback=False,
                        )
                        parent_fused = self._rrf_fusion(
                            parent_vector_results,
                            parent_keyword_results,
                            source_tier_policy=source_tier_policy,
                        )
                    else:
                        parent_fused = [
                            {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                            for rank, (chunk, doc, _distance) in enumerate(
                                parent_vector_results
                            )
                        ]

                    parent_ids = [
                        item["chunk"].id
                        for item in parent_fused[: parent_candidate_limit(top_k)]
                    ]
                    vector_results = self._vector_search(
                        query_vector,
                        knowledge_base_id,
                        child_pool_limit(top_k),
                        metadata_filter=metadata_filter,
                        chunk_levels=("child",),
                        parent_ids=parent_ids,
                    )
                    if hybrid_search:
                        keyword_results = self._keyword_search(
                            q,
                            knowledge_base_id,
                            child_pool_limit(top_k),
                            metadata_filter=metadata_filter,
                            chunk_levels=("child",),
                            parent_ids=parent_ids,
                        )
                        fused = self._rrf_fusion(
                            vector_results,
                            keyword_results,
                            source_tier_policy=source_tier_policy,
                        )
                    else:
                        fused = [
                            {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                            for rank, (chunk, doc, _distance) in enumerate(
                                vector_results
                            )
                        ]

                    fallback_vector_results = self._vector_search(
                        query_vector,
                        knowledge_base_id,
                        top_k * 10,
                        metadata_filter=metadata_filter,
                        chunk_levels=(None, "flat"),
                    )
                    if hybrid_search:
                        fallback_keyword_results = self._keyword_search(
                            q,
                            knowledge_base_id,
                            top_k * 10,
                            metadata_filter=metadata_filter,
                            chunk_levels=(None, "flat"),
                        )
                        fallback_fused = self._rrf_fusion(
                            fallback_vector_results,
                            fallback_keyword_results,
                            source_tier_policy=source_tier_policy,
                        )
                    else:
                        fallback_fused = [
                            {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                            for rank, (chunk, doc, _distance) in enumerate(
                                fallback_vector_results
                            )
                        ]
                    for item in fallback_fused:
                        item["hierarchy_fallback"] = True
                    fused.extend(fallback_fused)
                else:
                    vector_results = self._vector_search(
                        query_vector,
                        knowledge_base_id,
                        top_k * 10,
                        metadata_filter=metadata_filter,
                        chunk_levels=(None, "flat", "child"),
                    )

                    if hybrid_search:
                        keyword_results = self._keyword_search(
                            q,
                            knowledge_base_id,
                            top_k * 10,
                            metadata_filter=metadata_filter,
                            chunk_levels=(None, "flat", "child"),
                        )
                        fused = self._rrf_fusion(
                            vector_results,
                            keyword_results,
                            source_tier_policy=source_tier_policy,
                        )
                    else:
                        fused = []
                        for rank, (chunk, doc, distance) in enumerate(vector_results):
                            fused.append(
                                {
                                    "score": 1.0 / (60 + rank + 1),
                                    "chunk": chunk,
                                    "doc": doc,
                                }
                            )

                for item in fused[: top_k * 10]:
                    chunk_id = str(item["chunk"].id)
                    if chunk_id not in all_candidates:
                        all_candidates[chunk_id] = item
                    else:
                        if item["score"] > all_candidates[chunk_id]["score"]:
                            all_candidates[chunk_id] = item

        except Exception as exc:
            logger.error("Search failed: error_type=%s", type(exc).__name__)
            raise

        final_list = []
        merged_candidates = sorted(
            all_candidates.values(),
            key=lambda x: (
                x["score"],
                self._candidate_source_tier_priority(x, source_tier_policy),
            ),
            reverse=True,
        )

        if hybrid_search or use_multi_query or use_hierarchy:
            if use_rerank:
                candidates_to_rerank = merged_candidates[:100]
                reranked = self._rerank(
                    query,
                    candidates_to_rerank,
                    top_k,
                    source_tier_policy=source_tier_policy,
                )

                thresholded_reranked = [
                    item
                    for item in reranked
                    if float(
                        item.get(
                            "rerank_score",
                            item.get("similarity", item.get("score", 0.0)),
                        )
                    )
                    >= threshold
                ]
                for rank, item in enumerate(thresholded_reranked, start=1):
                    chunk = item["chunk"]
                    doc = item["doc"]
                    rerank_score = item.get(
                        "rerank_score",
                        item.get("similarity", item.get("score", 0.0)),
                    )
                    rrf_score = item.get("score", 0.0)  # 원본 RRF 점수

                    meta = self._chunk_metadata(chunk, doc)
                    meta["search_method"] = self._search_method(
                        use_hierarchy=use_hierarchy,
                        hybrid_search=hybrid_search,
                        use_multi_query=use_multi_query,
                        use_rerank=True,
                    )
                    meta["rerank_score"] = float(rerank_score)
                    meta["rrf_score"] = float(rrf_score)  # RRF 점수도 저장
                    meta["score"] = float(rerank_score)
                    if item.get("hierarchy_fallback"):
                        meta["hierarchy_fallback"] = True
                    if use_multi_query:
                        meta["num_queries"] = len(queries)

                    # 암호화된 content 복호화
                    content = self._decrypt_content(chunk.content)

                    final_list.append(
                        ChunkPreview(
                            content=content,
                            chunk_id=chunk.id,
                            parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                            document_id=doc.id,
                            filename=doc.filename,
                            page_number=meta.get("page"),
                            similarity_score=float(rerank_score),
                            score=float(rerank_score),
                            rank=rank,
                            token_count=getattr(chunk, "token_count", None),
                            metadata_summary=self._metadata_summary(meta),
                            hierarchy_path=self._hierarchy_path(meta),
                            metadata=self._metadata_summary(meta),
                        )
                    )
            else:
                thresholded_candidates = [
                    item
                    for item in merged_candidates
                    if float(item["score"]) >= threshold
                ][:top_k]
                for rank, item in enumerate(thresholded_candidates, start=1):
                    chunk = item["chunk"]
                    doc = item["doc"]
                    score = item["score"]

                    meta = self._chunk_metadata(chunk, doc)
                    meta["search_method"] = self._search_method(
                        use_hierarchy=use_hierarchy,
                        hybrid_search=hybrid_search,
                        use_multi_query=use_multi_query,
                    )
                    meta["rrf_score"] = float(score)
                    meta["score"] = float(score)
                    if item.get("hierarchy_fallback"):
                        meta["hierarchy_fallback"] = True
                    if use_multi_query:
                        meta["num_queries"] = len(queries)

                    # 암호화된 content 복호화
                    content = self._decrypt_content(chunk.content)

                    final_list.append(
                        ChunkPreview(
                            content=content,
                            chunk_id=chunk.id,
                            parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                            document_id=doc.id,
                            filename=doc.filename,
                            page_number=meta.get("page"),
                            similarity_score=float(score),
                            score=float(score),
                            rank=rank,
                            token_count=getattr(chunk, "token_count", None),
                            metadata_summary=self._metadata_summary(meta),
                            hierarchy_path=self._hierarchy_path(meta),
                            metadata=self._metadata_summary(meta),
                        )
                    )
        else:
            for rank, (chunk, doc, distance) in enumerate(vector_results[:top_k], start=1):
                similarity = 1 - distance
                if similarity < threshold:
                    continue

                # 암호화된 content 복호화
                meta = self._chunk_metadata(chunk, doc)
                meta["score"] = float(similarity)
                if use_rewrite:
                    meta["original_query"] = query
                content = self._decrypt_content(chunk.content)

                final_list.append(
                    ChunkPreview(
                        content=content,
                        chunk_id=chunk.id,
                        parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                        document_id=doc.id,
                        filename=doc.filename,
                        page_number=meta.get("page"),
                        similarity_score=float(similarity),
                        score=float(similarity),
                        rank=rank,
                        token_count=getattr(chunk, "token_count", None),
                        metadata_summary=self._metadata_summary(meta),
                        hierarchy_path=self._hierarchy_path(meta),
                        metadata=self._metadata_summary(meta),
                    )
                )

        return final_list

    def _chunk_metadata(self, chunk, doc=None) -> dict:
        return build_chunk_metadata(chunk, doc)

    def _metadata_summary(self, metadata: dict | None) -> dict:
        return build_metadata_summary(metadata)

    def _hierarchy_path(self, metadata: dict | None) -> list[str] | None:
        return build_hierarchy_path(metadata)

    @staticmethod
    def _candidate_source_tier_priority(candidate: dict, policy: str) -> int:
        if not source_tier_tie_break_enabled(policy):
            return 0
        return retrieval_candidate_source_tier_priority(candidate)

    def _decrypt_content(self, content: str) -> str:
        """
        암호화된 content를 복호화합니다.
        전체 암호화 또는 부분 암호화 모두 처리합니다.
        """
        if not content:
            return content

        # 전체 암호화 패턴
        if content.startswith("gAAAAAB"):
            try:
                return encryption_manager.decrypt(content)
            except Exception as e:
                logger.warning(f"Failed to decrypt full content: {e}")
                return "[ENCRYPTED CONTENT]"

        # 부분 암호화 패턴 (key: value 형식)
        encrypted_pattern = r"([\w_]+):\s*(gAAAAAB[A-Za-z0-9_-]+={0,2})"

        def decrypt_match(match):
            key = match.group(1)
            encrypted_value = match.group(2)
            try:
                decrypted = encryption_manager.decrypt(encrypted_value)
                return f"{key}: {decrypted}"
            except Exception as e:
                logger.warning(f"Failed to decrypt {key}: {e}")
                return f"{key}: [ENCRYPTED]"

        return re.sub(encrypted_pattern, decrypt_match, content)

    def search_documents_sync(
        self,
        query: str,
        knowledge_base_id: str = None,
        top_k: int = 5,
        threshold: float = 0.15,
        hybrid_search: bool = True,
        use_rerank: bool = True,
        metadata_filter: NormalizedMetadataFilter | None = None,
        hierarchy_mode: str = "auto",
        source_tier_policy: str = "tie_break",
        query_vector: list[float] | None = None,
        embedding_model_binding: EmbeddingModelBinding | None = None,
    ) -> list[ChunkPreview]:
        """
        [GEVENT] 동기 검색 API - gevent pool 호환성을 위해.

        주의: use_rewrite, use_multi_query 옵션은 LLM 호출이 필요하므로 생략.
        기본적인 하이브리드 검색 + 리랭킹만 지원합니다.
        """
        if not knowledge_base_id:
            logger.error("Missing knowledge_base_id")
            return []
        if self.organization_id is None:
            logger.error("Missing organization_id for synchronous retrieval")
            return []
        hierarchy_mode = normalize_hierarchy_mode(hierarchy_mode)
        source_tier_policy = normalize_source_tier_policy(source_tier_policy)

        all_candidates = {}

        try:
            kb = (
                self.db.query(KnowledgeBase)
                .filter(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.organization_id == self.organization_id,
                )
                .first()
            )
            if not kb or not kb.embedding_model:
                return []

            if embedding_model_binding is not None:
                if (
                    query_vector is None
                    or embedding_model_binding.model_identifier
                    != kb.embedding_model
                ):
                    return []
            else:
                model_info = (
                    self.db.query(LLMModel)
                    .filter(LLMModel.model_id_for_api_call == kb.embedding_model)
                    .first()
                )
                if model_info and model_info.type != "embedding":
                    return []

            has_hierarchy = self._has_valid_hierarchy(knowledge_base_id)
            if hierarchy_mode == HIERARCHY_MODE_PARENT_CHILD and not has_hierarchy:
                raise ValueError("hierarchy_unavailable")
            use_hierarchy = (
                hierarchy_mode in {"auto", HIERARCHY_MODE_PARENT_CHILD}
                and has_hierarchy
            )

            if query_vector is None:
                embed_client = LLMService.get_client_for_user(
                    self.db,
                    self.user_id,
                    kb.embedding_model,
                    organization_id=self.organization_id,
                )

                # [GEVENT] embed_sync 사용
                query_vector = embed_client.embed_sync(query)
            if use_hierarchy:
                parent_vector_results = self._vector_search(
                    query_vector,
                    knowledge_base_id,
                    parent_candidate_limit(top_k),
                    metadata_filter=metadata_filter,
                    chunk_levels=("parent",),
                    chunk_metadata_fallback=False,
                )
                if hybrid_search:
                    parent_keyword_results = self._keyword_search(
                        query,
                        knowledge_base_id,
                        parent_candidate_limit(top_k),
                        metadata_filter=metadata_filter,
                        chunk_levels=("parent",),
                        chunk_metadata_fallback=False,
                    )
                    parent_fused = self._rrf_fusion(
                        parent_vector_results,
                        parent_keyword_results,
                        source_tier_policy=source_tier_policy,
                    )
                else:
                    parent_fused = [
                        {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                        for rank, (chunk, doc, _distance) in enumerate(
                            parent_vector_results
                        )
                    ]
                parent_ids = [
                    item["chunk"].id
                    for item in parent_fused[: parent_candidate_limit(top_k)]
                ]
                vector_results = self._vector_search(
                    query_vector,
                    knowledge_base_id,
                    child_pool_limit(top_k),
                    metadata_filter=metadata_filter,
                    chunk_levels=("child",),
                    parent_ids=parent_ids,
                )
                if hybrid_search:
                    keyword_results = self._keyword_search(
                        query,
                        knowledge_base_id,
                        child_pool_limit(top_k),
                        metadata_filter=metadata_filter,
                        chunk_levels=("child",),
                        parent_ids=parent_ids,
                    )
                    fused = self._rrf_fusion(
                        vector_results,
                        keyword_results,
                        source_tier_policy=source_tier_policy,
                    )
                else:
                    fused = [
                        {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                        for rank, (chunk, doc, _distance) in enumerate(vector_results)
                    ]
                fallback_vector_results = self._vector_search(
                    query_vector,
                    knowledge_base_id,
                    top_k * 10,
                    metadata_filter=metadata_filter,
                    chunk_levels=(None, "flat"),
                )
                fallback_keyword_results = (
                    self._keyword_search(
                        query,
                        knowledge_base_id,
                        top_k * 10,
                        metadata_filter=metadata_filter,
                        chunk_levels=(None, "flat"),
                    )
                    if hybrid_search
                    else []
                )
                fallback_fused = (
                    self._rrf_fusion(
                        fallback_vector_results,
                        fallback_keyword_results,
                        source_tier_policy=source_tier_policy,
                    )
                    if hybrid_search
                    else [
                        {"score": 1.0 / (60 + rank + 1), "chunk": chunk, "doc": doc}
                        for rank, (chunk, doc, _distance) in enumerate(
                            fallback_vector_results
                        )
                    ]
                )
                for item in fallback_fused:
                    item["hierarchy_fallback"] = True
                fused.extend(fallback_fused)
            else:
                vector_results = self._vector_search(
                    query_vector,
                    knowledge_base_id,
                    top_k * 10,
                    metadata_filter=metadata_filter,
                    chunk_levels=(None, "flat", "child"),
                )

                if hybrid_search:
                    keyword_results = self._keyword_search(
                        query,
                        knowledge_base_id,
                        top_k * 10,
                        metadata_filter=metadata_filter,
                        chunk_levels=(None, "flat", "child"),
                    )
                    fused = self._rrf_fusion(
                        vector_results,
                        keyword_results,
                        source_tier_policy=source_tier_policy,
                    )
                else:
                    fused = []
                    for rank, (chunk, doc, distance) in enumerate(vector_results):
                        fused.append(
                            {
                                "score": 1.0 / (60 + rank + 1),
                                "chunk": chunk,
                                "doc": doc,
                            }
                        )

            for item in fused[: top_k * 10]:
                chunk_id = str(item["chunk"].id)
                if chunk_id not in all_candidates:
                    all_candidates[chunk_id] = item
                else:
                    if item["score"] > all_candidates[chunk_id]["score"]:
                        all_candidates[chunk_id] = item

        except Exception as exc:
            logger.error("Search failed: error_type=%s", type(exc).__name__)
            raise

        final_list = []
        merged_candidates = sorted(
            all_candidates.values(),
            key=lambda x: (
                x["score"],
                self._candidate_source_tier_priority(x, source_tier_policy),
            ),
            reverse=True,
        )

        if hybrid_search or use_hierarchy:
            if use_rerank:
                candidates_to_rerank = merged_candidates[:100]
                reranked = self._rerank(
                    query,
                    candidates_to_rerank,
                    top_k,
                    source_tier_policy=source_tier_policy,
                )

                thresholded_reranked = [
                    item
                    for item in reranked
                    if float(
                        item.get(
                            "rerank_score",
                            item.get("similarity", item.get("score", 0.0)),
                        )
                    )
                    >= threshold
                ]
                for rank, item in enumerate(thresholded_reranked, start=1):
                    chunk = item["chunk"]
                    doc = item["doc"]
                    rerank_score = item.get(
                        "rerank_score",
                        item.get("similarity", item.get("score", 0.0)),
                    )
                    rrf_score = item.get("score", 0.0)

                    meta = self._chunk_metadata(chunk, doc)
                    meta["search_method"] = self._search_method(
                        use_hierarchy=use_hierarchy,
                        hybrid_search=hybrid_search,
                        use_rerank=True,
                    )
                    meta["rerank_score"] = float(rerank_score)
                    meta["rrf_score"] = float(rrf_score)
                    meta["score"] = float(rerank_score)
                    if item.get("hierarchy_fallback"):
                        meta["hierarchy_fallback"] = True

                    content = self._decrypt_content(chunk.content)

                    final_list.append(
                        ChunkPreview(
                            content=content,
                            chunk_id=chunk.id,
                            parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                            document_id=doc.id,
                            filename=doc.filename,
                            page_number=meta.get("page"),
                            similarity_score=float(rerank_score),
                            score=float(rerank_score),
                            rank=rank,
                            token_count=getattr(chunk, "token_count", None),
                            metadata_summary=self._metadata_summary(meta),
                            hierarchy_path=self._hierarchy_path(meta),
                            metadata=self._metadata_summary(meta),
                        )
                    )
            else:
                thresholded_candidates = [
                    item
                    for item in merged_candidates
                    if float(item["score"]) >= threshold
                ][:top_k]
                for rank, item in enumerate(thresholded_candidates, start=1):
                    chunk = item["chunk"]
                    doc = item["doc"]
                    score = item["score"]

                    meta = self._chunk_metadata(chunk, doc)
                    meta["search_method"] = self._search_method(
                        use_hierarchy=use_hierarchy,
                        hybrid_search=hybrid_search,
                    )
                    meta["rrf_score"] = float(score)
                    meta["score"] = float(score)
                    if item.get("hierarchy_fallback"):
                        meta["hierarchy_fallback"] = True

                    content = self._decrypt_content(chunk.content)

                    final_list.append(
                        ChunkPreview(
                            content=content,
                            chunk_id=chunk.id,
                            parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                            document_id=doc.id,
                            filename=doc.filename,
                            page_number=meta.get("page"),
                            similarity_score=float(score),
                            score=float(score),
                            rank=rank,
                            token_count=getattr(chunk, "token_count", None),
                            metadata_summary=self._metadata_summary(meta),
                            hierarchy_path=self._hierarchy_path(meta),
                            metadata=self._metadata_summary(meta),
                        )
                    )
        else:
            for rank, (chunk, doc, distance) in enumerate(vector_results[:top_k], start=1):
                similarity = 1 - distance
                if similarity < threshold:
                    continue

                meta = self._chunk_metadata(chunk, doc)
                meta["score"] = float(similarity)
                content = self._decrypt_content(chunk.content)

                final_list.append(
                    ChunkPreview(
                        content=content,
                        chunk_id=chunk.id,
                        parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                        document_id=doc.id,
                        filename=doc.filename,
                        page_number=meta.get("page"),
                        similarity_score=float(similarity),
                        score=float(similarity),
                        rank=rank,
                        token_count=getattr(chunk, "token_count", None),
                        metadata_summary=self._metadata_summary(meta),
                        hierarchy_path=self._hierarchy_path(meta),
                        metadata=self._metadata_summary(meta),
                    )
                )

        return final_list

    async def retrieve_context(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        metadata_filter: NormalizedMetadataFilter | None = None,
        hierarchy_mode: str = "auto",
    ) -> str:
        """
        [Public API] 검색된 문서들의 내용을 하나의 문자열로 합쳐서 반환합니다. (비동기)
        """
        chunks = await self.search_documents(
            query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            metadata_filter=metadata_filter,
            hierarchy_mode=hierarchy_mode,
        )
        if not chunks:
            return ""

        return "\n\n".join([c.content for c in chunks])

    async def generate_answer(
        self,
        query: str,
        knowledge_base_id: str,
        model_id: str = "gpt-4o",
        top_k: int = 5,
        metadata_filter: NormalizedMetadataFilter | None = None,
        hierarchy_mode: str = "auto",
    ) -> RAGResponse:
        """
        [Public API] 검색 + 답변 생성 (Chat Interface용, 비동기)
        """
        relevant_chunks = await self.search_documents(
            query,
            knowledge_base_id,
            top_k=top_k,
            metadata_filter=metadata_filter,
            hierarchy_mode=hierarchy_mode,
        )

        if not relevant_chunks:
            return RAGResponse(
                answer="해당 질문에 답변할 수 있는 문서를 찾지 못했습니다.",
                references=[],
            )

        context_text = "\n\n".join([c.content for c in relevant_chunks])

        if self.llm_client and self.llm_client.model_id == model_id:
            pass
        else:
            try:
                self.llm_client = LLMService.get_client_for_user(
                    self.db,
                    self.user_id,
                    model_id,
                    organization_id=self.organization_id,
                )
            except Exception:
                self.llm_client = None

        if not self.llm_client:
            return RAGResponse(
                answer=f"⚠️ 답변 생성을 위한 모델({model_id})을 찾을 수 없습니다. (Credential 등록 필요)",
                references=relevant_chunks,
            )

        system_prompt = (
            "You are a helpful assistant. Use the following context to answer the user's question.\n"
            "If the answer is not in the context, say you don't know.\n\n"
            f"Context:\n{context_text}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

        try:
            result = await self.llm_client.invoke(messages)
            answer = result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"LLM Generation Failed: {e}")
            answer = f"오류가 발생하여 답변을 생성할 수 없습니다. ({str(e)})"

        return RAGResponse(answer=answer, references=relevant_chunks)
