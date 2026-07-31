# 프론트엔드 화면 명세

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 공통 구조

확정 근거:

- `apps/client/app/layout.tsx`
- `apps/client/app/dashboard/layout.tsx`
- `apps/client/lib/apiClient.ts`
- `apps/client/next.config.ts`

공통 API 클라이언트는 `NEXT_PUBLIC_API_URL`이 있으면 `${NEXT_PUBLIC_API_URL}/api/v1`, 없으면 `/api/v1`을 base URL로 사용한다. `withCredentials=true`로 쿠키 인증을 전송한다. 401 응답은 인증 페이지와 루트 경로를 제외하고 `/auth/login`으로 이동시킨다.

Next.js rewrite는 `/api/:path*`를 Gateway의 `/api/:path*`로 전달한다. SSE 스트리밍은 rewrite buffering 문제를 피하기 위해 `/stream-api/workflows/[workflowId]` API Route를 별도로 둔다.

## 화면 목록

| URL | 파일 | 목적 | 주요 API |
| --- | --- | --- | --- |
| `/` | `app/page.tsx` | 인증 상태 확인 후 대시보드 이동 또는 랜딩 표시 | `useAuthRedirect` |
| `/landing` | `app/landing/page.tsx` | 랜딩 페이지 | 없음 |
| `/auth/login` | `app/auth/login/page.tsx` | 이메일 로그인, Google 로그인 | `POST /auth/login`, `GET /auth/google/login` |
| `/auth/signup` | `app/auth/signup/page.tsx` | 회원가입 | `POST /auth/signup` |
| `/dashboard` | `app/dashboard/page.tsx` | 대시보드 홈 | 화면 파일 기준 정적/요약 |
| `/dashboard/mymodule` | `app/dashboard/mymodule/page.tsx` | 내 앱 목록, 앱 수정, 배포 활성 토글 | `GET /apps`, `PATCH /apps/{id}`, `PATCH /deployments/{id}/toggle` |
| `/dashboard/explore` | `app/dashboard/explore/page.tsx` | 마켓 공개 앱 탐색/복제 | `GET /apps/explore`, `POST /apps/{id}/clone` |
| `/dashboard/knowledge` | `app/dashboard/knowledge/page.tsx` | 지식베이스 목록/생성 진입 | `GET /knowledge` |
| `/dashboard/knowledge/[id]` | `app/dashboard/knowledge/[id]/page.tsx` | 지식베이스 상세, 문서 목록, 이름/설명/임베딩 모델 수정, 삭제 | `GET/PATCH/DELETE /knowledge/{id}`, `DELETE /rag/document/{document_id}` |
| `/dashboard/knowledge/[id]/document/[documentId]` | `app/dashboard/knowledge/[id]/document/[documentId]/page.tsx` | 문서 처리 설정, 청킹 미리보기, DB connector 설정, 진행률 SSE | `GET /knowledge/{id}`, `GET /knowledge/{id}/documents/{doc}`, `POST /knowledge/{id}/documents/{doc}/preview`, `POST /knowledge/{id}/documents/{doc}/process`, `/rag/document/{doc}/progress`, `/connectors/*` |
| `/dashboard/rag-test/[id]` | `app/dashboard/rag-test/[id]/page.tsx` | 지식베이스 검색 테스트 화면 | 내부 컴포넌트에서 `/rag/search-test/*` |
| `/dashboard/settings` | `app/dashboard/settings/page.tsx` | LLM provider/credential 설정 | `GET /llm/providers`, `GET/POST/DELETE /llm/credentials` |
| `/dashboard/settings/provider` | `app/dashboard/settings/provider/page.tsx` | 설정 페이지 redirect | `/dashboard/settings` |
| `/dashboard/statistics` | `app/dashboard/statistics/page.tsx` | 전역 로그/모니터링 탭 | statistics hooks |
| `/modules/[id]` | `app/modules/[id]/page.tsx` | 워크플로우 편집기 | workflow API, deployment API, stream API |
| `/shared/[urlSlug]` | `app/shared/[urlSlug]/page.tsx` | 공개 WebApp 실행 화면 | `GET /deployments/public/{slug}/info`, `POST /run-public/{slug}` |
| `/embed/chat/[urlSlug]` | `app/embed/chat/[urlSlug]/page.tsx` | iframe 임베드 채팅/위젯 | `GET /deployments/public/{slug}/info`, `POST /run-public/{slug}` |

## 인증 화면

### 로그인

파일: `apps/client/app/auth/login/page.tsx`

동작:

- 이메일/비밀번호를 `authApi.login()`으로 전송한다.
- 성공 시 대시보드로 이동한다.
- Google 로그인 버튼은 `authApi.googleLogin()`을 호출해 Gateway OAuth URL로 이동한다.
- 401/네트워크 오류에 대해 별도 메시지를 표시한다.

