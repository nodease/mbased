# ADR-0007: MVP 2 classification metadata 저장 방식

Status: Accepted
Date: 2026-06-29 01:24 KST
Original: ADR-202606290124-mvp2-classification-metadata-storage
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-0005-data-model-document-structure](ADR-0005-data-model-document-structure.md), [ADR-0004-audit-log-rag-trace-storage](ADR-0004-audit-log-rag-trace-storage.md)

## 배경

MVP 2 요구사항은 data classification을 지원하고, knowledge base/document classification 변경과 조회를 요구한다. 반면 현재 물리 데이터 모델은 기존 구조 보존 원칙에 따라 `knowledge_bases.classification`, `documents.classification` column을 추가하지 않도록 정리되어 있다.

현재 코드 기준으로 `documents`에는 `meta_info` JSONB column이 있지만, `knowledge_bases`에는 classification 전용 column이나 범용 metadata column이 없다. 따라서 MVP 2에서 classification을 정식 DB column으로 추가하면 물리 데이터 모델 문서의 schema extension boundary와 충돌한다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| DB column 추가 | `knowledge_bases.classification`, `documents.classification`을 추가한다. | 조회와 필터링은 단순하지만 현재 물리 데이터 모델의 기존 구조 보존 원칙을 깨고 migration 범위가 커진다. |
| Document metadata convention | `documents.meta_info.classification`에 저장하고 KB classification은 문서 metadata에서 파생한다. | schema 변경 없이 시작할 수 있지만 KB-level 값을 직접 저장하지 못한다. |
| 별도 policy table | classification과 policy decision을 별도 governance table로 관리한다. | 확장성은 좋지만 MVP 2 범위보다 크다. |

## 결정

MVP 2에서는 Document metadata convention을 채택한다.

1. `knowledge_bases.classification` column을 추가하지 않는다.
2. `documents.classification` column을 추가하지 않는다.
3. document classification은 `documents.meta_info.classification`에 application-level convention으로 저장한다.
4. 허용값은 MVP 2 요구사항의 `public`, `internal`, `confidential`, `pii`를 사용한다.
5. knowledge base classification은 저장 column을 만들지 않고 문서들의 classification을 기준으로 API/UI에서 파생하거나 표시한다.
6. classification 변경 이력과 policy decision은 `audit_logs.audit_metadata`에 저장한다.

## 근거

이 결정은 현재 물리 데이터 모델의 기존 table과 column을 보존한다. MVP 2의 data governance demo에는 document-level classification과 policy warn/block이 우선 필요하며, KB classification을 1급 DB column으로 검색/필터링하는 것은 MVP 2 필수 조건이 아니다.

또한 `documents.meta_info`는 이미 문서 source 설정, processing option, re-index flag 같은 application-level metadata를 저장하는 용도로 사용되고 있다. 따라서 classification도 같은 경계 안에서 시작하는 것이 가장 작은 변경이다.

## 영향

- 물리 데이터 모델: [data-model/physical-data-model.md](../../docs_old/data-model/physical-data-model.md)
- MVP 2 요구사항: [requirements/mvp-2-governance-rag-audit.md](../../docs_old/requirements/mvp-2-governance-rag-audit.md)
- Knowledge/RAG API: [api/knowledge-rag.md](../../docs_old/api/knowledge-rag.md)
- audit/trace 저장 기준: [ADR-0004-audit-log-rag-trace-storage](ADR-0004-audit-log-rag-trace-storage.md)

## 구현 기준

- API request/response schema는 필요하면 `classification`을 노출할 수 있지만, 저장소는 `documents.meta_info.classification`으로 매핑한다.
- KB detail/list에서 classification을 보여줘야 하면 documents의 classification을 집계해 derived value로 계산한다.
- policy result는 `audit_logs.status`가 아니라 `audit_logs.audit_metadata.policy_result`에 저장한다.
- `meta_info.classification`이 없으면 기본값은 `internal`로 해석한다.

## 후속 검토

- KB/document classification을 DB에서 직접 필터링해야 하는 요구가 생기면 별도 schema extension으로 `knowledge_bases.classification`, `documents.classification` 또는 governance policy table을 재검토한다.
- active 요구사항 문서의 "classification 필드 추가" 표현은 "metadata convention 및 API/UI 노출"로 정리한다.
