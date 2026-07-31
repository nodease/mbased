# Connectors Requirements

Status: Draft
Verified Against: feature/mba-302 @ b2d6467002b7becf1daa0badfe6fc155b3edaa57
Related Features: workflow, organization, audit-tracing, knowledge, conversation-memory

## Purpose

Connectors 기능은 외부 데이터 소스에 접속하기 위한 연결 정보를 관리한다. 현재 구현 범위는 PostgreSQL DB 연결 테스트, 연결 저장, 연결 상세 조회, 스키마 조회 API(`/api/v1/connectors`)와 `connections` 테이블이다. DB 비밀번호와 SSH 비밀번호/개인키는 서버에서 암호화 저장하고, 응답에는 secret 원문을 반환하지 않는다.

이 feature의 책임은 연결 등록과 조회, owner 기반 접근 제한, secret 저장/비노출, DB probe/schema 조회의 보안 경계다. 사용자 인증 생명주기, organization/team RBAC 부여, workflow 실행 권한, Knowledge Base 권한, Knowledge sync scheduler/worker는 connectors 자체의 책임이 아니다.

## User Stories

- 빌더는 외부 DB 연결 정보를 저장하기 전에 접속 가능 여부를 테스트할 수 있다.
- 빌더는 외부 DB 연결 정보를 저장하고, 이후 workflow 또는 Knowledge DB source 설정에서 connection id를 참조할 수 있다.
- 빌더는 저장한 연결의 테이블, 컬럼, FK 정보를 조회해 DB source의 테이블/컬럼 선택에 사용할 수 있다.
- 빌더는 저장한 연결 상세를 다시 열 때 비밀번호나 SSH secret 원문이 노출되지 않은 host/port/database/username/SSH metadata만 볼 수 있다.
- 플랫폼 운영자는 connector가 workflow 권한과 KB 권한의 책임을 대체하지 않는다는 점을 기준으로 운영 위험을 판단할 수 있다.

## Functional Requirements

### Current DB Connection Requirements

