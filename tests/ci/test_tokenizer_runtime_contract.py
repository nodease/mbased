import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DOCKERFILE = ROOT / "docker" / "gateway" / "Dockerfile"
WORKFLOW_DOCKERFILE = ROOT / "docker" / "workflow_engine" / "Dockerfile"
INGESTION_SERVICE = ROOT / "apps" / "gateway" / "services" / "ingestion" / "service.py"
MODEL_ROUTING_CLASSIFIER = (
    ROOT
    / "apps"
    / "workflow_engine"
    / "services"
    / "model_routing_local_classifier.py"
)


def test_gateway_image_preloads_runtime_tokenizers_and_nltk_resources() -> None:
    dockerfile = GATEWAY_DOCKERFILE.read_text(encoding="utf-8")

    assert "TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache" in dockerfile
    assert "NLTK_DATA=/opt/nltk-data" in dockerfile
    assert "get_encoding('cl100k_base')" in dockerfile
    assert "get_encoding('o200k_base')" in dockerfile
    assert "nltk.download(name" in dockerfile
    for resource in ("'punkt'", "'punkt_tab'", "'stopwords'"):
        assert resource in dockerfile
    assert "COPY --from=builder /tokenizer-cache /opt/tokenizer-cache" in dockerfile
    assert "COPY --from=builder /nltk-data /opt/nltk-data" in dockerfile


def test_workflow_image_preloads_runtime_tokenizers() -> None:
    dockerfile = WORKFLOW_DOCKERFILE.read_text(encoding="utf-8")

    assert "TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache" in dockerfile
    assert "NLTK_DATA=/opt/nltk-data" in dockerfile
    assert "get_encoding('cl100k_base')" in dockerfile
    assert "get_encoding('o200k_base')" in dockerfile
    assert "nltk.download(name" in dockerfile
    for resource in ("'punkt'", "'punkt_tab'", "'stopwords'"):
        assert resource in dockerfile
    assert "COPY --from=builder /tokenizer-cache /opt/tokenizer-cache" in dockerfile
    assert "COPY --from=builder /nltk-data /opt/nltk-data" in dockerfile


def test_ingestion_never_downloads_nltk_resources_at_runtime() -> None:
    source = INGESTION_SERVICE.read_text(encoding="utf-8")

    assert "nltk.download" not in source
    for resource in (
        '"tokenizers/punkt"',
        '"tokenizers/punkt_tab"',
        '"corpora/stopwords"',
    ):
        assert resource in source


def test_workflow_image_prefetches_exact_runtime_model_routing_snapshot() -> None:
    dockerfile = WORKFLOW_DOCKERFILE.read_text(encoding="utf-8")
    source = MODEL_ROUTING_CLASSIFIER.read_text(encoding="utf-8")

    model_id_match = re.search(
        r'^DEFAULT_MULTILINGUAL_E5_MODEL_ID = "([^"]+)"$',
        source,
        flags=re.MULTILINE,
    )
    revision_match = re.search(
        r'^DEFAULT_MULTILINGUAL_E5_REVISION = "([^"]+)"$',
        source,
        flags=re.MULTILINE,
    )
    assert model_id_match is not None
    assert revision_match is not None
    model_id = model_id_match.group(1)
    revision = revision_match.group(1)

    assert f"ARG MODEL_ROUTING_DIFFICULTY_MODEL_ID={model_id}" in dockerfile
    assert f"ARG MODEL_ROUTING_DIFFICULTY_MODEL_REVISION={revision}" in dockerfile
    assert "revision=revision, use_fast=False" in dockerfile
    assert "AutoModel.from_pretrained(model_id, revision=revision)" in dockerfile
    assert (
        "AutoTokenizer.from_pretrained("
        "model_id, revision=revision, use_fast=False, local_files_only=True"
        ")"
    ) in dockerfile
    assert (
        "AutoModel.from_pretrained("
        "model_id, revision=revision, local_files_only=True"
        ")"
    ) in dockerfile
    assert (
        "MODEL_ROUTING_EMBEDDING_MODEL_ID="
        "${MODEL_ROUTING_DIFFICULTY_MODEL_ID}"
    ) in dockerfile
    assert (
        "MODEL_ROUTING_EMBEDDING_MODEL_REVISION="
        "${MODEL_ROUTING_DIFFICULTY_MODEL_REVISION}"
    ) in dockerfile


def test_workflow_image_prefetches_opt_in_rag_reranker() -> None:
    dockerfile = WORKFLOW_DOCKERFILE.read_text(encoding="utf-8")
    final_stage = dockerfile.split("# --- 2단계: 실행(Final) 스테이지 ---", maxsplit=1)[1]

    assert (
        "ARG RAG_CROSS_ENCODER_MODEL_ID="
        "cross-encoder/ms-marco-MiniLM-L-12-v2"
    ) in dockerfile
    assert 'if [ "$INSTALL_RAG_RERANKER" = "true" ]' in dockerfile
    assert "from sentence_transformers import CrossEncoder" in dockerfile
    assert "CrossEncoder('${RAG_CROSS_ENCODER_MODEL_ID}', device='cpu')" in dockerfile
    assert "HF_HOME=/model-routing-hf-cache" in dockerfile
    assert "COPY --from=builder /model-routing-hf-cache /opt/huggingface" in dockerfile
    assert "ARG RAG_CROSS_ENCODER_MODEL_ID=" in final_stage
    assert (
        "ENV RAG_CROSS_ENCODER_MODEL=${RAG_CROSS_ENCODER_MODEL_ID}" in final_stage
    )
