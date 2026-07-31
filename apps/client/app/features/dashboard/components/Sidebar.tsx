'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useState, useEffect, useRef } from 'react';
import { isAxiosError } from 'axios';
import { toast } from 'sonner';
import { authApi } from '../../auth/api/authApi';
import {
  Bell,
  Search,
  Settings,
  BookOpen,
  BarChart3,
  Workflow,
  Home,
  LogOut,
  Menu,
  Building2,
  LayoutDashboard,
  ShieldCheck,
  ChevronDown,
  Check,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  ACTIVE_ORGANIZATION_CHANGED_EVENT,
  getStoredActiveOrganizationId,
  setActiveOrganizationId,
} from '@/lib/activeOrganization';
import { apiClient } from '@/lib/apiClient';
import {
  NOTIFICATIONS_REFRESH_EVENT,
  notificationsApi,
} from '../../notifications/api/notificationsApi';
import { NotificationOverlay } from '../../notifications/components/NotificationOverlay';
import type { NotificationItem } from '../../notifications/types/Notification';
import { adminApi } from '../../admin/api/adminApi';
import type { SecurityAlertSummaryResponse } from '../../admin/types/SecurityAlert';

const navigationItems = [
  {
    name: '홈',
    href: '/dashboard',
    icon: Home,
  },
  {
    name: '워크플로우 목록',
    href: '/dashboard/mymodule',
    icon: Workflow,
    operationsOnly: true,
  },
  {
    name: '마켓플레이스',
    href: '/dashboard/explore',
    icon: Search,
  },
  {
    name: '통계',
    href: '/dashboard/statistics',
    icon: BarChart3,
  },
  {
    name: '지식 관리',
    href: '/dashboard/knowledge',
    icon: BookOpen,
  },
  {
    name: '관리',
    href: '/dashboard/admin',
    icon: ShieldCheck,
    managerOnly: true,
  },
  {
    name: '설정',
    href: '/dashboard/settings',
    icon: Settings,
  },
];

const SECURITY_ALERT_TOAST_COOLDOWN_MS = 60_000;

