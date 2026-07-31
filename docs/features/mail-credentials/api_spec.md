# Mail Credentials API Spec

Status: Draft

## 공통 계약

- 모든 endpoint는 인증과 `X-Organization-Id` active organization context를 요구한다.
- Organization scope 밖 credential은 `404 resource.not_found`로 숨긴다.
- 같은 organization에서 action 권한이 부족하면 `403 permission.denied`를 반환한다.
- Request schema는 unknown field를 거부한다.
- Response에는 secret, ciphertext, encryption key metadata 또는 provider raw response를 포함하지 않는다.

## Endpoints

| Method | Path | 설명 | 권한 |
| --- | --- | --- | --- |
| POST | `/api/v1/mail/credentials` | Mail credential 등록 | Organization manager |
| GET | `/api/v1/mail/credentials` | 사용 가능한 safe option 목록 | `use` 이상 또는 manager |
| GET | `/api/v1/mail/credentials/{credential_id}` | Safe metadata 조회 | `read` 이상 또는 manager |
| PATCH | `/api/v1/mail/credentials/{credential_id}` | 이름 또는 non-OAuth secret 교체 | `manage` 또는 manager |
| DELETE | `/api/v1/mail/credentials/{credential_id}` | Credential revoke | `manage` 또는 manager |
| GET | `/api/v1/mail/credentials/{credential_id}/permissions` | User/team 권한 목록 | `manage` 또는 manager |
| PUT | `/api/v1/mail/credentials/{credential_id}/permissions/users/{user_id}` | User direct 권한 부여·변경 | `manage` 또는 manager |
| DELETE | `/api/v1/mail/credentials/{credential_id}/permissions/users/{user_id}` | User direct 권한 회수 | `manage` 또는 manager |
| PUT | `/api/v1/mail/credentials/{credential_id}/permissions/teams/{team_id}` | Team 권한 부여·변경 | `manage` 또는 manager |
| DELETE | `/api/v1/mail/credentials/{credential_id}/permissions/teams/{team_id}` | Team 권한 회수 | `manage` 또는 manager |
| POST | `/api/v1/permissions/bulk-grants` | 복수 Mail credential×User/Team 권한 원자 부여·변경 | 모든 credential의 `manage` 또는 manager |
| POST | `/api/v1/mail/credentials/oauth/google/start` | Gmail OAuth authorization 시작 | Organization manager |
| GET | `/api/v1/mail/credentials/oauth/google/callback` | State/PKCE 검증, Gmail credential 생성 후 safe redirect | Pending flow actor |

## Create Request

```json
{
  "credential_name": "업무용 메일",
  "provider": "gmail",
  "email_address": "mailbox@example.com",
  "auth_type": "app_password",
  "secret": "request-only-secret",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "use_ssl": true
}
```

`secret`은 request-only field다. 저장 응답과 이후 조회에 반환하지 않는다.

`use_ssl=true`는 `imap_port=993`의 implicit TLS를 의미한다. `use_ssl=false`는 평문 IMAP 허용이 아니라 `imap_port=143`에서 로그인 전에 STARTTLS를 강제한다. 다른 조합은 `mail.egress_target_denied`로 거부한다.

## Safe Response

`GET /credentials` picker option은 최소 공개 계약으로 다음 필드만 반환한다.

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "credential_name": "업무용 메일",
  "provider": "gmail",
  "auth_type": "oauth2",
  "email_preview": "m***@example.com",
  "status": "active"
}
```

상세 조회와 lifecycle mutation 응답은 다음 safe detail을 반환한다.

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "organization_id": "00000000-0000-0000-0000-000000000000",
  "credential_name": "업무용 메일",
  "provider": "gmail",
  "email_preview": "m***@example.com",
  "auth_type": "app_password",
  "status": "active",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "use_ssl": true
}
```

## PATCH 계약

PATCH는 `credential_name`, `secret` 중 하나 이상을 요구한다. OAuth credential의 `secret` 교체는 OAuth authorization flow만 사용하며 일반 PATCH에서는 `mail.oauth_credential_managed`로 거부한다. `email_address`, provider, auth type, `imap_host`, `imap_port`, `use_ssl` 변경은 mailbox identity 또는 secret 전송 endpoint 변경으로 간주하므로 새 credential 등록을 사용한다. Unknown field와 null field는 `validation.failed`로 거부한다.

## Permission 계약

