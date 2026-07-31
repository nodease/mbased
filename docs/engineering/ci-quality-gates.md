# PR CI 품질 게이트

Status: Draft

## 목적

Nodease의 PR 품질 게이트는 모든 테스트를 매번 실행하는 장치가 아니다. 변경 파일을 기능·도메인 영향 범위로 변환하고, 해당 변경에 필요한 검사만 실행한 뒤 하나의 안정적인 최종 check로 병합 가능 여부를 판단한다.

Coverage threshold는 MBA-192, Client 기존 ESLint warning 정리는 MBA-252, 전체 cross-domain 회귀는 MBA-30이 소유한다. EKS 전용 CD는 현재 지원 표면에서 제거되며 재도입 조건은 ADR-0068을 따른다.

## 진입점

PR 검증 진입점은 `.github/workflows/pr-quality-gate.yml`과 `.github/workflows/pr-ci-control-guard.yml`이다.

- 품질 게이트는 모든 `pull_request`에 실행되며 repository content read 권한만 사용한다.
- 신뢰 가드는 base 브랜치의 `pull_request_target` workflow로 실행되며 PR 코드를 checkout하거나 실행하지 않는다.
- 신뢰 가드는 변경 파일, review, reviewer 권한만 GitHub API로 읽고 현재 PR head에 정책 status만 기록한다.
- 같은 PR에 새 commit이 push되면 이전 실행을 취소하고 새 head를 다시 판정한다.
- 품질 게이트는 cloud credential, 배포 secret 또는 장기 credential을 요구하지 않는다.
- required check 후보는 `PR Quality Gate / ci-required`와 `trusted-ci-control/base-policy`다.

실제 required check context는 workflow가 `dev`에 병합된 뒤 probe PR에서 확인한다. 확인 전에는 GitHub ruleset에 이름을 추측해 등록하지 않는다.

## Job 구성

| Job | 실행 조건 | 책임 |
| --- | --- | --- |
| `change-scope` | 항상 | ADR 파일명·H1·번호·README 인덱스 검증, 지원 배포 실행 closure 검사와 base/head 변경 경로 분류 |
| `alembic-single-head` | 항상 | revision 중복, 누락 parent, cycle, multiple heads 검사 |
| `python-lint` | Python 파일 변경 | 존재하는 변경 Python 파일만 Ruff 검사 |
| `client-quality` | Client 영향 | ESLint, typecheck, 변경 dependency Vitest, build |
| `gateway-and-root-tests` | Gateway 또는 root 영향 | 선택된 Gateway와 root pytest |
| `workflow-engine-tests` | Workflow Engine 영향 | 선택된 Workflow Engine pytest |
| `shared-tests` | Shared 영향 | 선택된 Shared pytest |
| `log-system-tests` | Log System 영향 | 선택된 Log System pytest |
| `sandbox-tests` | Sandbox 영향 | Sandbox pytest |
| deployment-config-validation | Actions·Helm·지원 표면·Compose·Dockerfile 영향 | workflow·local action·위임 실행 closure, Helm render/schema·ConfigMap reference closure·CLOUD 필수값과 승인 executable/provider-specific content를 포함한 배포 설정 계약 검사 |
| `knowledge-postgres-contracts` | Knowledge runtime/ingestion/DB 영향 | 실제 PostgreSQL Knowledge 계약 검사 |
| `workflow-postgres-contracts` | migration/schedule/external effect 영향 | 실제 PostgreSQL workflow 계약 검사 |
| `agent-builder-postgres-contracts` | Agent Builder DB/CAS 영향 | 실제 PostgreSQL Agent Builder 계약 검사 |
| `memory-postgres-contracts` | Memory DB/adapter 영향 | 실제 PostgreSQL Memory 계약 검사 |
| `ci-required` | 항상 | 필수 job 결과를 fail-closed로 집계 |
| `ci-control-review` | PR 생성·동기화 또는 명시적 재검증 | base 브랜치 정책으로 CI 제어 변경과 current head 승인 검증 |

