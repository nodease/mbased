# Mail Credentials Component Spec

Status: Draft

## Gateway

- Endpoint는 active organization과 인증 사용자 확인 후 `MailCredentialService`를 호출한다.
- Service는 manager-only registration, scope hiding, `use/manage`, encryption, lifecycle과 audit을 소유한다.
- ORM row를 직접 response로 serialize하지 않고 safe response mapper를 사용한다.

## Shared

- `MailCredential`은 organization scope, safe metadata, encrypted secret envelope와 lifecycle 상태를 저장한다.
- User/team Mail permission row는 기존 additive resource permission pattern을 따른다.
- Credential encryption service는 `MAIL_CREDENTIAL_ENCRYPTION_KEYS`의 active key version으로 encrypt하고 row의 key version으로 decrypt한다. 설정이 없으면 기존 `ENCRYPTION_KEY`를 `v1`으로 사용하는 호환 경계를 유지한다.
- Gateway와 Worker deployment는 같은 keyring과 `MAIL_CREDENTIAL_ACTIVE_KEY_VERSION`을 주입한다. Rotation 중 구키는 기존 row 복호화를 위해 keyring에 유지한다. 양쪽 process startup은 keyring 형식과 active version을 검증하고 잘못된 설정이면 fail-fast한다.
- Permission helper는 organization manager override와 team/user direct grant의 strongest auth state를 계산한다.

## Workflow Engine

- `MailCredentialResolver`는 DB session, execution subject, organization id와 credential id를 입력받는다.
- Resolver는 명시 user execution subject, scope, active 상태, `use` 권한, egress, decrypt 순으로 검증한다. App/workflow owner `user_id`를 execution subject로 대체하지 않는다.
- Resolver는 IMAP host를 중앙 egress guard로 검증하고 검증된 IP로 socket을 고정한다. `993`은 검증용 기본 SSL context의 implicit TLS, `143`은 로그인 전 STARTTLS를 사용하며 TLS 인증서 검증과 SNI에는 canonical hostname을 사용한다. Connect와 socket read timeout은 기본 10초다.
- `MailNode`는 resolver가 반환한 runtime value로만 IMAP에 연결한다.
- Runtime value는 node data, node output, audit 또는 trace metadata에 저장하지 않는다.
- Legacy inline password field가 발견되면 provider 연결 전에 중지한다.
- Mail 검색 문자열은 IMAP quoted-string으로 인코딩하고 protocol control character를 거부한다. 연결 이후 provider/cleanup 오류도 safe reason code 밖으로 노출하지 않는다.
- `MailProcessingService`는 workflow/source node scoped message identity, processing lease와 terminal state를 소유한다.
- `GmailDraftService`는 credential resolve, draft effect admission, MIME build, provider port 호출과 safe outcome 기록을 조정한다.
- `GmailDraftProviderPort`는 create reply draft만 노출하고 send method를 정의하지 않는다.
- `TerminalAcknowledgementService`는 processing과 required effect provenance를 검증한 뒤 idempotent acknowledgement를 수행한다.
- `mailNode` durable mode는 opaque `processing_ref`를 출력하고, `gmailDraftNode`와 `mailAcknowledgeNode`는 해당 reference를 서버에서 다시 검증한다.

## Client

- Mail node panel은 password input 대신 safe credential picker를 제공한다.
- Picker는 active organization의 사용 가능한 credential만 표시한다.
- 선택 결과는 `credential_id`만 node data에 저장한다.
- 선택할 credential이 없거나 기존 선택이 더 이상 유효하지 않으면 unresolved 상태를 표시한다.
- Agent Builder가 생성한 Mail node는 `credential_id: null`이며 자동 선택하지 않는다.
- Draft는 unresolved Mail node를 보존할 수 있지만 deployment 생성·활성화는 최상위와 중첩 graph의 모든 Mail node가 유효한 credential을 참조해야 한다.
- 실행 로그 설정 요약은 credential id나 mailbox identity 대신 `연결됨` 또는 `연결 필요`만 표시한다.
- 관리자 콘솔의 resource 권한 화면과 actor access drawer는 Mail credential의 user/team `read/use/manage` 부여·회수를 지원한다.
- Gmail OAuth credential 연결은 Gateway가 발급한 authorization URL로만 시작하며 token이나 provider response를 browser storage에 저장하지 않는다.
- Gmail Draft node는 processing ref와 reply body selector를, Mail Acknowledge node는 processing/effect selector를 명시적으로 연결한다.
- Agent Builder가 Gmail Draft automation을 제안하면 Mail node는 durable mode, 외부 node credential은 unresolved 상태로 생성한다.

## Audit And Trace

- Lifecycle action은 `mail_credential.create`, `mail_credential.update`, `mail_credential.revoke`를 사용한다.
- Permission denial은 공통 permission denial audit 경계를 사용한다.
- Metadata allowlist는 credential id, organization id, provider, status, reason code만 허용한다.
