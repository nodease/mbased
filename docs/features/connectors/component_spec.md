# Connectors Component Spec

Status: Draft
Verified Against: feature/mba-302 @ b2d6467002b7becf1daa0badfe6fc155b3edaa57

## Screens

- 독립 Connectors 관리 화면: 현재 없음.
- DB source 추가 흐름: `apps/client/app/features/knowledge/components/create-knowledge-modal/index.tsx`
- Knowledge document DB source 설정 화면: `apps/client/app/dashboard/knowledge/[id]/document/[documentId]/page.tsx`

Connectors UI는 현재 Knowledge 화면 내부의 DB source 설정 부분으로 제공된다. Organization-wide connector inventory, connection 공유/권한 관리, connector health/remediation 전용 화면은 아직 구현되지 않았다.

## Target Knowledge Source Connector Boundary

목표 Knowledge Source Connector는 현재 workflow DB connector UI/API를 그대로 확장한 것이 아니라 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)의 server-side allowlist adapter 경계를 따른다. MCP/API source도 LLM 자유 tool-use surface가 아니며, source listing, changed item listing, content fetch, ACL/tombstone listing, capture event normalization, runtime authorization primitive처럼 승인된 operation만 호출할 수 있다.

Private source-managed retrieval에 쓰이는 connector는 `check_access_batch(subject_ref, source_item_refs[])`를 우선 제공해야 한다. Batch 미지원 source는 bounded single `check_access(subject_ref, source_item_ref)` fallback을 제공할 수 있지만, runtime authorization primitive가 없으면 Knowledge retrieval 후보가 아니라 remediation 대상이다. Connector component와 UI는 raw source payload, raw principal, raw source URL/path/title, raw tool error를 표시하거나 durable log/audit/trace에 남기지 않고 safe reason code와 remediation state만 전달한다.

File/page artifact connector는 egress guard 이후에도 artifact content를 trusted로 취급하지 않는다. Target component는 content safety gate와 parser isolation worker를 거쳐 macro/script/embedded object/executable, archive bomb, unsupported type, scan timeout/unknown을 fail-closed 또는 remediation으로 전달해야 한다.

## Components

### Production Connector TCP Transport

- `apps/shared/services/connector_tcp_transport.py`는 `connector-egress-v1`, exact internal proxy endpoint와 최대 16개의 배포 관리 target port를 검증한다.
- Strict Connector test, 저장된 PostgreSQL schema/runtime 사용과 Workflow SSH compatibility는 기존 egress guard가 반환한 literal public IP만 전용 `3130` CONNECT authority로 전달한다. Hostname을 proxy에서 다시 해석하지 않는다.
- PostgreSQL은 원래 hostname과 loopback relay `hostaddr`를 분리해 TLS hostname 의미를 보존한다. SSH는 검증 IP에 이미 연결된 socket을 tunnel adapter에 전달한다.
- Proxy 또는 local relay 생성 실패, disallowed/private/mapped target과 startup config drift는 provider I/O 전에 safe failure로 닫고 direct fallback하지 않는다. Connector credential과 target은 proxy log에 남기지 않는다.

### `connectorApi`

- 출처: `apps/client/app/features/knowledge/api/connectorApi.ts`
- 책임: `/connectors` API 호출 payload를 클라이언트 `DBConfig`에서 Gateway schema로 변환한다.
- 제공 호출:
  - `createConnector(config)`: `POST /connectors`
  - `testConnection(config)`: `POST /connectors/test`
  - `getSchema(connectionId)`: `GET /connectors/{connection_id}/schema`
  - `getConnectionDetails(connectionId)`: `GET /connectors/{connection_id}`
- 경계:
  - React 상태를 소유하지 않는다.
  - Secret redaction을 직접 수행하지 않는다. Secret 비노출은 Gateway 응답 계약에 의존한다.
  - API 오류는 status와 allowlist reason code만 `{ success: false, message, status?, reasonCode? }`로 정규화한다. Raw Axios error, request config와 backend diagnostic은 console/UI에 전달하지 않는다.

### `DBConnectionForm`

