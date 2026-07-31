# ADR-0031: Mail credential reference 경계

Status: Accepted

## 배경

현재 Mail node는 이메일 주소와 앱 비밀번호를 workflow graph에 직접 저장한다. Workflow graph는 편집 API, version, preview, 실행 payload와 테스트 fixture를 통과하므로 secret 저장 경계로 사용할 수 없다.

기존 `connections`는 사용자 소유 PostgreSQL 연결이고, `llm_credentials`는 provider-model relation과 quota를 포함한 LLM 전용 resource다. 어느 모델도 organization-scoped Mail 계정의 lifecycle과 runtime authorization을 안전하게 표현하지 못한다.

## 결정

1. Organization-scoped `mail_credentials` resource를 별도 bounded context로 추가한다.
2. Workflow graph의 Mail node에는 opaque `credential_id`만 저장한다. Mail node data는 최상위 graph와 모든 중첩 `subGraph`에서 allowlist로 검증하며 unknown field와 비어 있지 않은 `parameters`를 거부한다. Secret, ciphertext, token 또는 password field는 저장하지 않는다. `displayNumber`와 `visibleProperties`는 credential을 포함할 수 없는 UI metadata로만 허용한다.
3. Mail credential 등록은 active organization manager만 가능하다. 기존 credential의 조회·사용·관리는 resource permission의 `use`와 `manage`로 분리한다. Credential 생성 후 mailbox identity, provider, auth type과 IMAP endpoint/TLS mode는 불변이며 변경하려면 새 credential을 등록한다. `manage` 권한의 PATCH는 표시 이름과 secret 교체만 허용한다.
4. 인증된 test/deployment run은 명시적인 user execution subject와 canonical organization을 runtime resolver에 전달하고 실행 직전에 scope, 상태, `use` 권한을 재검증한다. App/workflow owner의 `user_id`를 execution subject로 대체하지 않는다. 명시 주체가 없는 public/schedule run은 service account 또는 assigned operator 정책이 확정되기 전까지 Mail credential 사용을 fail-closed한다.
5. Secret은 versioned encryption envelope로 저장한다. Key는 DB가 아니라 environment 또는 외부 secret manager에서 주입한다. Gateway와 Worker는 같은 keyring을 받아야 하며 rotation 중에는 구키와 신키를 함께 배포한 뒤 active version을 전환한다. Gateway와 Worker는 process startup에서 keyring 형식과 active version을 검증하고 잘못된 설정이면 요청 또는 task 소비 전에 fail-fast한다.
6. Credential 응답, audit, trace와 log는 safe metadata allowlist를 사용한다. Secret, ciphertext, mailbox email 원문과 provider raw error를 기록하지 않는다.
7. Legacy inline password graph는 자동 migration이나 fallback 없이 `mail.credential_reference_required`로 fail-closed한다.
8. 삭제 요청은 즉시 revoke하여 다음 실행부터 차단한다. 물리 삭제와 보존 기간은 별도 retention 결정에서 다룬다.
9. Mail IMAP 연결은 중앙 egress guard로 public target과 허용 포트를 검증한다. `993`은 검증용 기본 SSL context를 사용하는 implicit TLS, `143`은 로그인 전 STARTTLS로만 허용한다. DNS 재바인딩을 막기 위해 검증된 IP를 최종 CONNECT authority로 고정하되 TLS 인증서와 SNI는 canonical hostname으로 검증한다. Production proxy mode에서는 Worker 전용 Squid listener가 `143/993` TCP tunnel만 중계하며, proxy 실패 뒤 direct socket으로 fallback하지 않는다. Local/test direct mode는 같은 검증 IP로 직접 연결한다. Connect와 socket read는 기본 10초 timeout으로 제한한다.
10. Credential lifecycle과 permission mutation은 safe allowlist audit row를 같은 DB transaction에 기록한다. Audit 저장 실패 시 mutation도 rollback한다.
11. Revoke는 terminal lifecycle state다. Revoked credential의 수정과 신규 permission grant는 거부하되 기존 permission 회수와 감사 조회는 허용한다.
12. IMAP 검색 값은 protocol quoted-string으로 인코딩하고 CR/LF/NUL을 거부한다. 로그인 이후 select/search/fetch/cleanup에서 발생한 provider raw exception도 safe reason code로 변환하거나 cleanup에서 흡수한다.
13. Revoke된 credential의 기존 user/team permission row는 감사와 명시적 정리를 위해 보존할 수 있지만, 직접 권한 수, 팀 상속 source 수, 팀별 상속 resource 수와 권한 변경 영향 수 같은 모든 운영 권한 집계에서는 제외한다.
14. Workflow draft와 Agent Builder preview는 unresolved Mail reference를 저장할 수 있다. Deployment snapshot 생성과 기존 deployment 활성화는 최상위와 중첩 Mail node 모두 유효한 `credential_id`가 있어야 하며, 없으면 `mail.credential_reference_required`로 차단한다.

## 검토한 대안

### 기존 `connections` 재사용

현재 모델은 user-owned PostgreSQL 연결이며 organization scope와 resource permission이 없다. Mail을 추가하면 DB connector의 책임과 schema를 동시에 바꾸게 되어 채택하지 않았다.

### `llm_credentials` 재사용

LLM provider/model relation과 Mail provider 인증 lifecycle이 다르다. 이름만 credential이라는 이유로 같은 table을 공유하면 도메인 규칙과 권한 오류가 결합되므로 채택하지 않았다.

### Workflow graph 내부 암호화

Ciphertext와 key lifecycle이 graph, version, preview와 worker payload에 전파된다. Secret storage와 workflow definition의 책임을 분리하지 못하므로 채택하지 않았다.

### Legacy inline password 임시 허용

호환 fallback은 노출 경계를 계속 유지하고 권한 회수도 보장하지 못한다. 보안 이슈의 완료 조건과 충돌하므로 채택하지 않았다.

## 결과

- Mail credential용 model, permission rows, API/service와 runtime resolver가 추가된다.
- Mail node를 편집하려면 safe credential option을 선택해야 한다.
- 기존 inline secret Mail workflow는 관리자가 credential을 등록하고 graph를 갱신하기 전까지 실행되지 않는다.
- Workflow 저장뿐 아니라 Agent Builder apply/save, deployment snapshot 생성과 기존 deployment 활성화에서도 중첩 graph를 포함한 같은 Mail graph allowlist를 적용한다. Draft의 unresolved reference는 허용하지만 deployment 생성·활성화에서는 차단한다.
- Gmail OAuth token refresh와 draft 생성은 이 ADR의 확장 지점이지만 MBA-216 구현 범위는 아니다.
- 여러 외부 provider credential을 범용화하려면 별도 ADR과 migration이 필요하다.
- Key rotation은 Gateway와 Worker 양쪽에 구키·신키 keyring 배포, 구키 복호화 확인, active version 전환, 신규 ciphertext 확인 순서로 수행한다. 구키 제거는 해당 version row가 없음을 확인한 뒤에만 허용한다.
