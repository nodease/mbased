# ADR-0034: Knowledge 위임 관리와 KB RBAC 경계

Status: Accepted

Related ADRs: [ADR-0006](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0023](ADR-0023-audit-actor-access-management-boundary.md)

## Context

Knowledge Base API에는 Team/User resource permission이 이미 있지만, 일부 KB
설정·문서·처리·삭제 경로는 `knowledge_bases.user_id`를 권한 근거로 사용한다.
이 때문에 Team에 실무 권한을 부여해도 owner가 아니면 관리할 수 없는 경로와,
owner라는 이유만으로 중앙 RBAC를 우회하는 경로가 함께 존재한다.

Organization manager가 모든 KB와 Collection을 직접 만들고 사용자별 권한을
계속 부여하는 운영 방식도 확장하기 어렵다. 반면 Knowledge 관리 위임을 KB
content 사용 권한과 합치면 카탈로그 담당자가 인사·법무 문서 원문까지 읽는
과도한 권한을 얻게 된다. 관리 plane과 content plane을 분리하면서 Team 중심
위임, resource 담당자 예외, public exposure 통제가 함께 필요하다.

## Options

1. `user_id` owner bypass를 유지하고 누락 endpoint만 보강한다.
2. 모든 Knowledge 작업을 Organization manager 전용으로 만든다.
3. owner를 귀속 정보로 전환하고, resource RBAC와 별도의 organization-scoped
   Knowledge 관리 action을 도입한다.

## Decision

선택지 3을 채택한다.

### KB resource actions

KB의 canonical action은 다음과 같다.

| action | 최소 `auth_state` | 범위 |
| --- | --- | --- |
| `read` | `viewer` | safe KB label, 설명, 상태, safe document summary |
| `use` | `operator` | RAG retrieval. Source-managed KB는 source authorization gate도 필요 |
| `write` | `builder` | 이름·설정 변경, manual document 등록·처리·preview·sync |
| `content_read` | `builder` | eligible manual original content. Source-managed content는 별도 source/display policy도 필요 |
| `manage` | `manager` | resource permission, safe catalog metadata, archive/restore |

Organization manager override와 active Team/User direct grant 중 가장 강한
additive allow를 사용한다. Source policy grant는 문서화된 `use` 경로에만
합산하고 source authorization을 우회하지 않는다. `content_read`는 별도 저장
state를 만들지 않고 `builder` 이상에 매핑하되 property-level gate로 평가한다.

### Owner migration and creation

- `knowledge_bases.user_id`는 생성자/귀속/audit attribution이다. owner bypass는
  migration 완료 후 권한 근거로 사용하지 않는다.
- 같은 organization의 active member인 legacy owner에게 user-direct `manager`
  grant를 idempotent하게 backfill한다. 조직 불일치, inactive user, membership
  부재 또는 모호한 row는 grant하지 않고 safe finding code와 bucketed count만
  남긴다.
- Active organization member는 manual KB를 만들 수 있다. KB row, 생성자의
  user-direct `manager` grant, data-change audit는 한 transaction에서 commit한다.
- KB는 Collection에 속하지 않아도 되고, 0개 이상의 Collection에 연결될 수
  있다.

### Organization-scoped Knowledge delegation

다음 additive allow table을 도입한다.

- `team_knowledge_domain_permissions`
- `user_knowledge_domain_permissions`

각 row는 organization, grantee, `permission_action`, `assigned_by`,
`assigned_at`, optional `expires_at`, non-negative `flags`를 가진다. 허용 action은
다음과 같다.

| action | 허용 | 명시적 제외 |
| --- | --- | --- |
| `catalog_manage` | private manual Collection 생성, safe catalog metadata와 private membership 관리 | KB `use`/원문, Collection `route`, public exposure |
| `permission_delegate` | KB/Collection resource permission 조회·부여·회수 | domain permission 부여, self/own-Team content access 상승 |
| `lifecycle_manage` | eligible manual KB/Collection archive·restore | hard delete, system-managed source-owned mutation |
| `sync_manage` | 승인된 sync/remediation 실행 | source ACL 우회, raw source/credential 조회 |

Organization manager만 domain permission을 부여하거나 회수할 수 있다. Domain
permission은 KB `read/use/write/content_read/manage` 또는 Collection
`read/route/manage/sync`를 암묵적으로 부여하지 않는다.