`ci-required`는 선택된 job의 `success`만 허용한다. 선택된 job의 `failure`, `cancelled`, 비정상 `skipped`와 scope 결과 누락은 최종 실패다. 선택되지 않은 job의 의도된 `skipped`만 허용한다.

## CI 제어 신뢰 경계

PR workspace의 selector 결과만으로 required gate를 결정하지 않는다.

- 품질 게이트는 selector 실행 전에 보호된 CI 경로 변경을 독립적으로 확인한다. 이 경로가 바뀌면 selector 출력과 무관하게 Client, Python service smoke, root, PostgreSQL 계약 검사와 Actions·Helm·지원 표면·Compose·Dockerfile 정적 검증을 모두 선택한다.
- 동일한 독립 diff 단계가 Dockerfile, Dockerfile.*, *.Dockerfile 실변경 신호를 계산한다. CI 제어 파일과 실제 Dockerfile을 함께 수정한 PR은 selector 출력이 잘못되어도 smoke fixture로 대체하지 않고 변경된 실제 구성을 검증한다.
- GitHub workflow는 Actionlint로 검사하고, composite action metadata는 commit SHA로 고정한 action-validator와 저장소 fixture로 별도 검사한다.
- 신뢰 가드는 base 브랜치에서 `.github/workflows/**`, `.github/actions/**`, `scripts/ci/**`, `tests/ci/**` 변경을 별도로 확인한다. rename은 이전 경로와 새 경로를 모두 검사하고, 변경 파일 전체를 열거하지 못하면 실패한다.
- CI 제어 변경은 PR 작성자가 아닌 write 이상 권한 보유자가 현재 head commit에 남긴 `APPROVED` review가 있어야 통과한다. 이전 commit 승인은 재사용하지 않는다.
- 승인 뒤 `/recheck-ci-control`을 PR conversation에 comment하면 base 브랜치 가드가 current head 정책 status를 다시 계산한다. GitHub API 운영 장애 복구 뒤 재평가할 때도 같은 fallback을 사용한다.
- 동시성 제어는 이벤트와 comment body 조건을 통과한 `ci-control-review` job에만 적용한다. 일반 Linear/Codex/user comment는 기존 정책 검사를 취소하지 않으며, 새 PR head 또는 정확한 `/recheck-ci-control` 요청만 같은 PR의 이전 유효 검사를 교체한다.
- GitHub API 호출은 `actions/github-script`에서 최대 3회 재시도한다. 400, 401, 403, 404, 422는 재시도하지 않으며, 짧은 5xx·429·network failure 같은 재시도 가능 오류만 bounded backoff로 흡수한다.
- 승인 부재나 변경 파일 열거 누락 같은 정책 미충족은 PR head의 `trusted-ci-control/base-policy` status를 `failure`로 기록하되 evaluator job 자체는 정상 완료한다. 재시도 소진 같은 운영 오류도 current head를 알 수 있고 status 기록에 성공하면 `error`로 교체해 fail-closed한다. PR head를 알 수 없거나 authoritative status 기록 자체가 실패한 경우에만 evaluator job을 실패시킨다.
- reviewer permission 조회의 404는 write 권한이 없는 reviewer로 처리한다. 그 밖의 permission API 오류는 승인 부재로 오인하지 않고 운영 오류로 분류한다.
- ruleset의 authoritative required context는 `trusted-ci-control/base-policy` 하나다. raw `ci-control-review` job은 운영 진단용이며 required context로 중복 등록하지 않는다.
- 신뢰 가드는 PR source, test, build script를 checkout하거나 실행하지 않으며 repository secret을 사용하지 않는다.

가드를 최초로 추가하는 PR은 base 브랜치에 가드가 아직 없으므로 자기 자신을 보호할 수 없다. 최초 승격은 독립 review와 actionlint 결과를 수동으로 확인하고, 병합 뒤 probe PR에서 status 생성과 승인 재검증을 확인해야 한다.

