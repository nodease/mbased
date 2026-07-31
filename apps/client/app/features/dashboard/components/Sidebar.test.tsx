import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { AxiosError, type AxiosResponse } from 'axios';

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

const activeOrganizationMock = vi.hoisted(() => ({
  getStoredActiveOrganizationId: vi.fn(),
  setActiveOrganizationId: vi.fn(),
}));

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => routerMock,
}));

vi.mock('../../auth/api/authApi', () => ({
  authApi: {
    me: vi.fn(),
    logout: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    warning: vi.fn(),
  },
}));

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
  },
}));

vi.mock('@/lib/activeOrganization', () => ({
  ACTIVE_ORGANIZATION_CHANGED_EVENT: 'nodease-active-organization-changed',
  getStoredActiveOrganizationId:
    activeOrganizationMock.getStoredActiveOrganizationId,
  setActiveOrganizationId: activeOrganizationMock.setActiveOrganizationId,
}));

vi.mock('../../notifications/api/notificationsApi', () => ({
  NOTIFICATIONS_REFRESH_EVENT: 'nodease-notifications-refresh',
  notificationsApi: {
    listNotifications: vi.fn(),
    createEventSource: vi.fn(),
  },
}));

vi.mock('../../admin/api/adminApi', () => ({
  adminApi: {
    getSecurityAlertSummary: vi.fn(),
  },
}));

vi.mock('../../notifications/components/NotificationOverlay', () => ({
  NotificationOverlay: ({
    onClose,
    securityAlertSummary,
  }: {
    onClose: () => void;
    securityAlertSummary?: { open_count: number } | null;
  }) => (
    <div role="dialog" aria-label="알림">
      <span>보안 알림 {securityAlertSummary?.open_count ?? 0}개</span>
      <button onClick={onClose}>닫기</button>
    </div>
  ),
}));

import { authApi } from '../../auth/api/authApi';
import { apiClient } from '@/lib/apiClient';
import { notificationsApi } from '../../notifications/api/notificationsApi';
import type { ModuleOperationAppSummary } from '../../app/api/moduleOperationsApi';
import { adminApi } from '../../admin/api/adminApi';
import { toast } from 'sonner';
import Sidebar from './Sidebar';

type SidebarOrganizationFixture = {
  id: string;
  name: string;
  is_manager: boolean;
};

type SidebarDefaults = {
  currentOrganization?: SidebarOrganizationFixture;
  organizations?: SidebarOrganizationFixture[];
  operationRows?: Array<{ app: ModuleOperationAppSummary }>;
};

const managerOrganization: SidebarOrganizationFixture = {
  id: 'org-manager',
  name: 'Acme',
  is_manager: true,
};

const memberOrganization: SidebarOrganizationFixture = {
  id: 'org-member',
  name: 'Beta',
  is_manager: false,
};

const currentUserResponse: Awaited<ReturnType<typeof authApi.me>> = {
  user: {
    id: 'user-1',
    name: '홍길동',
    email: 'hong@example.com',
    emailVerified: true,
    role: 'user',
    isActive: true,
    createdAt: '2026-07-10T00:00:00Z',
    updatedAt: '2026-07-10T00:00:00Z',
  },
  session: {
    token: '[REDACTED]',
    expiresAt: '2026-07-11T00:00:00Z',
  },
};

const fakeEventSource = () => ({
  addEventListener: vi.fn(),
  close: vi.fn(),
});

const waitForEventListener = async (
  listeners: Map<string, EventListener>,
  eventName: string,
) => {
  await waitFor(() => {
    expect(listeners.has(eventName)).toBe(true);
  });
  return listeners.get(eventName) as EventListener;
};

