import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    listWorkflowUsage: vi.fn(),
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

import { toast } from 'sonner';
import { adminApi } from '../api/adminApi';
import { UsageTab } from './UsageTab';

const mockedList = vi.mocked(adminApi.listWorkflowUsage);

const usageResponse = {
  total: 2,
  period: {
    startAt: '2026-07-01T00:00:00+09:00',
    endAt: '2026-08-01T00:00:00+09:00',
  },
  usage_data_complete: true,
  unresolved_provider_call_count: 0,
  items: [
    {
      workflow_id: 'wf-1',
      workflow_name: '비싼 워크플로우',
      prompt_tokens: 12345,
      completion_tokens: 2345,
      call_count: 87,
      total_cost: 12.345678,
      workflow_execution_cost: 10,
      agent_builder_cost: 2.345678,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
    },
    {
      workflow_id: 'wf-2',
      workflow_name: '저렴한 워크플로우',
      prompt_tokens: 10,
      completion_tokens: 5,
      call_count: 2,
      total_cost: 0.123456,
      workflow_execution_cost: 0.1,
      agent_builder_cost: 0.023456,
      usage_data_complete: true,
      unresolved_provider_call_count: 0,
    },
  ],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('UsageTab', () => {
  it('workflow별 사용량을 비용 2자리 반올림과 이동 링크로 렌더링한다', async () => {
    mockedList.mockResolvedValue(usageResponse);

    render(<UsageTab />);

    expect(await screen.findByText('비싼 워크플로우')).toBeInTheDocument();
    // 원본 정밀도(12.345678)는 표시 직전 1회만 2자리로 반올림한다.
    expect(screen.getByText('$12.35')).toBeInTheDocument();
    expect(screen.getByText('$0.12')).toBeInTheDocument();
    expect(screen.getByText('12,345')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /workflow로 이동/ });
    expect(links[0]).toHaveAttribute('href', '/modules/wf-1');
    // 기간 미지정 기본 조회: 기간 파라미터 없이 호출한다 (이번 달 KST는 서버 기본값).
    expect(mockedList).toHaveBeenCalledWith({
      startAt: undefined,
      endAt: undefined,
      page: 1,
      limit: 20,
    });
    // 응답의 적용 기간을 표시한다.
    expect(screen.getByText(/적용 기간:/)).toBeInTheDocument();
  });

  it('기간을 한쪽만 입력하고 조회하면 안내 toast를 띄우고 호출하지 않는다', async () => {
    mockedList.mockResolvedValue({ ...usageResponse, total: 0, items: [] });

    render(<UsageTab />);
    await screen.findByText('표시할 워크플로우가 없습니다.');
    mockedList.mockClear();

    fireEvent.change(screen.getByLabelText('기간 시작'), {
      target: { value: '2026-07-01T00:00' },
    });
    fireEvent.click(screen.getByRole('button', { name: /조회/ }));

    expect(toast.error).toHaveBeenCalledWith(
      '기간은 시작과 끝을 함께 입력하거나 모두 비워야 합니다.',
    );
    expect(mockedList).not.toHaveBeenCalled();
  });

  it('미해결 provider 호출이 있으면 확정 비용이 아님을 경고한다', async () => {
    mockedList.mockResolvedValue({
      ...usageResponse,
      usage_data_complete: false,
      unresolved_provider_call_count: 2,
    });

    render(<UsageTab />);

    expect(
      await screen.findByText(
        '비용 미확정 provider 호출 2건이 있어 합계가 변경될 수 있습니다.',
      ),
    ).toBeInTheDocument();
  });

  it('기간을 모두 입력하면 해당 기간으로 1페이지부터 재조회한다', async () => {
    mockedList.mockResolvedValue({ ...usageResponse, total: 0, items: [] });

    render(<UsageTab />);
    await screen.findByText('표시할 워크플로우가 없습니다.');

    fireEvent.change(screen.getByLabelText('기간 시작'), {
      target: { value: '2026-07-01T00:00' },
    });
    fireEvent.change(screen.getByLabelText('기간 끝'), {
      target: { value: '2026-07-15T00:00' },
    });
    fireEvent.click(screen.getByRole('button', { name: /조회/ }));

    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({
        startAt: '2026-07-01T00:00',
        endAt: '2026-07-15T00:00',
        page: 1,
        limit: 20,
      }),
    );
  });

  it('403 응답이면 접근 권한 안내 문구를 표시한다', async () => {
    const error = new AxiosError('Forbidden');
    error.response = { status: 403 } as AxiosResponse;
    mockedList.mockRejectedValue(error);

    render(<UsageTab />);

    expect(
      await screen.findByText(/비용 조회 권한이 없습니다/),
    ).toBeInTheDocument();
  });
});
