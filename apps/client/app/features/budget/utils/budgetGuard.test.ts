// 예산 초과 실행 차단 UX 헬퍼 계약 테스트 — TDD red phase.
// docs/features/budget-management/component_spec.md:
// - 내 워크플로우 목록: exceeded면 실행 진입 차단 + tooltip (UX 보조, 최종 차단은 Gateway)
// - 테스트 실행: 429 budget.exceeded 응답을 일반 오류와 구분해 안내
import { describe, expect, it } from 'vitest';

import {
  BUDGET_EXCEEDED_MESSAGE,
  budgetRunBlockMessage,
  isBudgetExceededError,
} from './budgetGuard';

const axiosError = (status: number, data?: unknown) =>
  Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data },
  });

describe('isBudgetExceededError', () => {
  it('429 + budget.exceeded 코드를 예산 차단으로 판별한다', () => {
    const error = axiosError(429, {
      detail: { code: 'budget.exceeded', message: 'Workflow monthly budget exceeded.' },
    });

    expect(isBudgetExceededError(error)).toBe(true);
  });

  it('코드가 없는 429는 예산 차단으로 판별하지 않는다', () => {
    expect(isBudgetExceededError(axiosError(429, { detail: 'rate limited' }))).toBe(
      false,
    );
  });

  it('일반 실행 오류(500)와 비 axios 오류는 판별하지 않는다', () => {
    expect(isBudgetExceededError(axiosError(500))).toBe(false);
    expect(isBudgetExceededError(new Error('boom'))).toBe(false);
    expect(isBudgetExceededError(null)).toBe(false);
  });
});

describe('budgetRunBlockMessage', () => {
  it('exceeded 상태는 차단 tooltip 문구를 돌려준다', () => {
    expect(
      budgetRunBlockMessage({ usage_ratio: 1.2, status: 'exceeded' }),
    ).toBe(BUDGET_EXCEEDED_MESSAGE);
  });

  it('위험(at_risk)과 정상 상태는 실행을 막지 않는다', () => {
    expect(
      budgetRunBlockMessage({ usage_ratio: 0.95, status: 'at_risk' }),
    ).toBeNull();
    expect(
      budgetRunBlockMessage({ usage_ratio: 0.5, status: 'normal' }),
    ).toBeNull();
  });

  it('예산 미설정(null/undefined)은 기존 렌더링을 유지한다', () => {
    expect(budgetRunBlockMessage(null)).toBeNull();
    expect(budgetRunBlockMessage(undefined)).toBeNull();
  });
});
