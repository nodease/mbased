# Connectors API Spec

Status: Draft
Verified Against: feature/mba-302 @ b2d6467002b7becf1daa0badfe6fc155b3edaa57

기본 경로: `/api/v1`

## Endpoints

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| POST | `/connectors/test` | 기본 public PostgreSQL 또는 development exact-local PostgreSQL 연결 정보를 저장하지 않고 실제 접속 가능 여부를 테스트한다. | `auth_token`, active `X-Organization-Id` |
| POST | `/connectors` | DB/SSH 연결을 테스트한 뒤 secret을 암호화해 `connections`에 저장한다. | `auth_token` 쿠키 필요 |
| DELETE | `/connectors/{connection_id}` | Owner Connection을 잠그고 committed Knowledge document reference가 없을 때 삭제한다. | `auth_token` 쿠키 및 owner |
| GET | `/connectors/{connection_id}` | 저장된 connection 상세를 조회한다. Secret은 반환하지 않는다. | `auth_token` 쿠키 및 owner |
| GET | `/connectors/{connection_id}/schema` | 짧은 owner precheck와 독립 runtime snapshot을 거쳐 저장된 DB schema를 조회한다. | `auth_token` 쿠키 및 owner |

## Request And Response Models

### `POST /connectors/test`

요청 본문: `ConnectorTestRequest`. 인증과 organization scope 확인 뒤 actual body 32 KiB와 전체 receive 5초를 적용한다.

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `connection_name` | `string (1..100)` | 예 | 앞뒤 공백을 제거한 연결 식별용 별칭이다. 공백뿐인 값은 `422`이며 저장되지 않는다. |
| `type` | literal `"postgres"` | 예 | 다른 타입은 `422 validation.failed`다. |
| `host` | `string` | 예 | DB host이다. |
| `port` | `integer (1..65535)` | 아니오 | 기본값은 `5432`다. 서버의 deployment-managed allowlist 밖 port는 `connector.target_not_allowed`로 실패한다. |
| `database` | `string` | 예 | DB 이름이다. |
| `username` | `string` | 예 | DB 사용자명이다. |
| `password` | `string` | 예 | DB 비밀번호이다. 테스트 요청에서는 저장하지 않는다. |
| `ssh` | `SSHConfig \| null` | 아니오 | Legacy request shape를 파싱하지만 `enabled=true`는 network 전에 safe 실패다. |

`SSHConfig`:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `enabled` | `boolean` | 아니오 | 기본값은 `false`이다. |
| `host` | `string \| null` | 아니오 | SSH host이다. |
| `port` | `integer` | 아니오 | 기본값은 `22`이다. |
| `username` | `string \| null` | 아니오 | SSH 사용자명이다. |
| `auth_type` | `"password" \| "key"` | 아니오 | 기본값은 `"password"`이다. |
| `password` | `string \| null` | 아니오 | password 인증용 SSH secret이다. |
| `private_key` | `string \| null` | 아니오 | key 인증용 SSH private key 원문이다. |

성공/실패 응답: `200 OK`, `DBConnectionTestResponse`.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `success` | `boolean` | 연결 성공 여부이다. |
| `message` | `string` | Server-owned static 사용자 표시용 메시지다. Raw target/driver detail을 포함하지 않는다. |
| `reason_code` | `string \| null` | 실패 시 allowlist canonical reason code다. |

Expected target/SSH/connection 실패는 `200 OK`, `success=false`로 반환한다. Schema, 인증, organization, ingress와 admission 오류는 아래 HTTP 오류 계약을 따른다.

처리 순서:

1. 로그인과 active organization membership을 검증하고 공통 trusted-proxy resolver로 request network identity를 계산한다. Forwarded header는 설정된 trusted proxy peer에서만 사용하며 identity를 해석할 수 없으면 body와 probe 전에 `503 connector.admission_unavailable`로 닫는다.
2. 최외곽 ASGI middleware는 `/api/v1/connectors/test`와 단일 trailing-slash 동치 경로의 query 전체를 access-log-visible scope에서 제거하고 query 존재 boolean marker를 남긴다. Repository Nginx도 두 경로를 하나의 bounded secret-ingress location으로 처리하며 child path는 포함하지 않는다. Endpoint는 marker 또는 남은 query가 있으면 `400 connector.test_payload_invalid`로 닫고, 그 외 actual body, media type, UTF-8 JSON object와 strict field를 검증한다.
3. Port가 서버의 deployment-managed allowlist에 있는지 확인하고 process-local executor slot을 non-blocking 예약한다. Slot이 없으면 Redis rate를 소비하지 않고 `429 connector.test_busy`로 닫는다.
4. Redis에서 user/organization/network rate와 global/organization/user concurrency lease를 원자적으로 획득한다. Acquire, renew, release 각각에 Connector 전용 operation deadline을 적용하고 timeout은 `503 connector.admission_unavailable`로 닫는다. Acquire 실패 또는 request 취소 시 시작하지 않은 local 예약은 즉시 반환한다.
5. 기본 경로는 host의 전체 DNS 결과가 public인지 검사한다. Development exact-local target은 서버 설정의 정확한 hostname+port와 일치하고 모든 DNS 결과가 RFC1918, IPv6 ULA 또는 loopback일 때만 허용한다. 두 경로 모두 validated IP 하나로 연결을 고정한다.
6. Public target은 시스템 CA bundle, exact-local target은 서버가 설정한 전용 CA file을 명시해 TLS `verify-full`, connect 5초, statement 3초, API 10초 안에서 read-only `SELECT 1`을 한 번 수행한다. 선택된 CA가 없으면 startup 또는 DNS 전에 safe failure로 닫는다.
7. Actual work 중 owner-safe heartbeat로 lease를 연장한다. Safe timeout 응답 뒤에도 probe가 20초 hard deadline 전에 끝나면 completion까지 lease를 유지한다. Hard deadline에 도달하면 heartbeat를 중단하고 owner lease를 해제하며 async probe를 취소한다. 취소할 수 없는 driver thread의 local executor slot은 실제 종료 전까지 재사용하지 않는다.

Initial admission limits:

| Scope | Rate / concurrency |
| --- | --- |
| User | aligned 60초당 5 / active 1 |
| Organization | aligned 60초당 30 / active 4 |
| Request network | aligned 60초당 20 |
| Global | active 16 |

Port allowlist는 `CONNECTOR_TEST_ALLOWED_PORTS`의 중복 없는 `1..65535` 정수 1~16개다. 기본·production과 Docker-service demo는 `5432`, host-run demo는 `5432,55432`를 사용한다. 이 설정은 서버/Helm 소유이며 request body로 확장할 수 없다.

Trusted-local target은 port allowlist를 대체하지 않는다. `CONNECTOR_TEST_LOCAL_PROFILE_ENABLED=true`, `NODE_ENV=development`, `CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS`, `CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE` 네 조건을 모두 만족해야 한다. Flag가 없거나 false인데 target/CA가 있거나, flag만 있거나, production에서 세 local setting 중 하나라도 있으면 Gateway는 시작하지 않는다. Target 목록은 쉼표로 구분한 exact canonical hostname+port 1~4개이고 wildcard, CIDR, suffix와 raw IP target은 허용하지 않는다.

Security environment settings:

| 환경변수 | 기본값 | 허용 범위/관계 |
| --- | --- | --- |
| `CONNECTOR_TEST_RATE_WINDOW_SECONDS` | `60` | `1..300` |
| `CONNECTOR_TEST_USER_RATE_LIMIT` | `5` | `1..100`, organization rate 이하 |
| `CONNECTOR_TEST_ORGANIZATION_RATE_LIMIT` | `30` | `1..1000` |
| `CONNECTOR_TEST_NETWORK_RATE_LIMIT` | `20` | `1..1000` |
| `CONNECTOR_TEST_USER_CONCURRENCY_LIMIT` | `1` | `1..128`, organization/global 이하 |
| `CONNECTOR_TEST_ORGANIZATION_CONCURRENCY_LIMIT` | `4` | `1..128`, global 이하 |
| `CONNECTOR_TEST_GLOBAL_CONCURRENCY_LIMIT` | `16` | `1..128` |
| `CONNECTOR_TEST_CONNECT_TIMEOUT_SECONDS` | `5` | `1..10`, API timeout 미만 |
| `CONNECTOR_TEST_STATEMENT_TIMEOUT_SECONDS` | `3` | `1..10`, API timeout 미만 |
| `CONNECTOR_TEST_RESPONSE_TIMEOUT_SECONDS` | `10` | finite `0 < value <= 30`, connect/statement보다 크고 lease보다 작음 |
| `CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS` | `20` | finite `API timeout < value <= 60` |
| `CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS` | `1` | finite `0 < value <= 5`, API timeout 및 lease TTL의 3분의 1보다 작음 |
| `CONNECTOR_TEST_LEASE_TTL_SECONDS` | `30` | `1..120`, API timeout보다 큼 |
| `CONNECTOR_TEST_REDIS_URL` | unset | 선택적 Connector admission 전용 `redis`/`rediss` URL. Host와 logical DB `0..255` path가 필요하며 query/fragment는 허용하지 않는다. 미설정 시 platform async Redis를 사용한다. |
| `CONNECTOR_TEST_ADMISSION_HMAC_KEY` | local-only fallback | Production에서 별도 32 byte 이상 key 필수. Helm은 required Secret, Docker Compose는 외부 값의 Gateway passthrough를 사용하며 tracked 예시에 실제 값을 두지 않음 |
| `CONNECTOR_TEST_ALLOWED_PORTS` | `5432` | 중복 없는 port 1~16개. Local target port도 포함 |
| `CONNECTOR_TEST_LOCAL_PROFILE_ENABLED` | `false` | `true`/`false`만 허용. `true`는 development exact-local target과 CA를 모두 요구 |
| `CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS` | unset | `NODE_ENV=development` 전용 exact `host:port` 1~4개 |
| `CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE` | unset | 64 KiB 이하, 현재 유효한 단일 PEM `CA:TRUE` 공개 certificate. Private key와 certificate bundle 금지 |

