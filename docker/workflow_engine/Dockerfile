# ===================================================
# Workflow Engine Celery Worker Dockerfile
# ===================================================

# --- 1단계: 빌더(Builder) 스테이지 ---
FROM python:3.11-slim AS builder

WORKDIR /build

# 컴파일에 필요한 도구 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 공통 패키지 복사 및 설치
COPY apps/shared /build/apps/shared
COPY apps/workflow_engine /build/apps/workflow_engine
COPY docker/model-routing-cpu-constraints.txt /build/model-routing-cpu-constraints.txt

ARG INSTALL_RAG_RERANKER=false
ARG RAG_CROSS_ENCODER_MODEL_ID=cross-encoder/ms-marco-MiniLM-L-12-v2
ARG PREFETCH_MODEL_ROUTING_CLASSIFIER=true
ARG MODEL_ROUTING_DIFFICULTY_MODEL_ID=intfloat/multilingual-e5-base
ARG MODEL_ROUTING_DIFFICULTY_MODEL_REVISION=d13f1b27baf31030b7fd040960d60d909913633f

# [FIX] 의존성만 설치 (코드는 /app/에서 직접 사용, site-packages 중복 방지)
# 1. pyproject.toml 기반으로 전체 설치 (의존성 해결을 위해)
# 2. 설치 후 site-packages에서 workflow/services 코드 삭제 (코드 중복 방지)
RUN if [ "$INSTALL_RAG_RERANKER" = "true" ]; then \
        pip install --no-cache-dir --prefix=/install "sentence-transformers>=3.0.0,<4.0.0"; \
    fi && \
    pip install --no-cache-dir --prefix=/install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --constraint /build/model-routing-cpu-constraints.txt \
        /build/apps/shared /build/apps/workflow_engine && \
    rm -rf /install/lib/python3.11/site-packages/workflow && \
    rm -rf /install/lib/python3.11/site-packages/services && \
    rm -rf /install/lib/python3.11/site-packages/utils && \
    rm -rf /install/lib/python3.11/site-packages/tests && \
    rm -rf /install/lib/python3.11/site-packages/moduly_workflow_engine*.dist-info

# Runtime tokenizer resources are downloaded during the image build so the
# proxy-only worker never attempts network access on its first task.
RUN mkdir -p /tokenizer-cache /nltk-data && \
    TIKTOKEN_CACHE_DIR=/tokenizer-cache \
    NLTK_DATA=/nltk-data \
    PYTHONPATH=/install/lib/python3.11/site-packages \
    python -c "import nltk, tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base'); resources=(('punkt', 'tokenizers/punkt'), ('punkt_tab', 'tokenizers/punkt_tab'), ('stopwords', 'corpora/stopwords')); [nltk.download(name, download_dir='/nltk-data', quiet=True) for name, _ in resources]; [nltk.data.find(path) for _, path in resources]"

# 요청 난이도 분류기는 첫 정책 생성과 첫 자동 라우팅에서 사용된다. 실행 중에
# checkpoint를 내려받으면 worker가 오래 멈추거나 네트워크 상태에 따라 기본 모델로
# fallback할 수 있으므로, 기본 이미지에는 multilingual E5 가중치를 함께 넣는다.
# 로컬의 빠른 의존성 전용 빌드가 필요할 때만 build arg로 건너뛸 수 있다.
RUN mkdir -p /model-routing-hf-cache && \
    if [ "$PREFETCH_MODEL_ROUTING_CLASSIFIER" = "true" ]; then \
        PYTHONPATH=/install/lib/python3.11/site-packages \
        HF_HOME=/model-routing-hf-cache \
        HF_HUB_DISABLE_XET=1 \
        python -c "from transformers import AutoModel, AutoTokenizer; model_id='${MODEL_ROUTING_DIFFICULTY_MODEL_ID}'; revision='${MODEL_ROUTING_DIFFICULTY_MODEL_REVISION}'; AutoTokenizer.from_pretrained(model_id, revision=revision, use_fast=False); AutoModel.from_pretrained(model_id, revision=revision); AutoTokenizer.from_pretrained(model_id, revision=revision, use_fast=False, local_files_only=True); AutoModel.from_pretrained(model_id, revision=revision, local_files_only=True)"; \
    fi

# Opt-in reranking must not download model weights after proxy-only startup.
# Prefetch the exact runtime model into the cache copied to the final image.
RUN if [ "$INSTALL_RAG_RERANKER" = "true" ]; then \
        PYTHONPATH=/install/lib/python3.11/site-packages \
        HF_HOME=/model-routing-hf-cache \
        HF_HUB_DISABLE_XET=1 \
        python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RAG_CROSS_ENCODER_MODEL_ID}', device='cpu')"; \
    fi


# --- 2단계: 실행(Final) 스테이지 ---
FROM python:3.11-slim

ARG MODEL_ROUTING_DIFFICULTY_MODEL_ID=intfloat/multilingual-e5-base
ARG MODEL_ROUTING_DIFFICULTY_MODEL_REVISION=d13f1b27baf31030b7fd040960d60d909913633f
ARG RAG_CROSS_ENCODER_MODEL_ID=cross-encoder/ms-marco-MiniLM-L-12-v2

WORKDIR /app

ENV PYTHONPATH=/app
ENV MODEL_ROUTING_EMBEDDING_MODEL_ID=${MODEL_ROUTING_DIFFICULTY_MODEL_ID}
ENV MODEL_ROUTING_EMBEDDING_MODEL_REVISION=${MODEL_ROUTING_DIFFICULTY_MODEL_REVISION}
ENV RAG_CROSS_ENCODER_MODEL=${RAG_CROSS_ENCODER_MODEL_ID}
ENV PYTHONUNBUFFERED=1
ENV HF_HUB_DISABLE_XET=1
ENV HF_HOME=/opt/huggingface
ENV TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache
ENV NLTK_DATA=/opt/nltk-data

# 실행 시 필요한 최소 라이브러리 설치
RUN apt-get update && apt-get install -y \
    libpq5 \
    postgresql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

# 빌더 스테이지에서 설치된 패키지 복사
COPY --from=builder /install /usr/local
COPY --from=builder /tokenizer-cache /opt/tokenizer-cache
COPY --from=builder /nltk-data /opt/nltk-data
COPY --from=builder /model-routing-hf-cache /opt/huggingface

# 앱 코드 복사 (모노레포 구조 유지)
COPY apps/shared /app/apps/shared
COPY apps/workflow_engine /app/apps/workflow_engine

# Entrypoint 스크립트 복사
COPY docker/workflow_engine/docker-entrypoint.sh /docker-entrypoint.sh

# 보안: Root가 아닌 일반 유저로 실행
RUN useradd -m appuser && chown -R appuser:appuser /app /opt/huggingface

# Entrypoint 스크립트 실행 권한 설정
RUN chmod +x /docker-entrypoint.sh

USER appuser

# Entrypoint 스크립트 실행
ENTRYPOINT ["/docker-entrypoint.sh"]
