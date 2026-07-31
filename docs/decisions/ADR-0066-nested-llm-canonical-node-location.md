# ADR-0066: Nested LLM canonical node location

Status: Accepted

Related ADRs: [ADR-0064](ADR-0064-provider-execution-capability-boundary.md)

## 배경

[ADR-0064](ADR-0064-provider-execution-capability-boundary.md)는 immutable deployment version의 LLM node마다 active credential policy를 하나만 두고 policy와 `ProviderExecutionCapability`를 `node_id`에 binding했다. 그러나 Workflow는 Loop의 `data.subGraph` 안에 LLM node를 포함할 수 있고, 서로 다른 container에서 같은 `node_id`가 반복될 수 있다. `node_id` 단독 조회나 첫 일치 fallback은 다른 Loop의 credential policy를 잘못 적용할 수 있다.

WorkflowNode binding과 Workflow Engine의 child execution control에는 이미 ordered Loop `container_path`가 있다. 이 path 규칙을 generic graph identity로 승격하되, WorkflowNode target deployment 정보와 LLM credential 정책의 책임은 분리해야 한다.

## 결정

### Canonical node location

Workflow graph node의 durable identity는 `(container_path, node_id)`다.

- `container_path`는 root에서 현재 node의 부모 container까지 ordered segment다.
- V1 segment는 `{kind: "loop", node_id: <parent-loop-id>}`만 허용한다.
- root node의 path는 `[]`다.
- node와 segment ID는 비어 있지 않은 최대 255자 문자열이다.
- Loop nesting은 Shared graph limit인 16을 사용하며 WorkflowNode target 재귀 limit과 혼합하지 않는다.
- Loop 외 container kind는 별도 결정 없이 자동 허용하지 않는다.

Shared pure domain이 path parsing, graph traversal, exact lookup와 digest 계산을 소유한다. Gateway graph preflight, WorkflowNode binding, deployment credential policy와 Workflow Engine runtime은 이 계약을 재사용하며 별도 재귀 walker나 node-ID fallback을 만들지 않는다.

### Policy와 capability binding

Active credential policy uniqueness는 논리적으로 다음 범위다.

```text
organization_id + deployment_id + deployment_version + canonical node location
```

따라서 root `llm-1`, `loop-a/llm-1`, `loop-b/llm-1`은 각각 별도 active policy를 가질 수 있다. 같은 canonical location에서는 model UUID와 무관하게 active policy가 하나여야 한다.

`ProviderExecutionCapability`는 canonical node location과 실행별 `node_invocation_id`, `execution_admission_id`, `provider_attempt_id`, purpose를 모두 보존한다. Graph location은 배포 구성 identity이고 invocation/attempt는 실행 identity이므로 서로 대체하지 않는다. Issue와 admission에서 path, deployment/version 또는 policy가 다르면 credential config 복호화와 provider client 생성 전에 fail-closed한다.

ADR-0064의 credential principal, permission/relation/provider-routing/pricing revision, token·cost cap, expiry, revoke와 `deployment -> policy -> capability` lock 순서는 변경하지 않는다.

### API와 영속 표현

기존 `PUT /api/v1/deployments/{deployment_id}/llm-credential-policies/{node_id}` route를 유지하고 request body에 optional structured `container_path`를 추가한다. 생략과 빈 배열은 root만 의미한다. Client가 digest, credential principal 또는 capability scope를 보내면 거부한다. GET safe projection은 manager가 정책을 구분할 수 있도록 canonical path를 반환하되 secret과 capability 원문은 반환하지 않는다.

Policy와 capability row는 structured JSONB `container_path`, terminal `node_id`, server-derived lowercase SHA-256 `node_location_digest`를 저장한다. Digest V1 bytes는 다음 순서로 구성한다.

1. domain tag `nodease:canonical-node-location:v1`
2. unsigned 4-byte big-endian segment count
3. 각 segment의 kind와 node ID
4. terminal node ID

각 문자열은 UTF-8 byte 길이를 unsigned 4-byte big-endian으로 먼저 기록한다. Digest는 bounded lookup/index projection일 뿐 logical source of truth가 아니다. 조회 뒤 structured path와 terminal ID를 exact 비교하며 digest 불일치나 충돌을 승인 근거로 사용하지 않는다.

Manager policy API는 설정 관리에 필요한 structured path를 반환한다. 일반 audit·trace correlation에는 raw path 대신 `workflow-node-location:v1:<digest>` 형태의 bounded opaque reference만 사용하고, nested input과 credential/capability 원문은 기록하지 않는다.

### Migration과 호환성

Additive migration은 기존 policy와 capability를 root path `[]`로 backfill한다. MBA-249 구현은 top-level node만 policy 대상으로 허용했으므로 기존 row를 nested path로 추정하지 않는다. Backfill 뒤 path와 digest는 NOT NULL이며 새 write가 둘을 명시적으로 저장한다.

Active partial unique index는 `(organization_id, deployment_id, deployment_version, node_location_digest) WHERE is_active`로 교체한다. Nested row가 존재하는 상태의 downgrade는 node-ID-only identity로 손실 축소하지 않고 명시적으로 중단한다.

기존 top-level API 호출은 path 생략으로 호환한다. Malformed, stale, unknown-kind, missing 또는 ambiguous location은 safe configuration error로 닫고 다른 node의 존재나 credential 정보를 노출하지 않는다.

### Preflight와 activation 경계

Gateway의 graph preflight와 Workflow runtime은 같은 Shared walker를 사용해 동일 graph에서 같은 ordered location을 계산한다. Policy write는 immutable deployment snapshot의 exact location과 graph-owned model을 검증한다. Runtime preflight는 client graph나 input의 path가 아니라 trusted `NodeExecutionControl.binding_container_path`를 capability command로 전달한다.

현재 deployment 생성 전 configuration preflight에는 아직 생성되지 않은 deployment policy ID가 없으므로 policy row 존재를 성공 조건으로 추가하지 않는다. Capability path는 ADR-0064대로 dormant이며, policy readiness를 실제 execution admission에 연결하는 rollout은 MBA-320이 소유한다. Preflight 성공은 runtime authorization capability가 아니다.

## 결과

- 서로 다른 Loop에서 반복되는 `node_id`의 credential policy와 capability 교차 사용을 차단한다.
- 기존 root policy API와 실행 identity를 additive하게 유지한다.
- Structured path와 fixed-size index key를 분리해 delimiter와 index 크기 문제를 피한다.
- Mixed old/new Worker와 production activation은 MBA-320 readiness gate가 필요하다.
- Nested row가 있는 downgrade는 운영 정리 없이 수행할 수 없다.

## 검토한 대안

- **Graph 전체 node ID 전역 유일 강제**: 기존 nested graph와 container 의미를 깨뜨려 채택하지 않았다.
- **첫 일치 또는 DFS fallback**: graph 순서가 authorization을 바꾸므로 거부했다.
- **Delimiter 문자열 path**: escaping, Unicode와 향후 container kind 의미가 모호해 거부했다.
- **`node_invocation_id`만 사용**: 실행별 값이라 immutable deployment policy key가 될 수 없어 거부했다.
- **WorkflowNodeBinding 전체 재사용**: target App/deployment binding까지 포함해 책임이 다르므로 path primitive만 공유한다.
