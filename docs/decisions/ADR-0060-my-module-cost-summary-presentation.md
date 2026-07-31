# ADR-0060: 내 모듈 비용 요약 표시 경계

Status: Accepted

Related ADRs: [ADR-0055](ADR-0055-agent-builder-intent-usage-attribution.md)

이 ADR은 ADR-0055의 사용량 기록·귀속·집계 결정은 유지하고, 내 모듈의 page-level 총비용 표시 결정만 대체한다.

## Context

`/dashboard/mymodule`은 상단에 `예상 월 비용`, `평균 증가 추세`, `예산 위험`, `비용 위험 신호` 카드 네 개를 표시했다. 전체 예상 월 비용은 목록 pagination과 무관한 `GET /apps/operations/cost-summary`를 사용했지만, 나머지 요약은 현재 로드된 operations row를 기준으로 계산해 한 줄 안에서도 집계 범위가 달랐다. 같은 화면 아래의 workflow별 운영 카드와 표에도 월 예상 비용, 증가 추세, 예산 상태와 최적화 신호가 표시되어 정보가 중복됐다.

ADR-0055는 Agent Builder planner/repair 비용을 기존 비용 집계에 포함하고 workflow 실행 비용과 구분하도록 결정했다. 이 사용량 귀속과 집계 계약은 유지하되, 내 모듈 화면에서 page-level 전체 합계를 반드시 상단 카드로 표시해야 한다는 표현은 현재의 간결한 운영 현황 UI와 맞지 않는다.

## Decision

1. `/dashboard/mymodule`은 상단의 `예상 월 비용`, `평균 증가 추세`, `예산 위험`, `비용 위험 신호` 요약 카드를 렌더링하지 않는다.
2. 화면 진입과 새로고침에서 `GET /apps/operations/cost-summary`를 호출하지 않는다. pagination된 operations row를 클라이언트에서 합산해 전체 비용처럼 표시하지도 않는다.
3. 각 workflow의 row와 grid card는 기존 월 예상 총비용을 유지하고, 그 아래에 workflow 실행 비용과 Agent Builder 비용을 구분해 표시한다. workflow별 증가 추세, 예산 상태와 최적화 기능도 유지한다.
4. `GET /apps/operations/cost-summary`는 호환 API로 유지한다. 접근 가능한 활성 배포 primary workflow 전체의 총비용과 workflow 실행/Agent Builder 구분값을 반환하는 서버 집계 계약은 변경하지 않는다.
5. Admin 대시보드의 조직 월간 총비용과 예산 위험 요약은 유지한다. 이 결정은 Agent Builder 사용량 귀속, workflow 예산 사용액, 전월 추세 계산과 비용 데이터 보존 정책을 변경하지 않는다.

## Consequences

- 내 모듈 화면은 page-level 전체 활성 workflow 비용 합계를 표시하지 않고 workflow별 운영 정보에 집중한다.
- 사용자는 각 workflow의 월 예상 총비용과 비용 구성을 계속 확인할 수 있지만, 내 모듈 화면에서 모든 활성 workflow 비용을 하나의 숫자로 확인할 수는 없다.
- `cost-summary` API와 해당 서버 테스트는 다른 소비자와 호환성을 위해 유지한다. API 제거는 별도 결정으로 다룬다.
- ADR-0055의 사용량 기록·귀속·집계 결정은 유지한다. 다만 내 모듈 화면이 page-level 총비용 요약을 표시해야 한다는 기존 표현은 이 ADR의 표시 정책으로 대체한다.