- 출처: `apps/client/app/features/knowledge/components/create-knowledge-modal/DBConnectionForm.tsx`
- 책임: PostgreSQL DB 연결 정보와 선택적 SSH tunnel 정보를 입력하고 연결 테스트를 실행한다.
- Strict test는 기본적으로 public PostgreSQL과 deployment-managed port allowlist만 지원한다. Development demo는 서버가 설정한 exact local hostname+port와 전용 CA에 한해 동일 UI를 사용한다. UI 기본값은 `5432`이고 allowlist 밖 port는 safe target-policy 실패로 표시한다. SSH 입력은 create/schema compatibility를 위해 유지하지만 `ssh.enabled=true` test는 safe 미지원 결과를 표시한다.
- 소비자:
  - `CreateKnowledgeModal`
  - Knowledge document DB source 설정 화면의 connection edit flow
- 렌더링: 필수 연결 이름(최대 100자), DB 타입, DB host/port/database/username/password, 선택적 SSH tunnel 설정, SSH 인증 방식(`password`, `key`), private key file input, `연결 테스트` 버튼, 성공/실패 상태 메시지를 표시한다.
- 현재 기본값: `initialConfig`가 없으면 입력은 비어 있고 DB type은 `postgres`, port는 `5432`, SSH는 비활성이다. Host placeholder는 public hostname 예시이며 local target을 기본 허용으로 오해하게 하는 loopback IP를 제시하지 않는다.
- 경계:
  - 실제 저장은 직접 하지 않고 부모가 전달한 `onTestConnection`과 `onChange`에 위임한다.
  - private key file은 브라우저에서 text로 읽어 local state에 넣는다.
  - 입력값 secret은 화면 state에 존재하므로 로그/토스트/문서에 원문을 남기면 안 된다.

### `CreateKnowledgeModal` DB Source Flow

- 출처: `apps/client/app/features/knowledge/components/create-knowledge-modal/index.tsx`
- 책임: Knowledge Base에 DB source를 추가하는 과정에서 DB 연결 테스트와 connection 생성 요청을 조율한다.
- 동작:
  - source type이 `DB`이면 `DBConnectionForm`을 렌더링한다.
  - `handleTestDBConnection`은 `connectorApi.testConnection(config)`를 호출한다.
  - 테스트 성공 시 Gateway message 또는 `DB 연결 테스트 성공!` toast를 표시한다.
  - 테스트 실패 시 Gateway message 또는 `DB 연결에 실패했습니다.` toast를 표시한다.
  - DB source 제출 시 필수 DB 입력이 없으면 alert로 중단한다.
  - DB source 제출 시 `connectorApi.createConnector(dbConfig)`를 호출한다.
  - connection 생성 성공 시 반환된 `id`를 Knowledge source 생성 payload의 connection id로 사용한다.
  - connection 생성 실패 시 toast를 표시하고 Knowledge source 생성 흐름을 중단한다.
- 경계:
  - Knowledge Base 생성, 문서/source lifecycle, ingestion 실행은 Knowledge feature 책임이다.
  - Connector form이 성공했다고 해서 KB use permission이 승인된 것은 아니다.

### `DBSchemaSelector`

- 출처: `apps/client/app/features/knowledge/components/document-settings/DBSchemaSelector.tsx`
- 책임: 저장된 connection id로 DB schema를 불러오고, ingestion에 사용할 테이블/컬럼/민감 컬럼/alias/JOIN 구성을 선택한다.
- 렌더링: loading/empty 상태, 테이블 검색, 자동 청킹 checkbox, 선택적 `DB 연결 수정` 버튼, 테이블/컬럼 선택, 민감 컬럼 표시, alias 입력, FK 기반 JOIN 안내 또는 경고를 표시한다.
- 제한:
  - 한 번에 최대 2개 테이블 선택을 허용한다.
  - 2개 테이블 선택 시 FK 관계가 없으면 연결 불가 안내를 표시한다.
  - schema 조회 실패 시 `테이블 정보를 불러오는데 실패했습니다.` toast를 표시한다.
- 경계:
  - 실제 schema 조회 permission은 Gateway owner check에 의존한다.
  - 민감 컬럼 선택은 ingestion metadata/처리 정책에 넘길 UI 입력이며, connector API의 secret redaction을 대체하지 않는다.

### `DbSourceViewer`

- 출처: `apps/client/app/features/knowledge/components/ingestion-views/DbSourceViewer.tsx`
- 책임: Knowledge DB source 설정에서 DB schema selector를 표시하고 connection edit 진입점을 연결한다.
- 경계:
  - DB source 문서 처리와 chunk 생성은 Knowledge ingestion processor 책임이다.