## 변경 범위 규칙

| 변경 | 기본 검사 |
| --- | --- |
| 문서만 변경 | scope의 ADR 레지스트리 검사, Alembic graph, aggregate |
| `apps/client/**` | Client lint, typecheck, 관련 Vitest, build |
| `apps/gateway/**` | 대응 기능 test 또는 Gateway layer test |
| `apps/workflow_engine/**` | 대응 기능 test 또는 Workflow Engine layer test |
| `apps/shared/**` | Shared 대응 test와 실제 소비 서비스 관련 test |
| Log System task가 직접 소비하는 Shared service | Shared, Gateway, Workflow Engine, Log System 관련 test |
| Shared schema/DB model | Shared, Gateway, Workflow Engine, Log System, root 관련 test |
| `apps/shared/alembic/**` | 위 Python 범위와 PostgreSQL 계약 test |
| Knowledge runtime 경로 | Knowledge PostgreSQL 계약 test |
| Durable Knowledge ingestion shared service | Knowledge PostgreSQL 계약 test |
| schedule/external effect 경로 | Workflow PostgreSQL 계약 test |
| Agent Builder DB/CAS 경로 | Agent Builder PostgreSQL 계약 test |
| 품질 게이트 CI 제어 파일과 보호된 PostgreSQL workflow | 각 서비스 smoke, Client smoke, root CI 계약 test, PostgreSQL 계약 test, 전체 배포 정적 검증과 최신 독립 승인 |
| 기타 GitHub Actions workflow | actionlint, 승인 workflow/provider-specific content 검사와 최신 독립 승인 |
| 배포 workflow만 변경 | actionlint와 지원 표면 검사를 실행하고 runtime test는 선택하지 않음 |
| Helm 변경 | dependency lock, lint, 기본/production render, CLOUD storage negative render, ConfigMap reference closure, targeted support-surface/storage pytest와 Kubernetes schema 검사 |
| 지원 종료한 EKS/Terraform 경로 변경 | 현재 추적 파일과 승인 workflow 내용에 해당 운영 표면이 남지 않았는지 fail-closed 검사 |
| Docker Compose/Dockerfile 변경 | 변경 파일의 config 또는 build check 실행, Docker check 경고도 오류 처리 |
| 알 수 없는 실행 경로 | Client와 Python smoke 범위로 fail-closed 확장 |

변경 경로가 없거나 분류할 수 없다고 해서 모든 도메인 검사를 생략하지 않는다.

## Python test 선택

`scripts/ci/select_pytest_targets.py`는 다음 우선순위를 사용한다.

1. 변경된 `test_*.py` 파일을 직접 실행한다.
2. source 파일명과 기능 token이 대응되는 test를 찾는다.
3. 직접 대응 test가 없으면 해당 layer/domain test 디렉토리로 확장한다.
4. 공통 CI 변경은 서비스 전체 suite 대신 명시된 smoke target을 사용한다.
5. schema, DB model, migration처럼 영향이 넓은 변경은 관련 서비스와 PostgreSQL 계약 범위로 확장한다.

파일명 token만으로 찾기 어려운 Shared 응답 schema의 간접 소비자는 검토 가능한 명시적 source-to-test mapping으로 보완한다. 매핑된 source 또는 test 중 하나가 변경되면 전체 매핑의 source와 모든 target 존재성을 검증한다. 어느 하나라도 rename 또는 삭제되어 존재하지 않으면 조용히 제외하지 않고 selector가 실패해야 하며, mapping 변경에는 source 변경과 mapped test 변경 양쪽에서 전체 소비 관계를 검증하는 단위 테스트를 함께 둔다.

Gateway job이 선택되면 기능 test와 별개로 소규모 architecture import-boundary suite를 항상 실행한다.

`manual`, `load`, `evaluation`, browser E2E는 PR unit target에서 자동 선택하지 않는다. 이들은 해당 이슈 또는 전체 회귀 절차에서 명시적으로 실행한다.

