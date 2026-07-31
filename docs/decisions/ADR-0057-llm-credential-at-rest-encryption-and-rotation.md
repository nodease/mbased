# ADR-0057: LLM credential 저장 암호화와 key rotation

Status: Accepted

Related ADRs: ADR-0022, ADR-0031

## 배경

`llm_credentials.encrypted_config`는 이름과 달리 provider API key와 base URL을 평문 JSON으로 저장한다. Gateway, Workflow Engine, RAG answer와 embedding 경로도 이 값을 직접 `json.loads`한다. 응답·audit·trace에서 필드를 숨기더라도 DB snapshot, 운영 조회 또는 잘못된 진단 코드가 provider secret을 노출할 수 있고, key version이 없어 안전한 rotation과 구키 제거 시점을 판정할 수 없다.

기존 row를 보존하면서 Gateway와 Workflow Worker를 순차 배포해야 하므로 신규 암호화 저장만 추가해서는 충분하지 않다. 평문 전환, keyring 배포, 복호화 경계, startup 검증과 rollback 불가능 지점을 하나의 계약으로 고정해야 한다.

## 결정

1. LLM credential config는 application-managed versioned Fernet envelope로 저장한다. `LLM_CREDENTIAL_ENCRYPTION_KEYS` JSON object와 `LLM_CREDENTIAL_ACTIVE_KEY_VERSION`이 LLM keyring의 authoritative 설정이다. Active version은 공백이 아닌 최대 64자로 제한한다. 전용 keyring과 active version이 모두 미설정이거나 active version이 `v1`인 호환 상태에서만 기존 `ENCRYPTION_KEY`를 `v1` key로 사용한다. Keyring 없이 다른 active version만 설정된 부분 구성은 시작을 거부한다.
2. `llm_credentials`에 nullable `encryption_key_version`, `encryption_algorithm`을 추가하고 두 값은 모두 null이거나 모두 non-null이어야 한다. `encrypted_config`는 계속 ciphertext 본문을 저장한다. 현재 algorithm은 `fernet-v1`이다.
3. 전환은 dual-read/single-write로 수행한다. 신규·갱신 쓰기는 항상 active key로 암호화한다. 두 metadata가 모두 null인 기존 row만 legacy 평문 JSON으로 읽는다. metadata 일부 누락, unsupported algorithm, unknown key version, 손상 ciphertext 또는 복호화 뒤 invalid config에는 평문 fallback을 하지 않고 provider 호출 전에 fail-closed한다.
4. Shared `LLMCredentialConfigService`가 config 직렬화, 암호화, envelope 판정, 복호화와 schema validation의 단일 경계다. Gateway, Workflow Engine, RAG answer, embedding, parser와 seed 경로는 ORM의 `encrypted_config`를 직접 해석하지 않는다.
5. Alembic upgrade는 metadata 컬럼·pair constraint·key version index만 추가하고 application key를 읽거나 row를 변환하지 않는다. 평문 backfill과 구키 rotation은 별도 운영 명령이 stable order, 제한 batch, `FOR UPDATE SKIP LOCKED`와 batch transaction으로 수행한다.
6. Gateway는 요청 처리 전에, Workflow Worker와 Knowledge Worker의 parent/child process는 task 소비 전에 keyring 형식, key material과 active version을 검증한다. 세 process는 같은 keyring을 받아야 한다. Knowledge Worker의 migration readiness init container도 같은 검증을 수행한다. LLM credential을 사용하지 않는 Log System, Client와 Sandbox에는 keyring을 주입하지 않는다.
7. Rotation은 구키와 신키를 모두 포함한 keyring 배포, 양 process의 구키 복호화 가능 확인, active version 전환, 신규 ciphertext 확인, 제한 batch 재암호화, 구키 참조 row 0 확인, 구키 제거 순서로 수행한다. 이전 version row가 남아 있으면 구키를 제거하지 않는다.
8. encrypted metadata가 있는 row가 생성된 뒤 metadata 컬럼만 제거하면 ciphertext를 legacy 평문으로 오인한다. 따라서 encrypted row가 존재하는 schema downgrade는 fail-closed하며, 평문 복구를 자동 수행하지 않는다.
9. Credential 저장·복호화·등록 검증·rotation 경로의 config 평문, ciphertext, Fernet key, provider 검증 raw payload와 원본 복호화 예외는 API, audit, trace, log, exception, fixture, migration 또는 rotation 출력에 남기지 않는다. 운영 출력은 safe 상태와 count만 제공한다. 일반 LLM invocation 오류 계약은 이 ADR의 범위가 아니다.
10. `is_valid=false` row도 secret 보존 위험과 향후 감사·복구 가능성이 있으므로 backfill/rotation 대상에서 제외하지 않는다. Lifecycle·권한 검증은 저장 암호화와 독립적으로 provider 호출 직전에 계속 적용한다.

## 검토한 대안

### Mail credential keyring 공유

구현은 단순하지만 Mail key 회전이 LLM provider 호출 가용성에 결합되고 두 bounded context의 수명주기를 독립적으로 운영할 수 없다. 공통 Fernet 구현은 재사용하되 환경 keyring은 분리한다.

### 단일 `ENCRYPTION_KEY`만 사용

기존 환경과 호환되지만 version 식별과 단계적 rotation이 불가능하다. 호환 fallback으로만 유지하고 production은 전용 keyring을 사용한다.

### Alembic에서 즉시 전 행 암호화

Schema migration에 application secret을 주입하고 대량 row lock과 부분 실패를 결합한다. migration log와 rollback에도 secret 취급 책임이 생기므로 채택하지 않았다.

### Decrypt 실패 시 평문 fallback

손상 ciphertext, 잘못된 key 또는 공격자가 만든 문자열을 평문 config로 오인할 수 있다. metadata가 모두 없는 명시적 legacy row에만 평문 read를 허용한다.

### Read 시 opportunistic rewrite

일반 요청 transaction에 암호화 쓰기와 rotation 실패가 섞이고, 실행되지 않은 credential은 계속 평문으로 남는다. 명시적 제한 batch 운영 경계를 채택한다.

## 결과

- DB 유출만으로 신규 provider key 평문을 얻을 수 없고 모든 application consumer가 같은 fail-closed 복호화 규칙을 사용한다.
- 전환 기간에는 기존 평문 row와 암호화 row가 공존할 수 있으므로 운영자는 backfill 완료와 legacy row 0을 별도로 확인해야 한다.
- 전용 keyring을 설정하지 않은 환경은 기존 `ENCRYPTION_KEY`로 시작할 수 있지만 독립 rotation을 위해 production에서는 LLM 전용 keyring으로 전환해야 한다.
- metadata NOT NULL 및 legacy read 제거는 backfill 완료, 모든 process의 새 reader 배포와 rollback 계획을 확인한 뒤 별도 cutover로 수행한다.
