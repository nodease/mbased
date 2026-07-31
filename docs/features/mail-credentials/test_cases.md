# Mail Credentials Test Cases

Status: Draft

## Model And Encryption

- MAIL-CRED-TC-001: Migration upgrade/downgrade가 Mail credential과 user/team permission table을 정확히 반영한다.
- MAIL-CRED-TC-002: 저장된 ciphertext는 입력 secret과 다르고 올바른 key version으로만 복호화된다.
- MAIL-CRED-TC-003: Unknown key version과 손상된 ciphertext는 safe 오류로 실패한다.
- MAIL-CRED-TC-004: 구키·신키 keyring은 기존 `v1` ciphertext를 복호화하면서 신규 secret을 active `v2`로 암호화한다.
- MAIL-CRED-TC-005: Disposable PostgreSQL에서 Mail credential 고정 revision upgrade, Mail credential/user/team permission CRUD, one-step downgrade와 re-upgrade가 통과한다.
- MAIL-CRED-TC-006: Mail migration은 live ORM model과 최신 Alembic head에 의존하지 않고 고정 revision의 upgrade/downgrade를 재현한다.

## API And Permission

- MAIL-CRED-TC-010: Organization manager만 credential을 등록할 수 있다.
- MAIL-CRED-TC-011: `viewer`는 safe detail만 조회하고 `use` 권한자는 picker option과 runtime 사용이 가능하지만 누구도 secret을 조회할 수 없다.
- MAIL-CRED-TC-012: `manage` 권한자는 secret을 교체하고 revoke할 수 있다.
- MAIL-CRED-TC-013: Cross-organization 접근은 `404`, in-scope denial은 `403`이다.
- MAIL-CRED-TC-014: Unknown field와 빈 PATCH는 validation error다.
- MAIL-CRED-TC-015: Revoked credential은 option 목록에서 제외된다.
- MAIL-CRED-TC-016: Revoked credential의 수정과 신규 permission grant는 `409 mail.credential_revoked`로 거부되고 기존 permission 회수는 허용된다.
- MAIL-CRED-TC-017: Lifecycle 또는 permission audit row 저장 실패 시 credential mutation도 rollback된다.
- MAIL-CRED-TC-018: PATCH는 이름과 secret 교체만 허용하고 endpoint, TLS mode, mailbox identity 변경을 unknown field로 거부한다.

## Runtime

- MAIL-CRED-TC-020: Runtime resolver는 scope, active, `use`, decrypt를 통과한 credential만 반환한다.
- MAIL-CRED-TC-021: 권한 회수와 revoke는 다음 실행부터 즉시 반영된다.
- MAIL-CRED-TC-022: 인증 test/deployment run은 같은 resolver와 명시 execution subject를 사용한다. Public/schedule run은 owner fallback 없이 차단된다.
- MAIL-CRED-TC-023: Provider 연결 전에 permission denial과 decrypt failure가 발생한다.
- MAIL-CRED-TC-024: `993`은 implicit TLS, `143`은 로그인 전 STARTTLS를 수행하고 다른 mode/port 조합과 private target을 거부한다.
- MAIL-CRED-TC-025: 검색 입력의 quote/backslash는 IMAP quoted-string으로 escape하고 CR/LF/NUL은 provider 호출 전에 거부한다.
- MAIL-CRED-TC-026: Select/search/fetch/logout의 provider raw exception은 safe reason code로 변환되거나 cleanup에서 흡수되어 log에 남지 않는다.
- MAIL-CRED-TC-027: Revoked credential permission row가 남아 있어도 direct permission count, team source count, team inherited resource count와 team mutation 영향 count에는 포함되지 않는다.
- MAIL-CRED-TC-028: `993` implicit TLS와 `143` STARTTLS는 인증서·hostname을 검증하고 connect/read timeout 10초를 적용한다.
- MAIL-CRED-TC-029: Gateway와 Worker는 잘못된 keyring 또는 active version을 startup에서 safe 오류로 거부한다.

## Graph And Client

