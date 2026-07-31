# 로컬 Demo/Test DB Seed

Status: Draft

## 목적

최종 시연용 DB 상태와 개발/QA 테스트용 DB 상태를 분리해서 관리한다.

- `demo` profile: 최종 발표 시연 기준 데이터다. 시연 직전에 같은 상태로 복원한다.
- `test` profile: 팀원이 기능 구현 중 자유롭게 조작하고 다시 덮어쓸 수 있는 테스트 데이터다.

두 profile은 다른 조직, 계정, workflow UUID를 사용한다. 테스트 데이터를 반복 갱신해도 최종 시연용 demo seed를 직접 덮어쓰지 않는다.

## 공통 실행 위치

각자 로컬에 clone한 repo root에서 실행한다. 아래 경로는 예시이며, 팀원마다 다를 수 있다.

```powershell
cd <YOUR_NODEASE_REPO_ROOT>
```

로컬 venv 기준으로 실행한다.

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --dry-run
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --dry-run
```

## Demo Profile

최종 시연 기준 DB로 맞춘다.

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --profile demo --reset
```

기존 명령과의 호환을 위해 `--profile demo`는 생략할 수 있다.

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --reset
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --reset
```

동작:

- demo seed가 관리하는 고정 UUID row만 삭제하고 다시 만든다.
- 기존 로컬 DB 전체를 비우지는 않는다.
- 최종 발표 직전에는 이 명령을 사용한다.

## Test Profile

팀원 개발/QA용 테스트 DB 상태로 맞춘다.

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile test --reset
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --profile test --reset
```

동작:

- `노디즈 테스트 조직` 아래 test profile 고정 UUID 계정, 팀, workflow, 권한을 삭제하고 다시 만든다.
- test 조직의 Agent Builder 임시 Draft, Request, Session은 QA 기준 상태 복원을 위해 함께 제거한다.
- demo profile 데이터는 건드리지 않는다.
- 팀원이 고정 test seed 데이터를 직접 변경했더라도 이 명령으로 테스트 기준 상태를 다시 덮어쓸 수 있다.
- 임의로 생성한 모든 동적 데이터를 비우는 명령은 아니다. 전체 초기화가 필요하면 아래 `--drop-existing-data --yes` 경로를 사용한다.

새로운 기능의 고정 테스트 더미 데이터가 필요하면 `apps/shared/db/demo_seed.py`의 test profile seed 영역에 추가한다. 최종 발표용 데이터가 아니라면 demo profile에 바로 넣지 않는다.

## 전체 로컬 DB 초기화

로컬 DB를 완전히 비우고 선택한 profile만 다시 만든다.

Demo만 재생성:

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --drop-existing-data --yes
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --profile demo --reset --drop-existing-data --yes
```

Test만 재생성:

Windows:

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile test --reset --drop-existing-data --yes
```

macOS/Linux:

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --profile test --reset --drop-existing-data --yes
```

주의:

- 내부적으로 PostgreSQL `public` schema를 `CASCADE`로 재생성한다.
- 기존 개발/QA 데이터가 모두 삭제된다.
- 실수 방지를 위해 `--drop-existing-data`는 `--reset --yes`와 함께 쓸 때만 동작한다.

## Schema readiness / 오래된 로컬 DB

`scripts/seed_demo.py`는 빈 로컬 DB 편의를 위해 `Base.metadata.create_all()`을 호출하지만, 이 경로는 기존 테이블에 새 컬럼을 `ALTER`하지 않는다. 오래된 로컬 DB에 최신 demo seed를 그대로 실행하면 seed가 일부 성공한 것처럼 보여도 다른 Knowledge/RAG API나 runtime 경로가 뒤늦게 500으로 실패할 수 있다.

`create_all()`은 explicit demo/test bootstrap 전용이다. MBA-187 적용 뒤 Gateway server startup은 migration-managed table이나 enum을 자동 생성/보정하지 않으며, 시작 전에 Alembic single head와 DB revision readiness를 통과해야 한다. 빈 로컬 DB도 아래 migration 또는 명시적 seed/reset 절차를 사용한다.

그래서 demo profile seed는 데이터 쓰기 전에 Knowledge/RAG 데모 흐름이 의존하는 필수 테이블/컬럼과 Alembic migration readiness를 확인한다. `alembic_version` table이 없거나, DB revision이 코드의 단일 head와 맞지 않거나, 코드 migration graph에 head가 여러 개이면 seed를 중단하고 다음 중 하나를 선택하도록 안내한다.

기존 로컬 데이터를 보존해야 하는 경우 migration을 먼저 적용한다.

```powershell
apps/gateway/.venv/Scripts/python.exe -m alembic -c apps/shared/alembic.ini upgrade heads
```

데모 전용 disposable DB라면 전체 재생성이 가장 단순하다.

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --drop-existing-data --yes
```