- CONN-REQ-001: 시스템은 `/api/v1/connectors` 하위 API로 외부 DB 연결 테스트, 저장, 상세 조회, 스키마 조회를 제공해야 한다.
- CONN-REQ-002: 현재 지원 DB 타입은 Gateway `SupportedDBType` 기준 `postgres`여야 한다.
- CONN-REQ-003: 클라이언트는 PostgreSQL을 활성 선택지로 제공하고, MySQL은 disabled/Coming Soon 선택지로 표시해야 한다.
- CONN-REQ-004: DB 연결 요청은 `connection_name`, `type`, `host`, `port`, `database`, `username`, `password`, 선택적 `ssh` 설정을 받아야 한다.
- CONN-REQ-005: SSH 설정은 `enabled`, `host`, `port`, `username`, `auth_type`, `password`, `private_key`를 포함할 수 있어야 한다.
- CONN-REQ-006: `ssh.enabled`가 false이면 Gateway는 adapter에 전달하는 설정에서 SSH 구성을 비활성화해야 한다.
- CONN-REQ-007: `POST /connectors/test`는 요청 연결 정보로 실제 DB 접속을 확인하고 `success`와 `message`를 반환해야 한다.
- CONN-REQ-008: `POST /connectors/test`는 현재 저장소에 connection row를 만들지 않아야 한다.
- CONN-REQ-009: `POST /connectors/test`에서 지원하지 않는 DB 타입은 `success=false`와 지원하지 않는 타입 메시지로 반환되어야 한다.
- CONN-REQ-010: `POST /connectors`는 인증된 현재 사용자를 요구해야 한다.
- CONN-REQ-011: `POST /connectors`는 저장 전 같은 연결 정보로 DB 접속 테스트를 수행해야 한다.
- CONN-REQ-012: `POST /connectors`는 저장 전 접속 테스트에 10초 timeout을 적용해야 한다.
- CONN-REQ-013: 저장 전 접속 테스트가 실패하면 시스템은 connection row를 만들지 않아야 한다.
- CONN-REQ-014: 저장 성공 시 시스템은 `connections.user_id`를 현재 사용자 ID로 설정해야 한다.
- CONN-REQ-015: 저장 성공 시 시스템은 DB 비밀번호를 `encrypted_password`에 암호화 저장해야 한다.
- CONN-REQ-016: SSH password 인증을 사용하는 저장 성공 시 시스템은 SSH 비밀번호를 `encrypted_ssh_password`에 암호화 저장해야 한다.
- CONN-REQ-017: SSH key 인증을 사용하는 저장 성공 시 시스템은 SSH private key를 `encrypted_ssh_private_key`에 암호화 저장해야 한다.
- CONN-REQ-018: 저장 성공 응답은 생성된 connection id, success flag, 사용자 표시용 message만 반환해야 한다.
- CONN-REQ-019: 저장 성공은 `connection.create` audit action으로 기록되어야 한다.
- CONN-REQ-020: `GET /connectors/{connection_id}`는 인증된 현재 사용자를 요구해야 한다.
- CONN-REQ-021: `GET /connectors/{connection_id}`는 connection owner가 아니면 `403`으로 거부해야 한다.
- CONN-REQ-022: `GET /connectors/{connection_id}`는 비밀번호, SSH 비밀번호, SSH private key, encrypted secret 값을 반환하지 않아야 한다.
- CONN-REQ-023: `GET /connectors/{connection_id}/schema`는 인증된 현재 사용자를 요구해야 한다.
- CONN-REQ-024: `GET /connectors/{connection_id}/schema`는 connection owner가 아니면 `403`으로 거부해야 한다.
- CONN-REQ-025: `GET /connectors/{connection_id}/schema`는 저장된 encrypted secret을 서버에서만 복호화해 adapter 접속 설정을 구성해야 한다.
- CONN-REQ-026: PostgreSQL schema 조회는 table name, column name/type, FK의 column/referenced table/referenced column 정보를 반환해야 한다.
- CONN-REQ-027: DB schema 조회는 secret 원문을 응답 body에 포함하지 않아야 한다.
- CONN-REQ-028: 현재 `connections` 테이블은 user-owned resource이며 `organization_id`를 갖지 않는다.
- CONN-REQ-029: 현재 connection owner 판정은 organization/team permission helper가 아니라 `connections.user_id == current_user.id` 기준이어야 한다.
- CONN-REQ-030: connector test, create, schema 조회 실패 응답은 raw host, database name, secret, driver detail, stack trace를 포함하지 않고 safe message 또는 safe `reason_code`로 닫아야 한다.
- CONN-REQ-031: PostgreSQL 직접 연결의 기본 profile은 DB host/port를 사전 검증해 private, loopback, link-local, metadata, reserved target을 거부하고, 검증된 public IP로 실제 연결 대상을 고정해야 한다. CONN-REQ-049의 development exact-local profile만 명시적 예외다.
- CONN-REQ-032: 기존 workflow DB connector compatibility 경로는 문서화된 SSH tunnel 설정을 사용할 수 있다. 이 허용은 workflow connector API 경계에서 명시적으로 선택해야 하며, arbitrary SSH command execution 허용을 의미하지 않는다.
- CONN-REQ-033: Knowledge DB source ingestion이 공유 PostgreSQL adapter를 사용할 때는 기본 정책으로 SSH tunnel, proxy, private-network target을 거부해야 한다. 이 경로에서 tunnel을 열려면 별도 connector/egress ADR 또는 승인된 organization policy가 필요하다.
- CONN-REQ-034: PostgreSQL schema introspection은 table, column, foreign key 개수 상한을 적용하고, 잘린 결과는 safe truncation marker로 표시해야 한다.
- CONN-REQ-035: DB row fetch 경로는 SELECT-only guard, dangerous function/keyword blocklist, read-only transaction, statement timeout, batch size cap, total row cap을 적용해야 한다.

### Secure Connection Test Requirements