테스트 개수 자체보다 실행 시간과 영향 범위를 기준으로 판단한다. 예를 들어 Shared domain에 테스트 케이스가 많아도 실행 시간이 짧으면 domain fallback으로 유지할 수 있다.

## Client 검사

Client 영향이 있으면 다음을 실행한다.

```powershell
Set-Location apps/client
npm ci
npm run lint
npm run typecheck
npm run test -- --changed=<BASE_SHA> --passWithNoTests
npm run build
```

CI 제어 파일 변경은 dependency 기반 test가 0개일 수 있으므로 `proxy.test.ts` smoke를 추가 실행한다. 기존 ESLint warning은 MBA-252 범위이며 현재 gate는 error exit code를 기준으로 판정한다.

`package.json`, lockfile 또는 Client 공통 설정처럼 모든 test에 영향을 줄 수 있는 변경은 Vitest가 Client 전체 test를 선택할 수 있다. 이는 일반 기능 변경의 기본 동작이 아니라 공통 dependency/configuration 변경에 대한 의도된 확장이다.

## Alembic과 PostgreSQL

`scripts/ci/check_alembic_graph.py`는 migration module을 실행하지 않고 AST로 `revision`과 `down_revision`만 읽는다. 다음 상태는 모든 PR에서 실패한다.

- revision ID 중복
- 존재하지 않는 parent 참조
- revision cycle
- head가 0개 또는 2개 이상인 상태

기능별 migration 테스트는 해당 revision의 parent 관계나 현재 단일 head ancestry에 남아야 하는 historical revision을 고정할 수 있다. 그러나 repository의 현재 head revision 문자열 자체를 기대값으로 고정하지 않는다. 정상적인 additive migration이 추가돼도 unrelated 기능 테스트는 수정 없이 통과해야 하며, moving head는 `get_heads()`의 개수와 계산된 ancestry로 검증한다.

DB 관련 변경에서는 graph 검사에 더해 disposable PostgreSQL upgrade와 다중 session 계약을 기존 reusable workflow로 실행한다.

- `.github/workflows/test-knowledge-runtime-postgres.yml`
- `.github/workflows/test-schedule-dispatch-postgres.yml`
- `.github/workflows/test-agent-builder-postgres.yml`
- `.github/workflows/test-memory-postgres.yml`

네 workflow는 PR에서는 통합 gate가 호출하고, `dev` push에서는 기존 post-merge 방어선으로 계속 실행한다. Knowledge workflow는 runtime candidate SQL뿐 아니라 durable ingestion의 동시 claim, lease 만료, fencing, heartbeat, late finalization과 DB wall clock 계약을 실제 PostgreSQL에서 검증한다. PR selector와 `dev` push 경로는 `knowledge_document_ingestion_*.py` 및 `knowledge_ingestion_*.py` 서비스 계열을 동일하게 추적한다. 각 workflow 파일 자체의 변경은 보호된 CI 제어 변경으로 분류해 `tests/ci`와 네 PostgreSQL 계약 검사를 모두 실행한다. 일반 기능 코드 변경은 도메인 selector로 필요한 PostgreSQL workflow만 선택하되, 선택되지 않은 job의 skip을 성공 근거로 사용하지 않는다.

## 검증 및 실패 재현

정상 개발 절차에서는 push 전에 변경 범위의 빠른 검사를 로컬에서 먼저 실행한다.

- Python: 변경 파일 lint와 직접 관련된 pytest
- Client: 변경 범위 lint, typecheck와 직접 관련된 Vitest
- CI·배포 설정: 변경된 workflow의 actionlint와 사용 가능한 로컬 정적 검사
- Migration: Alembic revision graph 검사

disposable PostgreSQL 계약, E2E, 전체 build처럼 시간이 오래 걸리거나 실행 환경에 의존하는 검사는 원격 CI를 판정 기준으로 사용한다. 로컬 검사가 통과해도 required CI를 생략하거나 우회하지 않는다. 변경과 무관한 도메인의 전체 회귀는 로컬에서 반복하지 않는다.

