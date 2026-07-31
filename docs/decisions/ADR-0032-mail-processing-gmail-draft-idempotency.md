# ADR-0032: Mail 처리 claim과 Gmail 답장 초안 멱등성 경계

Status: Accepted
Related ADRs: ADR-0022, ADR-0031

## 배경

ADR-0031은 organization-scoped Mail credential과 workflow graph의 opaque `credential_id` 경계를 확정했다. 현재 Mail node는 IMAP 검색 후 선택적으로 메시지를 즉시 읽음 처리한다. 이 구조에는 다음 공백이 있다.

- IMAP sequence id는 durable message identity가 아니다.
- schedule 재실행이나 Celery 재전달이 같은 메일을 반복 처리할 수 있다.
- Gmail 답장 초안과 후속 알림이 끝나기 전에 원본 메일이 읽음 처리될 수 있다.
- Gmail `users.drafts.create`는 provider idempotency key를 지원하지 않아 요청 결과 유실 뒤 무조건 retry하면 중복 draft가 생길 수 있다.
- Gmail OAuth 수신 조회와 terminal acknowledgement에 IMAP XOAUTH2를 사용하면 full-mail scope가 필요하며 최소 권한 목표와 충돌한다.
- `gmail.modify` OAuth scope는 조회·읽음 변경·draft 생성에 필요하지만 send도 허용하므로 scope만으로 no-send를 보장할 수 없다.

## 결정

1. Durable Mail 자동화는 `(organization_id, workflow_id, source_node_id, credential_id, provider, message_identity_hash)`로 message processing을 식별한다. Deployment version이 바뀌어도 같은 workflow와 stable source node id는 같은 logical consumer다.
2. Mail 검색 node는 `search_only`와 `durable` processing mode를 지원한다. 기존 graph의 기본값은 `search_only`다. Durable mode는 최소 provider source reference를 암호화한 processing row를 생성하고 후속 node에는 opaque `processing_ref`만 전달한다.
3. IMAP sequence id 단독 사용을 금지한다. 비 OAuth IMAP source는 canonical folder, RFC Message-ID와 IMAP UID/UIDVALIDITY를 사용한다. Gmail OAuth source는 고정 Gmail REST API에서 받은 provider message id를 암호화된 source reference에 저장하고 Draft/Acknowledge 직전에 같은 id와 RFC Message-ID를 다시 확인한다.
4. Gmail 답장 초안은 별도 `gmailDraftNode`가 담당한다. 수신 검색과 provider mutation을 하나의 node operation으로 합치지 않는다. 단일 Draft에 직접 연결되는 Mail node는 `max_results=1`로 제한하고 최상위 `processing_ref`를 출력한다. 여러 메시지는 명시적 Loop 계약 없이 단일 Draft node에 연결하지 않는다.
5. Gmail OAuth는 로그인 OAuth와 분리된 ADR-0031 Mail credential resource로 저장한다. Refresh token은 기존 versioned encryption envelope를 사용하고 workflow graph, API response, audit, trace, log에 저장하지 않는다. OAuth Gmail Mail node와 acknowledgement는 `gmail.modify` scope의 고정 Gmail REST API를 사용하며 IMAP XOAUTH2로 우회하지 않는다. 기존 app password/password credential만 IMAP 경로를 유지한다.
6. Gmail provider adapter는 검색, 원문 조회, 읽음 변경과 `users.drafts.create` allowlist만 노출한다. `users.drafts.send`, `users.messages.send`와 arbitrary Gmail method/URL 호출을 구현하지 않는다. `gmail.modify` scope가 send도 허용하므로 이 application capability allowlist가 no-send의 실제 경계다.
7. Draft effect는 `(processing_id, node_id, operation_key_hash)`로 하나만 admission한다. Provider 호출 전에 durable claim을 commit한다. `claimed` 상태만으로 provider 호출을 허용하지 않고 현재 실행이 claim을 새로 획득한 경우에만 호출한다. 기존 활성 claim을 본 중복 실행은 provider 호출 전에 종료한다.
7A. Deployment가 바뀌어도 terminal success는 같은 logical consumer의 완료로 재사용한다. 아직 effect가 없는 `pending` processing만 현재 deployment로 원자 재귀속할 수 있으며, effect가 있거나 실행 중인 processing은 deployment conflict로 차단한다.
8. Effect outcome은 `succeeded`, `failed_before_effect`, `outcome_unknown`을 구분한다. 요청 전달 뒤 timeout이나 응답 유실처럼 생성 여부를 확정할 수 없는 경우 `outcome_unknown`으로 격리하고 자동 replay하지 않는다.
9. Terminal acknowledgement는 명시적 `mailAcknowledgeNode`가 processing row와 required effect reference를 서버에서 검증한 뒤 수행한다. Client가 제출한 성공 boolean은 신뢰하지 않는다.
10. Durable mode에서는 `mailNode.mark_as_read=true`를 거부한다. Search-only mode는 모든 선택 메시지 fetch가 성공한 뒤에만 일괄 읽음 처리할 수 있다. 일부 fetch가 실패하면 어느 메시지도 읽음 처리하지 않는다.
11. Draft 성공 뒤 acknowledgement가 실패하면 draft를 다시 생성하지 않고 acknowledgement만 재시도한다. `outcome_unknown`과 terminal success 상태는 일반 실행 경로에서 pending으로 되돌리지 않는다.
11A. Retry 가능한 Draft 실패에는 durable `next_attempt_at`과 attempt cap을 적용한다. Cap 소진은 effect `exhausted`와 parent processing `failed`를 같은 transaction에서 terminal 처리한다.
11B. Terminal acknowledgement는 deployment와 selector에서 계산한 required-effect contract hash를 최초 성공 검증 시 processing row에 고정하고 이후 다른 contract를 거부한다.
11C. Terminal acknowledgement도 processing row의 lease를 원자 획득한 실행만 provider를 호출한다. 이미 `succeeded`이면 provider를 호출하지 않고 성공을 재사용하며, 활성 lease가 있으면 중복 실행을 차단한다. 실패 시 lease를 해제하고 acknowledgement 단계만 재시도한다.
12. Gmail 답장 MVP는 원본 sender 한 명, `text/plain` UTF-8, 무첨부로 제한한다. Reply-all, CC, BCC, HTML, attachment와 자동 발송은 지원하지 않는다.
13. Processing/effect row와 telemetry에는 body, snippet, subject, recipient, MIME, token, raw provider identifier/response/error를 저장하지 않는다. Provider 재조회에 필요한 최소 identifier는 versioned encryption envelope로 보호하고 client에는 processing/effect resource를 가리키는 opaque reference만 반환한다.
13A. Mail 처리 metric은 registration, Draft admission/outcome, acknowledgement의 고정 event/outcome enum만 label로 사용한다. Message, tenant, provider response와 사용자 입력은 metric label이나 구조화 로그에 넣지 않는다.
13B. Mail content의 durable trace 최소화 lineage는 control edge뿐 아니라 value selector의 data dependency도 따라간다.
13C. OAuth refresh는 credential row에 짧은 durable lease를 먼저 commit한 뒤 DB transaction 밖에서 token HTTP를 수행한다. Replacement token rotation과 `invalid_grant` revoke 판정은 같은 lease owner만 짧은 row lock transaction으로 finalize한다. 다른 OAuth 4xx/401은 자동 revoke하지 않는다.
14. MBA-190의 범용 외부 effect 계약을 선행 조건으로 두지 않는다. 이 ADR은 Mail/Gmail 전용 port와 outcome 의미를 작게 구현하고, 이후 공통 계약이 확정되면 adapter로 정렬한다.