const mockSidebarDefaults = ({
  currentOrganization = managerOrganization,
  organizations = [managerOrganization, memberOrganization],
  operationRows = [],
}: SidebarDefaults = {}) => {
  activeOrganizationMock.getStoredActiveOrganizationId.mockReturnValue(
    currentOrganization.id,
  );
  vi.mocked(authApi.me).mockResolvedValue(currentUserResponse);
  vi.mocked(notificationsApi.listNotifications).mockResolvedValue({ items: [] });
  vi.mocked(adminApi.getSecurityAlertSummary).mockResolvedValue({
    open_count: 2,
    high_open_count: 1,
    recent_items: [],
  });
  vi.mocked(notificationsApi.createEventSource).mockReturnValue(
    fakeEventSource() as unknown as EventSource,
  );
  vi.mocked(apiClient.get).mockImplementation((path) => {
    if (path === '/organizations/current') {
      return Promise.resolve({ data: currentOrganization });
    }
    if (path === '/organizations') {
      return Promise.resolve({ data: organizations });
    }
    if (path === '/apps/operations') {
      return Promise.resolve({ data: operationRows });
    }
    return Promise.reject(new Error(`Unexpected path: ${path}`));
  });
};

beforeEach(() => {
  routerMock.push.mockReset();
  activeOrganizationMock.getStoredActiveOrganizationId.mockReset();
  activeOrganizationMock.setActiveOrganizationId.mockReset();
  mockSidebarDefaults();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('Sidebar notifications', () => {
  it('프로필 드롭다운에 알림 item을 표시하고 overlay를 연다', async () => {
    render(<Sidebar />);

    fireEvent.click(await screen.findByText('홍길동'));
    fireEvent.click(screen.getByRole('button', { name: '알림' }));

    expect(screen.getByRole('dialog', { name: '알림' })).toBeInTheDocument();
  });

  it('열린 보안 알림이 있으면 프로필 아이콘에 빨간 점을 표시한다', async () => {
    render(<Sidebar />);

    const notificationText = await screen.findByText('확인할 알림 있음');
    const profileButton = notificationText.closest('button');
    const visualDot = notificationText.previousElementSibling;

    expect(profileButton).toHaveAccessibleName(/확인할 알림 있음/);
    expect(notificationText).toHaveClass('sr-only');
    expect(visualDot).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('dialog', { name: '알림' })).not.toBeInTheDocument();
  });

  it('일반 member에게 조직 초대가 있으면 프로필 아이콘에 빨간 점을 표시한다', async () => {
    mockSidebarDefaults({ currentOrganization: memberOrganization });
    vi.mocked(notificationsApi.listNotifications).mockResolvedValue({
      items: [
        {
          id: 'invitation-1',
          type: 'organization.invitation',
          organization_id: 'org-invited',
          organization_name: '초대 조직',
          organization_auth_state: 'member',
          created_at: '2026-07-18T12:00:00Z',
        },
      ],
    });

    render(<Sidebar />);

    expect(
      await screen.findByRole('button', { name: /확인할 알림 있음/ }),
    ).toBeInTheDocument();
    expect(adminApi.getSecurityAlertSummary).not.toHaveBeenCalled();
  });

  it('조직 초대와 열린 보안 알림이 모두 없으면 빨간 점을 숨긴다', async () => {
    mockSidebarDefaults({ currentOrganization: memberOrganization });

    render(<Sidebar />);

    await waitFor(() =>
      expect(notificationsApi.listNotifications).toHaveBeenCalledTimes(1),
    );
    expect(
      screen.queryByRole('button', { name: /확인할 알림 있음/ }),
    ).not.toBeInTheDocument();
  });

  it('같은 조직의 보안 알림 재조회가 진행 중이거나 실패해도 기존 알림 점을 유지한다', async () => {
    const listeners = new Map<string, EventListener>();
    let rejectRefresh: ((reason?: unknown) => void) | undefined;
    const refreshRequest = new Promise<
      Awaited<ReturnType<typeof adminApi.getSecurityAlertSummary>>
    >((_, reject) => {
      rejectRefresh = reject;
    });
    vi.mocked(notificationsApi.createEventSource).mockReturnValue({
      addEventListener: vi.fn((event: string, callback: EventListener) => {
        listeners.set(event, callback);
      }),
      close: vi.fn(),
    } as unknown as EventSource);
    vi.mocked(adminApi.getSecurityAlertSummary)
      .mockResolvedValueOnce({
        open_count: 2,
        high_open_count: 1,
        recent_items: [],
      })
      .mockReturnValueOnce(refreshRequest);

    render(<Sidebar />);

    expect(
      await screen.findByRole('button', { name: /확인할 알림 있음/ }),
    ).toBeInTheDocument();
    const notificationsChangedListener = await waitForEventListener(
      listeners,
      'notifications.changed',
    );
    act(() => {
      notificationsChangedListener(new Event('notifications.changed'));
    });
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(2),
    );

    expect(
      screen.getByRole('button', { name: /확인할 알림 있음/ }),
    ).toBeInTheDocument();

    await act(async () => {
      rejectRefresh?.(new Error('temporary failure'));
      await refreshRequest.catch(() => undefined);
    });

    expect(
      screen.getByRole('button', { name: /확인할 알림 있음/ }),
    ).toBeInTheDocument();
  });

  it('SSE notifications.changed 이벤트를 받으면 초대와 Security Alert summary를 재조회한다', async () => {
    const listeners = new Map<string, EventListener>();
    const source = {
      addEventListener: vi.fn((event: string, callback: EventListener) => {
        listeners.set(event, callback);
      }),
      close: vi.fn(),
    };
    vi.mocked(notificationsApi.createEventSource).mockReturnValue(
      source as unknown as EventSource,
    );

    render(<Sidebar />);

    await waitFor(() =>
      expect(notificationsApi.listNotifications).toHaveBeenCalledTimes(1),
    );
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(1),
    );
    const notificationsChangedListener = await waitForEventListener(
      listeners,
      'notifications.changed',
    );
    act(() => {
      notificationsChangedListener(
        new CustomEvent('notifications.changed', {
          detail: { open_count: 999, secret: 'payload-must-not-be-used' },
        }),
      );
    });

    await waitFor(() =>
      expect(notificationsApi.listNotifications).toHaveBeenCalledTimes(2),
    );
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(2),
    );
  });

  it('SSE 재연결 open 이벤트에서 영속 API를 다시 조회하고 열린 Admin 화면에 알린다', async () => {
    const listeners = new Map<string, EventListener>();
    const source = {
      addEventListener: vi.fn((event: string, callback: EventListener) => {
        listeners.set(event, callback);
      }),
      close: vi.fn(),
    };
    const adminRefreshListener = vi.fn();
    window.addEventListener(
      'nodease-notifications-refresh',
      adminRefreshListener,
    );
    vi.mocked(notificationsApi.createEventSource).mockReturnValue(
      source as unknown as EventSource,
    );

    render(<Sidebar />);

    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(1),
    );
    const openListener = await waitForEventListener(listeners, 'open');
    act(() => {
      openListener(new Event('open'));
    });

    await waitFor(() =>
      expect(notificationsApi.listNotifications).toHaveBeenCalledTimes(2),
    );
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(2),
    );
    expect(adminRefreshListener).toHaveBeenCalledTimes(1);
    window.removeEventListener(
      'nodease-notifications-refresh',
      adminRefreshListener,
    );
  });

  it('manager는 Security Alert summary를 조회하고 open badge를 표시한다', async () => {
    render(<Sidebar />);

    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(1),
    );
    fireEvent.click(await screen.findByText('홍길동'));
    expect(
      screen.getByLabelText('미확인 보안 알림 2개'),
    ).toBeInTheDocument();
  });

  it('일반 member는 Security Alert summary를 요청하거나 badge를 보지 않는다', async () => {
    mockSidebarDefaults({ currentOrganization: memberOrganization });

    render(<Sidebar />);

    await screen.findByText('Beta');
    expect(adminApi.getSecurityAlertSummary).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('홍길동'));
    expect(
      screen.queryByLabelText(/미확인 보안 알림/),
    ).not.toBeInTheDocument();
  });

  it('SSE 재조회에서 403이면 manager UI와 이전 badge를 제거한다', async () => {
    const listeners = new Map<string, EventListener>();
    vi.mocked(notificationsApi.createEventSource).mockReturnValue({
      addEventListener: vi.fn((event: string, callback: EventListener) => {
        listeners.set(event, callback);
      }),
      close: vi.fn(),
    } as unknown as EventSource);
    const forbidden = new AxiosError('Forbidden');
    forbidden.response = { status: 403 } as AxiosResponse;
    vi.mocked(adminApi.getSecurityAlertSummary)
      .mockResolvedValueOnce({
        open_count: 2,
        high_open_count: 1,
        recent_items: [],
      })
      .mockRejectedValueOnce(forbidden);

    render(<Sidebar />);

    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(1),
    );
    expect(await screen.findByRole('link', { name: '관리' })).toBeInTheDocument();
    const notificationsChangedListener = await waitForEventListener(
      listeners,
      'notifications.changed',
    );
    act(() => {
      notificationsChangedListener(new Event('notifications.changed'));
    });

    await waitFor(() =>
      expect(screen.queryByRole('link', { name: '관리' })).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('홍길동'));
    expect(
      screen.queryByLabelText(/미확인 보안 알림/),
    ).not.toBeInTheDocument();
  });

  it('같은 alert occurrence SSE toast는 client cooldown 안에 한 번만 표시한다', async () => {
    const listeners = new Map<string, EventListener>();
    vi.mocked(notificationsApi.createEventSource).mockReturnValue({
      addEventListener: vi.fn((event: string, callback: EventListener) => {
        listeners.set(event, callback);
      }),
      close: vi.fn(),
    } as unknown as EventSource);
    const summaryItem = {
      id: 'alert-1',
      rule_id: 'repeated_permission_denied' as const,
      severity: 'medium' as const,
      actor: {
        id: 'user-1',
        display_name: '김사용자',
        state: 'active' as const,
      },
      occurrence_count: 5,
      last_detected_at: '2026-07-13T00:05:00Z',
    };
    vi.mocked(adminApi.getSecurityAlertSummary)
      .mockResolvedValueOnce({
        open_count: 1,
        high_open_count: 0,
        recent_items: [summaryItem],
      })
      .mockResolvedValueOnce({
        open_count: 1,
        high_open_count: 0,
        recent_items: [{ ...summaryItem, occurrence_count: 6 }],
      })
      .mockResolvedValueOnce({
        open_count: 1,
        high_open_count: 0,
        recent_items: [{ ...summaryItem, occurrence_count: 7 }],
      });

    render(<Sidebar />);
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(1),
    );
    const notificationsChangedListener = await waitForEventListener(
      listeners,
      'notifications.changed',
    );

    act(() => {
      notificationsChangedListener(new Event('notifications.changed'));
    });
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(2),
    );
    expect(toast.warning).toHaveBeenCalledWith('새 보안 알림이 있습니다.', {
      classNames: { icon: 'text-red-600' },
    });

    act(() => {
      notificationsChangedListener(new Event('notifications.changed'));
    });
    await waitFor(() =>
      expect(adminApi.getSecurityAlertSummary).toHaveBeenCalledTimes(3),
    );
    expect(toast.warning).toHaveBeenCalledTimes(1);
  });
});

