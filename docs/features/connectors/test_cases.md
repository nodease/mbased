# Connectors Test Cases

Status: Draft
Verified Against: feature/mba-302 @ b2d6467002b7becf1daa0badfe6fc155b3edaa57

## Minimum Failure Rule

이 문서는 정상 시나리오를 길게 반복하지 않고, 각 connectors 조건을 깨뜨리는 최소 입력, 상태, 또는 관찰값을 기준으로 테스트 케이스를 정의한다.

각 테스트는 해당 최소 조건 하나만으로 실패를 유도하거나, 성공 경로의 필수 관찰값 하나가 빠졌을 때 실패로 판단할 수 있어야 한다.

## Scope Boundary

이 문서는 현재 구현된 workflow DB connector의 연결 테스트, 연결 저장, 상세 조회, schema 조회, client 호출 경계, connection secret 보호만 직접 검증한다.

다음은 connectors test case의 직접 검증 범위가 아니다. 해당 API와 component 계약이 확정되는 feature 문서에서 별도로 검증한다.

- Knowledge source sync/preview/fetch lifecycle
- HTTP/URL/object-storage adapter egress 세부 정책
- `/api/v1/rag/proxy/preview`와 URL 기반 upload/preview 이관
- connector health/remediation 운영 화면
- KB content retrieval, raw access, compliance access 권한

