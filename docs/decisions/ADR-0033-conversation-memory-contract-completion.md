# ADR-0033: Conversation Memory 계약 공백 보정

Status: Accepted
Related ADRs: ADR-0022, ADR-0030

## 배경

ADR-0030은 독립 Memory bounded context와 Conversation Memory의 목표 경계를 확정했다. 후속 명세 검토에서 다음 공백이 확인됐다.

- 데이터 lineage가 값 입력 dependency만 다루고 Condition/Switch/Loop 같은 제어 흐름이 결과 선택에 준 영향을 표현하지 못한다.
- `ProviderExecutionCapability`의 필드와 검증 책임이 Memory, Workflow, Budget 문서에 중복 정의되어 credential revoke 또는 permission revision 변경 시 어느 도메인이 authoritative한지 불명확하다.
- `workflow_editor_test` session을 지원 대상으로 열거하지만 별도 인증, CSRF, idempotency, retention과 HTTP surface가 정의되지 않았다.
- Public Conversation Access Grant의 rotation/grace가 목표처럼 쓰이지만 V1 lifecycle과 API가 고정되지 않았다.

## 결정

### 1. 값 dependency와 활성 제어 dependency를 함께 전파한다

Workflow Runtime은 결과 값의 content dependency와 그 결과가 선택되도록 만든 활성 제어 dependency를 함께 추적한다.

- Condition/Switch는 predicate 계산에 사용한 dependency와 선택된 route identity를 active control context에 추가한다.
- Loop는 iterable/bound/continue/termination 판단에 사용한 dependency를 loop body와 loop 결과의 active control context에 추가한다.
- 선택된 branch의 상수 출력도 해당 선택을 만든 control dependency를 상속한다.
- 선택되지 않은 branch의 값 dependency는 결과에 합산하지 않는다.
- Transform/code/LLM/final output과 Memory Entry는 값 dependency와 활성 control dependency의 bounded 합집합을 상속한다.

V1 `RuntimeDataDependencyEnvelope`는 별도 optional dependency 의미를 추가하지 않는다. Runtime이 내부적으로 값과 control context를 구분해 계산하되, canonical envelope에는 둘의 필수 dependency 합집합과 completeness만 기록한다. Control lineage를 계산할 수 없으면 private/sensitive Memory write는 fail-closed한다.

### 2. ProviderExecutionCapability의 authoritative owner는 LLM Credentials다

LLM Credentials domain이 capability 발급, scope와 revision 의미, credential principal, credential permission decision revision, verified relation, egress/pricing revision, invocation/admission/provider-attempt binding, purpose, cap과 expiry를 소유한다. Workflow Runtime은 provider effect 전에 canonical provider attempt reference를 먼저 생성하고 issuer는 이를 capability에 고정한다.

Memory, Workflow와 Budget은 opaque capability identity/revision과 자기 operation에 필요한 binding만 소비한다. 이 도메인들은 credential principal이나 permission revision을 자체 계산하거나 capability 필드 목록을 재정의하지 않는다.

Credential revoke, credential permission decision revision 변경, verified relation/egress policy 변경 또는 capability expiry가 발생하면 stale capability는 새 Context lease claim, Budget reservation, provider attempt admission과 outbound provider call 전에 fail-closed한다. 이미 시작된 provider attempt의 실제 usage reconciliation은 호출 재허용과 분리해 처리한다.

### 3. Workflow Editor test session은 초기 Conversation Memory surface에서 제외한다

초기 Conversation Memory session surface는 public Chatbot으로 제한한다. 별도 인증·접근 정책을 갖춘 authenticated internal Chatbot은 후속 target이다.

Workflow Editor test는 일반 workflow test 실행과 동일한 임시 execution surface로 유지하고 Conversation Session을 자동 생성하지 않는다. Editor 전용 session을 도입하려면 인증/권한, CSRF/Origin, idempotency, retention, deployment snapshot binding과 HTTP API를 별도 기능 명세 및 보안 검토로 확정해야 한다.

### 4. V1 Access Grant는 standalone rotation과 grace를 지원하지 않는다

Public Conversation Access Grant V1 상태는 `active`, `transcript_only`, `revoked`, `expired`로 제한한다.

- Close는 current grant를 `transcript_only`로 전이해 허용된 transcript 조회만 유지한다.
- Reset은 old grant를 즉시 revoke하고 새 session과 새 grant를 원자적으로 발급한다. 이는 grace rotation이 아니라 replacement다.
- Delete, explicit revoke, deployment/version/audience/scope 불일치는 grant를 즉시 무효화한다.
- 별도 rotate endpoint, rotated-grant chain과 old/new grant 동시 유효 grace window는 제공하지 않는다.
- create/reset 응답 유실은 bounded encrypted replay store로만 복구하며 replay 만료 후 새 grant를 임의 발급하지 않는다.

이 제한은 bearer Access Grant 자체의 lifecycle/교체 정책에 관한 것이다. 서버의 HMAC verifier key 교체는 별도 운영 보안 경계이며 grant 권한이나 expiry를 연장하지 않는다. 새 grant/receipt는 active key로만 발급하고, 이미 발급된 값은 원래 state·scope·expiry 안에서만 검증할 수 있도록 active key와 최대 한 개의 previous key를 bounded keyring으로 유지할 수 있다. Previous key 제거 시점은 그 key로 발급된 live grant와 purge receipt가 모두 만료된 뒤여야 한다.

Standalone rotation 또는 grace를 도입하려면 overlap abuse, replay, audit cardinality와 revocation propagation을 다루는 별도 ADR과 API/security review가 필요하다.

## 검토한 대안

### Control dependency를 별도 영속 graph로 저장

상세 설명력은 높지만 V1 schema와 authorization 평가 복잡도가 커진다. Runtime은 구분된 control context를 계산하되 canonical dependency union을 저장하는 방식을 채택한다.

### 각 consumer가 ProviderExecutionCapability 최소 필드를 자체 정의

도메인별 문서가 서로 다른 revoke/revision 의미를 갖게 되고 stale capability 차단이 약해진다. LLM Credentials를 단일 authority로 두고 consumer는 opaque identity/revision과 필요한 binding만 검증한다.

### Workflow Editor test에서 곧바로 durable Conversation Session 생성

미정인 보안·retention 계약을 임시 route에 고정하게 된다. 별도 기능 계약 전에는 session을 만들지 않는다.

### Access Grant에 짧은 grace rotation 허용

사용성은 높일 수 있지만 두 bearer token의 동시 유효, revoke 전파와 audit 중복 문제가 생긴다. V1은 즉시 replacement/revoke만 채택한다.

## 결과

- Workflow Runtime의 provenance contract와 관련 테스트에 control-only 영향 사례가 추가된다.
- ProviderExecutionCapability의 상세 계약은 LLM Credentials 문서가 authoritative하다. Memory, Workflow와 Budget 문서는 해당 계약을 참조한다.
- `workflow_editor_test`는 초기 Conversation Memory 지원 enum과 session API에서 제외되고 후속 조건으로 문서화된다.
- Access Grant V1 schema/API는 rotation chain이나 grace window를 요구하지 않는다.
- 이 ADR은 목표 계약을 보정하며 현재 legacy `memory_mode` 또는 execution-log 기반 기억이 구현 완료됐음을 의미하지 않는다.
