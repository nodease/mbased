# RAG / 지식베이스

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 책임

지식베이스 기능은 파일, API, 외부 DB 데이터를 문서로 등록하고, 텍스트 추출/청킹/임베딩/벡터 저장 후 LLM 노드나 검색 테스트에서 활용하는 기능이다.

주요 파일:

- `apps/gateway/api/v1/endpoints/knowledge.py`
- `apps/gateway/api/v1/endpoints/rag.py`
- `apps/gateway/services/ingestion/service.py`
- `apps/gateway/services/ingestion/factory.py`
- `apps/gateway/services/ingestion/processors/*`
- `apps/shared/services/ingestion/*`
- `apps/shared/db/models/knowledge.py`
- `apps/workflow_engine/services/retrieval.py`
- `apps/workflow_engine/services/sync_service.py`

## 데이터 소스 타입

SourceType:

- `FILE`
- `API`
- `DB`

## 지식베이스 생성/관리

모델: `knowledge_bases`

설정:

- name
- description
- embedding_model
- top_k
- similarity_threshold
- user_id

문서와 cascade 관계를 가진다.

## 문서 등록 상태

모델: `documents`

상태값은 문자열로 관리된다. 코드 주석과 흐름에서 확인되는 상태:

- `pending`
- `indexing`
- `completed`
- `failed`
- `waiting_for_approval`

## 파일 업로드 흐름

1. Client가 `/rag/upload/presigned-url` 호출
2. `STORAGE_TYPE=LOCAL`이면 backend proxy 사용 지시
3. `STORAGE_TYPE=CLOUD`이면 S3 presigned PUT URL 발급
4. Client가 파일을 직접 S3에 올리거나 `/rag/upload`에 FormData로 보냄
5. Gateway가 document 레코드를 `pending`으로 생성
6. 처리 API를 통해 indexing 시작

Storage:

- `LocalStorageService`: `/app/uploads` 아래 파일 저장
- `S3StorageService`: `uploads/{user_id}/{uuid}_{filename}` key 생성

## 파일 처리

파일: `apps/gateway/services/ingestion/processors/file_processor.py`

지원 확장자:

- `.pdf`
- `.docx`
- `.txt`
- `.md`
- `.csv`
- `.xlsx`
- `.xls`

PDF 전략:

- `general`
- `llamaparse`

LlamaParse key 우선순위:

1. `LLAMA_CLOUD_API_KEY` 환경변수
2. DB의 `llamaparse` provider credential

## API 처리

파일: `apps/gateway/services/ingestion/processors/api_processor.py`

동작:

- URL, method, headers, body로 HTTP 요청
- 응답 JSON이면 `JsonParser`
- JSON이 아니면 response text를 문서 텍스트로 사용
- timeout 30초

## DB 처리

파일: `apps/shared/services/ingestion/processors/db_processor.py`

동작:

1. `connection_id`로 `connections` 조회
2. 암호화된 DB/SSH credential 복호화
3. DB type에 맞는 connector 생성
4. selections 기반 SQL 생성
5. 단일 테이블 또는 2개 테이블 JOIN 처리
6. Row를 자연어 텍스트로 변환
7. Adaptive chunker로 청킹

현재 명확히 구현된 connector:

- PostgreSQL

JOIN 정책:

- 2개 테이블 선택 시 `join_config.enabled=true`가 필요하다.
- FK 관계가 없으면 에러 처리한다.

## 문서 처리 Orchestrator

파일: `apps/gateway/services/ingestion/service.py`

주요 단계:

1. Redis distributed lock 획득: `doc_processing:{document_id}`
2. 문서 상태 `indexing`
3. source type별 processor 선택
4. 원문 block 추출
5. content hash 계산
6. 동일 hash와 동일 embedding model이면 재처리 생략
7. source type별 청킹
8. selection mode로 chunk filtering
9. vector DB 저장
10. 상태 `completed`
11. Redis progress 100 기록 후 TTL

실패 시:

- DB rollback
- 상태 `failed`
- error message 저장
- progress key 만료

## 청킹 옵션

문서별 설정:

- `chunk_size`
- `chunk_overlap`
- `segment_identifier`
- `remove_urls_emails`
- `remove_whitespace`
- `selection_mode`
- `chunk_range`
- `keyword_filter`
- `enable_auto_chunking`

selection mode:

- `all`
- `range`
- `keyword`

DB source는 override chunk size 8000을 사용하는 흐름이 있다.

## 진행률

Redis key:

- `knowledge_progress:{document_id}`

정책:

- 진행 중: TTL 600초
- 완료/실패: 짧은 TTL 30초
- 이전 값보다 작은 진행률은 덮어쓰지 않음

## 검색

API:

- `/rag/search-test/chat`
- `/rag/search-test/pure`

LLM Node:

- `knowledgeBases` 설정이 있으면 user prompt를 query로 사용해 검색
- 검색 결과는 prompt injection guard를 통해 비신뢰 context로 감싼다.

## 외부 DB 연결

API:

- `/connectors/test`
- `/connectors`
- `/connectors/{connection_id}`
- `/connectors/{connection_id}/schema`

저장:

- `connections` 테이블
- DB password와 SSH credential은 Fernet 기반 `ENCRYPTION_KEY`로 암호화

## 비용/승인 흐름

PDF LlamaParse 처리에는 분석/승인 흐름이 있다.

API:

- `POST /rag/document/{document_id}/analyze`
- `POST /rag/document/{document_id}/confirm?strategy=...`

`analyze_document`는 페이지 수 기반으로 LlamaCloud 비용 추정을 만든다. 문서에는 코드의 현재 정책만 기록하며 실제 외부 가격 최신성은 검증하지 않았다.

## 주의/검증 필요

- `FileProcessor._extract_key`는 `encrypted_config`를 바로 JSON parse한다. 다른 서비스에서 실제로 암호화된 JSON을 저장한다면 복호화 경로와 일치 여부를 확인해야 한다.
- `IngestionFactory` 내부 import에 `services.ingestion...` 형태가 섞여 있어 PYTHONPATH/패키지 실행 방식에 민감할 수 있다.
- DB ingestion에서 SQL column/table name 조합은 connector/query utility 검증 범위에 따라 SQL injection 방어 수준 확인이 필요하다.