## Unit Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-U001 | 지원 DB 타입은 Gateway `SupportedDBType` 기준이어야 한다. | `type="mysql"` 하나만 전달한다. | test는 `success=false`, create는 `400`. |
| CONN-TC-U002 | PostgreSQL connector check는 실제 연결 확인 쿼리를 실행해야 한다. | DB 연결은 되지만 `SELECT 1` 실행이 실패한다. | 연결 테스트 실패. |
| CONN-TC-U003 | SSH disabled 설정은 adapter에 전달되지 않아야 한다. | `ssh.enabled=false`인데 adapter config에 SSH tunnel 설정이 남는다. | 테스트 실패. |
| CONN-TC-U004 | SSH key 인증은 private key를 사용해야 한다. | `auth_type="key"`인데 password auth로 tunnel을 만든다. | 테스트 실패. |
| CONN-TC-U005 | SSH password 인증은 password를 사용해야 한다. | `auth_type="password"`인데 private key auth로 tunnel을 만든다. | 테스트 실패. |
| CONN-TC-U006 | 저장되는 DB/SSH secret은 원문과 구분되어야 한다. | `encrypted_password`, `encrypted_ssh_password`, `encrypted_ssh_private_key` 중 하나가 입력 원문과 같다. | 테스트 실패. |
| CONN-TC-U007 | 상세 응답 모델은 secret 필드를 포함하지 않아야 한다. | response model에 `password`, `private_key`, `encrypted_password`, `encrypted_ssh_private_key` 중 하나가 있다. | 테스트 실패. |
| CONN-TC-U008 | PostgreSQL schema introspection은 table, column, FK shape를 보존해야 한다. | 테이블이 있는데 `table_name`, column `name/type`, FK `referenced_table/referenced_column` 중 하나가 빠진다. | 테스트 실패. |
| CONN-TC-U009 | audit listener는 `Connection` 민감 필드를 마스킹 대상으로 등록해야 한다. | host/database/username/encrypted secret 필드 중 하나가 sensitive set에서 빠진다. | 테스트 실패. |
| CONN-TC-U010 | PostgreSQL 직접 연결은 검증된 public IP로 연결 대상을 고정해야 한다. | 사전 DNS 검증 후 SQLAlchemy/driver가 hostname을 다시 해석하도록 둔다. | 테스트 실패. |
| CONN-TC-U011 | PostgreSQL 기본 public profile은 private, loopback, link-local, metadata, reserved target을 거부해야 한다. | Trusted-local 설정 없이 `127.0.0.1`, `10.0.0.0/8`, metadata IP 중 하나로 연결을 연다. | safe reason code로 실패. |
| CONN-TC-U012 | PostgreSQL schema introspection은 table/column/FK cap을 적용해야 한다. | cap 초과 schema가 truncation marker 없이 전체 반환된다. | 테스트 실패. |
| CONN-TC-U013 | DB row fetch는 SELECT-only guard와 dangerous function blocklist를 적용해야 한다. | `pg_sleep`, `pg_read_file`, `dblink`, `COPY`, 복수 statement 중 하나가 통과한다. | `adapter.sql_not_allowed`. |
| CONN-TC-U014 | DB row fetch는 read-only transaction, statement timeout, batch size cap, total row cap을 적용해야 한다. | 사용자 SELECT가 cap 없이 실행되거나 row cap 초과를 부분 성공으로 반환한다. | safe reason code로 실패. |
| CONN-TC-U015 | `PostgresConnector()` 기본 생성자는 Knowledge-safe 경계로 SSH tunnel을 거부해야 한다. | 기본 생성자가 SSH tunnel을 연다. | `adapter.ssh_tunnel_not_allowed`. |
| CONN-TC-U016 | Workflow connector API는 기존 SSH tunnel compatibility를 명시적으로 선택해야 한다. | workflow connector API가 기본 생성자를 사용해 문서화된 SSH tunnel 설정을 깨뜨리거나, Knowledge ingestion까지 tunnel을 열어 둔다. | 테스트 실패. |
| CONN-TC-U017 | Redis admission test namespace는 bounded safe token이어야 한다. | Empty, leading/trailing delimiter, uppercase, whitespace, caller-supplied hash tag, 65자 이상 namespace를 전달한다. | Adapter construction 실패, production key namespace 영향 없음. |
| CONN-TC-U018 | Concurrency 거부는 rate를 부분 소비하지 않아야 한다. | 같은 user lease가 active인 상태에서 두 번째 request가 busy로 거부된 뒤 첫 lease를 해제하고 rate limit까지 재시도한다. | Busy request는 rate counter를 증가시키지 않고 다음 정상 acquire가 허용된다. |
| CONN-TC-U019 | Redis acquire/renew/release는 각각 무응답 deadline을 가져야 한다. | 취소 전까지 영원히 반환하지 않는 fake Redis로 세 operation을 각각 호출한다. | 설정된 operation deadline 안에 `connector.admission_unavailable`; 호출 task와 API가 무기한 대기하지 않음. |
| CONN-TC-U020 | Connection Use Resolver는 ID와 execution subject owner predicate를 한 query에서 평가해야 한다. | UUID만 조회하거나 owner check를 caller가 별도로 수행한다. | non-owner/missing/malformed 모두 `resource.hidden`. |
| CONN-TC-U021 | Background DB processor는 Connection을 use 직전에 다시 확인해야 한다. | 저장 뒤 Connection 삭제 또는 owner 변경 후에도 adapter를 호출한다. | adapter 0회, safe configuration failure. |
| CONN-TC-U022 | Credential 복호화 실패는 저장값 fallback 없이 닫혀야 한다. | 암호문을 password/private key로 adapter에 전달한다. | adapter 0회, raw credential/detail 비노출. |
| CONN-TC-U023 | Connection Use Resolver 저장소 조회 실패는 typed unavailable로 정규화해야 한다. | Raw SQLAlchemy/driver 오류가 Gateway 500, processor result 또는 log에 노출된다. | Gateway는 safe `503 connection.reference_unavailable`, processor는 `source.temporarily_unavailable`, adapter 0회. |
| CONN-TC-U024 | Resolver는 dial 시작 시점의 최신 owner를 재조회하되 runtime row lock을 소유하지 않아야 한다. | 동일 Session의 stale owner를 재사용하거나 외부 I/O 동안 Connection mutation을 불필요하게 차단한다. | PostgreSQL owner 변경을 다음 resolve가 반영하며 runtime lock protocol은 MBA-302 테스트로 분리. |
| CONN-TC-U025 | Runtime snapshot provider는 독립 session에서 owner·credential projection을 완료하고 session 종료 뒤에만 adapter를 호출해야 한다. | Caller/request session ORM을 반환하거나 snapshot session active 상태에서 connector를 생성한다. | Adapter 호출 시 snapshot session closed, immutable safe DTO만 전달. |
| CONN-TC-U026 | Snapshot provider의 hidden/configuration/store failure는 session과 transaction을 남기지 않아야 한다. | Error 뒤 checked-out session 또는 active transaction이 남는다. | Typed safe failure, adapter 0회, session release. |
| CONN-TC-U027 | Snapshot DTO와 processor result는 repr/serialization에 Connection identity와 credential을 포함하지 않아야 한다. | ID/name/host/username/ciphertext/plaintext 중 하나가 노출된다. | 테스트 실패. |
| CONN-TC-U028 | Reference lock SQLSTATE는 busy와 unavailable로 구분해야 한다. | Lock timeout/deadlock/serialization victim을 generic unavailable로 축소하거나 raw detail을 반환한다. | `connection.reference_busy`, retryable, 전체 rollback. |
| CONN-TC-U029 | PostgreSQL runtime connector는 connect/statement timeout과 row/byte cap을 적용해야 한다. | Connect가 무기한 대기하거나 10,000 row/16 MiB 초과를 일부 성공으로 반환한다. | Safe adapter failure와 resource cleanup. |
| CONN-TC-U030 | Lock 관측 정보는 coarse outcome/wait/hold bucket만 기록해야 한다. | Connection ID/target/SQL/credential 또는 raw driver detail을 label/log에 포함한다. | 테스트 실패. |
| CONN-TC-U031 | Schema 관리 API는 기존 404/403 precheck transaction을 종료하고 독립 runtime snapshot session까지 닫은 뒤 inspector를 호출해야 한다. | Request ORM session 또는 snapshot session이 외부 schema I/O 동안 active다. | 기존 관리 오류 계약 유지, 외부 호출 시 platform transaction 없음. |
| CONN-TC-U032 | Existing Document reference writer는 Connection 다음 Document를 잠그고 expected revision을 재검증해야 한다. | Concurrent settings commit을 stale writer가 덮어쓴다. | 전체 rollback과 non-retryable `connection.reference_conflict`. |
| CONN-TC-U033 | Schema 관리 API는 request-session rollback 전에 current user ID를 scalar로 고정해야 한다. | Rollback 뒤 expired ORM user attribute를 다시 읽어 새 request transaction이 외부 schema I/O 동안 열린다. | User ID 1회 조회, precheck rollback 뒤 독립 snapshot만 사용. |
| CONN-TC-U034 | Runtime snapshot은 stored PostgreSQL identifier와 passwordless SSH compatibility를 보존해야 한다. | database/username 앞뒤 공백을 trim하거나 `encrypted_ssh_password=NULL`을 복호화·configuration failure로 처리한다. | 공백뿐인 값만 거부하고 stored identifier text 유지, SSH password는 `None`, 불필요한 decrypt 0회. |
| CONN-TC-U035 | Reference mutation commit 오류는 lifecycle UoW가 rollback하고 typed safe error로 변환해야 한다. | `40001`/`40P01`/`55P03`/`57014` 또는 일반 SQLAlchemy commit 오류가 500/raw detail로 노출된다. | Transient SQLSTATE는 retryable `connection.reference_busy`, 기타는 `connection.reference_unavailable`, 전체 rollback. |
| CONN-TC-U036 | Production Connector proxy config는 exact internal `3130`, current revision과 bounded deployment port만 받아야 한다. | Missing/public/credential-bearing/path endpoint, stale revision, duplicate/invalid/17개 port를 각각 주입한다. | Startup-safe configuration failure, network dial 0회. |
| CONN-TC-U037 | Connector proxy dial은 검증된 literal public IP와 허용 포트만 사용해야 한다. | Hostname, private/link-local/metadata/mapped address 또는 allowlist 밖 port를 전달한다. | Proxy 연결 0회, `connector.egress_target_not_allowed`. |
| CONN-TC-U038 | CONNECT header 뒤 즉시 도착한 SSH/DB byte를 잃지 않아야 한다. | Proxy 200 header와 SSH banner가 같은 read에 도착한다. | Header까지만 소비하고 banner 전체를 tunnel consumer가 수신. |
| CONN-TC-U039 | Relay/SSH/engine 생성 실패는 열린 socket과 relay를 정리해야 한다. | CONNECT 성공 뒤 SSH start 또는 SQLAlchemy engine 생성이 실패한다. | Resource stop/close 1회, direct retry 0회, raw target/error 비노출. |

