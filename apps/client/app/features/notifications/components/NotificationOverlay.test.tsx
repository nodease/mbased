import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../organization/api/organizationApi', () => ({
  organizationApi: {
    acceptInvitation: vi.fn(),
    declineInvitation: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { organizationApi } from '../../organization/api/organizationApi';
import { NotificationOverlay } from './NotificationOverlay';

const notification = {
  id: 'organization_invitation:membership-1',
  type: 'organization.invitation' as const,
  organization_id: 'org-1',
  organization_name: 'Acme',
  organization_auth_state: 'member' as const,
  created_at: '2026-07-06T10:00:00.000Z',
};

const securityAlertSummary = {
  open_count: 2,
  high_open_count: 1,
  recent_items: [
    {
      id: 'alert-1',
      rule_id: 'repeated_permission_denied' as const,
      severity: 'medium' as const,
      actor: {
        id: 'user-1',
        display_name: '김사용자',
        state: 'active' as const,
      },
      occurrence_count: 5,
      last_detected_at: '2026-07-13T10:00:00.000Z',
    },
  ],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('NotificationOverlay', () => {
  it('organization 초대 알림과 수락/거절 버튼을 표시한다', () => {
    render(
      <NotificationOverlay
        notifications={[notification]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog', { name: '알림' })).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
    expect(screen.getByText('조직 멤버 초대')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /수락/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /거절/ })).toBeInTheDocument();
  });

  it('수락 클릭 시 accept API를 호출하고 목록을 갱신한다', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(organizationApi.acceptInvitation).mockResolvedValue(
      {} as Awaited<ReturnType<typeof organizationApi.acceptInvitation>>,
    );

    render(
      <NotificationOverlay
        notifications={[notification]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /수락/ }));

    await waitFor(() =>
      expect(organizationApi.acceptInvitation).toHaveBeenCalledWith('org-1'),
    );
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it('거절 클릭 시 decline API를 호출하고 목록을 갱신한다', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(organizationApi.declineInvitation).mockResolvedValue(
      {} as Awaited<ReturnType<typeof organizationApi.declineInvitation>>,
    );

    render(
      <NotificationOverlay
        notifications={[notification]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /거절/ }));

    await waitFor(() =>
      expect(organizationApi.declineInvitation).toHaveBeenCalledWith('org-1'),
    );
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it('알림이 없으면 empty state를 표시한다', () => {
    render(
      <NotificationOverlay
        notifications={[]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText('새 조직 초대가 없습니다.')).toBeInTheDocument();
  });

  it('manager에게 보안 알림과 조직 초대를 서로 다른 section으로 표시한다', () => {
    render(
      <NotificationOverlay
        notifications={[notification]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        showSecurityAlerts
        securityAlertSummary={securityAlertSummary}
        securityAlertsLoading={false}
        securityAlertsError={null}
        onRefreshSecurityAlerts={vi.fn()}
        onSelectSecurityAlert={vi.fn()}
        onViewAllSecurityAlerts={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('region', { name: '보안 알림' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: '조직 초대' }),
    ).toBeInTheDocument();
    expect(screen.getByText('반복된 권한 거부')).toBeInTheDocument();
    expect(screen.getByText('김사용자 · 5회')).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '확인' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '해결' })).not.toBeInTheDocument();
  });

  it('보안 알림 item과 모두 보기는 deep link callback을 호출한다', () => {
    const onClose = vi.fn();
    const onSelectSecurityAlert = vi.fn();
    const onViewAllSecurityAlerts = vi.fn();
    render(
      <NotificationOverlay
        notifications={[]}
        loading={false}
        error={null}
        onClose={onClose}
        onRefresh={vi.fn()}
        showSecurityAlerts
        securityAlertSummary={securityAlertSummary}
        securityAlertsLoading={false}
        securityAlertsError={null}
        onRefreshSecurityAlerts={vi.fn()}
        onSelectSecurityAlert={onSelectSecurityAlert}
        onViewAllSecurityAlerts={onViewAllSecurityAlerts}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /반복된 권한 거부/ }));
    expect(onClose).toHaveBeenCalled();
    expect(onSelectSecurityAlert).toHaveBeenCalledWith('alert-1');

    fireEvent.click(screen.getByRole('button', { name: '보안 알림 모두 보기' }));
    expect(onViewAllSecurityAlerts).toHaveBeenCalled();
  });

  it('보안 알림 조회 실패가 정상적인 조직 초대를 숨기지 않는다', () => {
    render(
      <NotificationOverlay
        notifications={[notification]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        showSecurityAlerts
        securityAlertSummary={null}
        securityAlertsLoading={false}
        securityAlertsError="보안 알림을 불러오지 못했습니다."
        onRefreshSecurityAlerts={vi.fn()}
        onSelectSecurityAlert={vi.fn()}
        onViewAllSecurityAlerts={vi.fn()}
      />,
    );

    expect(screen.getByText('보안 알림을 불러오지 못했습니다.')).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /수락/ })).toBeInTheDocument();
  });

  it('일반 member에게 보안 알림 section을 표시하지 않는다', () => {
    render(
      <NotificationOverlay
        notifications={[]}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        showSecurityAlerts={false}
        securityAlertSummary={securityAlertSummary}
        securityAlertsLoading={false}
        securityAlertsError={null}
        onRefreshSecurityAlerts={vi.fn()}
        onSelectSecurityAlert={vi.fn()}
        onViewAllSecurityAlerts={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('region', { name: '보안 알림' }),
    ).not.toBeInTheDocument();
  });
});