### 회원가입

파일: `apps/client/app/auth/signup/page.tsx`

동작:

- 이메일, 비밀번호, 이름으로 `authApi.signup()` 호출
- 성공 시 대시보드로 이동
- 이메일 중복/네트워크 오류 처리

## 내 모듈

파일: `apps/client/app/dashboard/mymodule/page.tsx`

기능:

- 현재 사용자의 앱 목록 조회
- 앱 공개 여부/메타데이터 수정
- 활성 배포 on/off 토글
- App card와 Deployment modal 사용

API:

- `GET /api/v1/apps`
- `PATCH /api/v1/apps/{app_id}`
- `PATCH /api/v1/deployments/{deployment_id}/toggle`

## 탐색

파일: `apps/client/app/dashboard/explore/page.tsx`

기능:

- `is_market=true`인 앱 목록 조회
- 공개 앱 복제

API:

- `GET /api/v1/apps/explore`
- `POST /api/v1/apps/{app_id}/clone`

주의:

- 복제는 서비스 레이어에서 활성 배포 snapshot 기반으로 새 앱/워크플로우를 만든다.
- 복제된 앱은 기본적으로 `is_market=false`이며 다시 공개할 수 없도록 제한된다.

## 워크플로우 편집기

파일:

- `apps/client/app/modules/[id]/page.tsx`
- `apps/client/app/features/workflow/components/editor/*`
- `apps/client/app/features/workflow/store/useWorkflowStore.ts`

기능:

- React Flow 기반 노드/엣지 편집
- 시작 노드 타입 선택: manual, webhook, schedule
- 노드 라이브러리에서 구현 노드 추가
- LLM/코드/HTTP/조건/템플릿/서브모듈/문서추출/변수추출/GitHub/메일/반복 노드 설정
- 초안 자동/수동 저장
- 테스트 실행 및 SSE 스트리밍 결과 표시
- 배포 생성/목록/활성화/삭제

주요 API:

- `GET /workflows/{workflow_id}/draft`
- `POST /workflows/{workflow_id}/draft`
- `POST /workflows/{workflow_id}/execute`
- `POST /stream-api/workflows/{workflow_id}`
- `POST /deployments`
- `GET /deployments?workflow_id=...`
- `GET /deployments/nodes`
- `PATCH /deployments/{deployment_id}/toggle`
- `DELETE /deployments/{deployment_id}`

## 지식베이스

파일:

- `apps/client/app/dashboard/knowledge/page.tsx`
- `apps/client/app/dashboard/knowledge/[id]/page.tsx`
- `apps/client/app/dashboard/knowledge/[id]/document/[documentId]/page.tsx`
- `apps/client/app/features/knowledge/api/knowledgeApi.ts`
- `apps/client/app/features/knowledge/api/connectorApi.ts`

기능:

- 지식베이스 생성/조회/수정/삭제
- 파일/API/DB source 업로드
- S3 presigned URL 또는 backend proxy 업로드
- 문서 분석, 청킹 미리보기, 처리 시작
- 문서 진행률 SSE
- DB connector 생성, 연결 테스트, schema 조회
- 검색 테스트 채팅/순수 검색

## 공개 실행 화면

### `/shared/[urlSlug]`

공개 WebApp 형태로 배포된 워크플로우를 실행한다. 배포 정보에서 입력 schema를 가져와 폼을 만들고, `/run-public/{urlSlug}`로 실행한다.

### `/embed/chat/[urlSlug]`

iframe 임베드용 채팅 UI다. Next config에서 `/embed`는 `frame-ancestors http: https: file: data:`로 허용된다.

## CSP/iframe 정책

확정 근거: `apps/client/next.config.ts`

- `/embed/*`와 `/shared/*`: 외부 frame ancestor 허용
- 그 외 경로: `X-Frame-Options: SAMEORIGIN`, `frame-ancestors 'self'`

## 주의/검증 필요

- `apps/client/package.json`의 `dev:all`은 현재 구조에 맞지 않는 `../server`를 참조한다.
- `pluginNode`는 UI registry에 있지만 `implemented=false`이고 Workflow Engine `NodeFactory`에는 없다.
- `note` 노드는 타입에는 있으나 NodeFactory에서 실행 노드로 만들지 않고 엔진에서 고립 노드 검증 제외 대상으로 다룬다.
- `knowledgeApi.updateKnowledgeBase()`는 `KnowledgeBaseResponse`를 반환한다고 타입 선언되어 있으나, 백엔드 `PATCH /knowledge/{kb_id}`는 204 No Content로 정의되어 있다.
