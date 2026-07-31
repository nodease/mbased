# Mail Credentials Requirements

Status: Draft
Related Features: workflow, organization, agent-builder, audit-tracing, deployment

## Purpose

Mail credential은 organization이 관리하는 Mail provider 인증 정보를 workflow definition과 분리해 저장하고, 권한이 있는 execution subject에게만 실행 시점 사용을 허용한다.

## Functional Requirements

- MAIL-CRED-REQ-001: Mail credential은 하나의 organization에 속해야 한다.
- MAIL-CRED-REQ-002: Credential 등록은 active organization manager만 수행할 수 있어야 한다.
- MAIL-CRED-REQ-003: Safe metadata 조회, 실행 사용과 관리는 각각 `read`, `use`, `manage` 권한으로 분리해야 한다.
- MAIL-CRED-REQ-004: Organization manager는 해당 organization Mail credential의 `use/manage` override를 가져야 한다.
- MAIL-CRED-REQ-005: Secret은 versioned encryption envelope로 저장하고 key 원문은 DB에 저장하지 않아야 한다.
- MAIL-CRED-REQ-006: API response, workflow graph, audit, trace, log와 fixture는 secret 또는 ciphertext 원문을 포함하지 않아야 한다.
- MAIL-CRED-REQ-007: Safe option에는 credential id, 표시 이름, provider, 마스킹된 mailbox identity, 상태만 포함해야 한다.
- MAIL-CRED-REQ-008: Runtime은 execution subject, organization, credential 상태와 `use` 권한을 실행 직전에 검증해야 한다.
- MAIL-CRED-REQ-009: Credential 누락, 다른 organization, revoked 상태, 권한 없음, 복호화 실패는 provider 연결 전에 fail-closed해야 한다.
- MAIL-CRED-REQ-010: 인증 test/deployment run은 같은 resolver와 명시 user execution subject를 사용해야 한다. 명시 주체가 없는 public/schedule run은 owner fallback 없이 차단해야 한다.
- MAIL-CRED-REQ-011: Workflow graph의 Mail node는 `credential_id`만 저장하고 inline password/token field를 금지해야 한다.
- MAIL-CRED-REQ-012: Legacy inline password graph를 자동 실행하거나 자동 migration하지 않아야 한다.
- MAIL-CRED-REQ-013: Credential revoke는 다음 실행부터 즉시 적용되어야 한다.
- MAIL-CRED-REQ-014: Agent Builder는 credential을 자동 선택하지 않고 unresolved reference를 생성해야 한다.
- MAIL-CRED-REQ-015: Permission denial과 lifecycle mutation은 safe metadata로 감사해야 한다.
- MAIL-CRED-REQ-016: IMAP runtime은 중앙 egress guard로 public target과 허용 포트를 검증하고 검증된 IP에 실제 연결을 고정해야 한다. `993`은 implicit TLS, `143`은 로그인 전 STARTTLS로만 허용한다.
- MAIL-CRED-REQ-017: Mail node data는 allowlist로 검증하고 unknown field와 비어 있지 않은 `parameters`를 저장·Agent Builder·deployment 생성·deployment 활성화에서 모두 거부해야 한다.
- MAIL-CRED-REQ-018: Credential lifecycle과 permission mutation은 같은 transaction의 canonical audit row와 함께 commit하고 audit persistence 실패 시 rollback해야 한다.
- MAIL-CRED-REQ-019: Revoked credential은 수정하거나 신규 permission을 부여할 수 없어야 하며 기존 permission 회수만 허용해야 한다.
- MAIL-CRED-REQ-020: IMAP command에 들어가는 credential과 검색 입력은 CR/LF/NUL을 거부하고 quoted-string 규칙으로 인코딩해야 한다. Provider raw exception은 로그인 이후 작업과 cleanup을 포함해 audit, trace, log에 남기지 않아야 한다.
- MAIL-CRED-REQ-021: Gateway와 Worker는 동일한 versioned keyring을 사용해야 한다. Rotation은 구키·신키 동시 배포 후 active version을 전환하며, 기존 ciphertext 복호화와 신규 version 암호화를 모두 검증해야 한다.
- MAIL-CRED-REQ-022: Revoked credential의 기존 permission row는 감사·정리 목적으로 유지할 수 있지만 direct/team 운영 권한 목록과 모든 운영 권한 집계에서는 제외해야 한다.
- MAIL-CRED-REQ-023: Credential 생성 후 mailbox identity, provider, auth type과 IMAP endpoint/TLS mode는 불변이어야 한다. 변경이 필요하면 새 credential을 등록하고 PATCH는 표시 이름과 secret 교체만 허용해야 한다.
- MAIL-CRED-REQ-024: Mail node allowlist와 credential 검증은 최상위 graph뿐 아니라 모든 중첩 `subGraph.nodes`에 적용해야 한다. `displayNumber`와 `visibleProperties`는 safe UI metadata로 허용할 수 있다.
- MAIL-CRED-REQ-025: Draft와 Agent Builder preview는 unresolved Mail reference를 허용할 수 있지만 deployment snapshot 생성과 기존 deployment 활성화는 모든 Mail node의 유효한 credential reference를 요구해야 한다.
- MAIL-CRED-REQ-026: `993` implicit TLS와 `143` STARTTLS는 모두 기본 trust store 기반 인증서 및 hostname 검증을 수행하고 connect/read timeout을 기본 10초로 제한해야 한다.
- MAIL-CRED-REQ-027: User direct permission은 active organization membership과 비활성화되지 않은 User를 함께 잠금 확인한 뒤에만 생성하거나 갱신해야 한다.
- MAIL-CRED-REQ-028: Gateway와 Worker는 process startup에서 Mail credential keyring 형식과 active version을 검증하고 잘못된 설정이면 요청 또는 task 소비 전에 fail-fast해야 한다.
- MAIL-CRED-REQ-029: Gmail OAuth credential은 로그인 OAuth와 분리된 organization Mail credential이어야 하며 refresh token은 기존 versioned encryption envelope로 저장해야 한다.
- MAIL-CRED-REQ-030: Gmail OAuth callback은 인증 사용자, active organization, manager 권한, state와 PKCE를 검증한 뒤에만 credential을 생성해야 한다.
- MAIL-CRED-REQ-031: Gmail Draft runtime은 `use` 권한과 active/revoked 상태를 외부 호출 직전에 다시 확인하고 access token과 refresh token을 API, graph, audit, trace, log에 노출하지 않아야 한다.
- MAIL-CRED-REQ-031A: OAuth credential의 secret은 일반 PATCH로 교체할 수 없으며 OAuth authorization flow를 통해서만 갱신해야 한다. Local revoke는 다음 runtime부터 즉시 fail-closed해야 한다.
- MAIL-CRED-REQ-032: Gmail Draft adapter는 `users.drafts.create`만 허용하며 Gmail send endpoint와 arbitrary method/URL 실행 surface를 제공하지 않아야 한다.
- MAIL-CRED-REQ-033: Durable processing identity는 organization, workflow, stable source node, credential과 provider message identity에 묶여야 하며 동일 logical consumer의 중복 실행을 하나의 processing row로 수렴해야 한다.
- MAIL-CRED-REQ-034: IMAP sequence id 단독 사용을 금지하고 canonical folder, RFC Message-ID, UID/UIDVALIDITY와 provider canonical lookup을 사용해야 한다.
- MAIL-CRED-REQ-035: Gmail Draft effect는 provider 호출 전에 durable admission을 완료하고 `succeeded`, `failed_before_effect`, `outcome_unknown`을 구분해야 한다.
- MAIL-CRED-REQ-036: `outcome_unknown`은 자동 replay하지 않아야 한다. Provider 호출 전 실패가 확정된 경우에만 bounded retry할 수 있다.
- MAIL-CRED-REQ-037: Terminal acknowledgement는 required effect가 모두 성공한 뒤 서버가 검증한 opaque reference를 기준으로 수행해야 한다.
- MAIL-CRED-REQ-038: Durable Mail mode에서는 검색 node의 즉시 `mark_as_read`를 금지하고 acknowledgement node만 읽음 처리를 수행해야 한다.
- MAIL-CRED-REQ-039: Search-only mode의 읽음 처리는 모든 선택 메시지 fetch 성공 뒤 일괄 수행하며 일부 fetch 실패 시 수행하지 않아야 한다.
- MAIL-CRED-REQ-040: 답장 초안은 원본 Gmail thread, RFC In-Reply-To/References와 정규화된 subject를 보존해야 한다.
- MAIL-CRED-REQ-041: Gmail 답장 MVP는 sender 한 명, text/plain UTF-8, 무첨부로 제한하고 header CR/LF/NUL과 과도한 MIME 크기를 거부해야 한다.
- MAIL-CRED-REQ-042: Processing/effect durable row와 telemetry에는 Mail body, subject, recipient, MIME, token, raw provider id/response/error를 저장하지 않아야 한다.
- MAIL-CRED-REQ-043: Client와 Agent Builder는 Gmail Draft credential을 자동 선택하지 않고 unresolved reference와 safe configuration issue만 생성해야 한다.
- MAIL-CRED-REQ-044: Gmail OAuth restricted scope와 application no-send 경계는 운영 승인 및 배포 설정에서 명시적으로 검증해야 한다.
- MAIL-CRED-REQ-045: Gmail OAuth Mail 조회, 읽음 변경과 Draft 생성은 `gmail.modify` scope의 고정 Gmail REST adapter를 사용해야 하며 OAuth credential을 IMAP XOAUTH2로 연결하지 않아야 한다. Password/app-password credential만 제한된 IMAP egress 경로를 사용한다.
- MAIL-CRED-REQ-046: Gmail REST provider message id는 최소 source reference envelope에만 암호화 저장하고 Mail node output, API, audit, trace, log에 노출하지 않아야 한다. Draft와 acknowledgement는 이 id를 서버에서 복호화하고 canonical RFC Message-ID를 재검증해야 한다.
- MAIL-CRED-REQ-047: IMAP Mail fetch와 Gmail REST body decode는 명시한 byte 상한을 적용하고 과도한 원문은 MIME/attachment 처리 전에 safe error로 거부해야 한다.
- MAIL-CRED-REQ-048: Retry 가능한 Draft 실패는 durable `next_attempt_at`과 attempt cap을 가져야 한다. Cap 소진은 effect `exhausted`와 parent processing `failed`를 원자적으로 terminal 처리해야 한다.
- MAIL-CRED-REQ-049: Terminal acknowledgement는 deployment와 graph selector에서 계산한 required-effect contract hash를 processing row에 고정하고 다른 contract의 완료 시도를 거부해야 한다.
- MAIL-CRED-REQ-050: Mail processing/effect는 workflow 운영 상태이며 workflow 삭제 시 함께 cascade 정리한다. 감사 보존은 별도 append-only audit log가 담당한다.
- MAIL-CRED-REQ-051: Mail 처리 metric과 구조화 상태 로그는 고정된 저카디널리티 event/outcome만 사용하고 raw message/draft/tenant/provider 식별자와 사용자 입력을 포함하지 않아야 한다.
- MAIL-CRED-REQ-052: OAuth access token refresh는 짧은 durable credential lease로 직렬화하고 외부 token HTTP 동안 DB row lock이나 transaction을 유지하지 않아야 한다. Replacement refresh token rotation과 `invalid_grant` local revoke는 lease owner 확인 후 audit와 같은 transaction에서 finalize해야 하며, 다른 provider 오류는 credential을 자동 revoke하지 않아야 한다.
- MAIL-CRED-REQ-053: 이미 성공한 terminal acknowledgement와 활성 acknowledgement lease를 본 중복 실행은 Gmail/IMAP provider acknowledgement를 다시 호출하지 않아야 한다.
- MAIL-CRED-REQ-054: Deployment 전환 시 effect가 없는 `pending` processing만 새 deployment로 재귀속할 수 있다. Active/effect-bearing processing은 conflict로 차단하고 terminal success는 provider 호출 없이 재사용해야 한다.
- MAIL-CRED-REQ-055: Mail trace lineage는 control edge와 selector data dependency를 모두 따라 downstream durable payload를 구조 요약으로 최소화해야 한다.
- MAIL-CRED-REQ-056: Google OAuth token exchange/refresh와 Gmail profile/message/modify/draft 호출은 server-owned operation ID, 고정 HTTPS/443 endpoint와 bounded request/response/timeout profile을 사용하는 공통 guarded requester를 통과해야 한다. Gateway/Worker service와 node는 concrete HTTP client 또는 arbitrary URL·method를 생성하지 않아야 한다.
- MAIL-CRED-REQ-057: 공통 requester는 network failure phase만 반환하고 OAuth/Gmail adapter가 provider status, `invalid_grant`, reauthorization, Draft outcome unknown과 acknowledgement 의미를 기존 safe 계약으로 변환해야 한다. Gmail send capability는 추가하지 않는다.