## Credential / Embedding 정책

demo seed는 기본적으로 precomputed Knowledge fixture를 사용해 공개 법령 PDF 7개와 휴가·복지 정책 Markdown 2개를 `DocumentChunk`와 `text-embedding-3-small` 1536차원 embedding까지 생성한다. Fixed fixture는 독립 문서마다 별도 Knowledge Base를 사용하며 한 KB에 여러 Document를 넣지 않는다. 여러 문서를 함께 검색하는 범위는 Knowledge Collection 또는 명시된 여러 KB reference로 구성한다. `사내문서:` 접두사의 기존 Markdown KB와 이를 전용으로 묶던 Collection은 seed에서 제거됐다. `demodata/`의 팀별 온보딩 PDF 세 개는 각각 별도 Document-level KB로 등록하며, `--enable-runtime-openai-credential` 또는 fixture 재생성 옵션에서는 같은 실행에서 실제 파싱·embedding 생성까지 수행한다. `사내 IT 문의 자동 처리`는 `gpt-5.4`·`gpt-5.4-mini`를 사용하고, `신입 사원 온보딩 챗봇`은 `gpt-5.6`과 fallback `gpt-5.4`를 자동 모델 라우팅으로 사용한다.

제품의 새 LLM 노드 기본값은 `scoreThreshold=0.3`, `topK=5`이며, `신입 사원 온보딩 챗봇`도 이 값을 명시적으로 사용한다.

- 기본 reset에는 `apps/shared/db/fixtures/demo_knowledge_chunks.jsonl.gz` fixture를 사용한다.
- 기본 reset에는 `OPENAI_API_KEY`와 법령 PDF 원본이 필요하지 않다.
- chunk content 암호화에는 `ENCRYPTION_KEY`가 필요하다.
- fixture에는 평문 chunk와 embedding vector가 들어 있으며, DB insert 시점에 chunk content를 암호화한다.
- fixture를 다시 만들 때만 repo root `.env` 또는 실행 환경의 `OPENAI_API_KEY`와 `local/legal-docs-labor/` 법령 PDF가 필요하다.
- seed는 API key 값을 출력하지 않는다.
- 기본 seed는 embedding 생성에 사용한 OpenAI key를 DB credential로 저장하지 않는다.
- 기본 seed에는 비용 탭/요약 카드 집계용 non-secret demo credential metadata row가 포함될 수 있으나, 실제 provider 호출용 key가 아니다.
- 실제 workflow LLM/RAG runtime 실행에는 `gpt-5.6`, `gpt-5.4`, `gpt-5.4-mini`, `text-embedding-3-small`을 사용할 수 있는 organization-scoped verified credential relation과 `operator` 이상 LLM permission이 필요하다.

발표 전 실제 브라우저 smoke/E2E처럼 workflow runtime까지 검증해야 하면 disposable demo DB에서만 다음 옵션을 사용한다. 이 옵션은 먼저 실행 환경의 `OPENAI_API_KEY`를 사용하고, 없으면 터미널에서 key를 숨김 입력으로 받는다. 입력한 key를 `.env`나 CLI 인자에 쓰지 않으며, seed는 key 값을 출력하지 않는다.

seed는 `text-embedding-3-small`에 짧은 검증 요청을 보내 1536차원 embedding을 받는지 확인한 뒤 실행한다. 이 요청에는 소량의 API 비용이 발생한다. 검증에 실패하면 raw provider 응답 없이 한 번만 경고하고 `Continue seeding with this key anyway? [Y/n]`을 표시한다. Enter 또는 `y`는 제공한 key로 계속 진행하고, `n`은 DB/schema 변경 전에 종료한다. 비대화형 실행에서 검증 실패 후 확인을 받을 수 없으면 seed는 종료한다.

