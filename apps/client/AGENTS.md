# AGENTS.md - apps/client

상위 `AGENTS.md`와 `apps/AGENTS.md`를 함께 따른다. 이 파일은 Next.js 클라이언트 코드 리뷰 기준을 보강한다.

## Review guidelines

- API 호출 변경은 Gateway 계약, request/response 타입, error response 처리, loading/empty/error 상태가 함께 맞는지 확인한다.
- 권한별 UI는 UX 차단으로만 취급하고, 보안 판단이 API에서 강제되는 흐름인지 확인한다.
- 제품 시연에서 사용하는 로그인 후 진입, 조직/팀 전환, workflow 편집 캔버스, node 설정, 실행 결과, 배포, RAG/Knowledge 화면이 막히거나 오작동하면 P0/P1 후보로 본다.
- workflow 편집, node 설정, deployment, credential, knowledge/RAG UI 변경은 저장 전 validation과 기존 workflow 호환성을 확인한다.
- React state, hook, server/client component 경계 변경은 stale state, race condition, double-submit, hydration mismatch 가능성을 확인한다.
- TypeScript 타입 완화, `any`, nullable 회피, unchecked cast가 API 계약 문제를 숨기지 않는지 확인한다.
- 사용자 입력, markdown/html 렌더링, URL, 파일 업로드/다운로드 경로는 XSS, injection, unsafe redirect, 민감 데이터 노출 위험을 확인한다.
- 긴 텍스트, 작은 화면, 빈 데이터, 실패 응답에서도 주요 UI가 겹치거나 사라지지 않는지 확인한다.
- 핵심 화면 흐름이 바뀌면 관련 Vitest, component-level 테스트, 또는 수동 검증 설명이 충분한지 확인한다.