## 검토한 대안

### Gmail draft를 기존 Mail node operation으로 추가

검색과 외부 mutation은 권한, retry, output과 실패 의미가 다르다. 기존 IMAP workflow 호환성과 effect admission 경계를 흐리므로 채택하지 않았다.

### Provider timeout을 동일 요청으로 자동 retry

Gmail Draft API에는 idempotency key가 없어 응답 유실 뒤 중복 draft를 만들 수 있다. 요청 전 실패가 확정된 경우만 bounded retry하고 결과 불명 상태는 격리한다.

### Workflow 실행 종료 hook에서 자동 acknowledgement

공통 Workflow Engine lifecycle을 Mail 전용 정책으로 변경하고 병렬 effect의 required/optional 의미를 암묵적으로 추론하게 된다. Graph에 명시적 acknowledgement node를 두는 방식을 채택한다.

### Organization/mailbox 단위 전역 message deduplication

서로 다른 workflow가 같은 메일을 합법적으로 처리할 수 없게 된다. Workflow와 stable source node를 logical consumer scope에 포함한다.

### Gmail OAuth 수신 조회에 IMAP XOAUTH2 사용

Gmail IMAP OAuth에는 broader full-mail scope가 필요하다. OAuth 수신·읽음 변경·Draft를 `gmail.modify` 기반 고정 REST adapter로 통합하고, app password/password credential만 IMAP으로 처리한다.

## 결과

- Mail processing과 Gmail draft effect용 additive table 및 migration이 필요하다.
- Processing/effect는 workflow 운영 상태이므로 workflow 삭제 시 함께 cascade 정리하며 audit log를 대체하지 않는다.
- Mail credential은 OAuth secret payload를 지원하도록 확장되지만 ADR-0031의 organization scope, permission, encryption, revoke 경계를 유지한다.
- Workflow node catalog, Editor, Agent Builder, save/deploy/runtime validation에 두 신규 node와 processing mode가 추가된다.
- 기존 `gmail.compose`만 승인한 OAuth credential은 재인가가 필요하다.
- Gmail OAuth restricted scope 운영에는 Google verification 및 필요 시 security assessment가 별도로 요구될 수 있다.
- 외부 provider까지 포함한 exactly-once 또는 자동 발송을 보장하지 않는다.