## States

### `DBConnectionForm`

- `config`, `loading`, `testStatus`를 로컬 state로 관리한다.
- `initialConfig`는 `connectorApi.getConnectionDetails`가 Gateway snake_case wire shape를 camelCase `DBConfig`로 검증·정규화한 값만 받는다. `connection_name`과 `ssh.auth_type`은 각각 `connectionName`과 `ssh.authType`으로 복원한다.
- DB/SSH 입력 변경 시 `config`를 갱신하고 부모 `onChange(newConfig)`를 호출하며 `testStatus`를 `idle`로 되돌린다.
- `ssh.enabled`와 `ssh.authType`에 따라 SSH password input 또는 private key file input을 표시한다.
- `handleTest`는 부모 `onTestConnection(config)` 결과에 따라 `연결 성공!` 또는 `연결 실패` 상태를 표시하고, pending 중 버튼을 disabled 처리한다.
- 모든 소비자는 boolean 대신 `{ success, retryAfter? }` 결과를 반환해야 한다. Knowledge document 편집 흐름도 같은 계약을 사용하되 현재 create API에는 cooldown metadata가 없으므로 `success`만 반환한다.

### `CreateKnowledgeModal` DB Flow

- 연결 테스트는 `connectorApi.testConnection`의 safe result에 따라 success/error toast를 표시하고 `success`, optional `retryAfter` 결과를 폼에 반환한다.
- `401/404`는 인증/organization context 오류, `429`는 잠시 후 재시도, `503`은 test service 일시 불가의 고정 메시지로 표시한다. Backend raw message는 표시하지 않는다.
- DB source 제출 전 공백 제거한 `connectionName`과 `host`, `port`, `database`, `username`, `password`를 검증하고 누락 시 alert로 중단한다.
- `connectorApi.createConnector`가 success와 id를 반환하면 Knowledge source payload에 connection id를 포함한다.
- connection 생성이 실패하거나 예외가 발생하면 toast를 표시하고 Knowledge source 제출을 중단한다.

### `DBSchemaSelector`

- `connectionId`가 있으면 mount 또는 id 변경 시 schema를 조회하고 loading/empty/error 상태를 표시한다.
- 조회 성공 시 `tables = res.tables || []`로 설정하고 검색어로 table list를 필터링한다.
- table/column 선택은 alias 기본값을 함께 관리하며, 민감 컬럼 toggle은 부모 state로 전달한다.
- 선택 테이블이 2개이면 FK 관계를 검사해 join config를 부모에 전달하거나 FK 없음 경고를 표시한다.

### `ConnectionUseResolver`

- 출처: `apps/shared/services/connection_use_resolver.py`
- 책임: opaque Connection UUID와 execution subject user UUID를 정규화하고 현재 user-owned 모델의 `Connection.id` + `Connection.user_id` predicate를 한 query에서 평가한다.
- 호출자: RAG DB source 등록, Knowledge document process/preview 설정 검증, `DbProcessor`를 통한 Gateway/Workflow Engine background ingestion.
- 경계:
  - Missing, malformed, deleted, owner 변경과 non-owner를 구분하지 않고 `resource.hidden`으로 닫는다.
  - 실제 DB use 조회는 connector 생성·credential 복호화·DB dial보다 먼저 수행하고 저장소 장애를 safe temporary failure로 닫는다. 이 조회 자체는 runtime row lock을 소유하지 않는다.
  - KB/Collection 권한, organization membership, HTTP 오류 shape와 DB protocol 정책을 소유하지 않는다.
  - Organization-scoped Connection 권한을 추측하거나 신규 permission을 만들지 않는다.

### `ConnectionRuntimeSnapshotProvider`

- 출처: `apps/shared/services/connection_runtime_snapshot.py`
- 책임: 주입된 session factory로 독립 session을 열고 `ConnectionUseResolver`의 owner 판정을 재사용해 adapter type과 최소 credential configuration을 immutable snapshot으로 투영한다.
- 경계:
  - Password/SSH credential 복호화는 provider 안에서만 수행하고 ORM entity와 encrypted field를 processor에 반환하지 않는다.
  - PostgreSQL database/username은 공백뿐인 값을 거부하되 저장된 text를 그대로 전달한다. 기존 password-auth SSH row에 encrypted password가 없으면 agent/default-key compatibility를 위해 `password=None`으로 투영하고 복호화를 호출하지 않는다.
  - Success, hidden, configuration failure와 store failure 모두 transaction을 종료하고 session을 닫은 뒤 caller에 typed result/error를 반환한다.
  - Connector 생성·dial·query, chunking과 embedding을 호출하지 않는다.
  - Snapshot DTO는 API/metadata/audit/trace serialization 대상이 아니며 repr에 secret을 포함하지 않는다.