## API Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-A001 | `POST /connectors/test`는 필수 필드를 요구해야 한다. | `host`, `database`, `username`, `password`, `connection_name`, `type` 중 하나가 없다. | `422` 검증 오류 envelope. |
| CONN-TC-A002 | `POST /connectors/test`는 지원하지 않는 타입을 저장 없이 거부해야 한다. | `type="mysql"`이다. | `422 validation.failed`, 저장 row 없음. |
| CONN-TC-A003 | `POST /connectors/test` 성공은 저장 row를 만들지 않아야 한다. | adapter check가 true인데 `connections` row count가 증가한다. | 테스트 실패. |
| CONN-TC-A004 | `POST /connectors/test` 실패는 `success=false`를 반환해야 한다. | adapter check가 false이거나 예외를 던진다. | `success=false`. |
| CONN-TC-A005 | `POST /connectors`는 인증을 요구해야 한다. | `auth_token` 쿠키 없이 요청한다. | Auth dependency의 401 응답. |
| CONN-TC-A006 | `POST /connectors`는 지원하지 않는 타입을 거부해야 한다. | 인증된 요청에서 `type="mysql"`이다. | `400`, `지원하지 않는 DB타입입니다.` |
| CONN-TC-A007 | `POST /connectors`는 저장 전 connection check 실패를 row 생성 없이 반환해야 한다. | adapter check가 false를 반환하거나 10초를 초과한다. | `400`, 저장 row 없음. |
| CONN-TC-A008 | `POST /connectors` 성공은 current user owner와 암호화 secret을 저장해야 한다. | `user_id`가 current user와 다르거나 저장 secret이 입력 원문과 같다. | 테스트 실패. |
| CONN-TC-A009 | `POST /connectors` 성공은 생성 id를 반환해야 한다. | `201` 응답에 `id`가 없다. | 테스트 실패. |
| CONN-TC-A010 | `GET /connectors/{id}`는 인증을 요구해야 한다. | `auth_token` 쿠키 없이 요청한다. | Auth dependency의 401 응답. |
| CONN-TC-A011 | `GET /connectors/{id}`는 없는 id를 숨기지 않고 404로 반환해야 한다. | DB에 없는 connection id이다. | `404`, `Connection not found`. |
| CONN-TC-A012 | `GET /connectors/{id}`는 non-owner를 거부해야 한다. | 다른 사용자의 connection id를 요청한다. | `403`, `Not authorized`. |
| CONN-TC-A013 | `GET /connectors/{id}` 성공은 secret을 반환하지 않아야 한다. | 성공 body에 password/private key/encrypted secret이 있다. | 테스트 실패. |
| CONN-TC-A014 | `GET /connectors/{id}/schema`는 인증을 요구해야 한다. | `auth_token` 쿠키 없이 요청한다. | Auth dependency의 401 응답. |
| CONN-TC-A015 | `GET /connectors/{id}/schema`는 없는 id를 숨기지 않고 404로 반환해야 한다. | DB에 없는 connection id이다. | `404`, `Connection not found`. |
| CONN-TC-A016 | `GET /connectors/{id}/schema`는 non-owner를 거부해야 한다. | 다른 사용자의 connection id를 요청한다. | `403`, `Not authorized`. |
| CONN-TC-A017 | `GET /connectors/{id}/schema`는 복호화 실패를 safe 오류로 반환해야 한다. | encrypted password가 복호화할 수 없는 값이다. | `500`, safe `reason_code` 응답. |
| CONN-TC-A018 | `GET /connectors/{id}/schema`는 지원하지 않는 저장 타입을 거부해야 한다. | row의 `type`이 adapter map에 없다. | `400`, `Unsupported DB type`. |
| CONN-TC-A019 | `GET /connectors/{id}/schema` 성공은 tables 배열을 반환해야 한다. | adapter schema 결과가 있는데 response에 `tables`가 없다. | 테스트 실패. |
| CONN-TC-A020 | `POST /connectors/test` 실패는 raw host/database/secret/driver detail을 노출하지 않아야 한다. | adapter가 secret 포함 예외를 던진다. | `success=false`, safe message와 safe `reason_code`. |
| CONN-TC-A021 | `POST /connectors` 저장 전 connection check 실패는 raw host/database/secret/driver detail을 노출하지 않아야 한다. | adapter가 secret 포함 예외를 던진다. | `400`, safe `reason_code`, 저장 row 없음. |
| CONN-TC-A022 | `GET /connectors/{id}/schema` fetch 실패는 raw host/database/secret/driver detail을 노출하지 않아야 한다. | adapter가 secret 포함 예외를 던진다. | `400`, safe `reason_code`. |
| CONN-TC-A023 | Connector test는 인증과 active organization을 network 전에 요구해야 한다. | auth 없음, header 없음/invalid, scope 밖, invited/suspended/removed membership 중 하나다. | `401/400/422/404`, admission/DNS/probe 0회. |
| CONN-TC-A024 | Connector test actual body는 32 KiB와 total 5초로 제한되어야 한다. | Exact/over, missing/duplicate/invalid/understated length, chunked crossing, slow stream을 보낸다. | Exact는 parse, over는 413, invalid는 400, slow는 408; admission/probe 0회. |
| CONN-TC-A025 | Connector test media/JSON은 strict해야 한다. | Wrong/duplicate content type, compressed body, BOM, invalid UTF-8/JSON, NaN, non-object root다. | `400/415`, raw body 비노출. |
| CONN-TC-A026 | SSH-enabled test는 network 전에 거부되어야 한다. | Valid SSH credential shape와 `enabled=true`다. | `200`, `connector.ssh_probe_not_supported`, DNS/probe 0회. |
| CONN-TC-A027 | Public PostgreSQL과 deployment-managed port allowlist만 허용해야 한다. | Allowlist 밖 port, private/loopback/link-local/metadata/CGNAT/reserved/mapped/mixed DNS target이다. | `connector.target_not_allowed`, admission/DNS/DB connect 0회. |
| CONN-TC-A028 | Connector test timeout 뒤 hard deadline 전까지 실제 blocking work와 lease 수명을 일치시켜야 한다. | API 10초를 넘긴 future가 20초 안에 끝나거나 실행 중 heartbeat가 필요하다. | Safe timeout 반환, completion 전 owner-safe renewal 지속, completion 뒤 lease 정확히 1회 해제. |
| CONN-TC-A029 | Admission 장애와 capacity 부족은 fail-closed해야 한다. | Redis timeout/script error, transport peer 없음, distributed/local concurrency full이다. | `429/503`, DNS/DB connect 0회, process-local unlimited fallback 없음. |
| CONN-TC-A030 | Connector test audit는 bounded metadata만 가져야 한다. | Success/target denial/driver failure/timeout이다. | `connection.test`, organization/actor/result/reason/duration만 기록하고 target/credential/network 원문 없음. |
| CONN-TC-A031 | 실제 Redis transport 장애는 API에서 DB probe 전에 닫혀야 한다. | 인증·active organization 요청을 loopback의 미사용 Redis port로 연결한다. | `503 connector.admission_unavailable`, local reservation 1회 예약·1회 반환, probe 호출 0회, secret 비노출. |
| CONN-TC-A032 | Connector 저장 이름은 정규화되고 공백 이름은 거부되어야 한다. | 앞뒤 공백이 있는 이름, 빈 값, whitespace-only, 101자 값을 `POST /connectors` schema에 전달한다. | 유효 이름은 trim되고 나머지는 `422`; 저장 전 probe와 row 생성 0회. |
| CONN-TC-A033 | 응답 뒤 probe가 hard deadline을 넘기면 distributed admission을 유한하게 정리해야 한다. | 취소 전까지 영원히 반환하지 않는 probe가 API timeout과 20초 hard deadline을 모두 넘긴다. | Hard deadline에 async probe/heartbeat 취소, owner lease 정확히 1회 해제, 이후 renewal 0회. 취소 불가능한 실제 driver thread의 local slot은 종료 전 재사용하지 않음. |
| CONN-TC-A034 | Connector network rate identity는 trusted-proxy resolver를 사용해야 한다. | Trusted proxy 뒤 서로 다른 client 요청을 raw socket peer 하나로 묶거나, untrusted peer의 forwarded header를 신뢰한다. | Trusted peer에서는 첫 untrusted client hop의 normalized network를 사용하고 untrusted peer에서는 socket peer를 사용한다. Identity 해석 실패는 body/probe 전 `503`. |
| CONN-TC-A035 | Connector test query는 direct Gateway access log 전에 제거되면서 endpoint에서 거부되어야 한다. | Next rewrite/Uvicorn에 `/api/v1/connectors/test?password=...`를 직접 보낸다. | Downstream·Uvicorn access path에는 query 원문이 없고 presence marker는 유지되며 API는 `400 connector.test_payload_invalid`, probe 0회. Exact path와 trailing slash만 적용되고 child path query는 변경하지 않음. |
| CONN-TC-A036 | Local executor busy는 Redis rate admission을 소비하지 않아야 한다. | 취소 불가능한 driver가 local slot을 점유한 상태에서 반복 요청한다. | 모든 요청은 probe·Redis acquire·Connector audit 0회로 `connector.test_busy`; slot 복구 뒤 정상 요청이 기존 rate budget으로 진행됨. Admission 실패·취소는 미사용 예약을 반환함. |
| CONN-TC-A037 | Browser Client가 429 cooldown header를 읽을 수 있어야 한다. | 허용 CORS origin에서 Connector test가 `429`를 반환한다. | `Retry-After: 1..60`과 `Access-Control-Expose-Headers`의 `Retry-After`가 함께 존재하고 origin allowlist/credentials 계약은 유지됨. |