- CONN-REQ-036: `POST /connectors/test`는 로그인 사용자와 `X-Organization-Id`의 active organization membership을 요구해야 한다. Invited/suspended/removed 또는 scope 밖 organization은 network와 admission 전에 fail-closed해야 한다.
- CONN-REQ-037: Active organization member/manager는 connection test capability를 갖지만, 이 capability는 connection create/use/manage 권한을 부여하지 않아야 한다.
- CONN-REQ-038: Gateway는 인증과 organization scope 확인 뒤 connector test actual JSON body를 최대 32 KiB, 전체 5초로 읽어야 한다. Declared length는 early hint이고 actual streamed bytes가 최종 기준이어야 한다.
- CONN-REQ-039: Connector test ingress는 query string 없이 단일 `application/json`, identity encoding, UTF-8 object root만 허용하고 duplicate/invalid length, BOM, invalid JSON, non-finite number, disconnect를 safe reason code로 거부해야 한다. Connector test 경로의 query 원문은 ASGI access log가 생성되기 전에 제거하되 query 존재 여부만 boolean marker로 보존해 endpoint가 `400 connector.test_payload_invalid`로 거부해야 한다.
- CONN-REQ-040: Connector test는 Redis atomic admission에서 aligned 60초 window당 user 5, organization 30, network 20 rate와 user 1, organization 4, global 16 concurrency를 적용해야 한다. Network identity는 공통 trusted-proxy resolver로 계산하고, forwarded header는 설정된 trusted proxy peer에서만 사용해야 한다. 환경 설정은 문서화된 positive/finite 상한과 scope/timeout 관계를 벗어나면 Gateway startup을 실패시켜야 한다.
- CONN-REQ-041: Admission identity는 scope-separated HMAC digest만 Redis에 저장해야 하며 raw organization/user/network identity, connection config와 secret을 key/member에 저장하지 않아야 한다. Owner-safe heartbeat는 probe 완료 또는 server-owned hard deadline까지 lease를 연장한다. Hard deadline 뒤 heartbeat와 distributed lease를 종료하되, 취소할 수 없는 blocking driver work가 실제로 끝나기 전에는 해당 local executor capacity를 재사용하지 않아야 한다. Process crash와 release 실패는 TTL로 복구한다.
- CONN-REQ-042: Redis/admission 장애, 해석할 수 없는 transport identity와 local executor capacity 부족은 probe 전에 fail-closed해야 한다. Local executor slot은 Redis rate admission 전에 non-blocking 예약하고, local busy는 Redis rate counter와 Connector audit을 소비하지 않아야 한다. Admission 실패·취소 시 시작하지 않은 예약은 즉시 반환하고, 시작한 blocking driver의 slot은 실제 종료 전 재사용하지 않아야 한다. Process-local unlimited fallback은 허용하지 않는다.
- CONN-REQ-043: Strict test는 `postgres`, 기본·production public-only target과 deployment-managed port allowlist만 허용하고 모든 DNS 결과를 검증한 뒤 한 validated IP로 실제 연결을 고정해야 한다. 기본·production과 Docker-service demo allowlist는 `5432`, host-run demo는 exact published target을 위해 `5432,55432`를 사용해야 한다. 설정은 중복 없는 `1..65535` 정수 1~16개로 제한하고 invalid 설정은 Gateway startup을 실패시켜야 하며 요청자는 allowlist를 확장할 수 없어야 한다.
- CONN-REQ-044: Public strict test는 시스템 CA bundle, development exact-local target은 서버가 소유한 전용 CA file을 명시해 TLS `verify-full`, connect 5초, statement 3초, API 10초, probe hard deadline 20초, distributed lease 30초, 요청당 한 번의 connection attempt, read-only `SELECT 1`과 one-row scalar result를 적용해야 한다. 선택된 CA file이 없으면 startup 또는 DNS 전에 fail-closed해야 하며 request가 CA나 SSL mode를 지정할 수 없어야 한다.
- CONN-REQ-045: `ssh.enabled=true` connector test는 approved host-key/private-network 정책 전까지 network 전에 `connector.ssh_probe_not_supported`로 거부해야 한다. 이 제한은 기존 create/schema compatibility를 자동 제거하지 않는다.
- CONN-REQ-046: Expected target/connection 실패는 static message와 allowlist reason code만 반환해야 하며 host/IP/port/database/username/password/private key/DSN/driver exception을 response, audit, application/client/edge log에 노출하지 않아야 한다.
- CONN-REQ-047: Admission 뒤 결과는 `connection.test` audit으로 organization, actor, result, canonical reason과 coarse duration만 기록해야 한다. Audit publish 실패는 probe를 자동 재시도하거나 성공 결과를 실패로 바꾸지 않아야 한다.
- CONN-REQ-048: Repository edge는 connector-test canonical route와 단일 trailing-slash 동치 경로에 동일한 32 KiB, 5초 idle receive, buffering off와 request-target log 억제를 적용해야 한다. Child path는 Connector test endpoint로 취급하지 않는다. Direct Gateway 또는 Next rewrite가 edge를 우회하더라도 최외곽 ASGI middleware가 같은 두 경로의 connector-test query 전체를 access-log-visible scope에서 제거해야 하며, Gateway actual-byte/total-deadline/query 거부 guard는 유지해야 한다.
- CONN-REQ-049: Local private/loopback target은 `CONNECTOR_TEST_LOCAL_PROFILE_ENABLED=true`, `NODE_ENV=development`, 서버가 설정한 최대 4개의 exact canonical hostname+port, 전용 공개 CA가 모두 존재할 때만 허용해야 한다. Wildcard, suffix, CIDR, raw IP target, request override를 허용하지 않고 target port가 deployment allowlist에도 있어야 하며, 모든 DNS 결과가 RFC1918, IPv6 ULA 또는 loopback이 아니거나 public/private mixed이면 거부해야 한다. Profile flag·target·CA가 불완전하거나 production에서 셋 중 하나라도 설정되면 startup을 실패시켜야 한다.
- CONN-REQ-050: Trusted-local CA file은 readable regular file, 64 KiB 이하, private-key marker가 없는 단일 PEM certificate여야 한다. Certificate는 현재 유효하고 `BasicConstraints CA:TRUE`여야 하며 invalid·expired·future·leaf·multiple-certificate·private-key-containing file은 DNS 전에 startup을 실패시켜야 한다.
- CONN-REQ-051: Local/Docker connector demo는 일반 platform 서비스와 분리된 explicit Compose profile과 전용 bridge network를 사용해야 한다. Host publish는 `127.0.0.1`에만 열고 Host-run `localhost:55432`와 Docker `connector-test-postgres:5432`를 각각 exact target으로 검증해야 한다. Docker Gateway의 admission은 Connector 전용 Redis URL로 demo Redis logical DB 15를 사용하고, 일반 platform Redis 연결은 바꾸지 않아야 한다. 일반 profile과 production Helm에는 demo service, network, target, CA mount, private volume이 없어야 한다.
- CONN-REQ-052: Demo PostgreSQL TCP 접속은 `hostssl`과 SCRAM, TLS 1.2 이상만 허용하고 `hostnossl`은 명시적으로 거부해야 한다. Connector가 사용하는 `connector_demo_user`는 superuser/create-role/create-db/replication/bypass-RLS 권한이 없는 connection-limited read-only role이어야 하며 platform DB 계정과 분리되어야 한다.
- CONN-REQ-053: Certificate init은 CA signing key를 one-shot 컨테이너 임시 filesystem에서만 사용하고 persistent volume, Gateway, PostgreSQL, verifier 또는 build context에 남기지 않아야 한다. Server TLS material, PostgreSQL bootstrap admin credential, Connector demo credential은 서로 다른 private volume에 두어야 한다. PostgreSQL만 세 private volume을 읽고, one-shot verifier는 Connector demo credential만, Gateway는 공개 CA만 read-only로 읽어야 한다. 반복 init은 유효한 자료를 재사용하고 partial temporary file을 정리해야 한다.
- CONN-REQ-054: 실제 Redis 검증은 실행별 안전한 key namespace만 사용·정리하고 `FLUSHDB`/`FLUSHALL`을 호출하지 않아야 한다. Concurrency 거부는 rate counter를 소비하지 않아야 하며 테스트 cleanup은 같은 logical DB의 무관한 key를 변경하지 않아야 한다.
- CONN-REQ-055: Redis admission의 acquire, renew, release는 각각 Connector 전용 operation deadline을 적용해야 한다. 기본값은 1초, 상한은 5초이며 API timeout보다 짧고 lease TTL의 3분의 1보다 짧아야 한다. 무응답·timeout·transport 오류는 network probe나 unlimited local fallback 없이 `connector.admission_unavailable`로 fail-closed해야 한다.
- CONN-REQ-056: Connector test와 저장의 `connection_name`은 서버에서 앞뒤 공백을 제거한 뒤 1~100자여야 한다. 공백뿐인 값은 `422`로 거부하고 connection row를 만들지 않아야 하며, Client의 DB source 저장 UI도 같은 값을 필수로 검사하고 정규화해 전송해야 한다.
- CONN-REQ-057: Client가 connection detail을 편집 form에 전달할 때 Gateway의 `connection_name`, `ssh.auth_type`을 각각 `connectionName`, `ssh.authType`으로 명시적으로 변환해야 한다. 응답에 없는 DB/SSH secret은 빈 재입력 상태로 유지하고 raw detail shape를 form에 직접 전달하지 않아야 한다.
- CONN-REQ-058: Connector test의 `429 Retry-After`는 설정된 credentialed CORS origin의 브라우저 JavaScript가 읽을 수 있도록 `Access-Control-Expose-Headers`에 포함해야 한다. 허용 origin 판정과 credential 정책을 완화해서는 안 된다.
- CONN-REQ-059: Gateway startup에 필요한 Connector 보안 설정은 지원되는 모든 배포 표면에서 동일하게 전달되어야 한다. Production Docker Compose는 tracked secret 값을 두지 않고 외부 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 Gateway 컨테이너로 전달해야 하며, 값이 없거나 32 byte 미만이면 runtime startup이 fail-closed해야 한다. Helm은 별도 Secret을 required 값으로 유지해야 한다.
- CONN-REQ-060: Connector security contract는 runtime startup, Docker Compose, Helm, Nginx와 ASGI middleware의 공통 불변식을 하나의 자동화 추적표로 검증해야 한다. 개별 surface 테스트가 통과하더라도 필수 설정 전달 또는 민감 경로 동치성이 빠지면 merge-ready로 간주하지 않는다.