### `ConnectionLifecycleService`

- 출처: `apps/gateway/services/connection_lifecycle_service.py`
- 책임: Connection reference 저장·교체·삭제를 owner Connection row lock으로 직렬화하고 committed Document reference 및 writer의 expected Document revision을 확인한다.
- 경계:
  - PostgreSQL local `lock_timeout=2s`를 적용하고 lock 획득 또는 reference mutation flush/commit의 timeout/deadlock/serialization victim을 retryable `connection.reference_busy`로 정규화한다. 기타 commit/store 오류는 전체 rollback 뒤 `connection.reference_unavailable`로 닫는다.
  - 전역 lock 순서는 `Connection -> KnowledgeBase -> Document/DocumentVersion`이며 caller는 역순으로 이 service를 호출하지 않는다.
  - Existing Document reference writer는 Connection 잠금 뒤 Document를 `populate_existing + FOR UPDATE`로 다시 읽고 최초 조회 `updated_at`과 다르면 `connection.reference_conflict`로 전체 rollback한다.
  - 외부 DB/storage/provider I/O를 수행하지 않고 typed failure 뒤 전체 transaction을 rollback한다.
  - Lock log/metric은 outcome과 coarse wait/hold bucket만 사용한다. Hold bucket은 실제 SQLAlchemy transaction 종료 시 기록한다.

### Lock Compatibility Matrix

| 작업 | Platform DB transaction/lock | 종료 시점 | 재시도 계약 |
| --- | --- | --- | --- |
| Knowledge 설정 preflight | Caller의 짧은 authorization read, row lock 없음 | 설정 검증 직후 | Hidden/configuration은 terminal, store unavailable만 surface 정책에 따라 retryable |
| Runtime snapshot | Provider 전용 session의 짧은 owner/config read, row lock 없음 | Snapshot DTO 반환 전 commit/rollback과 close | Store unavailable만 retryable |
| Reference 저장·교체 | Reference UoW가 `Connection`을 먼저 잠그고 이후 `KnowledgeBase -> Document/Version` 순서로 fresh lock/revision compare 후 mutation | Metadata flush/commit 또는 전체 rollback | Stale revision은 terminal `reference_conflict`, busy/store unavailable은 새 session·전체 transaction에서만 retryable |
| Connection 삭제 | Reference UoW가 `Connection`을 잠그고 committed Document reference를 확인 | Delete commit 또는 전체 rollback | `in_use`는 terminal, busy/store unavailable만 새 요청에서 retryable |
| 외부 DB schema/fetch | Runtime snapshot session은 닫혀 있고 Connection row lock은 없음. Gateway schema는 request transaction도 종료하며 KC sync의 document lock은 ADR-0048을 따른다. | Connector engine/tunnel 정리 | Partial result를 재사용하지 않으며 caller의 typed policy만 적용 |
| KC source read/chunk/finalization | ADR-0048의 document advisory/target row lock과 version finalization UoW | Version CAS commit/rollback | Connection lock과 중첩하지 않고 KC item attempt 정책을 사용 |

Reference UoW가 `Connection` lock을 보유한 동안 network/storage/provider 호출을 시작해서는 안 된다. 반대로 KC document/version lock을 보유한 경로는 Connection lock을 뒤늦게 획득하지 않는다.

## Interactions

### Connection Test

1. 사용자가 `DBConnectionForm`에서 연결 정보를 입력한다.
2. 사용자가 `연결 테스트`를 클릭한다.
3. `DBConnectionForm`은 부모 `onTestConnection(config)`를 호출한다.
4. `CreateKnowledgeModal`은 `connectorApi.testConnection(config)`를 호출한다.
5. `connectorApi`는 클라이언트 `DBConfig`를 Gateway strict `ConnectorTestRequest`로 매핑해 `POST /connectors/test`를 호출한다.
6. 성공하면 success toast와 `연결 성공!` 상태가 표시된다.
7. 실패하면 error toast와 `연결 실패` 상태가 표시된다.
8. Pending 중 중복 클릭을 막고, `429 Retry-After`가 있으면 bounded cooldown 동안 재시도를 비활성화한다.