`permission_delegate`만으로 resource permission을 관리하는 actor는 자신,
자신으로 resolve되는 user, 자신이 active member인 Team에 content-plane grant를
부여할 수 없다. Organization manager는 이미 recovery authority를 가지므로 이
제한에서 제외한다. Resource-specific KB/Collection manager는 해당 resource
범위 안에서만 기존 grant를 관리할 수 있다.

### Collection and destructive operations

- Domain `catalog_manage`로 생성한 Collection은 client 입력과 무관하게 private다.
- Private Collection membership은 Collection `manage` + 대상 KB `manage`, 또는
  domain `catalog_manage`로 관리한다.
- Public Collection의 link, unlink, reorder는 visibility flag 변경과 같은 public
  exposure mutation이다. Organization manager와 명시적 acknowledgement가
  필요하며 source-managed KB public approval은 별도로 검증한다.
- Public/private visibility 전환과 KB hard delete는 V1에서 Organization manager
  전용이다. Hard delete는 명시적 acknowledgement와 retention gate를 요구한다.
- Approved retention/legal-hold policy primitive가 production에 연결되기 전에는
  hard delete eligibility checker가 기본 거부한다. Transaction/cleanup test는
  명시적으로 allow checker를 주입해 mechanics를 검증하지만 production allow의
  근거로 사용하지 않는다.
- UI role bundle은 explicit action row를 transactionally 적용하는 편의 기능이다.
  Viewer=`read`, Workflow Router=`read+route`, Maintainer=`read+manage`, Sync
  Operator=`read+sync`이며 KB `use`를 만들지 않는다.
- 위임 대상 picker는 권한 변경 권한을 통과한 actor에게만 active Team/User의
  opaque id와 safe label을 반환한다. Team을 기본 선택으로 두며 raw principal,
  email, source identity는 위임 대상 응답과 감사 metadata에 포함하지 않는다.

### Resource hiding, audit, and architecture

- unknown, cross-organization, deleted 또는 completely invisible resource는
  `404 resource.hidden`; same-scope visible resource의 action 부족은
  `403 permission.denied`; list는 unauthorized row를 생략한다.
- 응답과 오류는 hidden id/name/count, raw source metadata, raw payload,
  credential을 포함하지 않는다.
- Permission, lifecycle, membership, domain grant/revoke mutation과 canonical
  audit row는 같은 transaction에서 commit한다. Audit에는 actor, organization,
  target type/id, action, 결과와 변경된 permission code 같은 safe metadata만
  허용한다.
- 신규 mutation flow는 ADR-0022의 application use case, port, SQLAlchemy adapter,
  UnitOfWork 경계를 따른다. Controller는 request/dependency/response mapping만
  담당한다.

### Document progress SSE authorization transport

Context: `GET /api/v1/rag/document/{document_id}/progress`는 document 상태와 safe
error를 지속적으로 반환하지만 기존 구현은 KB 권한을 확인하지 않았다. Native
`EventSource`는 custom `X-Organization-Id` header를 설정할 수 없다.

검토한 선택지는 (1) document에서 organization을 추론해 active organization
선택을 생략, (2) SSE URL query에 active organization UUID 전달, (3) frontend
streaming fetch parser 또는 same-origin proxy를 새로 도입하는 방식이다.

V1은 선택지 2를 채택한다. Client는 `organizationId` query parameter를 URL
encoding해 전달하고 Gateway는 UUID 형식과 active organization 범위, KB `read`를
stream 생성 전에 검증한다. Organization UUID는 credential은 아니지만 URL에는
raw document/source metadata나 token을 넣지 않는다. 이 결정은 기존
`EventSource` reconnect 동작을 유지하면서 unauthorized event 한 건도 전송하지
않는 가장 작은 cutover다.

영향 파일은 RAG progress endpoint, Knowledge client URL builder, Knowledge API/test
문서와 해당 Gateway/Client 테스트다. 후속 검토에서는 공통 authenticated SSE
proxy가 도입될 때 query transport를 header 기반으로 대체할지 평가한다.

### Hard-delete retention eligibility

Context: hard delete는 retention/legal-hold gate를 요구하지만 현재 KB schema와
서비스에는 승인된 eligibility primitive가 없다. 검토한 선택지는 (1) manager와
acknowledgement만으로 삭제 허용, (2) 임시 retention 필드를 이 이슈에서 설계,
(3) production checker를 기본 거부하고 후속 정책 primitive가 연결될 때만 허용이다.