## Component And Hook Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-C001 | `connectorApi.testConnection`은 `/connectors/test`로 POST해야 한다. | test 호출이 다른 path 또는 GET/PUT으로 나간다. | 테스트 실패. |
| CONN-TC-C002 | `connectorApi.createConnector`는 `/connectors`로 POST해야 한다. | create 호출이 다른 path 또는 GET/PUT으로 나간다. | 테스트 실패. |
| CONN-TC-C003 | `connectorApi.getSchema`는 `/connectors/{id}/schema`로 GET해야 한다. | schema 호출이 다른 path 또는 POST로 나간다. | 테스트 실패. |
| CONN-TC-C004 | `connectorApi.getConnectionDetails`는 `/connectors/{id}`로 GET해야 한다. | detail 호출이 다른 path 또는 POST로 나간다. | 테스트 실패. |
| CONN-TC-C005 | `connectorApi`는 클라이언트 `authType`을 Gateway `auth_type`으로 매핑해야 한다. | `authType="key"`인데 payload `auth_type`이 `"password"`이다. | 테스트 실패. |
| CONN-TC-C006 | `DBConnectionForm` 입력 변경은 부모 `onChange` 호출과 test status 초기화를 해야 한다. | 한 필드 변경 후 `onChange`가 호출되지 않거나 `testStatus`가 `idle`이 아니다. | 테스트 실패. |
| CONN-TC-C007 | `DBConnectionForm` 연결 테스트는 pending/success/error 상태를 표시해야 한다. | 테스트 pending인데 버튼이 활성 상태이거나, 성공/실패 결과 메시지가 표시되지 않는다. | 테스트 실패. |
| CONN-TC-C008 | `DBConnectionForm`은 `Retry-After` cooldown을 bounded 적용해야 한다. | `429` 뒤 즉시 중복 요청하거나 비정상 header가 무제한 disable을 만든다. | `1..60`초만 재시도 비활성화하고 raw error/header를 표시하지 않는다. |
| CONN-TC-C009 | `DBSchemaSelector`는 connection id로 schema를 조회해야 한다. | `connectionId`가 있는데 `connectorApi.getSchema`가 호출되지 않는다. | 테스트 실패. |
| CONN-TC-C010 | `DBSchemaSelector`는 schema 조회 실패를 toast로 표시해야 한다. | `getSchema`가 reject된다. | `테이블 정보를 불러오는데 실패했습니다.` 표시. |
| CONN-TC-C011 | `DBSchemaSelector`는 최대 2개 테이블 제한을 적용해야 한다. | 2개 테이블이 선택된 상태에서 3번째 테이블을 선택한다. | 선택 차단, 제한 toast. |
| CONN-TC-C012 | `DBSchemaSelector`는 FK 있는 2개 테이블 선택 시 join config를 생성해야 한다. | FK metadata가 있는데 `onJoinConfigChange`에 enabled config가 전달되지 않는다. | 테스트 실패. |
| CONN-TC-C013 | DB source UI는 연결 이름을 필수로 검사하고 trim해야 한다. | Whitespace-only 이름으로 저장하거나 앞뒤 공백 이름으로 test/create를 호출한다. | 저장 API와 Knowledge source API는 호출되지 않으며, 유효 이름은 trim된 payload로 전송된다. |
| CONN-TC-C014 | 모든 `DBConnectionForm` 소비자는 구조화된 test result 계약을 지켜야 한다. | Knowledge document 편집 handler가 boolean을 반환해 form의 `success` 판정 또는 Client build가 깨진다. | 편집 성공·실패 모두 `{ success }`를 반환하고 TypeScript build가 통과한다. |
| CONN-TC-C015 | Connection detail은 form용 camelCase shape로 정규화되어야 한다. | Gateway가 `connection_name`, `ssh.auth_type`을 반환한 기존 DB 문서를 연 뒤 다른 필드를 편집한다. | Form의 연결 이름과 SSH auth type이 복원되고 create/test payload에 빈 `connection_name`이 전송되지 않음. Secret은 응답에서 복원하지 않고 빈 재입력 상태임. |