Gateway는 request network를 raw socket peer 문자열로 직접 사용하지 않고 공통 trusted-proxy resolver를 통해 `/24` IPv4 또는 `/64` IPv6 단위 identity로 정규화한다. 설정된 trusted proxy에서 온 요청만 forwarded chain을 사용하며, identity를 해석할 수 없으면 DB 입력 body를 처리하기 전에 fail-closed한다.

### Local/Docker TLS Demo

Local demo는 일반 stack과 분리된 `connector-demo` Compose profile을 명시적으로 시작한 경우에만 사용한다.

1. Host-run은 `docker compose -f dev/docker-compose.yml --profile connector-demo up -d --build --wait connector-test-redis connector-test-tls-init connector-test-postgres`로 격리된 Redis와 TLS PostgreSQL을 시작하고 health 완료를 기다린다.
2. Docker 통합 모드는 `docker compose -f docker/docker-compose.yml -f docker/docker-compose.connector-demo.yml --profile connector-demo up -d --build --wait`를 사용한다. Service-name runtime probe는 같은 파일에 `--profile connector-demo --profile connector-demo-verify run --rm connector-test-runtime-verifier`를 사용한다.
3. `scripts/copy_connector_demo_password.ps1 -Mode dev` 또는 `-Mode docker`를 실행해 생성 credential을 stdout 없이 clipboard에 복사한다.
4. Host-run Gateway에서는 `localhost`, port `55432`; Docker Gateway에서는 `connector-test-postgres`, port `5432`를 입력한다. Database는 `connector_demo`, username은 `connector_demo_user`를 사용한다.
5. Gateway가 사용하는 explicit local-profile flag, exact-target, 공개 CA와 Connector 전용 Redis 환경 설정은 profile 또는 `dev/.env.example`을 따른다. Production 설정으로 복사하지 않는다.

Runtime-generated 공개 CA는 git-ignore된 `local/connector-test-tls/<mode>/ca.crt`에 export한다. CA signing key는 init container의 임시 filesystem에서만 사용하고 persistent volume에 쓰지 않는다. Server TLS material, PostgreSQL bootstrap admin credential, Connector demo credential은 서로 다른 private named volume에 두고 Gateway는 어느 private volume도 mount하지 않는다. Public CA 파일도 source artifact나 Docker build context로 커밋하지 않는다.

Demo PostgreSQL은 전용 bridge network와 loopback publish를 함께 사용한다. TCP는 TLS 1.2 이상 `hostssl`/SCRAM만 허용하고 평문은 거부한다. `connector_demo_user`는 read-only 기본 transaction, bounded statement timeout과 connection limit을 가진 non-superuser다. PostgreSQL만 server TLS, bootstrap admin credential, Connector demo credential volume을 읽는다. One-shot verifier는 Connector demo credential volume만 읽고 server TLS와 bootstrap admin credential volume은 읽지 않는다.

Docker demo Gateway는 Connector admission에만 `connector-test-redis` logical DB 15를 사용한다. Platform Redis URL은 유지하므로 workflow broker/result, pub/sub와 다른 Gateway 기능을 demo Redis로 우회시키지 않는다. Connector-specific client는 Gateway composition이 소유하고 lifespan 종료 시 닫는다.

### Connection Create During DB Source Submit

1. 사용자가 DB source 정보를 입력하고 Knowledge source 추가를 제출한다.
2. `CreateKnowledgeModal`은 공백 제거한 연결 이름을 포함한 DB 필수 입력을 확인한다. 공백뿐인 이름이면 API를 호출하지 않는다.
3. `connectorApi`가 연결 이름의 앞뒤 공백을 제거한 뒤 `createConnector(dbConfig)` 요청을 보낸다.
4. Gateway는 연결을 재테스트하고 secret을 암호화 저장한다.
5. 성공하면 반환된 connection id가 Knowledge source 생성 payload에 포함된다.
6. Gateway는 document/KB 생성 전에 current user가 해당 Connection owner인지 resolver로 확인하고 document metadata에는 opaque id만 저장한다.
7. 실패하면 resource identity를 노출하지 않는 오류를 반환하고 Knowledge source 생성이 중단된다.

### Knowledge DB Source Process And Preview

