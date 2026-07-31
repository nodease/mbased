# ADR-0004: audit_logs와 RAG Trace 저장 기준

Status: Accepted
Date: 2026-06-27 15:59 KST
Original: ADR-202606271559-audit-log-rag-trace-storage
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 배경

초기 초안은 `audit_events`와 `rag_retrieval_traces` 신규 table을 제안했다. 현재 코드 기준에는 이미 `audit_logs`, trace policy table, `trace_payloads`, `trace_payload_access_events`가 있다.

중복 audit/RAG trace table을 만들면 schema가 커지고 같은 사건을 여러 table에 나누어 저장할 위험이 있다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| 신규 audit/RAG table 추가 | `audit_events`, `rag_retrieval_traces`를 만든다. | 정규화는 강하지만 schema 확장이 크고 중복 위험이 있다. |
| 현재 table 재사용 | `audit_logs`와 trace payload/metadata를 사용한다. | 현재 구조를 보존하고 중복 로그 모델을 피한다. |

## 결정

현재 table을 재사용한다.

- `audit_events`를 만들지 않는다.
- `rag_retrieval_traces`를 만들지 않는다.
- canonical action은 `audit_logs.action`에 저장한다.
- 정책 결과와 풍부한 context는 `audit_logs.audit_metadata`에 저장한다.
- RAG retrieval의 per-chunk evidence는 `trace_payloads.payload_kind='rag.retrieval'`에 저장하고, run/node trace metadata에는 retrieved chunk count, document/citation id, score summary, fallback flag 같은 redaction-safe summary만 저장한다.

## 근거

이 결정은 현재 물리 데이터 모델을 보존하고, audit 모델 중복을 피하며, raw/redacted payload 처리를 기존 tracing policy boundary 안에 둔다.

## 영향

- 물리 데이터 모델: [data-model/physical-data-model.md](../../docs_old/data-model/physical-data-model.md)
- 추적/감사 아키텍처: [architecture/tracing-audit.md](../../docs_old/architecture/tracing-audit.md)
- API 계약: [api/tracing-audit.md](../../docs_old/api/tracing-audit.md)
- 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- RAG retrieval summary metadata allowlist를 구현 fixture로 고정한다.
- raw chunk text와 `retrieved_chunks` 배열이 run/node metadata에 복사되지 않도록 검증한다.
- audit action과 trace payload access 기록 테스트를 추가한다.
