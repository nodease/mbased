# ===================================================
# Gateway API 서버 Dockerfile
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
COPY apps/gateway /build/apps/gateway
COPY apps/workflow_engine /build/apps/workflow_engine
COPY docker/model-routing-cpu-constraints.txt /build/model-routing-cpu-constraints.txt

# pyproject.toml 기반으로 설치
RUN pip install --no-cache-dir --prefix=/install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --constraint /build/model-routing-cpu-constraints.txt \
    /build/apps/shared \
    /build/apps/gateway \
    /build/apps/workflow_engine

# Runtime tokenizer/corpus resources are downloaded during the image build so
# proxy-only workloads never attempt network access on their first request.
RUN mkdir -p /tokenizer-cache /nltk-data && \
    TIKTOKEN_CACHE_DIR=/tokenizer-cache \
    NLTK_DATA=/nltk-data \
    PYTHONPATH=/install/lib/python3.11/site-packages \
    python -c "import nltk, tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base'); resources=(('punkt', 'tokenizers/punkt'), ('punkt_tab', 'tokenizers/punkt_tab'), ('stopwords', 'corpora/stopwords')); [nltk.download(name, download_dir='/nltk-data', quiet=True) for name, _ in resources]; [nltk.data.find(path) for _, path in resources]"


# --- 2단계: 실행(Final) 스테이지 ---
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV HF_HUB_DISABLE_XET=1
ENV TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache
ENV NLTK_DATA=/opt/nltk-data

# 실행 시 필요한 최소 라이브러리 설치
RUN apt-get update && apt-get install -y \
    ca-certificates \
    libpq5 \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 빌더 스테이지에서 설치된 패키지 복사
COPY --from=builder /install /usr/local
COPY --from=builder /tokenizer-cache /opt/tokenizer-cache
COPY --from=builder /nltk-data /opt/nltk-data

# 앱 코드 복사 (모노레포 구조 유지)
COPY apps/shared /app/apps/shared
COPY apps/gateway /app/apps/gateway
COPY apps/memory /app/apps/memory
COPY apps/workflow_engine /app/apps/workflow_engine
COPY scripts/seed_demo.py /app/scripts/seed_demo.py
COPY scripts/check_schedule_dispatch_rollback.py /app/scripts/check_schedule_dispatch_rollback.py
COPY scripts/rotate_llm_credentials.py /app/scripts/rotate_llm_credentials.py
COPY scripts/reconcile_provider_usage.py /app/scripts/reconcile_provider_usage.py

# Entrypoint 스크립트 복사
COPY docker/gateway/docker-entrypoint.sh /docker-entrypoint.sh

# 보안: Root가 아닌 일반 유저로 실행
RUN useradd -m appuser && chown -R appuser:appuser /app

# Entrypoint 스크립트 실행 권한 설정
RUN chmod +x /docker-entrypoint.sh

# app/uploads 생성
RUN mkdir -p /app/uploads && \
    chown -R appuser:appuser /app/uploads

USER appuser

# 헬스 체크 설정
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

EXPOSE 8000

# Entrypoint 스크립트 실행
ENTRYPOINT ["/docker-entrypoint.sh"]