## Permission Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-P001 | `POST /connectors/test`는 active organization member/manager만 호출해야 한다. | 미인증 또는 active scope 밖 사용자가 probe를 시작한다. | Network 전 차단. |
| CONN-TC-P002 | 저장된 connection 상세/schema는 owner mismatch를 거부해야 한다. | 다른 사용자의 connection id로 상세 또는 schema를 요청한다. | `403`, `Not authorized`. |
| CONN-TC-P003 | 현재 user-owned `connections`는 workflow/KB 권한만으로 자동 공유되지 않아야 한다. | workflow/KB 접근 권한만 있는 사용자가 다른 사용자의 connection을 사용한다. | 테스트 실패 또는 403/404. |
| CONN-TC-P004 | Knowledge upload/process/preview는 current user 소유가 아닌 Connection UUID를 저장하거나 사용하지 않아야 한다. | 사용자 A가 사용자 B의 UUID를 제출한다. | document mutation/background 등록 전 `404 resource.hidden`. |
| CONN-TC-P005 | KC/background direct-call은 Gateway 검증을 신뢰하지 않아야 한다. | processor를 타 사용자 UUID와 actor로 직접 호출한다. | connector 생성·dial 0회, safe failure. |

## Edge Cases

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-X001 | `connectorApi`는 connector create/test 실패 시 raw Axios error 객체를 console에 전달하지 않아야 한다. | Axios error의 request config/data에 DB password, SSH password, private key가 포함된다. | Operation과 status만 safe warning으로 남기고 sentinel은 UI/console에 없음. |
| CONN-TC-X002 | Secret 원문은 문서, fixture, audit metadata에 남지 않아야 한다. | DB password, SSH password, private key 원문이 문서, 테스트 fixture, audit metadata 중 하나에서 관찰된다. | 테스트 실패. |
| CONN-TC-X003 | Redis admission은 multi-replica 경쟁에서도 rate/concurrency 상한을 넘지 않아야 한다. | 마지막 slot을 병렬 acquire하거나 wrong owner release, long-running heartbeat, stale lease, clock skew를 만든다. | Redis time/atomic script 기준 정확한 winner, owner-safe renew/release와 process crash/release-failure TTL recovery. |