### Knowledge DB Connection Use Requirements

- CONN-REQ-061 (MBA-281): Knowledge DB source가 저장된 Connection을 사용할 때 현재 최소 권한은 `connections.user_id == execution_subject_user_id`다. Workflow/KB/Collection 권한이나 같은 organization membership만으로 다른 사용자의 Connection use를 허용하지 않아야 한다.
- CONN-REQ-062 (MBA-281): Connection 조회·사용 판정은 Shared Connection Use Resolver가 소유해야 한다. Knowledge upload/process/preview와 Gateway/Workflow Engine background DB ingestion이 owner predicate를 각자 복제하지 않아야 한다.
- CONN-REQ-063 (MBA-281): Knowledge DB source 설정 저장 전에 Connection을 검증하고, 외부 DB dial 직전에는 같은 resolver로 다시 검증해야 한다. Queue 대기 중 삭제 또는 owner 변경이 발생하면 adapter 호출 전에 fail-closed해야 한다. 이 판정은 dial 시작 시점의 권한 스냅샷이며 runtime row lock, 실행 도중 revoke 취소와 transaction 조율은 MBA-302 범위다.
- CONN-REQ-064 (MBA-281): Knowledge document metadata에는 opaque `connection_id`만 Connection reference로 저장할 수 있다. Connection name/type/host/database/username, decrypted/encrypted password와 SSH credential은 document metadata, processor result, chunk source label, audit와 log에 복제하지 않아야 한다.
- CONN-REQ-065 (MBA-281): Missing, malformed, deleted와 non-owner Connection reference는 Knowledge use surface에서 동일한 `resource.hidden` 결과로 처리하고 Connection 존재·owner·상세를 노출하지 않아야 한다. Connector 관리 detail/schema API의 기존 403/404 계약은 이 요구로 변경하지 않는다.
- CONN-REQ-066 (MBA-281): 저장 credential 복호화가 실패하면 Knowledge DB processor는 저장값을 평문 credential처럼 fallback하지 않고 configuration failure로 닫아 외부 adapter를 호출하지 않아야 한다.
- CONN-REQ-067 (MBA-302): Runtime DB use는 독립된 짧은 SQLAlchemy session에서 owner를 재검증하고 adapter type과 최소 credential configuration을 immutable snapshot으로 만든 뒤 transaction/session을 종료해야 한다. ORM Connection과 encrypted storage shape를 processor에 반환해서는 안 되며 connector 생성·외부 DB dial은 snapshot session 종료 뒤에만 허용한다. Snapshot projection은 공백뿐인 database/username을 거부하되 저장된 PostgreSQL identifier text를 임의 trim하지 않고, encrypted SSH password가 없는 기존 password-auth row의 agent/default-key compatibility를 보존해야 한다.
- CONN-REQ-068 (MBA-302): Connection reference 저장·교체·삭제만 owner Connection row lock을 사용한다. 여러 Knowledge row가 함께 필요한 mutation의 전역 순서는 `Connection -> KnowledgeBase -> Document/DocumentVersion`이며 existing Document reference writer는 Connection 다음 Document를 fresh read lock하고 최초 조회 revision을 재검증해 stale writer를 명시적 non-retryable conflict로 닫아야 한다. 외부 network/storage/provider I/O, chunking과 embedding을 Connection lock transaction 안에서 수행해서는 안 된다.
- CONN-REQ-069 (MBA-302): PostgreSQL Connection reference lock wait는 local 2초로 제한한다. Lock 획득 또는 같은 reference mutation의 flush/commit에서 발생한 lock timeout, deadlock victim과 serialization failure는 전체 transaction rollback 뒤 새 session에서만 재시도 가능한 `connection.reference_busy`, 기타 store failure는 `connection.reference_unavailable`로 구분하고 raw SQL/driver/lock detail을 노출하지 않아야 한다.
- CONN-REQ-070 (MBA-302): Runtime PostgreSQL fetch와 schema introspection은 connect 5초, statement 5초와 read-only transaction을 적용한다. Fetch는 bounded batch·총 10,000 row·총 16 MiB row payload 상한을 적용하고, 상한 초과를 일부 성공으로 반환하지 않으며 종료·예외·취소에서 engine과 tunnel을 정리해야 한다.
- CONN-REQ-071 (MBA-302): Runtime snapshot은 dial 시작 시점의 authorization snapshot이며 실행 중 즉시 revoke를 보장하지 않는다. Lock 관측 정보는 outcome과 coarse wait/hold bucket만 허용하고 Connection identity, target, SQL과 credential을 metric label, log, audit 또는 trace에 포함하지 않아야 한다.
- CONN-REQ-072 (MBA-357): Production external PostgreSQL/SSH는 DNS 정책으로 검증·고정한 public IP와 배포 관리 포트만 `connector-egress-v1`의 internal `3130` CONNECT listener에 전달해야 한다. Strict test allowlist는 egress allowlist의 부분집합이어야 하고 저장 connection schema/runtime 및 Workflow SSH compatibility도 같은 전용 transport를 사용해야 한다. Proxy config/tunnel 실패 뒤 direct public dial은 허용하지 않는다. Local exact-private demo와 platform internal DB는 기존 dedicated direct 경계를 유지한다.

