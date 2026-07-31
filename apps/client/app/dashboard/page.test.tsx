import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

const activeOrganizationMock = vi.hoisted(() => ({
  storedOrganizationId: 'org-1' as string | null,
  setActiveOrganizationId: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
}));

vi.mock('@/lib/activeOrganization', () => ({
  ACTIVE_ORGANIZATION_CHANGED_EVENT: 'nodease-active-organization-changed',
  activeOrganizationHeaders: (organizationId?: string | null) =>
    organizationId ? { 'X-Organization-Id': organizationId } : {},
  getStoredActiveOrganizationId: () =>
    activeOrganizationMock.storedOrganizationId,
  resolveActiveOrganizationId: (organizations: { id: string }[]) =>
    activeOrganizationMock.storedOrganizationId &&
    organizations.some(
      (organization) =>
        organization.id === activeOrganizationMock.storedOrganizationId,
    )
      ? activeOrganizationMock.storedOrganizationId
      : null,
  setActiveOrganizationId: activeOrganizationMock.setActiveOrganizationId,
}));

import DashboardHomePage from './page';

const jsonResponse = (data: unknown) =>
  Promise.resolve({
    ok: true,
    json: async () => data,
  } as Response);

describe('DashboardHomePage active organization changes', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    routerMock.push.mockReset();
    activeOrganizationMock.storedOrganizationId = 'org-1';
    activeOrganizationMock.setActiveOrganizationId.mockReset();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/organizations')) {
        return jsonResponse([
          { id: 'org-1', name: 'Acme', is_manager: false },
          { id: 'org-2', name: 'Beta', is_manager: false },
        ]);
      }
      if (url.endsWith('/organizations/current')) {
        return jsonResponse({ id: 'org-1', name: 'Acme', is_manager: false });
      }
      if (url.endsWith('/apps')) {
        return jsonResponse([]);
      }
      return jsonResponse([]);
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('active organization 변경 event를 받으면 dashboard home 데이터를 재조회한다', async () => {
    render(<DashboardHomePage />);

    await waitFor(() => expect(organizationListCalls()).toBe(1));

    const title = screen.getByRole('heading', { level: 1, name: 'Nodease' });
    expect(title.firstElementChild).toHaveClass('lucide-house');

    act(() => {
      window.dispatchEvent(new Event('nodease-active-organization-changed'));
    });

    await waitFor(() => expect(organizationListCalls()).toBe(2));
  });

  const organizationListCalls = () =>
    fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith('/organizations'),
    ).length;
});