선택지 3을 채택한다. 선택지 1은 Accepted gate를 우회하고 선택지 2는 별도
retention policy, legal-hold, recovery 계약 없이 schema를 선점한다. Gateway
lifecycle service는 checker 미구성 또는 평가 오류를 safe `policy.denied`로 닫고
storage/DB mutation을 시작하지 않는다. Transaction/cleanup 테스트만 명시적인
test-only allow checker를 주입해 mechanics를 검증한다.

영향 파일은 Knowledge lifecycle service/endpoint, lifecycle/API/PostgreSQL 테스트,
Knowledge API/test 문서다. 후속 이슈에서 approved retention/legal-hold policy port와
운영 상태 모델을 확정한 뒤 production checker를 연결하고 이 default deny를
재검토한다.

### MBA-241 post-merge response and revocation hardening

Context: MBA-231 merge 후 세 가지 property-level 경계 결함이 확인됐다. 첫째,
KB detail과 document detail이 내부 `documents.meta_info`를 그대로 투영하면
encrypted API source config, connection/source identifier와 미래에 추가되는 내부
field가 KB `read` actor에게 노출될 수 있다. 둘째, domain grant 회수도 grant와
같은 active subject lock을 요구하면 제거·비활성화된 User 또는 inactive Team의
기존 row를 Organization manager가 회수할 수 없다. 셋째, domain
`catalog_manage`만 가진 actor의 Collection item projection이 manual KB `name`을
fallback label로 사용하면 KB `read`를 우회해 resource label을 노출한다.

검토한 선택지는 다음과 같다.

1. 기존 pass-through와 active subject lock, raw name fallback을 유지한다.
2. 알려진 secret/source key만 denylist하고 inactive subject를 임시 재활성화한 뒤
   회수하며 모든 manual KB label을 generic하게 만든다.
3. Document metadata는 safe operational field allowlist로 투영하고, grant와 revoke
   lock 경계를 분리하며, Collection label은 safe metadata와 독립 KB `read` 판정을
   조합한다.

선택지 3을 채택한다.

- KB/document read response의 `meta_info`는 progress, processing state/timestamp,
  bounded processing option과 numeric cost estimate처럼 명시된 safe operational
  field만 반환한다. `api_config`, encrypted config field, `connection_id`,
  `source_identity_id`, connector/source reference, DB connection label/config와
  unknown nested field는 값이 암호화됐는지와 관계없이 반환하지 않는다. 새 내부
  field는 allowlist에 명시되고 negative test가 추가되기 전까지 기본 비노출이다.
- Domain grant는 계속 같은 organization의 active Team 또는 active member User를
  row lock으로 확인한다. Revoke는 active subject 존재를 요구하지 않고
  organization, subject type/id, action으로 기존 permission row 자체를 lock한 뒤
  삭제한다. Row가 없으면 idempotent unchanged이며, row가 있으면 delete와 canonical
  audit를 같은 transaction에서 commit한다.
- Collection item/link-candidate label은 유효한 `safe_metadata.safe_label` 또는
  display-policy-approved source safe label을 우선한다. 그런 label이 없을 때 manual
  KB의 실제 `name`은 caller가 별도의 KB `read` 판정을 통과한 경우에만 반환하고,
  domain `catalog_manage`만 있는 caller에게는 generic label을 반환한다.

이 결정은 API path나 DB schema를 바꾸지 않는다. 영향 파일은 Knowledge response
projection helper, KB/document endpoint와 query service, domain permission use
case/repository, Collection service, Knowledge 공식 문서와 해당 unit/API regression
test다. 후속 검토에서는 source config 편집 UI가 필요한 경우 KB `write`와 별도
property-level response model을 가진 전용 endpoint를 설계하며, 이번 read projection을
다시 넓히지 않는다.

### MBA-241 review follow-up: edit configuration, safe failures, and revoke proof

Context: default-deny document metadata projection은 encrypted source config 노출을
차단하지만, 기존 Client는 같은 `meta_info`에서 `segment_identifier`, DB selection,
`connection_id`를 복원했다. 빈 초기값으로 document process request를 보내면 저장된
DB edit configuration을 덮어쓸 수 있다. 또한 persisted legacy `str(exception)`이
`DocumentResponse.error_message`와 progress SSE의 `message`/`error`로 다시 노출되고,
revoke repository unit test가 filter expression을 실행하지 않는 fake를 사용해
organization predicate와 row lock 누락을 잡지 못했다.

