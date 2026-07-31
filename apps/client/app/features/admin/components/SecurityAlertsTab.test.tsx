import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    listSecurityAlerts: vi.fn(),
  },
}));

vi.mock('./SecurityAlertDetailDrawer', () => ({
  SecurityAlertDetailDrawer: ({
    onManageActor,
  }: {
    onManageActor?: (actorId: string) => void;
  }) => (
    <div role="dialog" aria-label="보안 알림 상세">
      <button onClick={() => onManageActor?.('user-1')}>사용자 접근 관리</button>
    </div>
  ),
}));

vi.mock('./ActorAccessDrawer', () => ({
  ActorAccessDrawer: ({
    onClose,
    returnLabel,
  }: {
    onClose: () => void;
    returnLabel?: string;
  }) => (
    <div role="dialog" aria-label="행위자 접근 관리">
      <button onClick={onClose}>{returnLabel || '닫기'}</button>
    </div>
  ),
}));

import { adminApi } from '../api/adminApi';
import { SecurityAlertsTab } from './SecurityAlertsTab';
import type { OrganizationMember } from '../../organization/types/Organization';

const mockedList = vi.mocked(adminApi.listSecurityAlerts);

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const members = [
  {
    id: 'membership-1',
    user_id: 'user-1',
    user_name: '김사용자',
    user_email: 'secret@example.com',
    membership_state: 'active',
    organization_auth_state: 'member',
  },
] as unknown as OrganizationMember[];

const alertItem = {
  id: '123e4567-e89b-42d3-a456-426614174000',
  organization_id: 'org-1',
  rule_id: 'repeated_permission_denied' as const,
  rule_version: 'v1',
  severity: 'medium' as const,
  status: 'open' as const,
  policy_reason: null,
  actor: { id: 'user-1', display_name: '김사용자', state: 'active' as const },
  occurrence_count: 5,
  first_detected_at: '2026-07-13T00:00:00Z',
  last_detected_at: '2026-07-13T00:05:00Z',
  version: 1,
  created_at: '2026-07-13T00:05:00Z',
  updated_at: '2026-07-13T00:05:00Z',
};