## Policies And Edge Cases

- `POST /connectors/test`는 active organization context를 요구한다. 이 endpoint의 test capability는 현재 user-owned connection의 create/use/manage 권한과 분리된다.
- 현재 `POST /connectors`는 `get_current_user`를 요구하지만 resource permission table을 사용하지 않는다.
- 현재 `GET /connectors/{connection_id}`와 `GET /connectors/{connection_id}/schema`는 없는 connection에 `404`, owner mismatch에 `403`을 반환한다.
- create/test/schema 실패 메시지는 raw host, database, secret, driver detail을 응답에 포함하지 않아야 한다.
- 현재 schema 조회는 기존 관리 API의 404/403 precheck를 짧은 request transaction에서 수행하고 rollback한 뒤, 독립 runtime snapshot provider로 owner와 credential을 다시 확인한다. Snapshot session 종료 뒤 SQLAlchemy inspector가 read-only/statement-timeout 설정으로 schema metadata를 읽는다.
- schema 조회 cap은 UX용 metadata preview 범위를 제한하기 위한 것이며, connector가 전체 DB inventory를 durable storage, audit, trace, log에 저장해도 된다는 의미가 아니다.
- SSH tunnel compatibility는 기존 workflow DB connector 기능을 보존하기 위한 경계다. Knowledge source ingestion의 기본 경계와 다르며, SSH tunnel 허용은 remote shell command 실행 허용으로 해석하지 않는다.
- SSH tunnel compatibility는 create/schema/runtime의 기존 계약에 한정된다. Strict `/connectors/test`는 ADR-0049에 따라 SSH를 열지 않는다.
- Development exact-local profile은 로컬 시연 전용 배포 설정이며 Organization별 운영 private-network 권한이나 CIDR 승인 기능이 아니다.
- Production Gateway는 32 byte 이상의 별도 connector-test admission HMAC key를 요구한다. Helm에서는 `secrets.connectorTestAdmissionHmacKey`로 provisioning하고, Docker Compose에서는 외부 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 컨테이너에 전달한다. 두 경로 모두 auth/session key와 재사용하지 않고 tracked 예시에 실제 값을 두지 않는다.
- 현재 `DbProcessor`는 Knowledge DB source ingestion에서 Shared runtime snapshot provider로 execution subject 소유 Connection을 dial 직전에 재검증하고 snapshot session을 닫은 뒤 선택된 테이블/컬럼 기반 SQL을 생성한다. 이 ingestion lifecycle은 Knowledge feature 책임이다.
- `connections`에는 `created_at/updated_at`과 `organization_id`가 없다.

