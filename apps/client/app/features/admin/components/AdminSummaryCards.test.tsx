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

describe('AdminSummaryCards', () => {
  it('이번 달 비용을 USD 2자리로 표시하고 budget null은 예산 미설정으로 표시한다', async () => {
    mockedSummary.mockResolvedValue({
      month: '2026-07',
      total_cost: 123.456789,
      workflow_execution_cost: 120,
      agent_builder_cost: 3.456789,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: null,
    });

    render(<AdminSummaryCards {...summaryProps} />);

    expect(await screen.findByText('$123.46')).toHaveClass('text-2xl');
    expect(screen.getByText('워크플로 실행 $120.00')).toBeInTheDocument();
    expect(screen.getByText('Agent Builder $3.46')).toBeInTheDocument();
    expect(screen.getByText('예산 미설정')).toBeInTheDocument();
    expect(
      screen.getByRole('link', {
        name: '이번 달 비용과 예산 비용 탭에서 확인',
      }),
    ).toHaveAttribute('href', '/dashboard/admin?tab=usage');
  });

  it('6개 지표를 3개 그룹 카드 한 줄로 묶고 기존 이동 경로를 유지한다', async () => {
    mockedSummary.mockResolvedValue({
      month: '2026-07',
      total_cost: 4.43,
      workflow_execution_cost: 4.43,
      agent_builder_cost: 0,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
      budget: null,
    });

    render(<AdminSummaryCards {...summaryProps} />);

    expect(await screen.findByText('비용·예산')).toBeInTheDocument();
    expect(screen.getByText('조직 구성')).toBeInTheDocument();
    expect(screen.getByText('운영 리소스')).toBeInTheDocument();
    expect(screen.getByLabelText('관리 요약')).toHaveClass('lg:grid-cols-3');

    expect(
      screen.getByRole('link', {
        name: '활성 멤버 조직 구성 멤버 보기에서 확인',
      }),
    ).toHaveAttribute(
      'href',
      '/dashboard/admin?tab=organization-structure&view=members',
    );
    expect(
      screen.getByRole('link', {
        name: '활성 팀 조직 구성 팀 보기에서 확인',
      }),
    ).toHaveAttribute(
      'href',
      '/dashboard/admin?tab=organization-structure&view=teams',
    );
    expect(
      screen.getByRole('link', { name: 'LLM Credentials 탭에서 확인' }),
    ).toHaveAttribute('href', '/dashboard/admin?tab=credentials');
    expect(
      screen.getByRole('link', { name: '지식 기반 탭에서 확인' }),
    ).toHaveAttribute('href', '/dashboard/admin?tab=knowledge');
  });

  it('요약 조회 실패 시 오류 상태를 표시한다', async () => {
    mockedSummary.mockRejectedValue(new Error('boom'));

    render(<AdminSummaryCards {...summaryProps} />);

    expect(
      await screen.findByText('요약을 불러오지 못했습니다'),
    ).toBeInTheDocument();
  });

  it('비용이 불완전하면 미해결 provider 호출 건수를 표시한다', async () => {
    mockedSummary.mockResolvedValue({
      month: '2026-07',
      total_cost: 4.43,
      workflow_execution_cost: 4.43,
      agent_builder_cost: 0,
      usage_data_complete: false,
      unresolved_provider_call_count: 1,
      budget: null,
    });

    render(<AdminSummaryCards {...summaryProps} />);

    expect(
      await screen.findByText('비용 미확정 provider 호출 1건'),
    ).toBeInTheDocument();
  });
});