1. Gateway는 submitted DB config의 Connection reference를 기존 opaque reference와 함께 정규화한다.
2. Connection Use Resolver가 current user owner 조건을 확인한 뒤 allowlisted table/column/JOIN/chunk 설정만 document metadata에 저장하거나 preview runtime config로 전달한다.
3. Background `DbProcessor`는 외부 DB dial 직전에 `ConnectionRuntimeSnapshotProvider`를 호출한다. Provider는 독립된 짧은 session에서 같은 resolver로 owner를 재검증하고 최소 runtime snapshot을 만든 뒤 session을 닫는다.
4. Snapshot provider가 종료된 뒤에만 connector를 생성하고 외부 DB를 호출한다. Connection 삭제, owner 변경, malformed/non-owner reference 또는 credential 복호화 실패는 connector 호출 전에 safe configuration/resource-hiding failure로 종료한다.
5. Runtime PostgreSQL fetch는 connect/statement timeout, batch·row·byte cap을 적용하고 취소/예외에서 adapter resource를 정리한다.
6. Processor result와 chunk source label은 Connection id/name/host/user/credential을 포함하지 않는다.

### Schema Selection

1. `DBSchemaSelector`는 `connectionId`를 받으면 `connectorApi.getSchema(connectionId)`를 호출한다.
2. Gateway는 current user ID를 scalar로 먼저 복사하고 기존 관리 API의 404/403 owner precheck를 짧은 transaction에서 수행한 뒤 rollback한다. Rollback 뒤 request-session ORM user attribute를 다시 읽지 않는다.
3. 독립 `ConnectionRuntimeSnapshotProvider`가 owner를 다시 확인하고 최소 runtime config를 만든 뒤 session을 닫는다.
4. PostgreSQL connector는 read-only/statement-timeout 설정으로 schema를 조회한다.
5. UI는 table/column/FK 정보를 표시한다.
6. 사용자는 최대 2개 테이블과 필요한 컬럼을 선택한다.
7. 선택한 컬럼 alias, 민감 컬럼, 자동 청킹, JOIN config는 부모 Knowledge 설정 state로 전달된다.

### Connection Edit

1. document settings 화면에서 사용자가 `DB 연결 수정`을 클릭한다.
2. 화면은 `connectorApi.getConnectionDetails(connectionId)`로 secret 없는 connection detail을 조회한다.
3. `connectorApi`는 detail 응답의 필수 문자열·port·지원 type을 검증하고 `connection_name`, `ssh.auth_type`을 form의 `connectionName`, `ssh.authType`으로 변환한다. `DBConnectionForm`은 이 정규화된 `DBConfig`를 `initialConfig`로 받아 열린다.
4. detail 응답에는 DB password, SSH password, SSH private key가 없고, form은 secret 입력값에 현재 fallback을 사용한다. 재연결 시 필요한 secret은 사용자가 다시 입력해야 한다.
5. document settings 화면의 `연결 테스트`는 `handleConnectionRequest`를 통해 새 connection 생성을 수행하고, 반환된 id로 상위 DB config를 재연결한 뒤 `{ success }` 결과를 form에 반환한다.
6. 현재 구현에는 별도 update endpoint가 없으므로 기존 connection row의 in-place update로 해석하지 않는다.

## Accessibility

### `DBConnectionForm`

- 기본 `input`, `select`, `button` 요소를 사용하고, `연결 테스트` 버튼은 loading 중 disabled 상태를 사용한다.
- 현재 label 요소는 보이는 텍스트를 제공하지만 `htmlFor`와 input `id`가 연결되어 있지 않다.
- 현재 성공/실패 상태 메시지는 보이는 텍스트와 icon으로 표시되지만 `role="status"`, `role="alert"`, `aria-live`를 설정하지 않는다.
- private key file input은 숨겨진 input과 label 클릭 영역으로 동작한다.

### `DBSchemaSelector`

- 검색 input, 자동 청킹 checkbox, DB 연결 수정 button은 기본 form/button 요소를 사용한다.
- icon-only에 가까운 control 일부는 `title` 또는 보이는 텍스트를 제공하지만, 모든 icon control이 명시적 accessible name을 보장한다고 보기는 어렵다.
- FK/JOIN 상태는 텍스트 안내를 함께 표시한다.
- schema 조회 실패는 toast로만 표시되며, selector 내부의 접근 가능한 오류 영역은 없다.