describe('Sidebar organization switcher', () => {
  it('펼친 사이드바는 232px 너비를 사용한다', async () => {
    render(<Sidebar />);

    await screen.findByRole('button', { name: 'Nodease' });

    expect(screen.getByRole('complementary')).toHaveClass('w-[232px]');
  });

  it('펼친 사이드바의 Nodease 옆에 장식 아이콘을 표시하지 않는다', async () => {
    render(<Sidebar />);

    const nodeaseButton = await screen.findByRole('button', {
      name: 'Nodease',
    });
    const brandHeader = nodeaseButton.parentElement?.parentElement;

    expect(brandHeader).not.toBeNull();
    expect(brandHeader?.querySelector('svg')).not.toBeInTheDocument();
  });

  it('organization manager는 워크플로우 목록 메뉴를 볼 수 있다', async () => {
    render(<Sidebar />);

    const workflowLink = await screen.findByRole('link', {
      name: '워크플로우 목록',
    });

    expect(workflowLink).toHaveAttribute('href', '/dashboard/mymodule');
    expect(workflowLink.querySelector('svg')).toHaveClass('lucide-workflow');
  });

  it('운영 가능한 row가 없는 일반 멤버에게 워크플로우 목록 메뉴를 숨긴다', async () => {
    mockSidebarDefaults({ currentOrganization: memberOrganization });

    render(<Sidebar />);

    await screen.findByText('Beta');
    await waitFor(() => {
      expect(
        screen.queryByRole('link', { name: '워크플로우 목록' }),
      ).not.toBeInTheDocument();
    });
  });

  it('운영 가능한 row가 있는 일반 멤버에게 워크플로우 목록 메뉴를 표시한다', async () => {
    mockSidebarDefaults({
      currentOrganization: memberOrganization,
      operationRows: [
        {
          app: {
            id: 'app-1',
            name: '작성자 모듈',
            created_at: '2026-07-10T00:00:00Z',
            updated_at: '2026-07-10T00:00:00Z',
          },
        },
      ],
    });

    render(<Sidebar />);

    expect(
      await screen.findByRole('link', { name: '워크플로우 목록' }),
    ).toHaveAttribute('href', '/dashboard/mymodule');
  });

  it('현재 organization 이름과 구분 badge를 표시한다', async () => {
    render(<Sidebar />);

    expect(await screen.findByText('Acme')).toBeInTheDocument();
    expect(screen.getAllByText('내 조직').length).toBeGreaterThan(0);
  });

  it('organization이 2개 이상이면 dropdown에서 내 조직과 멤버 조직을 구분한다', async () => {
    render(<Sidebar />);

    fireEvent.click(await screen.findByRole('button', { name: '조직 전환' }));

    expect(
      screen.getByRole('menuitem', { name: 'Acme 조직 선택' }),
    ).toHaveAttribute('aria-current', 'true');
    expect(
      screen.getByRole('menuitem', { name: 'Beta 조직 선택' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('내 조직').length).toBeGreaterThan(0);
    expect(screen.getByText('멤버 조직')).toBeInTheDocument();
  });

  it('다른 organization 선택 시 active organization을 저장하고 dashboard로 이동한다', async () => {
    render(<Sidebar />);

    fireEvent.click(await screen.findByRole('button', { name: '조직 전환' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Beta 조직 선택' }));

    expect(activeOrganizationMock.setActiveOrganizationId).toHaveBeenCalledWith(
      'org-member',
    );
    expect(routerMock.push).toHaveBeenCalledWith('/dashboard');
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('멤버 조직')).toBeInTheDocument();
  });

  it('현재 organization을 다시 선택하면 저장하지 않고 dropdown만 닫는다', async () => {
    render(<Sidebar />);

    await screen.findByText('Acme');
    activeOrganizationMock.setActiveOrganizationId.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '조직 전환' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Acme 조직 선택' }));

    expect(activeOrganizationMock.setActiveOrganizationId).not.toHaveBeenCalled();
    expect(routerMock.push).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('menuitem', { name: 'Beta 조직 선택' }),
    ).not.toBeInTheDocument();
  });

  it('organization이 1개뿐이면 switcher dropdown을 열지 않는다', async () => {
    mockSidebarDefaults({ organizations: [managerOrganization] });

    render(<Sidebar />);

    const switcher = await screen.findByRole('button', { name: '조직 전환' });
    expect(switcher).toBeDisabled();
    fireEvent.click(switcher);
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
  });
});