## Policies And Edge Cases

- Credential id는 secret이 아니지만 organization 밖에서는 resource existence를 숨긴다.
- `use` 권한자는 secret 원문을 조회할 수 없다.
- `viewer`는 safe metadata만 조회할 수 있고 workflow 실행에는 credential을 사용할 수 없다.
- Secret 교체는 `manage` 권한을 요구하며 기존 secret을 응답하지 않는다.
- Endpoint와 mailbox identity는 기존 secret이 다른 서버로 전달되는 것을 막기 위해 생성 후 변경할 수 없다.
- Mailbox email 원문은 credential option과 audit에서 마스킹한다.
- Credential 삭제 API는 hard delete가 아니라 revoke semantics를 사용한다.
- Revoked credential을 다시 사용하려면 기존 row를 수정하는 대신 새 credential을 등록한다.
- OAuth refresh/token exchange, Gmail draft 생성, Message ID idempotency와 terminal acknowledgement는 MBA-217 통합 범위다.
- Production OAuth start는 명시된 HTTPS callback URI와 안전한 session signing key가 없으면 실패해야 한다. 요청 Host 헤더로 public callback URI를 구성하지 않는다.
- 기존 `gmail.compose`만 승인한 credential은 runtime scope 검증에서 fail-closed하며 `gmail.modify` 재인가가 필요하다.
- Schedule Mail 실행에 필요한 service account 또는 assigned operator 정책은 MBA-219 또는 별도 ADR에서 확정한다.