Invalid security setting은 Gateway startup을 실패시킨다. Helm 배포는 `secrets.connectorTestAdmissionHmacKey`로 별도 key를 제공한다. Production Docker Compose는 `.env` 또는 shell에서 받은 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 Gateway 컨테이너로 전달하되, 값이 비어 있으면 development fallback만 허용하고 production runtime startup은 실패한다.

Local demo profile은 다음 두 target을 각각 사용한다.

| Gateway 실행 위치 | 입력 host/port | CA ownership |
| --- | --- | --- |
| Host-run Gateway | `localhost:55432` | `local/connector-test-tls/dev/ca.crt`의 runtime-generated 공개 CA |
| Docker Gateway | `connector-test-postgres:5432` | container에 read-only mount된 runtime-generated 공개 CA |

CA signing key는 one-shot init container의 임시 filesystem에서만 사용하고 종료 전에 제거한다. Server TLS material, PostgreSQL bootstrap admin credential, Connector demo credential은 분리된 private volume에 두며 API와 Gateway mount에는 포함하지 않는다. One-shot verifier와 clipboard helper에는 Connector demo credential만 제공하고 stdout에 출력하지 않는다.

Host-run demo는 선택적으로 `CONNECTOR_TEST_REDIS_URL`을 loopback-published demo Redis DB 15로 설정한다. Docker demo override는 Gateway에 `redis://connector-test-redis:6379/15`를 명시해 실제 admission도 전용 Redis를 사용한다. 이 override는 workflow, pub/sub와 Celery가 사용하는 platform Redis 설정을 변경하지 않으며, Gateway shutdown은 자신이 생성한 Connector 전용 Redis client만 닫는다.

### `POST /connectors`

요청 본문: `DBConnectionTestRequest`.

인증 입력: `auth_token` 쿠키.

`connection_name`은 서버에서 앞뒤 공백을 제거한 뒤 1~100자여야 한다. 빈 문자열, 공백뿐인 값, 100자를 넘는 값은 `422`이며 저장 전 probe와 connection row 생성은 수행하지 않는다.

동작:

1. `type`이 지원 DB 타입인지 확인한다.
2. `ssh.enabled=false`이면 SSH 설정을 비활성화한다.
3. adapter `check`를 threadpool에서 실행하며 10초 timeout을 적용한다.
4. 접속 테스트가 성공하면 DB password와 SSH password/private key를 암호화한다.
5. `connections` row를 현재 사용자 소유로 저장한다.
6. `connection.create` audit action을 기록한다.

성공 응답: `201 Created`.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | `string` | 생성된 connection id이다. |
| `success` | `boolean` | 성공 시 `true`이다. |
| `message` | `string` | 사용자 표시용 메시지이다. |

