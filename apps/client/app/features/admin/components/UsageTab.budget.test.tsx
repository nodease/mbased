// UsageTab 예산 컬럼 확장(FR-051 진입점) 계약 테스트 — TDD red phase.
// docs/features/budget-management/component_spec.md:
// 비용 탭 테이블에 예산(USD)/사용률/상태 배지/예산 설정 버튼을 추가하고,
// budget null은 "미설정"으로 표시한다. 예산 설정 버튼은 BudgetEditModal을 연다.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    listWorkflowUsage: vi.fn(),
  },
}));

vi.mock('../../budget/api/budgetApi', () => ({
  budgetApi: {
    getWorkflowBudget: vi.fn(),
    upsertWorkflowBudget: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    ...props
  }: React.ComponentProps<'a'> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { adminApi } from '../api/adminApi';
import { budgetApi } from '../../budget/api/budgetApi';
import { UsageTab } from './UsageTab';

const mockedList = vi.mocked(adminApi.listWorkflowUsage);
const mockedGetBudget = vi.mocked(budgetApi.getWorkflowBudget);

const usageResponse = {
  total: 2,
  usage_data_complete: true,
  unresolved_provider_call_count: 0,
  period: {
    startAt: '2026-07-01T00:00:00+09:00',
    endAt: '2026-08-01T00:00:00+09:00',
  },
  items: [
    {
      workflow_id: 'wf-1',
      workflow_name: '비싼 워크플로우',
      prompt_tokens: 12345,
      completion_tokens: 2345,
      call_count: 87,
      total_cost: 92.345678,
      workflow_execution_cost: 90,
      agent_builder_cost: 2.345678,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: {
        monthly_budget_usd: 100,
        current_month_cost: 92.345678,
        usage_ratio: 0.923457,
        status: 'at_risk',
        usage_data_complete: true,
        unresolved_provider_call_count: 0,
      },
    },
    {
      workflow_id: 'wf-2',
      workflow_name: '예산 없는 워크플로우',
      prompt_tokens: 10,
      completion_tokens: 5,
      call_count: 2,
      total_cost: 0.123456,
      workflow_execution_cost: 0.1,
      agent_builder_cost: 0.023456,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: null,
    },
  ],
};

afterEach(() => {
  vi.resetAllMocks();
});

describe('UsageTab 예산 컬럼', () => {
  it('활성 예산은 금액/사용률/상태 배지를 표시하고 미설정은 미설정으로 표시한다', async () => {
    mockedList.mockResolvedValue(usageResponse);

    render(<UsageTab />);

    expect(await screen.findByText('비싼 워크플로우')).toBeInTheDocument();
    // 예산 금액은 USD 2자리 표시
    expect(screen.getByText('$100.00')).toBeInTheDocument();
    // BudgetStatusBadge — 사용률 정수 반올림 + 상태 라벨
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getByText('위험')).toBeInTheDocument();
    expect(screen.getByText('워크플로 실행 $90.00')).toBeInTheDocument();
    expect(screen.getByText('Agent Builder $2.35')).toBeInTheDocument();
    // budget null은 오류가 아니라 미설정 상태
    expect(screen.getByText('미설정')).toBeInTheDocument();
    // 두 행 모두 예산 설정 진입을 제공한다
    expect(
      screen.getAllByRole('button', { name: '예산 설정' }),
    ).toHaveLength(2);
  });

  it('예산 설정 버튼을 누르면 BudgetEditModal이 열린다', async () => {
    mockedList.mockResolvedValue(usageResponse);
    mockedGetBudget.mockResolvedValue({
      workflow_id: 'wf-1',
      workflow_name: '비싼 워크플로우',
      monthly_budget_usd: 100,
      is_enabled: true,
      current_month_cost: 92.345678,
      usage_ratio: 0.923457,
      status: 'at_risk',
    });

    render(<UsageTab />);
    await screen.findByText('비싼 워크플로우');

    fireEvent.click(
      screen.getAllByRole('button', { name: '예산 설정' })[0],
    );

    expect(
      await screen.findByLabelText('월 예산(USD)'),
    ).toBeInTheDocument();
  });
});