## MBA-302 PostgreSQL Transaction And Lock Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-TC-PG001 | Writer가 Connection row lock을 보유할 때 contender reference/delete는 bounded wait를 사용해야 한다. | 2초를 넘겨 대기하거나 generic 500으로 끝난다. | `connection.reference_busy`, contender 전체 rollback. |
| CONN-TC-PG002 | Busy transaction 뒤 새 session은 같은 Connection을 정상 조회·mutation할 수 있어야 한다. | Aborted transaction 또는 pool connection이 재사용된다. | 새 session 정상 동작, stale partial state 없음. |
| CONN-TC-PG003 | Writer가 reference를 commit한 뒤 delete는 committed reference를 확인해야 한다. | Lock 경합 뒤 dangling reference를 남기며 Connection을 삭제한다. | `connection.in_use`, Connection과 Document reference 유지. |
| CONN-TC-PG004 | Deadlock victim은 전체 rollback 뒤 새 session에서만 재시도해야 한다. | 같은 session의 partial state를 재사용하거나 raw PostgreSQL detail을 노출한다. | Retryable busy 분류, 새 transaction 정상 동작. |
| CONN-TC-PG005 | 최초 조회 뒤 다른 session이 Document를 갱신하면 reference writer는 Connection -> Document lock 뒤 stale revision을 감지해야 한다. | 마지막 writer가 먼저 commit된 설정을 조용히 덮어쓴다. | `connection.reference_conflict`, stale transaction rollback, 먼저 commit된 값 유지. |
| CONN-TC-PG006 | Runtime snapshot provider가 반환된 뒤 외부 fetch가 지연돼도 snapshot session은 반환돼야 한다. | 외부 DB generator가 끝날 때까지 authorization session이 checked out 상태다. | Snapshot session closed, Connection mutation 불필요한 대기 없음. |
| CONN-TC-X004 | Admission key/member는 opaque해야 한다. | Redis key/hash/zset에 raw organization/user/network ID, host/database/username/password가 관찰된다. | HMAC identity와 random owner token만 존재. |
| CONN-TC-X005 | Strict probe는 validated IP 한 곳에 선택된 server-owned CA file을 명시한 TLS `verify-full`로 한 번만 연결해야 한다. | Multiple DNS, rebinding, first-attempt failure, plaintext/downgrade, system/local CA 누락 또는 SAN mismatch를 유도한다. | Pinned one-attempt, no fallback/retry, target별 CA와 hostname certificate 검증. CA 누락은 startup 또는 DNS 전에 safe failure. |
| CONN-TC-X006 | Port allowlist는 배포 관리자만 bounded 설정할 수 있어야 한다. | Empty token, duplicate, non-integer, `0`, `65536`, 17개 port를 설정하거나 request로 allowlist 밖 port를 보낸다. | Invalid 설정은 startup 실패. Request는 safe target-policy 실패이며 admission/DNS/probe 0회. |
| CONN-TC-X007 | Trusted-local target은 development exact hostname+port만 허용해야 한다. | Unlisted private host, raw IP, wildcard/CIDR/suffix, allowlist 밖 port, mixed public/private DNS를 사용한다. | Network 전 safe target-policy 실패. Exact target의 RFC1918/ULA/loopback 결과만 검증 IP 하나에 pin. |
| CONN-TC-X008 | Local profile은 명시적 flag와 완전한 설정 조합을 요구해야 한다. | Flag 없음/false+target/CA, flag만 있음, target만, CA만, non-development, invalid boolean을 각각 주입한다. | Gateway startup 실패, DNS/Redis/probe 0회. `true`+development+exact target+CA만 허용. |
| CONN-TC-X009 | Production은 모든 local-profile 설정을 거부해야 한다. | `NODE_ENV=production`에서 flag, target, CA를 각각 단독으로 또는 함께 주입한다. | 모든 조합에서 Gateway startup 실패, public-only 경계 유지. |
| CONN-TC-X010 | Local CA startup validation은 공개 root certificate만 받아야 한다. | Empty/invalid PEM, leaf `CA:FALSE`, expired/future CA, certificate 2개, private-key marker, 64 KiB 초과, missing/directory/unreadable path를 각각 제공한다. | DNS 전에 startup 실패. 현재 유효한 단일 PEM `CA:TRUE`만 통과. |
| CONN-TC-X011 | Local CA는 exact target에만 적용해야 한다. | Local profile이 설정된 상태에서 public target을 probe한다. | Public target은 시스템 CA를 사용하고 local CA는 사용하지 않는다. |
| CONN-TC-X012 | Demo PostgreSQL은 TLS-only 최소권한이어야 한다. | Verified TLS, `sslmode=disable`, write DDL, role attribute 조회를 각각 수행한다. | TLS 성공, 평문/DDL 실패, current role은 non-superuser·non-create-role/db·non-replication·non-bypass-RLS·read-only. |
| CONN-TC-X013 | TLS 인증 실패는 downgrade 없이 닫혀야 한다. | Valid but unrelated CA와 CA는 맞지만 SAN이 다른 logical host를 각각 사용한다. | `connector.connection_failed`, 평문 fallback·다른 IP retry 0회, raw TLS detail 비노출. |
| CONN-TC-X014 | Runtime certificate init은 CA signing key를 영속화하지 않아야 한다. | First init, valid-material repeated init, interrupted temp artifact를 점검한다. | CA key는 container temp에만 존재하고 종료 후 volume에 없음. Server key/CA certificate는 match하며 mode `0600/0644`, valid material은 재사용, stale temp는 정리. |
| CONN-TC-X015 | Server TLS, bootstrap admin credential, Connector demo credential은 최소권한으로 분리되어야 한다. | Gateway, PostgreSQL, one-shot verifier mount와 각 credential volume의 파일 목록·mode를 렌더링/검사한다. | Gateway는 공개 CA만, PostgreSQL은 세 private volume을 읽는다. Verifier는 Connector credential만 읽고 server key/admin credential은 읽지 않는다. 두 credential volume에는 각각 기대한 파일 하나만 있고 mode는 `0600`이다. |
| CONN-TC-X016 | Demo service는 default/production 배포에 섞이지 않아야 한다. | Profile 없는 dev Compose, base Docker Compose, Helm values/templates 전체를 렌더링·스캔한다. | Demo service/network/volume/mount/local env가 없고 일반 stack config가 유효하다. |
| CONN-TC-X017 | Host publish와 network 분리는 함께 유지되어야 한다. | Dev/Docker Compose port와 network membership을 검사하고 host에서 실제 연결한다. | PostgreSQL/Redis는 `127.0.0.1`에만 publish되고 demo 전용 bridge를 사용하며 Docker PostgreSQL은 platform `moduly-network`에 직접 연결되지 않는다. |
| CONN-TC-X018 | 실제 Redis test cleanup은 무관한 key를 보존해야 한다. | 같은 DB에 sentinel key를 둔 뒤 rate/concurrency/owner/TTL test와 cleanup을 실행한다. | Test namespace만 삭제되고 sentinel은 유지된다. `FLUSHDB`/`FLUSHALL` 호출 0회. |
| CONN-TC-X019 | 실제 Redis atomic acquire는 partial state를 만들지 않아야 한다. | Last-slot race, cross-adapter global/user/org concurrency, busy-then-release, wrong/duplicate owner release, TTL recovery를 실행한다. | 정확한 winner/cap, busy는 rate 미소비, wrong/duplicate release는 다른 lease에 영향 없음, TTL 뒤 복구. |
| CONN-TC-X020 | Host API와 Docker service-name 경로가 모두 실제 dependency를 사용해야 한다. | Host ASGI API는 실제 Redis+`localhost:55432`, one-shot Docker verifier는 `connector-test-redis`+`connector-test-postgres:5432`를 사용한다. | 두 경로 모두 TLS `verify-full`/`SELECT 1` 성공, output은 canonical status만 포함. |
| CONN-TC-X021 | Demo 재시작은 readiness race 없이 복구되어야 한다. | Certificate init을 반복하고 PostgreSQL을 재시작한 뒤 health와 probe를 확인한다. | Init completion→PostgreSQL health 순서를 지키고 valid certificate 재사용, probe retry 없이 다음 호출 성공. |
| CONN-TC-X022 | Local generated material은 source와 image build context에 들어가지 않아야 한다. | Git tracked files, `.dockerignore`, public bind directory, image source를 검사한다. | `local/`은 build context 제외, tracked private material 0건, public directory에는 `ca.crt` 하나만 존재. |
| CONN-TC-X023 | Credential init은 손상·중단 artifact를 안전하게 복구해야 한다. | Empty/malformed/wrong-mode credential, stale `.password.*`, 반대 volume의 known credential file을 각각 주입하고 init을 반복한다. | 유효 credential은 값 보존+`0600` 복구, invalid credential은 원자 교체, stale/cross artifact 제거. 각 volume에는 기대 파일 하나만 남고 원문 출력은 없다. |
| CONN-TC-X024 | Credential/data volume을 부분 삭제해도 silent fallback하지 않아야 한다. | Running demo에서 Connector credential volume만 제거해 health/probe를 확인하고, 별도로 bootstrap admin credential volume만 제거해 노출 surface를 검사한다. | Connector credential 불일치는 health/probe에서 fail-closed한다. Regenerated bootstrap file은 기존 DB admin credential로 간주하지 않으며 verifier/Gateway에 노출되지 않는다. 어떤 경우에도 default/empty credential, plaintext, superuser fallback은 없고 복구는 runbook의 demo data+credential project-scope reset을 따른다. |
| CONN-TC-X025 | Port allowlist 기본값은 실행 위치의 실제 network port만 포함해야 한다. | Helm default/local/production, base Docker, Docker env example, host-run env example을 대조한다. | Helm·base Docker·Docker-service는 `5432`만, host-run은 `5432,55432`만 허용하며 관성적인 `54322`는 Connector allowlist에 없음. |
| CONN-TC-X026 | Docker demo Gateway의 admission은 실제 전용 Redis를 사용해야 한다. | Demo verifier만 `connector-test-redis`를 사용하고 Gateway composition은 platform Redis singleton을 사용한다. | Gateway override가 Connector-specific DB 15 URL을 주입하고 composition이 별도 client를 생성·종료한다. Platform Redis 설정은 변경하지 않음. |
| CONN-TC-X027 | Certificate validity API와 선언된 dependency 하한은 일치해야 한다. | `not_valid_before_utc`/`not_valid_after_utc`를 사용하면서 Gateway가 `cryptography<42` 설치를 허용한다. | Gateway dependency minimum이 `42.0.0` 이상이고 CA startup validation test가 UTC validity API를 실행한다. |
| CONN-TC-X028 | Production Connector startup secret은 모든 지원 배포 표면에서 같은 fail-closed 계약을 가져야 한다. | Runtime과 Helm은 admission HMAC key를 요구하지만 base Docker Compose가 외부 값을 Gateway 컨테이너에 전달하지 않거나 tracked env example에 실제 key를 둔다. | Compose는 빈 development default를 보존하면서 외부 key 이름만 passthrough하고, production의 누락·짧은 값은 Gateway startup에서 실패한다. Helm required Secret 계약과 key 비재사용·비노출은 유지된다. |
| CONN-TC-X029 | Connector test 민감 경로 집합은 Nginx와 ASGI sanitizer에서 일치해야 한다. | `/api/v1/connectors/test/`가 일반 `/api` location으로 떨어지거나 child path까지 Connector test로 취급된다. | Canonical path와 단일 trailing slash만 같은 32 KiB/5초/buffering-off/log-off 경계를 사용하고 child path는 두 matcher 모두에서 제외된다. |
| CONN-TC-X030 | Knowledge DB metadata와 chunk label은 opaque reference 외 Connection 상세를 복제하지 않아야 한다. | `connection_name`, `db_type`, host/database/username, encrypted/decrypted credential 중 하나가 document metadata, processor result 또는 chunk source label에 남는다. | 테스트 실패. |
| CONN-TC-X031 | Connector egress port 목록은 application, Squid와 NetworkPolicy에 동일하게 배포되어야 한다. | Strict test port가 proxy 목록에 없거나 custom port가 ACL/IPv4/IPv6 중 하나에만 렌더된다. | Startup/Helm render fail-closed 또는 세 경계에 동일 포트 렌더. |