selector 또는 CI 제어 코드를 수정할 때는 다음 빠른 검사를 push 전에 실행한다. CI에서 추가 실패가 발생하면 해당 job과 도메인만 최소 범위로 재현한다.

먼저 비교할 commit SHA를 준비한다.

```powershell
$base = git rev-parse origin/dev
$head = git rev-parse HEAD
```

변경 범위를 확인한다.

```powershell
python -m scripts.ci.changed_scope --base $base --head $head
```

Alembic graph를 확인한다.

```powershell
python scripts/ci/check_alembic_graph.py
```

선택될 pytest target을 실행하지 않고 확인한다.

```powershell
python -m scripts.ci.select_pytest_targets `
  --component gateway `
  --base $base `
  --head $head `
  --broad false
```

CI helper 변경은 다음 범위로 검증한다.

```powershell
python -m pytest tests/ci -q
python -m ruff check scripts/ci tests/ci
actionlint .github/workflows/pr-quality-gate.yml .github/workflows/pr-ci-control-guard.yml
```

각 서비스 test 실행 명령은 repository `AGENTS.md`를 따른다. 전체 `scripts/test.sh`와 disposable PostgreSQL 계약은 일반적인 로컬 선행 검사가 아니며, 원격 CI 실패와 무관한 전체 회귀를 반복 실행하지 않는다.

## dev → main 고정 승격 후보

`dev → main` PR은 일반 기능 PR과 달리 두 branch 사이의 누적 변경 전체를 검증한다. `dev`는 다른 PR이 병합될 때마다 head가 바뀌므로, moving `dev`에서 서로 다른 시점에 얻은 CI 성공·리뷰·승인을 조합해 승격 근거로 사용하지 않는다.

최종 승격은 다음 순서를 따른다.

1. 승격 차단 수정이 `dev`에 병합된 뒤 짧은 promotion window 동안 추가 merge를 멈춘다.
2. `origin/dev`의 40자리 commit SHA를 release candidate로 기록하고 승격 PR의 `headRefOid`와 일치하는지 확인한다.
3. 같은 candidate에서 선택된 전체 PR quality gate와 `ci-required`를 한 번 완료한다.
4. 같은 candidate 환경에서 인증, Workflow 저장·실행, 권한 기반 RAG, trace/audit safe projection 같은 시연 핵심 흐름을 한 번 smoke한다.
5. 모든 code/document push가 끝난 뒤 current-head code review와 독립 write-maintainer `APPROVED` review를 받는다.
6. CI control 변경이 포함되면 승인 뒤 `/recheck-ci-control`로 `trusted-ci-control/base-policy`를 다시 계산한다.
7. 병합 직전에 branch head, PR head, CI, review, approval과 smoke evidence가 모두 candidate SHA를 가리키는지 다시 확인한다.

Candidate 고정 뒤 `dev`에 새 commit이 들어오면 이전 CI, review, smoke와 approval은 stale evidence다. 새 head를 승격하려면 candidate를 다시 기록하고 위 절차를 반복한다. 짧은 merge freeze를 보장할 수 없어 별도 release branch와 replacement PR이 필요하면 기존 `dev → main` 절차의 예외이므로 사전에 명시적으로 결정한다.

개발 중에는 실패한 lint 파일과 owning domain test만 대상으로 검증한다. 모든 domain suite와 PostgreSQL 계약을 포함하는 release gate를 수정 commit마다 반복하지 않는다. 동일 SHA의 GitHub 5xx·network failure처럼 코드와 무관한 transient failure만 원인을 확인한 뒤 제한적으로 재실행한다.

`ci-required` 실패는 선택된 하위 job의 실패·취소·비정상 skip을 집계한 결과이므로 하위 원인을 먼저 해결한다. `ci-control-review` evaluator job 성공과 authoritative `trusted-ci-control/base-policy` 성공도 구분한다. Evaluator가 정상 실행됐더라도 current-head 독립 승인이 없으면 base policy failure가 올바른 결과다.