type SidebarOrganization = {
  id: string;
  name: string;
  is_manager: boolean;
};

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [userName, setUserName] = useState('사용자');
  const [userEmail, setUserEmail] = useState('');
  const [activeOrganizationId, setActiveOrganizationIdState] = useState<
    string | null
  >(null);
  const [organizationName, setOrganizationName] = useState('');
  const [isOrganizationManager, setIsOrganizationManager] = useState(false);
  const [canSeeOperationsNav, setCanSeeOperationsNav] = useState(false);
  const [organizations, setOrganizations] = useState<SidebarOrganization[]>([]);
  const [isOrganizationDropdownOpen, setIsOrganizationDropdownOpen] =
    useState(false);
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(true);
  const [notificationsError, setNotificationsError] = useState<string | null>(
    null,
  );
  const [securityAlertSummary, setSecurityAlertSummary] =
    useState<SecurityAlertSummaryResponse | null>(null);
  const [securityAlertsLoading, setSecurityAlertsLoading] = useState(false);
  const [securityAlertsError, setSecurityAlertsError] = useState<string | null>(
    null,
  );
  const securityAlertLoadSequenceRef = useRef(0);
  const securityAlertSnapshotRef = useRef<Map<string, number>>(new Map());
  const securityAlertSnapshotInitializedRef = useRef(false);
  const securityAlertToastAtRef = useRef<Map<string, number>>(new Map());
  const dropdownRef = useRef<HTMLDivElement>(null);
  const organizationDropdownRef = useRef<HTMLDivElement>(null);

  const loadNotifications = useCallback(async () => {
    setNotificationsLoading(true);
    setNotificationsError(null);
    try {
      const data = await notificationsApi.listNotifications();
      setNotifications(data.items);
    } catch {
      setNotifications([]);
      setNotificationsError('알림을 불러오지 못했습니다.');
    } finally {
      setNotificationsLoading(false);
    }
  }, []);

  const clearSecurityAlertSummary = useCallback(() => {
    securityAlertLoadSequenceRef.current += 1;
    setSecurityAlertSummary(null);
    setSecurityAlertsLoading(false);
    setSecurityAlertsError(null);
    securityAlertSnapshotRef.current = new Map();
    securityAlertSnapshotInitializedRef.current = false;
    securityAlertToastAtRef.current = new Map();
  }, []);

  const loadSecurityAlertSummary = useCallback(async (notify = false) => {
    const sequence = ++securityAlertLoadSequenceRef.current;
    setSecurityAlertsLoading(true);
    setSecurityAlertsError(null);
    try {
      const data = await adminApi.getSecurityAlertSummary();
      if (sequence === securityAlertLoadSequenceRef.current) {
        const previousSnapshot = securityAlertSnapshotRef.current;
        if (notify && securityAlertSnapshotInitializedRef.current) {
          const now = Date.now();
          let shouldNotify = false;
          for (const item of data.recent_items) {
            const previousCount = previousSnapshot.get(item.id);
            const changed =
              previousCount === undefined ||
              item.occurrence_count > previousCount;
            const lastToastAt = securityAlertToastAtRef.current.get(item.id);
            if (
              changed &&
              (lastToastAt === undefined ||
                now - lastToastAt >= SECURITY_ALERT_TOAST_COOLDOWN_MS)
            ) {
              securityAlertToastAtRef.current.set(item.id, now);
              shouldNotify = true;
            }
          }
          for (const [alertId, lastToastAt] of securityAlertToastAtRef.current) {
            if (now - lastToastAt >= SECURITY_ALERT_TOAST_COOLDOWN_MS) {
              securityAlertToastAtRef.current.delete(alertId);
            }
          }
          if (shouldNotify) {
            toast.warning('새 보안 알림이 있습니다.', {
              classNames: { icon: 'text-red-600' },
            });
          }
        }
        securityAlertSnapshotRef.current = new Map(
          data.recent_items.map((item) => [item.id, item.occurrence_count]),
        );
        securityAlertSnapshotInitializedRef.current = true;
        setSecurityAlertSummary(data);
      }
    } catch (loadError) {
      if (sequence === securityAlertLoadSequenceRef.current) {
        setSecurityAlertsError('보안 알림을 불러오지 못했습니다.');
        if (isAxiosError(loadError) && loadError.response?.status === 403) {
          setSecurityAlertSummary(null);
          securityAlertSnapshotRef.current = new Map();
          securityAlertSnapshotInitializedRef.current = false;
          securityAlertToastAtRef.current = new Map();
          setIsOrganizationManager(false);
        }
      }
    } finally {
      if (sequence === securityAlertLoadSequenceRef.current) {
        setSecurityAlertsLoading(false);
      }
    }
  }, []);

  // Fetch user info
  useEffect(() => {
    const fetchUserInfo = async () => {
      try {
        const userInfo = await authApi.me();
        if (userInfo.user?.name) {
          setUserName(userInfo.user.name);
        }
        if (userInfo.user?.email) {
          setUserEmail(userInfo.user.email);
        }
      } catch {
        // Silent error handling
      }
    };

    fetchUserInfo();
  }, []);

  useEffect(() => {
    loadNotifications();
  }, [loadNotifications]);

  const refreshNotificationsFromSource = useCallback((notify: boolean) => {
    loadNotifications();
    if (isOrganizationManager) {
      loadSecurityAlertSummary(notify);
    }
    window.dispatchEvent(new Event(NOTIFICATIONS_REFRESH_EVENT));
  }, [isOrganizationManager, loadNotifications, loadSecurityAlertSummary]);

  const handleNotificationsChanged = useCallback(() => {
    refreshNotificationsFromSource(true);
  }, [refreshNotificationsFromSource]);

  const handleNotificationsOpen = useCallback(() => {
    refreshNotificationsFromSource(false);
  }, [refreshNotificationsFromSource]);

  useEffect(() => {
    let eventSource: EventSource | null = null;
    try {
      eventSource = notificationsApi.createEventSource();
      eventSource.addEventListener(
        'notifications.changed',
        handleNotificationsChanged,
      );
      eventSource.addEventListener('open', handleNotificationsOpen);
    } catch {
      // SSE 연결 실패는 초기/수동 조회로 보완한다.
    }

    return () => {
      eventSource?.close();
    };
  }, [handleNotificationsChanged, handleNotificationsOpen]);

  useEffect(() => {
    const fetchOrganization = async () => {
      clearSecurityAlertSummary();
      try {
        const organizationId = getStoredActiveOrganizationId();
        if (!organizationId) {
          setActiveOrganizationIdState(null);
          setOrganizationName('');
          setIsOrganizationManager(false);
          setCanSeeOperationsNav(false);
          setOrganizations([]);
          return;
        }

        const [currentResponse, organizationsResponse] = await Promise.all([
          apiClient.get('/organizations/current'),
          apiClient.get('/organizations'),
        ]);
        if (currentResponse.data?.name) {
          setOrganizationName(currentResponse.data.name);
        }
        const isManager = currentResponse.data?.is_manager === true;
        setActiveOrganizationIdState(currentResponse.data?.id || organizationId);
        setIsOrganizationManager(isManager);
        if (isManager) {
          setCanSeeOperationsNav(true);
          loadSecurityAlertSummary();
        } else {
          try {
            const operationsResponse = await apiClient.get('/apps/operations', {
              params: { limit: 1 },
            });
            setCanSeeOperationsNav(
              Array.isArray(operationsResponse.data) &&
                operationsResponse.data.length > 0,
            );
          } catch {
            setCanSeeOperationsNav(false);
          }
        }
        setOrganizations(
          Array.isArray(organizationsResponse.data)
            ? organizationsResponse.data
            : [],
        );
      } catch {
        setActiveOrganizationIdState(null);
        setOrganizationName('');
        setIsOrganizationManager(false);
        setCanSeeOperationsNav(false);
        setOrganizations([]);
      }
    };

    fetchOrganization();
    window.addEventListener(ACTIVE_ORGANIZATION_CHANGED_EVENT, fetchOrganization);
    return () =>
      window.removeEventListener(
        ACTIVE_ORGANIZATION_CHANGED_EVENT,
        fetchOrganization,
      );
  }, [clearSecurityAlertSummary, loadSecurityAlertSummary]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        organizationDropdownRef.current &&
        !organizationDropdownRef.current.contains(event.target as Node)
      ) {
        setIsOrganizationDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const selectOrganization = (organization: SidebarOrganization) => {
    if (organization.id === activeOrganizationId) {
      setIsOrganizationDropdownOpen(false);
      return;
    }

    setActiveOrganizationId(organization.id);
    clearSecurityAlertSummary();
    setActiveOrganizationIdState(organization.id);
    setOrganizationName(organization.name);
    setIsOrganizationManager(organization.is_manager === true);
    setCanSeeOperationsNav(organization.is_manager === true);
    setIsOrganizationDropdownOpen(false);
    router.push('/dashboard');
  };

  const handleLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      // Silent error handling
    } finally {
      localStorage.removeItem('access_token');
      router.push('/auth/login');
    }
  };

  const canSwitchOrganization = organizations.length > 1;
  const organizationTypeLabel = isOrganizationManager ? '내 조직' : '멤버 조직';
  const hasNotifications =
    notifications.length > 0 ||
    (isOrganizationManager && (securityAlertSummary?.open_count ?? 0) > 0);

  return (
    <aside
      className={cn(
        'relative flex h-full flex-col justify-between border-r border-slate-200 bg-white px-4 py-5 transition-all duration-300',
        isCollapsed ? 'w-[80px]' : 'w-[232px]',
      )}
    >
      {/* Toggle Button */}
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className={cn(
          'absolute z-50 rounded-md p-1.5 text-slate-500 transition-all duration-300 hover:bg-slate-100 hover:text-slate-900',
          isCollapsed ? 'left-1/2 top-6 -translate-x-1/2' : 'right-4 top-5',
        )}
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Logo */}
      {isCollapsed ? (
        <button
          onClick={() => router.push('/dashboard')}
          className="mt-12 grid h-10 w-10 place-items-center rounded-lg bg-slate-950 text-white transition-colors hover:bg-slate-800"
          aria-label="대시보드 홈"
        >
          <LayoutDashboard size={20} />
        </button>
      ) : (
        <div className="mb-7">
          <div className="min-w-0">
            <button
              onClick={() => router.push('/dashboard')}
              className="block text-left text-sm font-black text-slate-950"
            >
              Nodease
            </button>
            <span className="block truncate text-xs font-semibold text-slate-500">
              AI workflow control
            </span>
          </div>
        </div>
      )}

      {/* Main Navigation */}
      <nav className="flex-1 space-y-1">
        {navigationItems
          .filter(
            (item) =>
              (!item.managerOnly || isOrganizationManager) &&
              (!item.operationsOnly || canSeeOperationsNav),
          )
          .map((item) => {
            const isActive =
              pathname === item.href ||
              (item.href !== '/dashboard' &&
                pathname.startsWith(`${item.href}/`));
            const Icon = item.icon;

            return (
              <Link
                key={item.name}
                href={item.href}
                className={cn(
                  'flex items-center gap-3 rounded-lg py-2.5 text-sm font-semibold transition-colors',
                  isCollapsed ? 'justify-center px-2' : 'px-3',
                  isActive
                    ? 'bg-slate-950 text-white shadow-sm'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950',
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!isCollapsed && <span>{item.name}</span>}
              </Link>
            );
          })}
      </nav>

      {!isCollapsed && organizationName && (
        <div className="relative mb-3" ref={organizationDropdownRef}>
          <button
            type="button"
            aria-label="조직 전환"
            aria-haspopup={canSwitchOrganization ? 'menu' : undefined}
            aria-expanded={
              canSwitchOrganization ? isOrganizationDropdownOpen : undefined
            }
            disabled={!canSwitchOrganization}
            onClick={() =>
              setIsOrganizationDropdownOpen((isOpen) =>
                canSwitchOrganization ? !isOpen : false,
              )
            }
            className={cn(
              'flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left transition-colors',
              canSwitchOrganization
                ? 'hover:border-blue-200 hover:bg-blue-50'
                : 'cursor-default',
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              <Building2 className="h-3.5 w-3.5 shrink-0 text-blue-600" />
              <div className="min-w-0">
                <span className="block truncate text-xs font-semibold text-slate-700">
                  {organizationName}
                </span>
                <span className="mt-1 inline-flex rounded-md bg-white px-1.5 py-0.5 text-[11px] font-semibold text-slate-500">
                  {organizationTypeLabel}
                </span>
              </div>
            </div>
            {canSwitchOrganization && (
              <ChevronDown
                className={cn(
                  'h-4 w-4 shrink-0 text-slate-400 transition-transform',
                  isOrganizationDropdownOpen && 'rotate-180',
                )}
              />
            )}
          </button>

          {canSwitchOrganization && isOrganizationDropdownOpen && (
            <div className="absolute bottom-full left-0 z-50 mb-2 w-full overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
              <div className="max-h-64 overflow-y-auto">
                {organizations.map((organization) => {
                  const isSelected = organization.id === activeOrganizationId;
                  return (
                    <button
                      key={organization.id}
                      type="button"
                      role="menuitem"
                      aria-label={`${organization.name} 조직 선택`}
                      aria-current={isSelected ? 'true' : undefined}
                      onClick={() => selectOrganization(organization)}
                      className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50"
                    >
                      <Building2 className="h-4 w-4 shrink-0 text-blue-600" />
                      <div className="min-w-0 flex-1">
                        <span className="block truncate font-semibold text-slate-900">
                          {organization.name}
                        </span>
                        <span className="mt-1 inline-flex rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-500">
                          {organization.is_manager ? '내 조직' : '멤버 조직'}
                        </span>
                      </div>
                      {isSelected && (
                        <Check className="h-4 w-4 shrink-0 text-blue-600" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* User Info Footer */}
      <div
        className={cn(
          'relative border-t border-slate-200 pt-4 transition-all',
          isCollapsed && 'items-center justify-center',
        )}
        ref={dropdownRef}
      >
        <button
          onClick={() => setIsDropdownOpen(!isDropdownOpen)}
          className={cn(
            'flex w-full items-center gap-3 rounded-lg text-left transition-colors hover:bg-slate-50',
            isCollapsed ? 'justify-center p-0' : 'p-2',
          )}
        >
          <div className="relative grid h-8 w-8 flex-shrink-0 place-items-center rounded-full bg-slate-200 text-xs font-black text-slate-700">
            {userName.charAt(0).toUpperCase()}
            {hasNotifications && (
              <>
                <span
                  aria-hidden="true"
                  className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-red-600"
                />
                <span className="sr-only">확인할 알림 있음</span>
              </>
            )}
          </div>
          {!isCollapsed && (
            <div className="flex-1 min-w-0">
              <p className="truncate text-sm font-black text-slate-900">
                {userName}
              </p>
              <p className="truncate text-xs font-semibold text-slate-500">
                {userEmail || '사용자'}
              </p>
            </div>
          )}
        </button>

        {/* Dropdown Menu (Upwards) */}
        {isDropdownOpen && (
          <div
            className={cn(
              'absolute bottom-full mb-2 w-full z-50',
              isCollapsed ? 'left-10 w-48' : 'left-0 px-2',
            )}
          >
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
              <button
                aria-label="알림"
                onClick={() => {
                  setIsDropdownOpen(false);
                  setIsNotificationsOpen(true);
                }}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 transition-colors"
              >
                <Bell className="w-4 h-4" />
                <span>알림</span>
                {isOrganizationManager &&
                  (securityAlertSummary?.open_count ?? 0) > 0 && (
                    <span
                      aria-label={`미확인 보안 알림 ${securityAlertSummary!.open_count}개`}
                      className="ml-auto rounded-full bg-red-600 px-2 py-0.5 text-xs font-semibold text-white"
                    >
                      {securityAlertSummary!.open_count}
                    </span>
                  )}
              </button>
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-600 hover:bg-red-50 transition-colors"
              >
                <LogOut className="w-4 h-4" />
                <span>로그아웃</span>
              </button>
            </div>
          </div>
        )}
      </div>
      {isNotificationsOpen && (
        <NotificationOverlay
          notifications={notifications}
          loading={notificationsLoading}
          error={notificationsError}
          onClose={() => setIsNotificationsOpen(false)}
          onRefresh={loadNotifications}
          showSecurityAlerts={isOrganizationManager}
          securityAlertSummary={securityAlertSummary}
          securityAlertsLoading={securityAlertsLoading}
          securityAlertsError={securityAlertsError}
          onRefreshSecurityAlerts={() => loadSecurityAlertSummary(false)}
          onSelectSecurityAlert={(alertId) =>
            router.push(
              `/dashboard/admin?tab=security-alerts&alertId=${alertId}`,
            )
          }
          onViewAllSecurityAlerts={() =>
            router.push('/dashboard/admin?tab=security-alerts')
          }
        />
      )}
    </aside>
  );
}