## MBA-281 Automation Traceability

| Test case | 자동 검증 위치 | 수준 |
| --- | --- | --- |
| U020 | `apps/shared/tests/services/test_connection_use_resolver.py`, `test_connection_use_resolver_postgres.py` | Unit + actual PostgreSQL |
| U021-U022 | `apps/shared/tests/services/test_db_processor_connection_use.py` | Processor unit |
| P004 | `apps/gateway/tests/api/test_knowledge_db_connection_use.py` | Gateway API |
| P005 | `apps/workflow_engine/tests/adapters/test_knowledge_collection_sync_document.py`, `apps/workflow_engine/tests/services/test_sync_service.py` | Background/KC execution |
| X030 | `apps/gateway/tests/api/test_knowledge_db_connection_use.py`, `apps/shared/tests/services/test_db_processor_connection_use.py` | Metadata/redaction |

## MBA-246 Automation Traceability

| Test case | 자동 검증 위치 | 수준 |
| --- | --- | --- |
| X008-X011 | `apps/gateway/tests/composition/test_connector_test_composition.py`, `apps/gateway/tests/adapters/connectors/test_postgres_probe.py` | Unit/startup |
| U017-U019, X018-X019 | `apps/gateway/tests/adapters/connectors/test_redis_test_admission.py`, `test_redis_test_admission_integration.py` | Unit + actual Redis |
| A031, X012-X013, X020 | `apps/gateway/tests/integration/test_connector_demo_integration.py` | Actual transport/API/TLS PostgreSQL |
| X014-X017, X021-X024 | `apps/gateway/tests/architecture/test_connector_demo_boundary.py`, Compose healthcheck, `scripts/verify_connector_demo_runtime.py`, MBA-246 Docker lifecycle smoke | Architecture + actual Docker smoke |
| A032, C013, X025 | `apps/shared/tests/test_connector_test_schema.py`, `apps/gateway/tests/api/test_connector_test_api.py`, Client Connector tests, `apps/gateway/tests/architecture/test_connector_helm_boundary.py` | Schema + API + Client + architecture |
| A028, A033 | `apps/gateway/tests/application/connectors/test_connection.py`, `apps/gateway/tests/adapters/connectors/test_postgres_probe.py` | Application + executor lifecycle |
| A034 | `apps/gateway/tests/api/test_connector_test_api.py`, `apps/gateway/tests/adapters/authentication/test_client_network.py` | API + trusted proxy adapter |
| X026 | `apps/gateway/tests/composition/test_connector_test_composition.py`, `apps/gateway/tests/architecture/test_connector_demo_boundary.py` | Composition + deployment contract |
| X027 | `apps/gateway/tests/architecture/test_connector_dependency_boundary.py`, Connector composition CA tests | Dependency contract + startup |
| A035 | `apps/gateway/tests/middleware/test_webhook_query_redaction.py`, `apps/gateway/tests/api/test_connector_test_api.py` | ASGI access-path + API ingress |
| A036 | `apps/gateway/tests/application/connectors/test_connection.py`, `apps/gateway/tests/adapters/connectors/test_postgres_probe.py` | Application + executor reservation lifecycle |
| A037, C008 | `apps/gateway/tests/api/test_connector_test_api.py`, `apps/client/app/features/knowledge/api/connectorApi.test.ts`, `DBConnectionForm.test.tsx` | CORS API + Client cooldown |
| C015 | `apps/client/app/features/knowledge/api/connectorApi.test.ts`, document settings TypeScript contract | Client adapter normalization |
| X028-X029 | `apps/gateway/tests/architecture/test_connector_deployment_contract.py` | Runtime + Compose + Helm + Nginx + ASGI deployment contract |

실제 Redis/TLS test는 opt-in 환경 설정이 없으면 skip할 수 있지만 MBA-246 merge evidence에서는 skip을 허용하지 않는다. 각 실행은 UUID 기반 namespace만 삭제하고 logical Redis DB 전체를 초기화하지 않는다. 실제 credential, certificate 본문, fingerprint, raw request는 pytest output이나 문서에 기록하지 않는다.

## Knowledge Source Connector Target Tests

