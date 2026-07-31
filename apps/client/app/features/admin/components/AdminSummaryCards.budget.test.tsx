// AdminSummaryCards 예산 카드 실데이터(FR-015) 계약 테스트 — TDD red phase.
// docs/features/budget-management/api_spec.md GET /admin/summary 확장:
// budget 블록의 위험 개수를 실제 데이터로 표시한다.
// null은 기존 "예산 미설정" 유지(기존 테스트가 보증).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    getOrganizationSummary: vi.fn(),
  },
}));

import { adminApi } from '../api/adminApi';
import { AdminSummaryCards } from './AdminSummaryCards';

const mockedSummary = vi.mocked(adminApi.getOrganizationSummary);

const summaryProps = {
  members: { active: 11, invited: 1, suspended: 1, removed: 1 },
  teams: { active: 13, assignments: 12 },
  credentials: { active: 1, providers: 4 },
  knowledgeBases: 25,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('AdminSummaryCards 예산 카드', () => {
  it('budget 블록이 있으면 비율 대신 위험 개수만 간결하게 표시한다', async () => {
    mockedSummary.mockResolvedValue({
      month: '2026-07',
      total_cost: 123.456789,
      workflow_execution_cost: 120,
      agent_builder_cost: 3.456789,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: {
        budgeted_workflow_count: 5,
        at_risk_count: 1,
        exceeded_count: 1,
        ratio: 0.4,
      },
    });

    render(<AdminSummaryCards {...summaryProps} />);

    expect(await screen.findByText('2개')).toBeInTheDocument();
    expect(screen.getByText('위험')).toBeInTheDocument();
    expect(screen.getByText('예산 임박 1')).toBeInTheDocument();
    expect(screen.getByText('예산 초과 1')).toBeInTheDocument();
    expect(screen.getByTestId('budget-at-risk-dot')).toHaveClass(
      'bg-amber-400',
    );
    expect(screen.queryByText('40%')).not.toBeInTheDocument();
    expect(screen.queryByText('가장 위험한 workflow')).not.toBeInTheDocument();
    expect(
      screen.queryByText('현재 위험한 workflow가 없습니다'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', {
        name: '이번 달 비용과 예산 비용 탭에서 확인',
      }),
    ).toHaveAttribute('href', '/dashboard/admin?tab=usage');
    expect(screen.queryByText('예산 미설정')).not.toBeInTheDocument();
  });

  it('활성 예산이 모두 정상이면 0개와 정상 상태만 표시한다', async () => {
    mockedSummary.mockResolvedValue({
      month: '2026-07',
      total_cost: 10,
      workflow_execution_cost: 8,
      agent_builder_cost: 2,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: {
        budgeted_workflow_count: 3,
        at_risk_count: 0,
        exceeded_count: 0,
        ratio: 0,
      },
    });

    render(<AdminSummaryCards {...summaryProps} />);

    expect(await screen.findByText('0개')).toBeInTheDocument();
    expect(screen.queryByText('가장 위험한 workflow')).not.toBeInTheDocument();
  });
});