- MAIL-CRED-TC-030: Mail node save payload에는 `credential_id`만 있고 password/token이 없다.
- MAIL-CRED-TC-031: Legacy inline secret graph는 `mail.credential_reference_required`로 실패한다.
- MAIL-CRED-TC-032: Mail panel은 safe option만 표시하고 password input을 렌더링하지 않는다.
- MAIL-CRED-TC-033: Agent Builder는 unresolved `credential_id`를 만들고 자동 선택하지 않는다.
- MAIL-CRED-TC-034: 관리자 콘솔과 actor access drawer는 Mail credential user/team 권한을 조회·부여·회수한다.
- MAIL-CRED-TC-035: Unknown Mail data field와 비어 있지 않은 `parameters`는 workflow 저장, Agent Builder apply/save, 비활성 포함 deployment 생성과 기존 deployment 활성화에서 모두 거부된다.
- MAIL-CRED-TC-036: 최상위와 중첩 `subGraph`의 Mail node에 같은 allowlist와 credential 검증을 적용하고 safe UI metadata는 허용한다.
- MAIL-CRED-TC-037: Draft와 Agent Builder preview는 unresolved Mail node를 허용하지만 deployment 생성과 활성화는 credential reference 누락을 거부한다.
- MAIL-CRED-TC-038: 실행 로그 설정 요약은 credential UUID 대신 연결 상태만 표시한다.
- MAIL-CRED-TC-039: Active membership row가 남아 있어도 `User.deactivated_at`이 설정된 사용자에게 direct Mail credential 권한을 생성하거나 갱신하지 않는다.

## Non-Exposure

- MAIL-CRED-TC-040: API response, graph, audit, trace, log와 fixture에 secret/ciphertext가 없다.
- MAIL-CRED-TC-041: 로그인 및 로그인 이후 작업/cleanup의 provider raw exception과 mailbox email 원문이 audit/trace/log에 없다.
- MAIL-CRED-TC-042: DB 예외 문자열에 mailbox identity나 ciphertext가 포함되어도 audit metadata와 API 오류에는 안전한 error code만 남는다.

## Gmail OAuth, Draft And Processing