이 섹션은 현재 workflow DB connector 테스트를 대체하지 않고, 목표 Knowledge source connector가 따라야 할 보안 경계를 추가로 검증한다. Knowledge source connector target case는 [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)의 egress/adapter baseline과 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)의 MCP/API adapter boundary를 따른다.

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| CONN-KNOW-TC-001 | Knowledge source collection의 SSH tunnel, proxy, approved private network segment는 별도 connector/egress ADR 승인 전 기본 거부되어야 한다. | 승인 정책 없이 SSH tunnel 또는 private target을 사용한다. | safe reason code로 거부. |
| CONN-KNOW-TC-002 | DNS rebinding과 redirect chain은 최종 target 기준으로 검증되어야 한다. | 최초 host는 안전하지만 최종 target이 private/link-local/metadata IP이다. | safe reason code로 거부. |
| CONN-KNOW-TC-003 | Source connector의 public ACL은 organization-wide read/use로 자동 materialize되지 않아야 한다. | public ACL 하나만으로 KB `use` grant가 생성된다. | 테스트 실패. |
| CONN-KNOW-TC-004 | Slack/meeting connector는 channel을 collection으로, thread/huddle recap/canvas/bot-generated meeting summary/pinned-message group을 document-level KB로 매핑해야 한다. | huddle recap 또는 canvas가 channel-level KB 하나에 섞인다. | 테스트 실패. |
| CONN-KNOW-TC-005 | Slack/meeting artifact-level ACL이 있으면 artifact ACL과 containing channel/workspace ACL의 교집합만 source authorization provenance를 얻어야 한다. | channel membership만으로 artifact ACL 없는 requester가 통과한다. | fail-closed 또는 remediation. |
| CONN-KNOW-TC-006 | Slack/meeting DM, raw audio, raw transcript는 별도 opt-in policy 없이 수집되지 않아야 한다. | opt-in 없이 raw transcript가 ingestion 대상에 포함된다. | 테스트 실패. |
| CONN-KNOW-TC-007 | Slack/meeting ACL sync 실패나 partial ACL response는 raw channel/source title/path/url, raw principal, raw exception을 UI, audit, trace, log에 남기지 않아야 한다. | 실패 응답 또는 로그에 raw source metadata가 포함된다. | safe reason code만 남김. |
| CONN-KNOW-TC-008 | MCP/API Knowledge source connector는 allowlist operation만 호출해야 한다. | LLM이 임의 MCP tool을 선택하거나 adapter가 allowlist 밖 operation, raw source data direct fetch, prompt/completion 기반 source query를 실행한다. | 요청 거부, safe reason code, raw tool response 비노출. |
| CONN-KNOW-TC-009 | Private source-managed KB retrieval은 runtime authorization primitive가 없으면 fail-closed되어야 한다. | Source connector가 `check_access_batch`와 bounded `check_access` fallback을 모두 제공하지 않는데 retrieval 후보가 된다. | 후보 제외 또는 remediation, raw source metadata 비노출. |
| CONN-KNOW-TC-010 | Runtime authorization fallback은 bounded concurrency와 timeout을 적용해야 한다. | Batch 미지원 source에서 candidate 수만큼 unbounded `check_access` 호출을 실행하거나 aggregate timeout 없이 대기한다. | safe partial/fail-closed, rate-limit-safe retry policy. |
| CONN-KNOW-TC-011 | Conversation Memory authorization result는 principal-neutral stable revision contract를 제공해야 한다. | Batch result에 decision, principal kind, authorization decision/resource/policy revision 또는 evaluated_at이 누락되거나 source ACL revision이 decision revision에 반영되지 않는다. | 해당 private/sensitive dependency를 `unknown`으로 fail-closed. |
| CONN-KNOW-TC-012 | Partial batch result를 allow로 채우지 않아야 한다. | 요청 item 중 일부만 connector 응답에 존재한다. | 누락 item은 unknown, raw source identity 비노출. |
| CONN-KNOW-TC-013 | Bot/webhook/app installation visibility는 requester authorization으로 쓰면 안 된다. | Bot이 볼 수 있는 source item이라는 이유만으로 private retrieval evidence에 포함한다. | source subject mapping/runtime authorization gate를 통과하지 못하면 fail-closed. |
| CONN-KNOW-TC-014 | File/page artifact connector는 content safety gate 전 raw artifact를 trusted normalized content로 취급하면 안 된다. | Macro-enabled document, embedded script/object, executable child file, unsupported content type 중 하나가 redacted canonical text/chunk/embedding으로 진행된다. | fail-closed 또는 remediation, safe reason code만 저장. |
| CONN-KNOW-TC-015 | Archive connector ingestion은 expansion cap과 nested content policy를 적용해야 한다. | Zip bomb, nested archive cap 초과, archive 내부 executable/script/macro-enabled file이 indexing-visible artifact가 된다. | ingestion 제외 또는 quarantine/remediation. |
| CONN-KNOW-TC-016 | Parser/scanner failure는 raw content leakage 없이 닫혀야 한다. | Parser exception, scan timeout, scan unknown/error가 raw bytes, active marker, parser raw stack detail을 response/log/audit/trace/dead-letter에 남긴다. | safe reason code와 retryability/remediation state만 남김. |
| CONN-KNOW-TC-017 | Pre-normalization raw payload는 connector 실패 경로에 남지 않아야 한다. | Normalization 전 예외가 발생했을 때 raw payload가 connector debug/error log, retry/dead-letter payload, audit, trace 중 하나에 남는다. | 테스트 실패, safe reason code만 저장. |
| CONN-KNOW-TC-018 | Live-linked source-side search는 requester-scoped 또는 opaque-ref-only여야 한다. | Broad service-account search가 authorization 전 title, snippet, count, score를 반환한다. | 검색 후보 제외 또는 metadata suppression, side-channel 없음. |
| CONN-KNOW-TC-019 | Connector result는 server-derived RuntimeDataDependencyEnvelope를 제공해야 한다. | Content에 영향을 준 connector/source item version이 누락되거나 client/node가 canonical dependency를 발급한다. | Private/sensitive Memory write fail-closed. |
| CONN-KNOW-TC-020 | Relevant source mutation은 authorization decision revision을 변경해야 한다. | Source ACL/mapping/policy/lifecycle/public exposure 변경 뒤 old revision이 allow로 재사용된다. | Stale lease/cache 거부. |
| CONN-KNOW-TC-021 | Anonymous source authorization은 synthetic subject를 만들지 않아야 한다. | Bot owner/app installation/Conversation grant를 requester subject로 사용한다. | Explicit public exposure만 평가, 그 외 fail-closed. |