const forbiddenError = () => {
  const error = new AxiosError('Forbidden');
  error.response = { status: 403 } as AxiosResponse;
  return error;
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('SecurityAlertsTab', () => {
  it('위험 신호 안내와 기본 목록을 사용자용 라벨로 표시한다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [alertItem] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );

    const table = await screen.findByRole('table');
    expect(within(table).getByText('반복된 권한 거부')).toBeInTheDocument();
    expect(within(table).getByText('보통')).toBeInTheDocument();
    expect(within(table).getByText('미확인')).toBeInTheDocument();
    expect(within(table).getByText('김사용자')).toBeInTheDocument();
    expect(within(table).getByText('5')).toBeInTheDocument();
    expect(screen.queryByText('secret@example.com')).not.toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledWith({ page: 1, limit: 20 });
  });

  it('필터 입력은 조회 전까지 적용하지 않고 조회 시 1페이지부터 요청한다', async () => {
    mockedList.mockResolvedValue({ total: 0, items: [] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    await screen.findByText('탐지된 보안 알림이 없습니다.');

    fireEvent.change(screen.getByLabelText('심각도'), {
      target: { value: 'high' },
    });
    fireEvent.change(screen.getByLabelText('상태'), {
      target: { value: 'acknowledged' },
    });
    fireEvent.change(screen.getByLabelText('탐지 규칙'), {
      target: { value: 'repeated_policy_block' },
    });
    fireEvent.change(screen.getByLabelText('사용자'), {
      target: { value: 'user-1' },
    });

    expect(mockedList).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '조회' }));

    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({
        severity: 'high',
        status: 'acknowledged',
        ruleId: 'repeated_policy_block',
        actorId: 'user-1',
        page: 1,
        limit: 20,
      }),
    );
    expect(
      await screen.findByText('조건에 맞는 보안 알림이 없습니다.'),
    ).toBeInTheDocument();
  });

  it('종료 시각이 시작 시각보다 늦지 않으면 API를 호출하지 않는다', async () => {
    mockedList.mockResolvedValue({ total: 0, items: [] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    await screen.findByText('탐지된 보안 알림이 없습니다.');

    fireEvent.change(screen.getByLabelText('시작 시각'), {
      target: { value: '2026-07-13T10:00' },
    });
    fireEvent.change(screen.getByLabelText('종료 시각'), {
      target: { value: '2026-07-13T10:00' },
    });
    fireEvent.click(screen.getByRole('button', { name: '조회' }));

    expect(
      await screen.findByText('종료 시각은 시작 시각보다 늦어야 합니다.'),
    ).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledTimes(1);
  });

  it('다음 페이지에서도 적용된 필터를 유지한다', async () => {
    mockedList.mockResolvedValue({ total: 21, items: [alertItem] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    await screen.findByRole('table');

    fireEvent.change(screen.getByLabelText('상태'), {
      target: { value: 'open' },
    });
    fireEvent.click(screen.getByRole('button', { name: '조회' }));
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
    await screen.findByRole('table');

    fireEvent.click(screen.getByRole('button', { name: '다음' }));

    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({
        status: 'open',
        page: 2,
        limit: 20,
      }),
    );
  });

  it('notification refresh 신호에서 적용된 필터와 페이지를 유지해 재조회한다', async () => {
    mockedList.mockResolvedValue({ total: 21, items: [alertItem] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    await screen.findByRole('table');

    fireEvent.change(screen.getByLabelText('상태'), {
      target: { value: 'open' },
    });
    fireEvent.click(screen.getByRole('button', { name: '조회' }));
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
    await screen.findByRole('table');
    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(3));

    act(() => {
      window.dispatchEvent(new Event('nodease-notifications-refresh'));
    });

    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(4));
    expect(mockedList).toHaveBeenLastCalledWith({
      status: 'open',
      page: 2,
      limit: 20,
    });
  });

  it('notification background refresh 중 기존 목록을 유지한다', async () => {
    const refreshRequest = deferred<{
      total: number;
      items: typeof alertItem[];
    }>();
    mockedList
      .mockResolvedValueOnce({ total: 1, items: [alertItem] })
      .mockReturnValueOnce(refreshRequest.promise);

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    expect(await screen.findByText('5')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event('nodease-notifications-refresh'));
    });
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));

    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(
      screen.queryByText('보안 알림을 불러오는 중...'),
    ).not.toBeInTheDocument();

    refreshRequest.resolve({
      total: 1,
      items: [{ ...alertItem, occurrence_count: 6 }],
    });
    expect(await screen.findByText('6')).toBeInTheDocument();
  });

  it('notification background refresh 실패 시 기존 목록을 유지한다', async () => {
    mockedList
      .mockResolvedValueOnce({ total: 1, items: [alertItem] })
      .mockRejectedValueOnce(new Error('temporary network failure'));

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    expect(await screen.findByText('5')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event('nodease-notifications-refresh'));
    });
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));

    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(
      screen.queryByText('보안 알림을 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();
  });

  it('조직 전환 전에 시작한 늦은 응답은 현재 조직 목록을 덮어쓰지 않는다', async () => {
    const oldOrganizationRequest = deferred<{
      total: number;
      items: typeof alertItem[];
    }>();
    const currentOrganizationRequest = deferred<{
      total: number;
      items: typeof alertItem[];
    }>();
    const currentOrganizationAlert = {
      ...alertItem,
      id: '223e4567-e89b-42d3-a456-426614174000',
      organization_id: 'org-2',
      occurrence_count: 9,
    };
    mockedList
      .mockReturnValueOnce(oldOrganizationRequest.promise)
      .mockReturnValueOnce(currentOrganizationRequest.promise);

    const { rerender } = render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));

    rerender(
      <SecurityAlertsTab members={members} organizationId="org-2" />,
    );
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));

    currentOrganizationRequest.resolve({
      total: 1,
      items: [currentOrganizationAlert],
    });
    expect(await screen.findByText('9')).toBeInTheDocument();

    oldOrganizationRequest.resolve({ total: 1, items: [alertItem] });
    await waitFor(() => expect(screen.getByText('9')).toBeInTheDocument());
    expect(screen.queryByText('5')).not.toBeInTheDocument();
  });

  it('403이면 cached 목록을 지우고 관리 권한 안내를 표시한다', async () => {
    mockedList
      .mockResolvedValueOnce({ total: 1, items: [alertItem] })
      .mockRejectedValueOnce(forbiddenError());

    const onCloseAlert = vi.fn();
    render(
      <SecurityAlertsTab
        members={members}
        organizationId="org-1"
        onCloseAlert={onCloseAlert}
      />,
    );
    await screen.findByRole('table');
    fireEvent.click(screen.getByRole('button', { name: '조회' }));

    expect(
      await screen.findByText('보안 알림 관리 권한이 없습니다.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(onCloseAlert).toHaveBeenCalledTimes(1);
  });

  it('사용자 접근 관리에서 닫으면 기존 보안 알림 상세로 돌아간다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [alertItem] });
    const onCloseAlert = vi.fn();

    render(
      <SecurityAlertsTab
        members={members}
        organizationId="org-1"
        selectedAlertId={alertItem.id}
        onCloseAlert={onCloseAlert}
      />,
    );

    expect(
      await screen.findByRole('dialog', { name: '보안 알림 상세' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '사용자 접근 관리' }));

    expect(
      screen.queryByRole('dialog', { name: '보안 알림 상세' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('dialog', { name: '행위자 접근 관리' }),
    ).toBeInTheDocument();
    expect(onCloseAlert).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole('button', { name: '보안 알림 상세로 돌아가기' }),
    );

    expect(
      await screen.findByRole('dialog', { name: '보안 알림 상세' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('dialog', { name: '행위자 접근 관리' }),
    ).not.toBeInTheDocument();
    expect(onCloseAlert).not.toHaveBeenCalled();
  });

  it('재시도 가능한 오류에서 다시 시도할 수 있다', async () => {
    mockedList
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ total: 0, items: [] });

    render(
      <SecurityAlertsTab members={members} organizationId="org-1" />,
    );

    expect(
      await screen.findByText('보안 알림을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));

    expect(
      await screen.findByText('탐지된 보안 알림이 없습니다.'),
    ).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledTimes(2);
  });
});