### Knowledge Source Connector Target Requirements

- CONN-KNOW-REQ-001: 목표 Knowledge source connector와 KB source collection으로 승격되는 server-side test, preview, fetch, probe는 중앙 `OutboundEgressGuard` 또는 승인된 client/dialer factory를 통과해야 한다 ([ADR-0014](../../decisions/ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)). 이 요구사항은 기존 workflow DB connector API 전체가 이미 같은 보호를 받는다는 뜻이 아니다.
- CONN-KNOW-REQ-002: HTTP/URL 계열 Knowledge connector는 DNS resolve 후 IP 재검증, IDNA/punycode/CNAME/IPv4 obfuscation canonicalization, redirect마다 재검증, private/link-local/metadata IP 차단, scheme allowlist, HTTPS downgrade 금지, `verify=false` 금지, sensitive header redirect stripping, timeout/size/content-type cap, compression bomb 방지, rate limit을 적용해야 한다.
- CONN-KNOW-REQ-003: DB/SSH connector는 arbitrary SQL/command를 connector test, preview, sync 경로에서 허용하지 않아야 한다. 필요한 경우 read-only probe, schema introspection cap, credential scope 제한, tunnel/proxy 정책을 adapter별로 문서화해야 한다.
- CONN-KNOW-REQ-004: Knowledge Slack/meeting connector의 초기 baseline은 channel을 Knowledge Collection으로, thread/huddle recap/canvas/bot-generated meeting summary/pinned-message group을 document-level KB로 매핑한다.
- CONN-KNOW-REQ-005: Slack/meeting artifact-level ACL이 있으면 artifact ACL과 containing channel/workspace ACL의 교집합을 requester authorization으로 사용하고, artifact ACL을 확인할 수 없으면 fail-closed 또는 remediation 상태로 둔다. Channel membership만으로 huddle recap, canvas, meeting summary를 자동 공개하지 않는다.
- CONN-KNOW-REQ-006: Slack/meeting DM, raw audio, raw transcript는 별도 opt-in policy 없이 수집하지 않는다.
- CONN-KNOW-REQ-007: Source ACL sync는 source authorization provenance와 freshness evidence를 만든다. Source ACL fact만으로 mbased KB `use`를 부여하지 않으며, auto-ingested KB retrieval에는 admin/team/user grant 또는 organization-approved connector/source policy가 provision한 explicit KB `use`가 필요하다.
- CONN-KNOW-REQ-008: MCP/API 기반 Knowledge source connector는 LLM 자유 tool-use surface가 아니라 server-side adapter allowlist로 동작해야 한다. Allowlist 밖 operation, raw source data direct fetch, LLM prompt/completion을 source query parameter로 사용하는 flow는 허용하지 않는다.
- CONN-KNOW-REQ-009: Knowledge source connector는 runtime authorization primitive로 `check_access_batch(subject_ref, source_item_refs[])`를 우선 제공해야 한다. Batch 미지원 source는 bounded single `check_access(subject_ref, source_item_ref)` fallback을 제공할 수 있지만, 둘 다 없으면 private source-managed KB retrieval은 fail-closed 대상이다.
- CONN-KNOW-REQ-010: Connector는 source별 raw payload를 Knowledge가 소비할 safe normalized shape로 변환해야 하며, 변환 전 raw payload는 connector debug/error log, retry/dead-letter payload, audit, trace에 남기지 않아야 한다.
- CONN-KNOW-REQ-011: Connector가 source-side search를 Live-linked mode에 제공하려면 requester-scoped search이거나 opaque source ref-only result여야 한다. Broad service-account search가 authorization 전 title, snippet, count, score를 반환하는 flow는 기본 구현으로 허용하지 않는다.
- CONN-KNOW-REQ-012: File/page artifact를 가져오는 Knowledge source connector는 egress guard 이후에도 content를 untrusted로 취급해야 한다. Connector 또는 ingestion boundary는 지원 file type/content type allowlist, archive depth/expanded-size/file-count cap, macro/script/embedded object/executable 차단, parser sandbox/least-privilege 실행, malware/content scan hook을 redacted canonical text 생성 전에 적용해야 한다. Scan failure, timeout, unsupported type, active content detection은 indexing-visible artifact를 만들지 않고 fail-closed 또는 remediation으로 처리한다.
- CONN-KNOW-REQ-013 (Conversation Memory Target Integration): `check_access_batch` 결과는 item별 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 반환해야 한다. Source ACL revision과 connector policy revision은 authorization decision revision 산정에 반영해야 한다. Source가 stable revision을 제공하지 못하거나 partial result가 누락되면 private/sensitive dependency는 `unknown`으로 fail-closed 한다.
- CONN-KNOW-REQ-014 (Conversation Memory Target Integration): Connector/tool result가 output content에 영향을 주면 adapter는 canonical connector/source item version, organization, sensitivity와 authorization-safe reference를 completeness marker가 있는 `RuntimeDataDependencyEnvelope`로 발급해야 한다. Raw source identity/payload/ACL은 포함하지 않아야 한다.
- CONN-KNOW-REQ-015 (Conversation Memory Target Integration): Source ACL, mapping epoch, connector policy, resource lifecycle 또는 public exposure approval이 authorization 결과에 영향을 주면 decision revision이 변경되어야 한다. Stale result/cache는 current allow 근거로 재사용할 수 없어야 한다.
- CONN-KNOW-REQ-016 (Conversation Memory Target Integration): Anonymous public audience는 synthetic subject/ACL revision 없이 explicit source public exposure policy로 평가해야 한다. Bot/app installation visibility와 Conversation Access Grant를 requester authorization으로 사용하지 않아야 한다.