## 배포 설정 검증

deployment-config-validation은 일반 배포 설정 변경에서는 변경된 종류만 검사한다. 품질 게이트 CI 제어 파일이 바뀌면 검증 명령 자체의 회귀를 놓치지 않도록 다섯 배포 검증기를 모두 실행한다.

- GitHub Actions: 일반 변경에서는 추가·수정·이름 변경된 workflow를 검사한다. CI 제어 변경에서는 기존 배포 workflow의 ShellCheck 부채와 분리된 안정적 smoke 대상인 품질 게이트, 신뢰 가드와 네 PostgreSQL 계약 workflow에 PR에서 실제 변경한 workflow를 합치고 중복을 제거해 고정 버전 actionlint로 검사한다.
- Helm: dependency build 전에 Chart.lock이 tracked regular file인지 확인하고 build 뒤 내용 불변을 검사한다. 기본/production values를 lint·render하고 CLOUD storage의 unknown type, 빈 bucket, 빈 region과 legacy component storage key를 각각 거부한 뒤 kubeconform v0.7.0으로 Kubernetes 1.31 compatibility schema를 검사한다. 이어서 deployment job이 명시적으로 integration flag를 설정한 exact support-surface/storage 계약 pytest로 `LOCAL`/`CLOUD`의 Pod env와 ConfigMap key reference closure를 확인한다. Schedule dispatch mode 검증은 optional Gateway/Worker template과 분리된 chart 전역 render 경계에서 실행되어 두 workload가 모두 비활성화돼도 non-disabled 값을 거부한다. 다른 test job의 runner에 Helm binary가 우연히 존재하는 것만으로 render test를 실행하지 않는다. 이 baseline은 EKS 지원 선언이 아니다.
- 지원 표면: 모든 PR에서 scope 분류 전에 승인 executable의 repository execution closure를 검사하고, executable workflow와 composite action metadata 변경에서는 deployment validation도 선택한다. 승인 path allowlist 밖의 workflow, 삭제된 workflow의 stale allowlist entry, 승인 workflow/local action 또는 지원하는 정적 형식으로 전이 참조한 `scripts/**` 실행 파일·Python module 내부의 AWS credential/ECR/EKS/eksctl 실행 신호, legacy dev/EKS path와 `infra/k8s`, `infra/terraform` prefix가 있으면 실패한다. 발견된 참조의 경로 이탈, 허용 prefix 밖 실행, 누락·symlink·non-UTF-8·크기/깊이/개수 한도 초과는 fail-closed한다. 따라서 참조 스크립트만 바꾸는 PR, 삭제 PR과 파일 이름 변경도 같은 검사를 거치며 정적 신호 검사는 current-head 독립 승인을 대체하지 않는다.
- Docker Compose: Compose 변경 또는 CI 제어 변경 시 tracked Compose 구성을 모두 해석한다. `compose.<variant>.yml`과 `docker-compose.<variant>.yml`은 같은 디렉터리의 기본 Compose 파일과 합성하고 선언된 profile을 활성화해 검사한다.
- Dockerfile: `Dockerfile`, `Dockerfile.*`, `*.Dockerfile` 이름을 지원한다. 실제 Dockerfile 변경에서는 rename을 delete+add로 해석해 이전 경로를 선택 근거로 보존하고, 현재 존재하는 변경 파일에 BuildKit check를 실행한다. 삭제 또는 비지원 이름으로의 rename은 검증 대상이 존재하지 않는 상태로 허용한다. Dockerfile 검증이 선택되면 `tests/ci/test_demo_seed_image_contract.py`도 실행해 로컬 demo guide가 호출하는 `/app/scripts/**`와 Gateway 이미지의 `COPY` 목적지 계약을 확인한다. CI 제어만 변경된 경우에는 기존 Dockerfile의 lint 부채와 분리된 `tests/ci/fixtures/dockerfile-smoke/Dockerfile`로 같은 명령 계약을 검증한다.