- MAIL-CRED-TC-050: OAuth start/callback은 manager, active organization, state, PKCE, expiry를 검증하고 token을 response/log에 노출하지 않는다.
- MAIL-CRED-TC-051: Login OAuth token은 Mail credential resolver에서 사용할 수 없고 Gmail refresh token은 versioned encryption envelope로만 저장된다.
- MAIL-CRED-TC-052: Revoked, cross-organization, `use` 권한 없는 credential은 token refresh와 Gmail 호출 전에 차단된다.
- MAIL-CRED-TC-053: 같은 workflow/source node/credential/provider message를 동시 claim해도 processing row와 draft provider 호출은 하나다.
- MAIL-CRED-TC-054: 다른 workflow 또는 다른 source node는 같은 provider message를 독립 logical consumer로 처리할 수 있다.
- MAIL-CRED-TC-055: IMAP sequence id만 있는 message는 durable mode에서 거부하고 UID/UIDVALIDITY 또는 canonical provider identity를 요구한다.
- MAIL-CRED-TC-056: Gmail Draft는 원본 thread, In-Reply-To, bounded References와 정규화된 subject를 보존한다.
- MAIL-CRED-TC-057: Header injection, 다중 recipient, CC/BCC/reply-all, HTML, attachment와 size limit 초과는 provider 호출 전에 거부된다.
- MAIL-CRED-TC-058: Request 전달 후 timeout/응답 유실은 `outcome_unknown`이며 duplicate delivery가 Gmail create를 다시 호출하지 않는다.
- MAIL-CRED-TC-059: Provider 호출 전 확정 실패만 동일 operation key와 bounded backoff로 retry한다.
- MAIL-CRED-TC-060: Draft 성공 후 required effect 또는 acknowledgement 실패는 draft를 다시 생성하지 않고 미완료 단계만 재시도한다.
- MAIL-CRED-TC-061: Durable mode의 `mark_as_read=true`는 graph validation에서 실패하고 search-only mode는 전체 fetch 성공 후에만 일괄 ack한다.
- MAIL-CRED-TC-062: Production adapter와 catalog에는 Gmail send endpoint/capability가 존재하지 않는다.
- MAIL-CRED-TC-063: Processing/effect row, API, graph, audit, trace, log와 fixture에 body, MIME, token, raw provider id/response/error가 없다.
- MAIL-CRED-TC-064: Migration은 최신 단일 head를 유지하고 기존 IMAP credential/workflow를 보존한다.
- MAIL-CRED-TC-065: OAuth start는 `gmail.modify`만 요청하고 기존 `gmail.compose`-only secret은 provider 호출 전에 scope 부족으로 거부한다.
- MAIL-CRED-TC-066: OAuth Gmail Mail node는 고정 Gmail REST API로 검색·조회하며 IMAP socket/XOAUTH2를 호출하지 않는다. Password/app-password credential은 기존 IMAP TLS 경로를 유지한다.
- MAIL-CRED-TC-067: Gmail REST 검색의 provider message id는 Mail output에 없고 암호화 source reference와 identity hash에만 반영된다.
- MAIL-CRED-TC-068: Gmail REST 메시지 상세 조회가 하나라도 실패하면 search-only 일괄 읽음 처리를 호출하지 않는다. 성공한 전체 집합만 `batchModify`로 읽음 처리한다.
- MAIL-CRED-TC-069: Gmail provider 401/403은 재인가 필요, 429/5xx/timeout은 safe unavailable code로 분류하며 provider raw response/error는 노출하지 않는다.
- MAIL-CRED-TC-070: OAuth terminal acknowledgement는 암호화 source reference의 provider message id로 고정 `messages.modify`를 호출하고 IMAP UID 경로를 사용하지 않는다.
- MAIL-CRED-TC-071: 과도한 IMAP raw message와 Gmail REST body payload는 MIME/attachment 처리 전에 size limit으로 거부된다.
- MAIL-CRED-TC-072: `failed_before_effect`는 `next_attempt_at` 전에 reclaim되지 않고 attempt cap 소진 시 effect/processing이 `exhausted`/`failed` terminal 상태가 된다.
- MAIL-CRED-TC-073: 같은 processing을 다른 deployment 또는 다른 required-effect selector contract로 acknowledge하면 provider 호출 전에 거부된다.
- MAIL-CRED-TC-074: Workflow 삭제 시 processing/effect operational row는 cascade 정리되고 별도 audit row lifecycle에는 영향을 주지 않는다.
- MAIL-CRED-TC-075: Mail processing metric에 임의 event/outcome을 전달하면 `unknown`으로 정규화하며 raw message/draft/tenant/provider 값이 label이나 구조화 로그에 남지 않는다.
- MAIL-CRED-TC-076: selector로만 Mail output을 참조하는 downstream LLM/template node와 그 후손도 Mail-sensitive lineage로 분류되어 durable trace가 구조 요약으로 치환된다.
- MAIL-CRED-TC-077: 이미 성공한 acknowledgement 또는 다른 실행의 활성 ack lease를 만나면 provider acknowledgement 호출 수는 증가하지 않는다. Ack 실패는 lease를 해제하고 Draft를 재생성하지 않는다.
- MAIL-CRED-TC-078: 재배포 후 effect 없는 pending processing은 현재 deployment로 재귀속되지만 active/effect-bearing processing은 conflict로 차단되고 terminal success는 재사용된다.
- MAIL-CRED-TC-079: 동시 OAuth refresh는 짧은 credential lease winner만 provider를 호출하고 외부 HTTP 동안 DB transaction을 유지하지 않는다. Replacement token rotation과 audit은 lease owner를 확인한 finalize transaction에서 commit하며 `invalid_grant`만 local revoke한다.
- MAIL-CRED-TC-080: Opt-in disposable PostgreSQL race에서 동일 message registration은 한 processing id로 수렴하고 동시 Draft admission의 acquired winner는 하나다.
- MAIL-CRED-TC-081: Google OAuth/Gmail adapter는 고정 operation과 guarded requester를 사용하며 wrong origin, private/mixed DNS, peer mismatch, redirect와 response 상한 위반을 추가 요청 없이 safe failure로 차단한다.
- MAIL-CRED-TC-082: OAuth `invalid_grant`, Gmail 401/403/429/5xx, connect failure와 전송 뒤 응답 상실은 기존 provider별 safe code와 failure phase를 유지하고 token, raw URL/body/response/exception을 노출하지 않는다. Production adapter registry에는 Gmail send operation이 없다.
- MAIL-CRED-TC-083: Gmail REST 검색의 목록 조회와 최대 100개 상세 조회는 같은 read operation과 승인 origin에 묶인 context-managed guarded session 하나를 사용한다. 각 상세 URL은 client 생성·network I/O 전에 다시 검증하고 session client 초기화·요청 실패는 raw 예외 없이 기존 safe unavailable code로 변환한다.