예시:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "success": true,
  "message": "연결 정보가 안전하게 저장되었습니다."
}
```

### `GET /connectors/{connection_id}`

요청 본문: 없음.

인증 입력: `auth_token` 쿠키.

성공 응답: `200 OK`, `DBConnectionDetailResponse`.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | `string` | connection id이다. |
| `connection_name` | `string` | 연결 별칭이다. |
| `type` | `string` | DB 타입이다. |
| `host` | `string` | DB host이다. |
| `port` | `integer` | DB port이다. |
| `database` | `string` | DB 이름이다. |
| `username` | `string` | DB 사용자명이다. |
| `ssh` | `object \| null` | SSH 사용 시 secret을 제외한 SSH metadata이다. |

`ssh` 객체는 `enabled`, `host`, `port`, `username`, `auth_type`만 포함한다. DB password, SSH password, SSH private key, encrypted secret은 반환하지 않는다.

Client의 `connectorApi.getConnectionDetails`는 이 wire shape를 form용 `DBConfig`로 변환한다. `connection_name`은 `connectionName`, `ssh.auth_type`은 `ssh.authType`으로 바꾸고 응답에 없는 secret 입력은 빈 값으로 둔다.

### `DELETE /connectors/{connection_id}`

요청 본문: 없음.

인증 입력: `auth_token` 쿠키.

동작:

1. 현재 사용자가 owner인 Connection row를 PostgreSQL local 2초 timeout으로 잠근다.
2. Committed Knowledge Document metadata의 canonical/legacy Connection reference를 재검사한다.
3. Reference가 없으면 같은 transaction에서 Connection을 삭제한다.
4. Lock timeout/deadlock/serialization failure는 transaction을 rollback하고 `503 connection.reference_busy`로 닫는다. 같은 transaction의 부분 상태를 재사용하거나 내부에서 자동 재시도하지 않는다.

성공 응답: `204 No Content`.

### `GET /connectors/{connection_id}/schema`

요청 본문: 없음.

인증 입력: `auth_token` 쿠키.

동작:

1. 짧은 request transaction에서 connection 존재와 현재 사용자 owner 여부를 확인해 기존 404/403 관리 계약을 적용하고 transaction을 rollback한다.
2. 독립 runtime snapshot session에서 owner를 다시 확인하고 DB password와 필요한 SSH secret을 최소 immutable DTO로 복호화한 뒤 session을 닫는다.
3. Snapshot session 종료 뒤 adapter의 read-only, connect/statement-timeout schema introspection을 호출한다.

성공 응답: `200 OK`.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `tables` | `SchemaTable[]` | 조회된 테이블 목록이다. |

`SchemaTable`:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `table_name` | `string` | 테이블명이다. |
| `columns` | `SchemaColumn[]` | 컬럼 목록이다. |
| `foreign_keys` | `ForeignKey[]` | FK 목록이다. 현재 PostgreSQL connector는 첫 번째 constrained/referred column을 기록한다. |

`SchemaColumn`:

| 필드 | 타입 |
| --- | --- |
| `name` | `string` |
| `type` | `string` |

`ForeignKey`:

| 필드 | 타입 |
| --- | --- |
| `column` | `string` |
| `referenced_table` | `string` |
| `referenced_column` | `string` |

예시:

```json
{
  "tables": [
    {
      "table_name": "users",
      "columns": [
        { "name": "id", "type": "UUID" },
        { "name": "email", "type": "VARCHAR" }
      ],
      "foreign_keys": []
    }
  ]
}
```

## Errors

HTTP 예외는 Gateway 공통 `detail` 응답을 사용하고, 검증 오류는 Gateway 공통 검증 오류 envelope를 따른다.

구현된 connector 오류 사례:

| 상태 | 엔드포인트 | 상세 / 본문 | 조건 |
| --- | --- | --- | --- |
| 200 | `POST /connectors/test` | `success=false`, `connector.ssh_probe_not_supported` | SSH-enabled test다. Network는 열지 않는다. |
| 200 | `POST /connectors/test` | `success=false`, `connector.target_not_allowed` | Public/port/host target policy를 통과하지 못한다. |
| 200 | `POST /connectors/test` | `success=false`, `connector.connection_timeout` | API probe deadline을 초과한다. |
| 200 | `POST /connectors/test` | `success=false`, `connector.connection_failed` | Driver 연결/검증이 실패한다. |
| 400 | `POST /connectors/test` | `organization.required` | `X-Organization-Id`가 없다. |
| 400 | `POST /connectors/test` | `connector.test_payload_invalid` | Invalid length/UTF-8/JSON/root/disconnect다. |
| 401 | `POST /connectors/test` | Auth 공통 envelope | 로그인 정보가 없거나 유효하지 않다. |
| 404 | `POST /connectors/test` | `resource.not_found` | Active organization scope 밖이다. |
| 408 | `POST /connectors/test` | `connector.test_payload_timeout` | Body receive 5초를 초과한다. |
| 413 | `POST /connectors/test` | `connector.test_payload_too_large` | Declared 또는 actual body가 32 KiB를 초과한다. |
| 415 | `POST /connectors/test` | `connector.test_media_type_not_supported` | 단일 JSON/identity media 계약을 위반한다. |
| 422 | `POST /connectors/test` | `validation.failed` | Organization UUID 또는 strict request field가 유효하지 않다. |
| 429 | `POST /connectors/test` | `connector.test_rate_limited` | Rate를 초과한다. `Retry-After`를 포함한다. |
| 429 | `POST /connectors/test` | `connector.test_busy` | Distributed/local concurrency가 가득 찼다. `Retry-After`를 포함한다. |
| 503 | `POST /connectors/test` | `connector.admission_unavailable` | Redis 또는 trusted transport identity를 사용할 수 없다. |
| 400 | `POST /connectors` | `지원하지 않는 DB타입입니다.` | 지원하지 않는 DB 타입이다. |
| 400 | `POST /connectors` | Safe timeout/connection message 또는 `connector.connection_failed` | 저장 전 접속 테스트가 실패하거나 timeout/adapter 예외가 발생한다. |
| 500 | `POST /connectors` | `connection_config.encrypt_failed` | secret 암호화에 실패한다. |
| 404 | `DELETE /connectors/{connection_id}` | `resource.hidden` | Connection이 없거나 current user owner가 아니다. |
| 409 | `DELETE /connectors/{connection_id}` | `connection.in_use` | Committed Knowledge Document reference가 남아 있다. |
| 503 | `DELETE /connectors/{connection_id}` | `connection.reference_busy` | Bounded row lock timeout, deadlock victim 또는 serialization failure다. 새 요청/transaction에서만 재시도할 수 있다. |
| 503 | `DELETE /connectors/{connection_id}` | `connection.delete_unavailable` | 기타 Connection lifecycle 저장소 장애다. |
| 404 | `GET /connectors/{connection_id}`, `GET /connectors/{connection_id}/schema` | `Connection not found` | connection id를 찾을 수 없다. |
| 403 | `GET /connectors/{connection_id}`, `GET /connectors/{connection_id}/schema` | `Not authorized` | connection owner가 아니다. |
| 404 | `GET /connectors/{connection_id}/schema` | `resource.hidden` | 관리 precheck 뒤 owner가 변경·삭제되어 runtime snapshot 재검증이 실패한다. |
| 503 | `GET /connectors/{connection_id}/schema` | `connection.reference_unavailable` | 관리 precheck 또는 runtime snapshot 저장소 조회가 일시적으로 실패한다. |
| 500 | `GET /connectors/{connection_id}/schema` | `connection_config.decrypt_failed` | 저장된 secret 복호화에 실패한다. |
| 400 | `GET /connectors/{connection_id}/schema` | `Unsupported DB type` | 저장된 connection type에 맞는 adapter가 없다. |
| 400 | `GET /connectors/{connection_id}/schema` | `connector.schema_fetch_failed` | schema introspection이 실패한다. |
| 422 | `POST /connectors/test`, `POST /connectors` | 검증 오류 envelope | 요청 본문이 Pydantic 검증에 실패한다. |

Connector test의 429 `Retry-After`는 Redis state 또는 local busy 정책에서 계산한 1..60초 값이다. 설정된 credentialed CORS origin에는 `Access-Control-Expose-Headers: Retry-After`를 반환해 브라우저 Client가 bounded cooldown을 적용할 수 있게 한다. 이 노출은 origin allowlist를 확장하지 않는다. `POST /connectors`, 상세 조회, schema 조회의 인증 실패 응답은 Auth 공통 dependency의 `auth_token` 쿠키 검증 결과를 따른다.

## Permissions

- `POST /connectors/test`는 `get_current_user`와 active `X-Organization-Id` membership을 요구한다. Active member/manager 모두 test할 수 있다.
- `POST /connectors`는 `auth_token` 쿠키로 현재 사용자를 식별해야 하며, 생성된 row의 `user_id`는 현재 사용자 ID이다.
- `DELETE /connectors/{connection_id}`는 current user owner만 허용하고 참조 중인 Connection 삭제를 거부한다.
- `GET /connectors/{connection_id}`와 `GET /connectors/{connection_id}/schema`는 current user가 `connections.user_id`와 같을 때만 허용한다.
- 현재 connectors API는 organization/team resource permission table을 사용하지 않는다.
- 현재 `connections`에는 `organization_id`가 없으므로 workflow/KB 권한이 connection 사용 권한을 자동으로 대체하지 않는다.
- Connection 생성은 `connection.create`, admitted 연결 테스트 결과는 `connection.test` audit action으로 기록된다. Test audit에는 organization/actor/result/reason/coarse duration만 허용한다.
- Knowledge source connector와 KB retrieval 권한은 Knowledge feature 책임이다.

## Target Knowledge Connector API Boundary

현재 `/connectors/*` DB endpoint는 목표 Knowledge Source Connector API 계약이 아니다. Knowledge source connector endpoint가 추가될 경우 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)의 allowlist operation, runtime authorization primitive, raw payload 비저장, safe reason code, source subject mapping fail-closed 규칙을 따라 별도 API/test 계약으로 고정한다.
