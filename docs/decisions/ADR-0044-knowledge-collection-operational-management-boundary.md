# ADR-0044: Knowledge Collection 운영 관리 경계

Status: Accepted

Related ADRs: [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0034](ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md), [ADR-0039](ADR-0039-knowledge-workflow-collection-routing-integration.md)

## Context

MBA-231~233은 Knowledge 위임 RBAC, runtime candidate resolution과 Workflow
Collection 선택을 구현했고 MBA-263은 Manual Collection의 안전 표시 이름을 관리
UI에 연결했다. 그러나 운영 관리 surface에는 다음 공백이 남아 있다.

- archive한 Manual Collection을 복구할 수 없다.
- item reorder API가 partial item set, duplicate rank와 concurrent link/unlink를
  검출하지 않는다.
- 권한 대상 조회가 active Team/User 전체를 한 response로 반환한다.
- bundle은 부여만 가능하고 회수는 action row를 여러 번 삭제해야 한다.
- 같은 subject의 bundle을 여러 Collection에 원자적으로 적용할 수 없다.

이 공백은 대규모 조직의 관리 UX뿐 아니라 stale write, partial permission mutation,
last-manage 상실과 cross-organization 정보 노출 위험을 만든다. 기존 additive allow와
Collection/KB content-plane 분리는 유지하면서 lifecycle, order와 permission mutation의
일관된 lock·transaction 경계가 필요하다.

## Options Considered

1. 현재 endpoint를 유지하고 Client에서 전체 목록, reorder 정합성과 bundle 조합을
   계산한다.
2. Collection version column, role/bundle table와 permission deny/expiry를 추가해 관리
   model을 전면 재설계한다.
3. 기존 lifecycle/item/explicit permission row를 유지하고 Gateway application 경계에
   bounded query, opaque revision, Collection-first lock와 atomic bulk operation을
   추가한다.

## Decision

선택지 3을 채택한다.

### Lifecycle restore

- `POST /api/v1/knowledge/collections/{collection_id}/restore`는 archived manual
  Collection만 active로 전이한다.
- Organization manager, effective Collection `manage`, Knowledge domain
  `lifecycle_manage`가 수행할 수 있다.
- Active restore retry는 새 mutation과 audit가 없는 idempotent success다.
- System-managed Collection은 connector/source owner가 관리하고 `source_deleted`는
  별도 source recovery 없이 restore하지 않는다.
- Archive와 restore는 organization-scoped Collection row를 `FOR UPDATE`로 잠근 뒤
  상태와 권한을 평가하고 canonical audit를 같은 transaction에 저장한다.
- Restore는 permission, membership, child KB `use` 또는 Workflow `route`를 만들지
  않는다.

### Exact item ordering

- Item order revision은 current ordered membership의 Collection id, item id와 rank에서
  계산한 versioned opaque digest다. DB authorization state나 capability가 아니다.
- Reorder는 현재 전체 item set, unique item id, unique contiguous `0..N-1` rank와
  `expected_order_revision`을 요구한다.
- Collection row를 먼저 잠그고 membership row를 deterministic order로 잠근 뒤
  revision과 exact set을 다시 비교한다. Stale/partial/foreign request는 mutation 없이
  safe conflict다.
- Link, unlink, reorder와 visibility mutation은 같은 Collection-first lock protocol을
  사용한다.
- GET, link와 reorder 성공 response는 최신 전체 ordered projection과 revision을
  반환하고 unlink는 `204` 뒤 재조회를 유지한다. 독립 action 모델에서 `manage`가
  `read`를 암묵적으로 만들지 않으므로 link/reorder 성공 projection은 완료된 mutation
  authority를 다시 확인하는 safe management projection을 사용한다.
- Legacy link request의 optional `rank`는 호환 목적으로만 수용하고 무시한다. 신규 item은
  Collection lock 아래 current ordered set의 끝에 추가하고 전체 rank를 연속값으로
  정규화한다. 명시 순서 변경은 revision을 요구하는 reorder endpoint만 담당한다.