실제 runtime credential을 seed하면 key는 로컬 DB의 demo credential에 저장된다. 현재 구현의 `llm_credentials.encrypted_config`는 이름과 달리 평문 JSON 저장이라는 알려진 한계가 있으므로 운영/공유 DB에서 사용하지 않는다.

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --enable-runtime-openai-credential
```

fixture 재생성과 runtime credential 준비를 한 번에 수행할 때만 두 옵션을 함께 사용한다. 이 명령은 법령 fixture 전체를 다시 만들기 때문에 `local/legal-docs-labor/` 법령 PDF 원본도 필요하다. `demodata/` PDF만 검색 가능하게 만들 목적이라면 사용하지 않는다.

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --regenerate-knowledge-fixture --enable-runtime-openai-credential
```

fixture를 원본 PDF와 OpenAI embedding으로 재생성할 때만 다음 옵션을 사용한다.

```powershell
apps/gateway/.venv/Scripts/python.exe scripts/seed_demo.py --profile demo --reset --regenerate-knowledge-fixture
```

## Demo 계정

모든 계정 비밀번호는 `123123`이다.

| 이메일 | 표시명 | 용도 |
| --- | --- | --- |
| `admin@nodease.demo` | 관리자 김도윤 | 권한 신청 승인, audit/운영 지표 확인 |
| `rookie@nodease.demo` | 신입사원 이서연 | 최초 생성/배포 권한 제한과 승인 흐름 확인 |
| `author@nodease.demo` | 운영자 박민준 | trace 확인과 LLM 노드 최적화 |
| `tester.manager@nodease.demo` | 테스트 관리자 | manager 권한 확인 |
| `tester.builder@nodease.demo` | 테스트 빌더 | 생성/편집/배포 권한 확인. runtime credential opt-in seed에서는 Agent Builder intent model `operator` 권한 포함 |
| `tester.member@nodease.demo` | 테스트 멤버 | 일반 member 화면과 권한 제한 확인 |
| `dev@nodease.demo` | 개발팀 사용자 정개발 | 플랫폼 온보딩 KB 접근 권한 확인 |
| `planning@nodease.demo` | 기획팀 사용자 김기획 | 영업 온보딩 KB 접근 권한 확인 |
| `seoyeon.kim@nodease.demo` | 김서연 | 플랫폼개발팀 온보딩 문서 접근 시연 |
| `junho.lee@nodease.demo` | 이준호 | 영업팀 온보딩 문서 접근 및 타 팀 차단 시연 |
| `jimin.park@nodease.demo` | 박지민 | People 팀 온보딩 관리자, 전체 팀 KB·audit 관리 |
| `invited@nodease.demo` | 초대대기 한지민 | invited 상태 UI 확인 |
| `suspended@nodease.demo` | 정지회원 최유진 | suspended 상태 UI 확인 |
| `removed@nodease.demo` | 제거회원 정하늘 | removed 상태 UI 확인 |

Demo 조직:

- `노디즈 데모 조직`

Demo 주요 workflow:

- `온보딩 챗봇`
- `사내 IT 문의 자동 처리`
- `신입 사원 온보딩 챗봇` (목록 마지막)

`사내 IT 문의 자동 처리`의 초기 캔버스 노드 좌표에는 편집 화면의 `레이아웃 최적화` 버튼과 동일한 canonical 좌→우 자동 레이아웃이 적용되어 있다.

`온보딩 챗봇`은 새 workflow를 만든 직후처럼 `입력` 노드 하나만 있는 미배포 draft이며, edge나 다른 노드는 seed하지 않는다. `플랫폼개발팀`과 `영업팀`에는 이 workflow의 실행 권한인 `operator`를 부여한다.