## Knowledge Source Connector Policies

- 현재 `connections` 테이블은 user 소유이며 `organization_id`가 없다. 조직 경계 판정이 다른 리소스와 다르므로, target Knowledge source connector에서 workflow/KB 권한만으로 connection use가 자동 허용된다고 해석하지 않는다.
- 현재 user-owned `connections`의 connection `use`는 execution subject가 connection owner인 경우에만 허용한다. Workflow/KB/Collection 권한 또는 organization manager 권한이 이 owner gate를 대체하지 않는다. Organization-scoped 공유, 별도 `use/manage` permission과 owner/manager override는 후속 ADR·migration 전까지 구현하지 않는다 ([data_model.md](../../data_model.md) `connections` 참조).
- Internal DB/API/private-network targets are denied by default for Knowledge source collection unless a future connector/egress ADR defines an explicit organization policy, approved network segment, audit-safe reason code, and operational owner.
- Bot/webhook/app installation visibility는 capture signal 또는 event detection에 사용할 수 있지만 requester authorization으로 취급하지 않는다. Private source-managed retrieval은 delegated OAuth, source subject mapping, or runtime authorization primitive를 통해 별도 확인해야 한다.

## Open Questions

- `connections`를 organization-scoped resource로 전환할지, user-owned resource로 유지할지 결정해야 한다.