- 초기 reorder surface는 500개 이하만 지원한다. 초과 상태는
  `item_reorder_limit_exceeded` fixed reason으로 비활성화한다.
- Empty Collection을 포함해 current 전체 set을 제출할 수 있고 current order와 같은
  request는 no-op이며 새 audit를 만들지 않는다.
- Public Collection의 reorder와 public 노출을 추가하는 link는 Organization manager
  acknowledgement 뒤에도 source identity/connector 또는 source-managed child가 있으면
  public exposure approval primitive 부재 상태에서 `source_public_exposure_required`로
  fail-closed한다.

### Bounded delegation subjects

- Collection과 Knowledge domain delegation subject endpoint는 `subject_type=team|user`,
  최대 100자 safe prefix query, opaque cursor와 기본 25/최대 50 page contract를
  공유한다.
- Endpoint별 authority를 subject query와 limit보다 먼저 확인한다.
- Current organization의 active Team 또는 active member User만 UUID keyset으로
  조회한다. Offset과 total count는 사용하지 않는다.
- Team/User name만 검색하고 email, login principal, raw source identity를 검색하거나
  반환하지 않는다.
- Cursor는 version, subject type, normalized query와 last UUID를 결합한 bounded
  transport token이다. Secret이나 permission token이 아니며 context가 바뀌면
  fail-closed한다.

### Bundle revoke and multi-Collection bulk

- Viewer=`read`, Workflow Router=`read+route`, Maintainer=`read+manage`, Sync
  Operator=`read+sync` mapping을 유지한다.
- Bundle은 저장 role이나 grant provenance가 아니라 action set이다. Revoke는 현재
  존재하는 mapping action row만 삭제하며 없는 row는 idempotent unchanged다.
- Bulk endpoint는 unique Collection 1~50개, 한 subject, 한 bundle과
  `grant|revoke` operation만 받는다.
- Adapter는 Collection UUID 순으로 row를 잠그고 모든 target의 organization,
  mutation authority, self/own-Team grant와 last-manage revoke 정책을 mutation 전에
  검증한다. 하나라도 실패하면 permission과 audit 전체를 rollback한다.
- Grant는 active subject만 허용한다. Revoke는 inactive Team 또는 removed/deactivated
  User의 기존 row를 정리할 수 있다.
- Target별 authorization 의미는 유지하되 organization/permission projection은 bounded
  bulk query로 읽어 target 수만큼 N+1 query를 만들지 않는다.
- Actor가 모든 target의 effective Collection `manage`를 가진 경우 domain
  `permission_delegate`도 보유했더라도 resource-manager authority를 우선한다. 일부
  target에만 `manage`가 있으면 domain-delegate 정책을 적용해 self/own-Team grant를
  차단한다.
- 성공한 각 Collection mutation에는 같은 transaction의 canonical audit를 남긴다.
  Response와 audit metadata는 raw subject/Collection label, target id 목록과 exact hidden
  count를 포함하지 않고 fixed operation/action과 safe count bucket만 사용한다.

### Architecture and data model

- Lifecycle과 exact order flow는 FastAPI endpoint → Knowledge administration
  application use case → repository/audit port → SQLAlchemy/audit adapter → Unit of Work
  순서를 따른다.
- Subject page, bundle revoke와 multi-Collection permission bulk는 기존 Gateway
  Collection management service의 authorization/audit helper와 permission row model을
  확장한다. 이 범위에서 별도 permission application 계층으로 대규모 이동하지 않으며
  endpoint는 request/response mapping만 담당한다.
- Controller는 request parsing, authentication dependency, use case 호출과 safe error
  mapping만 담당한다.
- Existing lifecycle, membership rank와 Team/User Collection permission table을
  재사용한다. Order revision column이나 bundle/role row를 추가하지 않는다.
