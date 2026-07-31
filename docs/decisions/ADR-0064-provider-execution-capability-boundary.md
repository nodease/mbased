# ADR-0064: Provider 실행 capability와 deployment credential 정책 경계

Status: Accepted

Related ADRs: ADR-0033, ADR-0050, ADR-0057, ADR-0066, ADR-0067

## 배경

기존 Workflow LLM runtime은 실행 context의 사용자나 소유자 정보로 credential을 찾고 provider client를 즉시 생성한다. 이 방식은 public·schedule·Memory 실행에서 execution subject, credential principal, billing principal과 audit actor를 혼동하기 쉽고, credential revoke나 권한 변경과 provider 호출 사이의 시간차를 닫지 못한다. Workflow graph에 credential을 저장하거나 이름·최신 row·소유자 fallback으로 선택하면 배포 snapshot의 재현성과 organization 권한 경계도 깨진다.

Conversation Memory 목표 계약은 LLM Credentials가 provider 실행 capability의 권위자가 되어야 한다고 정했지만, deployment별 credential 선택, capability의 영속 identity, admission 상한과 provider 호출 전 transaction 경계가 공식 문서에 고정되어 있지 않았다.

## 결정

1. LLM Credentials domain이 deployment credential policy와 `ProviderExecutionCapability`의 유일한 권위자다. Workflow, Memory, Budget과 Client는 credential principal, permission revision, capability scope 또는 secret을 만들거나 덮어쓰지 않는다.
2. Deployment credential policy는 immutable deployment version의 LLM node마다 active row를 최대 한 개만 둔다. Active organization manager만 정책을 교체할 수 있으며, server는 정책 작성자를 credential principal로 기록하고 작성 시점과 실행 시점에 organization scope, generation purpose의 active chat exact graph model, active credential, provider/model relation과 credential `use`를 검증한다. 같은 node의 기존 active row는 model UUID와 무관하게 먼저 비활성화한다.
3. Workflow graph는 provider API model identifier만 소유한다. Credential ID, credential principal, default/latest/name/order fallback, auto routing과 provider fallback은 capability-required path의 선택 근거가 아니다. 정책 없음·복수 active row·graph 직접 credential·모델 불일치는 provider client 생성 전에 fail-closed한다.
4. Execution subject, credential principal, billing principal과 audit actor를 별도 typed principal로 저장한다. User 실행은 같은 user audit actor, anonymous-public 실행은 public audit actor, system 실행은 system audit actor만 허용하고 billing principal은 binding organization과 같아야 한다. Public 또는 system actor를 credential principal로 승격하지 않는다. Capability 권한 거부 audit은 발급 입력에서 확정한 typed audit actor를 사용하며 runtime context에서 actor를 다시 추론하지 않는다.
5. Capability는 organization, workflow, deployment/version, node invocation, execution admission, provider attempt, purpose, policy/model/provider/credential, credential principal, permission·relation·provider-routing·pricing revision, token/cost 상한과 expiry에 binding된 short-lived opaque row다. Policy와 capability의 `node_id`는 영속 계약에 맞춰 최대 255자로 제한하며 초과 입력은 row write 전에 거부한다. 같은 organization/provider-attempt/purpose 재시도는 동일 binding, principal과 상한일 때만 같은 row로 수렴한다. Policy write는 deployment 후 policy를, issue/admission은 policy 후 capability를 lock하며 이미 획득한 lock의 역순을 사용하지 않는다. 모든 `FOR UPDATE` 조회는 ORM identity map의 기존 객체를 강제로 갱신한 뒤 권한과 revision을 판정한다. Capability 발급 시 `created_at`, `updated_at`, `expires_at`은 한 번 읽은 PostgreSQL wall clock을 사용하고 admission의 최초·최종 만료 판정도 PostgreSQL wall clock으로 수행해 process clock skew로 TTL이나 lifecycle 순서가 왜곡되지 않게 한다.
6. Admission은 발급 상한을 그대로 되돌려 비교하지 않는다. Messages와 tools·response schema를 포함한 provider-visible parameter 구조 전체의 UTF-8 byte upper bound를 input token 요청량으로 사용하고, 실제 provider request의 generic `max_tokens`를 output 요청량으로 사용한다. Canonical model pricing으로 최대 비용을 micro-USD 단위 올림 계산하며 가격이 없거나 요청량이 상한을 넘으면 client 생성 전에 fail-closed한다. Provider client materialization 뒤 adapter가 provider-specific JSON schema format을 만들어 request를 확장하면 같은 capability를 완성된 request bound로 다시 admission하고, 실패 시 usage intent와 provider I/O 전에 닫는다. Admission이 확정한 canonical model UUID는 provider client 선택부터 비용 계산과 usage 기록까지 보존하며, 중복 가능한 provider API model identifier로 다시 선택하지 않는다. Capability mode에서 output limit이 생략되면 server cap을 `max_tokens`로 적용하고 provider별 우회 alias는 허용하지 않는다. Capability usage projection은 admission transaction에서 봉인한 pricing revision과 immutable input/output 가격 snapshot만 사용하고 provider 호출 뒤 mutable model row의 가격을 다시 읽지 않으며, legacy usage projection만 exact row 가격이 없을 때 shared pricing catalog fallback을 명시적으로 요청할 수 있다.
7. Capability 발급과 최종 admission은 provider client를 반환하기 전에 전용 DB transaction으로 commit한다. Workflow node의 legacy/shared session을 재사용하거나 그 session의 다른 변경을 함께 commit하지 않으며 session factory가 같은 객체를 반환해도 fail-closed한다. Provider network I/O 중에는 이 control transaction의 DB session을 잡고 있지 않으며 provider 실패가 발급 사실을 rollback하지 않는다. Provider-start/outcome/usage reconciliation의 장기 원장은 MBA-287이 소유하고, 이 capability row를 청구·감사 원장으로 사용하지 않는다.
8. Credential materialization은 ADR-0057의 Shared `LLMCredentialConfigService`만 사용한다. ORM의 `encrypted_config` 직접 파싱, 암호화 실패의 평문 fallback, config/API key/ciphertext/provider raw payload의 API·audit·trace·log 노출을 금지한다.
9. `egress_revision`은 provider catalog routing field와 [ADR-0067](ADR-0067-production-https-and-operation-bound-outbound.md)의 current LLM transport profile revision을 함께 감지하는 fingerprint다. Capability client는 admission에서 잠근 provider catalog URL로 구성하며 credential config에 저장된 과거 URL snapshot으로 목적지를 덮어쓰지 않는다. Guarded transport는 current profile과 endpoint를 실제 dial에 적용한다. 이 revision은 GitHub/Gmail 등 다른 adapter나 cluster proxy-only enforcement의 완료를 의미하지 않는다.
10. Policy와 아직 usage ledger에 연결되지 않은 capability는 deployment 실행 제어 row이므로 deployment 삭제와 함께 cascade delete한다. 과거 token/cost/provider attempt 보존은 MBA-287의 durable usage ledger가 safe snapshot과 retention으로 담당한다.
11. Capability-required flag, audience와 상한은 client request나 graph field가 아닌 server-owned runtime control이다. Capability-required flag는 엄격한 boolean으로만 해석하며 누락 또는 `false`만 legacy를 선택하고, 다른 malformed 값은 legacy로 강등하지 않고 fail-closed한다. MBA-249는 policy API와 dormant target path를 구현하며 legacy runtime의 전면 activation, staged rollout과 rollback gate는 MBA-320이 담당한다.
12. Workflow Engine의 `LLMNode`는 application `ProviderExecutionRuntime`과 `ProviderUsageRecorder` port만 의존한다. Composition root가 process-local dependency로 runtime과 recorder를 주입하고 `WorkflowNode`와 `LoopNode`가 만든 자식 엔진에도 같은 dependency 묶음을 전달하며 execution context, graph 또는 task payload에 직렬화하지 않는다. Runtime router는 server-owned strategy 선택만 담당하고 Shared capability domain/service와 legacy LLM service를 import하지 않는다. Capability adapter만 Shared capability command·service, credential config materialization, provider client 생성과 전용 control unit-of-work를 알고, legacy adapter는 기존 user-scoped selection만 담당하되 resolver 결과의 model, organization과 credential principal을 요청 scope와 다시 대조한다. 두 strategy는 Node에 raw provider client 대신 attribution과 `invoke()`만 제공하는 opaque lease를 반환한다. Capability plan은 한 번만 resolve되어 lease를 하나만 만들 수 있다. Lease는 승인된 request를 생성 시점에 봉인하고 첫 provider 호출 시도 전에 소진해 같은 admission의 재호출과 outcome-unknown replay를 막는다. Usage projection adapter는 admission이 확정한 organization, canonical model UUID와 immutable pricing snapshot을 그대로 사용하며 MBA-287 durable ledger로 교체 가능한 경계로 유지한다.

