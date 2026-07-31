# AGENTS.md - Nodease Documentation Review

이 파일은 `docs/` 아래 문서 변경에 적용되며 루트 `AGENTS.md`의 규칙을 보완한다.

## 문서 권위

문서가 충돌하면 다음 순서를 사용한다.

1. `docs/decisions/`의 Accepted ADR
2. `docs/PRD.md`
3. `docs/architecture.md`
4. `docs/data_model.md`
5. Feature `requirements.md`
6. Feature `api_spec.md`
7. Feature `component_spec.md`
8. Feature `test_cases.md`
9. `docs_old/`의 역사 자료

`docs/README.md`의 예외도 함께 적용한다. 더 새로운 Accepted ADR 또는 명시적 current code comparison이
오래된 상위 문서를 정정하는 경우에는 구현 경로와 테스트 증거를 확인한 뒤 그 override를 인정한다.
단순히 현재 코드가 다르다는 사실만으로 Target 정책이나 상위 문서를 자동으로 무효화하지 않으며,
override 근거가 불명확하면 문서와 구현의 충돌로 보고한다.

하위 문서가 상위 문서나 Accepted ADR을 조용히 재정의하면 finding으로 보고한다. 상위 문서끼리 충돌하면
임의의 기준을 선택하지 말고 충돌 자체를 보고한다.

## Code Review Rules

### 계약 추적

- 정책 또는 동작 변경은 `불변조건 -> actor -> command/query -> precondition -> organization/resource scope -> success/error -> transaction/audit -> test` 순서로 한 번에 추적한다.
- 같은 계약이 여러 문서에 걸치면 Accepted ADR, requirements, API, component와 test case를 모두 대조한 뒤 finding을 작성한다. 한 근본 원인의 파생 불일치는 하나의 finding에 영향받는 문서를 함께 적는다.
- 권한 또는 보호 리소스 변경은 management API/UI, preflight, runtime/background, lifecycle, audit/redaction과 테스트 경계를 `완료`, `해당 없음`, `후속 이슈` 중 하나로 추적했는지 확인한다.
- Mutation 계약은 nullable 최초 상태, optimistic concurrency/CAS, commit 직전 권한 재검증, audit 원자성, 실패 시 zero-write와 retry/idempotency 의미를 필요한 범위에서 함께 검토한다.

### 현재·목표·후속 범위

- `Current`, `As-Is`, 현재 baseline 또는 `Verified Against`를 포함한 문장은 실제 코드와 테스트 증거에 대조한다. `Verified Against`가 없다는 이유만으로 current claim 검증을 생략하지 않는다.
- `Target`와 `Pending Merge`는 현재 구현 완료 여부가 아니라 상위 공식 문서, Accepted ADR, 같은 PR에서 제안한 계약과 안전한 과도기 상태에 대조한다. 같은 PR이 구현 완료까지 주장하는 경우에는 코드와 테스트 증거도 함께 대조한다.
- 구현 공백이 이름 있는 후속 이슈로 연결되고 현재 경로가 fail-closed 또는 비활성이며 현재 구현 완료를 주장하지 않으면, 그 공백만으로 현재 PR의 누락이라고 지적하지 않는다.
- 반대로 권한 우회, secret 노출, 외부 I/O 전 fail-closed 실패, 데이터 손상 또는 기존 관리 경로 단절을 만드는 필수 경계는 후속 이슈 표기만으로 허용하지 않는다.
- 미병합 ADR은 `dev`의 current authority 또는 전역 예약된 ADR 번호로 간주하지 않는다. 다만 해당 ADR을 도입하는 PR 안에서는 proposed target authority로 취급하여 같은 PR의 requirements, API, component와 test case가 일치하는지 검토한다. 기존 Accepted ADR이나 상위 문서와 충돌하면 그 충돌을 보고한다.
- Rebase 후에는 ADR 번호, 링크와 인덱스 충돌을 다시 확인한다.

### 검증 가능한 문장과 테스트

- 추상적인 원칙보다 입력, 상태, actor, 결과, 오류와 부수효과를 검증할 수 있는 문장을 요구한다.
- 테스트 문서는 정상 경로를 반복 나열하기보다 권한 회수, cross-organization, stale revision, concurrent winner/loser, retry/replay, partial failure와 redaction처럼 실제 위험이 있는 경계를 포함해야 한다.
- API 필드, 오류 code, audit action, lifecycle state와 용어는 문서 간 exact string이 일치하는지 확인한다. 단순 문체나 표현 선호는 finding으로 남기지 않는다.
- Markdown 링크, UTF-8 without BOM, line ending, code fence와 conflict marker는 정적 검사로 확인하고, 검사 실패가 있을 때만 리뷰 finding으로 보고한다.
