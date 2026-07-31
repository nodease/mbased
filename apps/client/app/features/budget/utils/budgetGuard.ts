import { isAxiosError } from 'axios';
import type { BudgetStatusPayload } from '../types';

export const BUDGET_EXCEEDED_MESSAGE =
  '월 예산 초과로 실행이 차단되었습니다';

const getErrorCode = (data: unknown) => {
  if (
    typeof data === 'object' &&
    data !== null &&
    'detail' in data
  ) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'object' && detail !== null && 'code' in detail) {
      return (detail as { code?: unknown }).code;
    }
  }
  return undefined;
};

export const isBudgetExceededError = (error: unknown) => {
  if (!isAxiosError(error)) return false;
  return (
    error.response?.status === 429 &&
    getErrorCode(error.response.data) === 'budget.exceeded'
  );
};

export const budgetRunBlockMessage = (
  budgetStatus?: BudgetStatusPayload | null,
) => {
  if (budgetStatus?.status === 'exceeded') return BUDGET_EXCEEDED_MESSAGE;
  return null;
};