검토한 선택지는 다음과 같다.

1. KB `read` detail에 기존 `meta_info`를 복원한다.
2. DB/API document 설정 UI를 전부 비활성화한다.
3. Read projection은 유지하고 KB `write` 전용 edit-config endpoint를 추가하며,
   Client는 해당 configuration hydration 성공 전 preview/process를 차단한다. Public
   status/error projection은 fixed safe message/code만 사용하고, revoke query는
   compile 가능한 organization-scoped `SELECT ... FOR UPDATE` statement로 검증한다.

선택지 3을 채택한다. 선택지 1은 property-level authorization을 다시 무너뜨리고,
선택지 2는 안전하지만 기존 manual document 운영 흐름을 불필요하게 제거한다.

- `GET /api/v1/knowledge/{kb_id}/documents/{document_id}/edit-config`는 active
  organization과 KB `write`를 요구한다. Response는 chunk/process option과 DB edit에
  필요한 bounded allowlist만 반환한다. `connection_id`는 opaque UUID reference로만
  허용하며 API URL/header/body, encrypted value, credential, connection secret/raw
  connection detail은 반환하지 않는다. Malformed legacy config는 raw fallback하지
  않고 safe unavailable response로 닫으며 Client는 저장을 차단한다. Nested field별
  cap 외에도 aggregate item/serialized response budget을 적용하고 성공 응답은
  `Cache-Control: no-store`로 중간 저장을 금지한다. Client 권한/config readiness는
  현재 KB/document scope에 결박하고 늦게 도착한 이전 request 결과는 폐기한다.
- KB detail/direct document detail의 `error_message`와 progress SSE의 processing
  step/error는 persisted raw string을 그대로 사용하지 않는다. Status에 대응하는
  fixed public message와 generic failure message만 반환하고, legacy exception string은
  DB 내부에 남아 있어도 response/SSE에 나타나지 않는다. Progress SSE는
  `Cache-Control: no-cache, no-store`와 proxy buffering disable header를 사용한다.
- Domain revoke adapter는 organization, subject, action predicate와 `FOR UPDATE`를
  포함한 statement를 실행한다. Unit contract는 PostgreSQL dialect로 statement와
  bind value를 검증한다. Opt-in disposable PostgreSQL test는 cross-organization
  isolation, audit failure rollback, concurrent revoke의 exactly-one delete/audit 및
  idempotent unchanged를 검증한다. PostgreSQL이 없는 local run에서는 이 integration을
  skip하되 fake filter 결과를 성공 근거로 사용하지 않는다.

영향 파일은 document edit/public projection service와 schema, Knowledge/RAG endpoint,
Knowledge Client settings page/API/type, domain permission repository test, opt-in
PostgreSQL test와 Knowledge 공식 문서다. API source의 원문 URL/header/body를 다시
표시하거나 수정하는 기능은 credential-aware 별도 UX/rotation 계약 없이는 추가하지
않는다.

## Consequences

장점:

- Organization manager → Knowledge 전담 Team → 개별 KB/Collection 담당자 구조를
  최소 권한으로 운영할 수 있다.
- owner-only와 resource RBAC가 섞인 경로를 하나의 object/property authorization
  계약으로 정리한다.
- 카탈로그 관리자가 문서 원문이나 RAG 사용 권한을 자동으로 얻지 않는다.
- 공개 노출과 hard delete의 위험한 권한을 조직 관리자에게 유지한다.

비용:

- additive schema, owner backfill, endpoint cutover와 client capability migration이
  필요하다.
- Domain permission과 resource permission을 UI에서 명확히 분리해야 한다.
- Source-managed original content는 approved display/raw primitive가 없으면
  manager에게도 fail-closed된다.

## Follow-up

- MBA-231은 이 ADR의 관리/RBAC/schema/API/UI cutover를 구현한다.
- MBA-232는 이 permission contract를 사용해 direct KB + selected Collection
  runtime candidate resolver를 구현한다.
- MBA-233은 LLM node graph, Builder, deployment preflight와 Workflow Engine에
  resolver를 연결한다.