Permission PUT body는 `auth_state`에 `viewer`, `operator`, `builder`, `manager` 중 하나만 허용한다. 기존 row가 있으면 갱신하고 없으면 생성한다. User는 active organization member이면서 비활성화되지 않은 계정이어야 하고, Team은 같은 organization의 active Team이어야 한다. User direct grant는 대상 membership과 User를 잠금 확인하고 동시 변경은 credential row lock 안에서 직렬화한다. Revoked credential에는 신규 권한을 부여할 수 없지만 기존 권한 회수는 허용한다.

- `viewer`: safe detail `read`
- `operator`: `read`, runtime `use`
- `builder`: `read`, `use`, 일반 resource write 계층과의 일관성을 위한 상위 상태
- `manager`: `read`, `use`, `manage`

## 오류

- `mail.credential_reference_required`: Legacy inline password graph 또는 credential reference 누락
- `mail.credential_not_available`: Credential 없음 또는 revoked 상태
- `mail.credential_permission_denied`: Execution subject에게 `use` 권한 없음
- `mail.credential_decryption_failed`: Secret 복호화 실패
- `mail.credential_revoked`: Revoked credential 수정 또는 신규 permission 부여 시도
- `mail.credential_persistence_failed`: Credential 또는 canonical audit transaction 저장 실패
- `mail.egress_target_denied`: Private/loopback/metadata target 또는 허용되지 않은 IMAP port
- `validation.failed`: Unknown field, null field, 빈 PATCH, 잘못된 endpoint 또는 port

오류 detail에는 secret, ciphertext, email 원문, IMAP raw exception을 포함하지 않는다.

## Gmail OAuth 계약

`POST /oauth/google/start` body는 `credential_name`만 허용한다. Server는 인증 사용자와 active organization을 pending flow에 묶고 Google authorization URL을 반환한다. URL은 `gmail.modify` restricted scope, offline access와 명시 consent를 요청한다. Client가 organization, redirect URI, scope 또는 provider endpoint를 임의 지정할 수 없다.

Callback은 server-issued state, PKCE verifier, pending flow actor와 만료를 검증한다. Google token response의 refresh/access token은 response나 redirect query에 포함하지 않는다. Refresh token과 승인 scope는 Mail credential encryption envelope에 저장하며 mailbox identity는 provider userinfo에서 검증한다. 성공 후 동일 출처의 정적 완료 화면으로 `303` 전환하며 credential id를 query나 HTML에 포함하지 않는다.

Production은 `GOOGLE_OAUTH_REDIRECT_URI`에 사전 등록된 HTTPS callback URI를 명시하고 안전한 `SECRET_KEY`를 제공해야 한다. Local development에서만 `localhost` 또는 `127.0.0.1` callback을 요청 URL로 유도할 수 있다.

OAuth 취소 또는 실패는 `mail.oauth_cancelled`, `mail.oauth_state_invalid`, `mail.oauth_flow_expired`, `mail.oauth_token_exchange_failed`, `mail.oauth_scope_insufficient`, `mail.oauth_refresh_token_required` 중 safe code로 반환하며 provider raw response를 노출하지 않는다. Provider `error` 원문은 응답 detail에 반영하지 않는다.

OAuth Gmail credential의 Mail 조회와 읽음 처리는 고정 Gmail REST API만 사용한다. 저장된 scope에 `gmail.modify`가 없으면 token refresh/provider 호출 전에 `mail.oauth_scope_insufficient`로 실패하고 재인가를 요구한다. OAuth credential은 IMAP XOAUTH2 fallback을 사용하지 않는다.

Google token endpoint, Gmail profile/message/modify/draft endpoint는 server-owned operation-bound transport를 사용한다. HTTPS/443, no-redirect, bounded timeout/request/response와 DNS-to-dial peer 검증을 적용하며 request에서 provider origin, method 또는 proxy를 지정할 수 없다. Guard/provider 실패는 기존 safe `mail.*` code로 반환하고 raw URL, token, message body, provider response와 exception을 노출하지 않는다.

## Workflow Runtime Reference 계약

- `mailNode` durable output은 provider raw id 대신 opaque `processing_ref`를 반환한다.
- `gmailDraftNode`는 `credential_id`, `processing_ref` selector와 reply body selector만 받으며 opaque `draft_ref`와 safe status를 반환한다.
- `mailAcknowledgeNode`는 `processing_ref`와 required effect reference selector만 받는다. Client가 제출한 success boolean이나 provider id는 받지 않는다.
- Gmail Draft와 acknowledgement는 일반 public REST mutation API로 노출하지 않고 authenticated workflow runtime에서만 수행한다.