## 검토한 대안

### Workflow graph에 credential ID 저장

편집과 실행은 단순하지만 배포 snapshot에 secret resource 선택이 결합되고 권한 변경 뒤에도 과거 graph가 credential을 다시 선택할 수 있다. Graph와 credential policy의 소유권을 분리한다.

### 실행 사용자 또는 deployment creator fallback

Interactive 실행에는 편리하지만 public·schedule·Memory 실행에서 data subject와 credential principal을 혼동한다. 명시적 manager policy가 없으면 실행하지 않는다.

### Capability를 메모리 객체로만 유지

DB 쓰기는 줄지만 retry, duplicate delivery와 provider 실패 뒤 발급·admission 사실을 재현할 수 없다. Provider 호출 전에 durable control row를 commit한다.

### 발급 상한을 admission 요청량으로 재사용

항상 같은 값끼리 비교하게 되어 실제 prompt, output limit과 비용을 제한하지 못한다. 실제 request upper bound와 canonical pricing을 별도로 계산한다.

## 결과

- Credential 선택과 provider 호출 권한이 deployment 정책과 current authorization에 고정되고 public·system 실행이 owner credential을 암묵적으로 상속하지 않는다.
- Provider 호출 전에 capability가 영속화되므로 실패와 retry에서도 같은 attempt identity를 추적할 수 있다.
- Conservative token upper bound는 provider별 tokenizer보다 크게 계산될 수 있다. Activation 전 운영 상한을 조정하고, 더 정확한 estimator를 도입하더라도 이 ADR의 fail-closed 상한을 약화해서는 안 된다.
- MBA-287과 MBA-320이 완료되기 전에는 이 target path를 일반 legacy 실행에 자동 적용하지 않는다.