`신입 사원 온보딩 챗봇`은 입력·LLM·출력 노드로 구성하고 활성 `internal_chatbot`으로 배포한다. 인증 실행 URL은 `/modules/97000000-0000-0000-0000-000000000002/run?deploymentId=97000000-0000-0000-0000-000000000003`으로 고정된다. LLM node는 `gpt-5.6`과 fallback `gpt-5.4`를 사용하고 자동 모델 라우팅이 켜져 있다. RAG는 `팀별 온보딩 접근 제어 문서` Collection 및 하위 온보딩 KB 3개를 포함하며 `scoreThreshold=0.3`, `topK=5`를 사용한다. `플랫폼개발팀`과 `영업팀`에는 실행 권한인 `operator`를 부여한다. 세 데모 App의 정렬 시각을 시드 실행 시점부터 1초 간격으로 지정해 `updated_at DESC` 목록에서 `온보딩 챗봇`, `사내 IT 문의 자동 처리`, `신입 사원 온보딩 챗봇` 순서를 유지한다.

이전에 seed하던 나머지 workflow 11개와 해당 App, Deployment, 실행·권한·사용량 데이터는 일반 demo seed와 `--reset` 모두에서 retired cleanup 대상으로 삭제한다.

## Demo Security Alert

demo seed는 `신입사원 이서연`이 관리자 전용 보안 알림 목록 조회 5회와 해결 처리 5회를 시도해 `permission.denied`가 반복된 상황을 미리 생성한다. 10개 사건은 5분 안에 발생한 safe audit evidence로 연결되고, `repeated_permission_denied` v1 규칙의 `medium`·`open` Security Alert 한 건으로 표시된다. 따라서 관리자 계정에서는 Sidebar 알림 요약과 Admin Dashboard의 `보안 알림` 탭에서 reset 직후 바로 확인할 수 있다.

시드 audit에는 검증된 organization ID, 고정 permission/operation/reason code와 opaque target만 저장한다. 이메일, URL/path, request body, 임의 alert ID와 raw exception은 저장하지 않는다.

## Demo Knowledge / RAG 데이터

demo seed의 precomputed fixture는 다음 공개 법령을 `documents.status = completed`와 `document_chunks` embedding까지 생성한다.

Public 법령 자료:

- `근로기준법`
- `남녀고용평등과 일·가정 양립 지원에 관한 법률`
- `남녀고용평등과 일·가정 양립 지원에 관한 법률 시행령`
- `개인정보 보호법`
- `산업안전보건법`
- `근로자퇴직급여 보장법`
- `채용절차의 공정화에 관한 법률`

법령 KB는 `공개 노동·온보딩 법령 컬렉션`에 연결되며 `safe_metadata.visibility = public`으로 seed된다. 로그인 runtime에서도 권한 helper를 통과해야 하므로 demo 주요 팀에는 법령 KB `operator` 권한을 함께 부여한다.

`사내문서:` 접두사의 기존 Markdown KB 11개와 `사내 온보딩·운영 문서 컬렉션`은 더 이상 생성하지 않는다. 과거 seed로 만든 고정 UUID 행은 일반 demo seed와 `--reset` 모두에서 retired cleanup 대상으로 삭제한다. 민감 재무 예시 문서는 기존처럼 일반 RAG `use` 권한을 주지 않는다.

### 팀별 온보딩 Knowledge 데이터

demo seed는 `demodata/`의 아래 PDF를 대응 KB에 자동 등록한다. 검색 가능한 chunk와 embedding까지 한 번에 만들려면 `--enable-runtime-openai-credential` 옵션만 사용한다. 기존 precomputed fixture는 공개 법령을 채우고, 입력한 OpenAI key는 `demodata/` PDF 세 개의 embedding과 실제 workflow runtime credential에 사용된다.

| Knowledge Base | 자동 등록할 파일 | 접근 팀 |
| --- | --- | --- |
| `온보딩 문서: 플랫폼개발팀` | `platform_team_onboarding_v4.pdf` | 플랫폼개발팀, People 팀, 개발팀 |
| `온보딩 문서: 영업팀` | `sales_team_onboarding_v2.pdf` | 영업팀, People 팀, 기획팀 |
| `온보딩 문서: 재무팀` | `finance_team_onboarding_v3.pdf` | 재무팀, People 팀 |