- Subject prefix query index는 실제 PostgreSQL query plan이 필요성을 증명할 때만
  additive migration으로 추가한다. Alembic autogenerate diff만으로 index를
  추가·삭제하지 않는다.
- Gateway와 Client의 reorder/subject response는 같은 release에서 coordinated cutover한다.
  Revision 없는 reorder 또는 unbounded subject fallback은 제공하지 않는다.

## Rationale

기존 row model을 유지하면 MBA-231의 additive allow와 위임 정책을 바꾸지 않으면서
운영 공백을 닫을 수 있다. Server-owned exact revision과 Collection-first lock는 Client가
보안·동시성 권위가 되는 것을 막는다. UUID keyset page는 raw principal이나 전체 조직
목록을 전송하지 않고도 검색 UX를 제공한다. Bundle을 action set으로 유지하면 겹치는
bundle을 role provenance로 잘못 해석하는 문제를 피하고, all-or-nothing bulk는 실무자의
반복 작업을 줄이면서 partial permission 상태를 막는다.

## Consequences

- Reorder Client는 전체 item set과 current revision을 보내야 하므로 legacy request는
  validation failure가 된다.
- Legacy link `rank`는 deprecated 호환 입력이며 실제 삽입 위치를 결정하지 않는다.
- Delegation subject Client는 subject type과 page contract를 사용해야 하며 전체 목록을
  한 번에 받을 수 없다.
- Bundle revoke 뒤 action 조합은 어떤 role을 부여했던 기록이 아니라 현재 explicit
  permission 상태만 나타낸다.
- Bulk는 한 target의 실패로 전체가 실패하므로 UI는 partial success로 표현하지 않는다.
- 500개 초과 Collection은 membership link/unlink를 계속 사용할 수 있지만 이번
  reorder UI는 비활성화된다.
- `sync` action과 Sync Operator bundle은 계속 부여·회수할 수 있으나 실제 KC sync
  endpoint, Celery job과 progress UI는 MBA-265 범위다.
- Explicit deny, per-Collection expiry, bundle storage와 Collection route에서 child KB
  use 자동 grant는 도입하지 않는다.

## Affected Files

- `docs/features/knowledge/{requirements,api_spec,component_spec,test_cases}.md`
- `apps/shared/schemas/knowledge.py`
- `apps/gateway/application/knowledge_administration/`
- `apps/gateway/adapters/db/knowledge_collection_operations.py`
- `apps/gateway/adapters/audit/knowledge_collection_operations.py`
- `apps/gateway/composition/knowledge_administration.py`
- `apps/gateway/api/v1/endpoints/knowledge.py`
- `apps/gateway/services/knowledge_collection_service.py`
- `apps/client/app/features/knowledge/`
- 관련 Gateway, Client와 PostgreSQL integration test

## Implementation State

MBA-264 구현은 manual restore, exact revision reorder, bounded subject page, bundle
revoke, multi-Collection atomic bulk와 해당 Client 관리 surface를 연결한다. 기존 DB row를
재사용하므로 migration은 추가하지 않는다. 구현 완료 판정은 관련 Gateway/Shared/Client
자동화와 disposable PostgreSQL 동시성·rollback 검증 결과를 함께 사용한다.

## Follow-up Review Notes

- Disposable PostgreSQL 검증은 organization predicate, 실제 `FOR UPDATE`, concurrent
  restore/reorder/bulk와 audit rollback을 fake condition 없이 실행한다.
- Subject prefix query의 `EXPLAIN (ANALYZE, BUFFERS)`를 확인하고 index가 필요하면 근거와
  함께 별도 additive migration을 검토한다.
- MBA-265에서 `sync` action을 실제 비동기 실행에 연결할 때 idempotency, retry,
  connector owner와 progress non-disclosure를 별도 결정한다.
- 운영에서 500개 초과 Collection reorder가 필요해지면 partial ordering/virtualization과
  revision semantics를 새 ADR로 검토한다.
