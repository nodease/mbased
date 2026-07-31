// budgetApi(FR-051 관리자 예산 API 클라이언트) 계약 테스트 — TDD red phase.
// docs/features/budget-management/api_spec.md의 GET/PUT /admin/workflow-budgets/{workflow_id}.
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    put: vi.fn(),
  },
}));

import { apiClient } from '@/lib/apiClient';
import { budgetApi } from './budgetApi';

const mockedGet = vi.mocked(apiClient.get);
const mockedPut = vi.mocked(apiClient.put);

afterEach(() => {
  vi.resetAllMocks();
});

describe('budgetApi.getWorkflowBudget', () => {
  it('workflow id로 예산 단건을 조회한다', async () => {
    const budget = {
      workflow_id: 'wf-1',
      workflow_name: '비싼 워크플로우',
      monthly_budget_usd: 100,
      is_enabled: true,
      current_month_cost: 92.345678,
      usage_ratio: 0.923457,
      status: 'at_risk',
    };
    mockedGet.mockResolvedValueOnce({ data: budget });

    const result = await budgetApi.getWorkflowBudget('wf-1');

    expect(mockedGet).toHaveBeenCalledWith('/admin/workflow-budgets/wf-1');
    expect(result).toEqual(budget);
  });
});

describe('budgetApi.upsertWorkflowBudget', () => {
  it('금액과 활성 여부를 PUT body로 보낸다', async () => {
    mockedPut.mockResolvedValueOnce({
      data: { workflow_id: 'wf-1', monthly_budget_usd: 250, is_enabled: false },
    });

    const result = await budgetApi.upsertWorkflowBudget('wf-1', {
      monthly_budget_usd: 250,
      is_enabled: false,
    });

    expect(mockedPut).toHaveBeenCalledWith('/admin/workflow-budgets/wf-1', {
      monthly_budget_usd: 250,
      is_enabled: false,
    });
    expect(result.monthly_budget_usd).toBe(250);
  });
});