세 KB의 `safe_metadata.safe_label`에는 위 표의 Knowledge Base 이름을 저장한다. 따라서 direct KB로 사용된 근거의 사용자 Citation은 일반 `참조 문서` 대신 `온보딩 문서: 플랫폼개발팀`과 같은 KB 이름을 표시한다. Collection 경유 근거는 기존 보안 경계에 따라 하위 KB 이름이 아니라 Collection의 승인된 표시 라벨을 사용한다.

현재 실행 권한 경계는 document-level KB다. 한 PDF 안의 일부 chunk만 `manager`에게 허용하는 동적 `role_acl`은 지원하지 않는다. 따라서 플랫폼 PDF 원본은 보존하되, 일반 플랫폼 KB에 저장·색인하는 복사본에서는 manager-only 마지막 페이지를 제외한다. 제외된 내용을 시연하려면 후속으로 manager 전용 KB/PDF를 별도 구성해야 한다.

발표 전에 아래 명령으로 runtime credential 등록과 PDF embedding 생성을 함께 수행할 수 있다.

```bash
apps/gateway/.venv/bin/python scripts/seed_demo.py --profile demo --reset --enable-runtime-openai-credential
```

기본 reset은 fixture를 사용하므로 법령 PDF 원본이 없어도 RAG 검색용 chunk와 embedding을 생성한다. 원본 PDF 재생성 모드에서는 로컬 `local/legal-docs-labor/`에 법령 PDF가 있어야 한다.
같은 법령의 PDF가 여러 개 있으면 seed는 파일명 끝의 시행일 `YYYYMMDD`가 가장 큰 PDF를 선택한다.

## Demo workflow 입력 예시

`사내 IT 문의 자동 처리`

웹훅 또는 테스트 payload에는 `department`와 `message`를 넣는다. 수동 검증용 10개 예시는 `docs/demo/internal-it-helpdesk-manual-payloads.jsonl`에 있다.

```json
{
  "department": "플랫폼개발",
  "message": "MFA를 재등록한 뒤 SSO는 로그인되지만 VPN 연결 후 Git 저장소에서 권한 거부가 납니다. 계정, MFA, VPN, Git 권한 중 무엇부터 점검해야 하는지 문서 근거로 순서를 정리해 주세요."
}
```

## Test 계정

모든 계정 비밀번호는 `123123`이다.

| 이메일 | 표시명 | 용도 |
| --- | --- | --- |
| `test.admin@test.nodease.demo` | 테스트 관리자 | 테스트 조직 manager |
| `test.builder@test.nodease.demo` | 테스트 빌더 | 테스트 workflow manager |
| `test.member@test.nodease.demo` | 테스트 멤버 | 테스트 workflow viewer |
| `test.invited@test.nodease.demo` | 테스트 초대대기 | invited 상태 확인 |
| `test.suspended@test.nodease.demo` | 테스트 정지회원 | suspended 상태 확인 |

Test 조직:

- `노디즈 테스트 조직`

Test workflow:

- `테스트용 기능 검증 워크플로우`

## Test workflow 입력 예시

`테스트용 기능 검증 워크플로우`

테스트 실행 Sidebar의 입력 변수 `message`에 넣는다.

```text
테스트 프로파일에서 workflow 실행, 권한, 응답 표시가 정상인지 확인합니다.
```

반복 QA용 입력:

```text
QA 중 수정한 화면과 API 연결이 test profile seed 이후에도 정상 동작하는지 확인합니다.
```

## Docker Gateway에 적용

Docker Gateway 이미지가 현재 checkout의 seed 코드보다 오래됐다면 이미지를 반드시 다시 빌드한다. Seed는 Shared DB model뿐 아니라 도메인별 persistence adapter와 schema에도 의존하므로 `scripts/seed_demo.py`와 `apps/shared/db/demo_seed.py`만 컨테이너에 복사하는 부분 갱신은 지원하지 않는다.

```powershell
docker compose -f docker/docker-compose.yml up -d --build gateway
docker compose -f docker/docker-compose.yml exec gateway sh -lc "PYTHONPATH=/app python /app/scripts/seed_demo.py --profile demo --reset"
```

Test profile 적용:

```powershell
docker compose -f docker/docker-compose.yml exec gateway sh -lc "PYTHONPATH=/app python /app/scripts/seed_demo.py --profile test --reset"
```