CI 제어와 PostgreSQL workflow가 사용하는 공식 Action은 40자리 commit SHA로 고정한다. Action 내부 runtime은 Node 24 기반 공식 major를 사용한다. Client build의 Node 20은 현재 Docker runtime과 별도 제품 계약이다. Provider-specific cloud CD와 장기 credential은 현재 지원 표면이 아니며 재도입 시 별도 ADR과 통합 증거가 필요하다.

## Cache와 artifact

- Client는 `package-lock.json` 기반 npm cache를 사용한다.
- Python은 uv dependency cache를 사용한다.
- Workflow Engine과 Log System은 기존 `uv.lock`을 사용한다. Gateway와 Shared의 독립 lock 정책은 별도 engineering 과제로 남아 있으며 cache가 재현성을 보장하지는 않는다.
- test selection state나 이전 성공 결과는 cache하지 않는다.
- 초기 gate는 별도 artifact를 업로드하지 않고 Actions log를 사용한다.
- DB dump, `.env`, raw payload, token과 credential은 cache나 artifact에 넣지 않는다.

## 실패 해석

| 실패 check | 우선 확인할 내용 |
| --- | --- |
| `change-scope` | base/head fetch, 잘못된 path, selector 단위 테스트 |
| `alembic-single-head` | 같은 parent에서 분기한 migration, 누락 merge revision |
| `python-lint` | 변경 Python 파일의 Ruff 오류 |
| `client-quality` | ESLint error, test fixture 타입, 영향 test, build |
| Python service job | Actions log에 출력된 실제 선택 target과 dependency 설치 |
| PostgreSQL contract | migration upgrade, race, 실제 SQL 계약 |
| `deployment-config-validation` | workflow·action metadata, Helm render/schema, 지원 종료 경로 재도입, Compose 또는 Dockerfile 오류 |
| `ci-required` | 선택된 하위 job의 실패, 취소 또는 비정상 skip |
| `trusted-ci-control/base-policy` | CI 제어 경로 변경, 현재 head 승인 부재, reviewer 권한 확인 실패, 변경 파일 열거 누락 |
| `ci-control-review` evaluator job | PR head 식별 불가, authoritative status 기록 실패, 재시도 소진 뒤 status 기록도 실패한 GitHub 운영 오류 |

검사가 실패했을 때 required check를 해제해 우회하지 않는다. selector 누락이면 mapping과 해당 단위 테스트를 함께 보강한다.

## GitHub ruleset 적용

MBA-326의 기존 red 상태는 PR #546으로 해소되었고, MBA-328은 해당 변경이 포함된 `dev @ 29a2bcf8` 위로 rebase했다. MBA-328 workflow가 `dev`에 병합된 뒤 probe PR에서 실제 context를 확인한다. 확인 전에는 ruleset을 먼저 활성화하지 않는다.

1. `dev`와 `main` 모두 `PR Quality Gate / ci-required`를 required status check로 등록한다.
2. CI control 변경의 `trusted-ci-control/base-policy`가 non-control PR에서도 안정적인 success context를 만드는지 probe로 확인한 뒤 required 등록 방식을 확정한다.
3. check의 expected source를 실제 probe에서 확인한 GitHub Actions app으로 제한한다.
4. required check가 최신 base 기준으로 다시 실행되고 stale head 결과를 재사용하지 않도록 strict 정책을 적용한다.
5. 두 branch 모두 PR 경유, unresolved review conversation 해결, branch 삭제 방지와 non-fast-forward 방지를 요구한다.
6. `main`은 최소 1명의 독립 승인을 요구한다. `dev`의 일반 승인 수는 CI control 변경의 독립 승인 정책과 분리한다.
7. 일반 bypass는 추가하지 않는다. 감사 가능한 예외 절차는 MBA-271에서 별도로 결정한다.

merge queue는 초기 범위가 아니다. 추후 도입하면 `merge_group` event에서도 같은 aggregate check가 생성되는지 먼저 검증한다.
