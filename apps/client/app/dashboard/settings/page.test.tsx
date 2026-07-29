import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/csrfToken', () => ({
  csrfFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, init),
}));

const activeOrganizationMock = vi.hoisted(() => ({
  organizationId: 'org-1',
  setActiveOrganizationId: vi.fn(),
}));

const knowledgeApiMock = vi.hoisted(() => ({
  getKnowledgeBases: vi.fn(),
}));

vi.mock('@/lib/activeOrganization', () => ({
  activeOrganizationHeaders: (organizationId?: string | null) =>
    organizationId ? { 'X-Organization-Id': organizationId } : {},
  getStoredActiveOrganizationId: () => activeOrganizationMock.organizationId,
  resolveActiveOrganizationId: (organizations: { id: string }[]) =>
    organizations.some(
      (organization) =>
        organization.id === activeOrganizationMock.organizationId,
    )
      ? activeOrganizationMock.organizationId
      : null,
  setActiveOrganizationId: activeOrganizationMock.setActiveOrganizationId,
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: knowledgeApiMock,
}));

import SettingsPage from './page';

const jsonResponse = (data: unknown) =>
  Promise.resolve({
    ok: true,
    json: async () => data,
  } as Response);

const emptyPermissions = {
  resource_type: 'workflow',
  resource_id: 'workflow-1',
  organization_id: 'org-1',
  team_permissions: [],
  user_permissions: [],
};

describe('SettingsPage tabs', () => {
  const fetchMock = vi.fn();
  let isManager = false;
  let permissionGranted = false;

  beforeEach(() => {
    isManager = false;
    permissionGranted = false;
    activeOrganizationMock.setActiveOrganizationId.mockReset();
    knowledgeApiMock.getKnowledgeBases.mockReset();
    knowledgeApiMock.getKnowledgeBases.mockResolvedValue([]);
    fetchMock.mockReset();
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), 'http://localhost');
        const method = init?.method || 'GET';

        if (url.pathname === '/api/v1/organizations') {
          return jsonResponse([
            { id: 'org-1', name: 'Acme', is_manager: isManager },
          ]);
        }
        if (url.pathname === '/api/v1/organizations/current') {
          return jsonResponse({
            id: 'org-1',
            name: 'Acme',
            is_manager: isManager,
          });
        }
        if (url.pathname === '/api/v1/llm/providers') {
          return jsonResponse([
            {
              id: 'provider-1',
              name: 'OpenAI',
              type: 'openai',
              base_url: 'https://api.example.test',
              models: [{ id: 'model-1', name: 'Test Model' }],
            },
          ]);
        }
        if (url.pathname === '/api/v1/llm/credentials') {
          return jsonResponse([
            {
              id: 'credential-1',
              provider_id: 'provider-1',
              organization_id: 'org-1',
              credential_name: 'Primary credential',
              config_preview: 'configured',
              is_valid: true,
              created_at: '2026-07-13T00:00:00Z',
            },
          ]);
        }
        if (url.pathname === '/api/v1/apps') {
          return jsonResponse([
            {
              id: 'app-1',
              name: 'Support workflow',
              workflow_id: 'workflow-1',
            },
          ]);
        }
        if (url.pathname === '/api/v1/organizations/org-1/members') {
          return jsonResponse([]);
        }
        if (url.pathname === '/api/v1/teams') {
          return jsonResponse([
            {
              id: 'team-1',
              organization_id: 'org-1',
              name: 'Platform',
              is_active: true,
            },
          ]);
        }
        if (url.pathname === '/api/v1/teams/team-1/members') {
          return jsonResponse([]);
        }
        if (url.pathname === '/api/v1/permissions/workflows/workflow-1') {
          if (method === 'PUT') {
            permissionGranted = true;
            return jsonResponse({});
          }
          return jsonResponse(
            permissionGranted
              ? {
                  ...emptyPermissions,
                  team_permissions: [
                    {
                      id: 'permission-1',
                      grantee_type: 'team',
                      grantee_id: 'team-1',
                      grantee_name: 'Granted Platform Team',
                      auth_state: 'viewer',
                      assigned_at: '2026-07-13T00:00:00Z',
                    },
                  ],
                }
              : emptyPermissions,
          );
        }
        if (
          url.pathname ===
          '/api/v1/permissions/workflows/workflow-1/teams/team-1'
        ) {
          permissionGranted = true;
          return jsonResponse({});
        }
        if (
          url.pathname === '/api/v1/permissions/llm-credentials/credential-1'
        ) {
          return jsonResponse({
            ...emptyPermissions,
            resource_type: 'llm_credential',
            resource_id: 'credential-1',
          });
        }

        throw new Error(`Unexpected request: ${method} ${url.pathname}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('member에게 LLM Credentials만 표시하고 load/refresh에서 audit log를 요청하지 않는다', async () => {
    render(<SettingsPage />);

    expect(
      await screen.findByRole('heading', { name: 'OpenAI' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'LLM Credentials' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Access' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Activity' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Audit Activity')).not.toBeInTheDocument();
    expect(auditRequestCount()).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: '새로고침' }));

    await waitFor(() => expect(organizationRequestCount()).toBe(2));
    expect(auditRequestCount()).toBe(0);
  });

  it('manager의 Access와 LLM Credentials를 유지하고 권한 부여 후 audit log를 요청하지 않는다', async () => {
    isManager = true;
    render(<SettingsPage />);

    expect(
      await screen.findByRole('heading', { name: 'OpenAI' }),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Access' })).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'LLM Credentials' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Activity' }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Access' }));

    expect(await screen.findByRole('heading', { name: 'Teams' })).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'Resource Permissions' }),
    ).toBeVisible();

    const grantButton = screen.getByRole('button', { name: '부여' });
    await waitFor(() => expect(grantButton).toBeEnabled());
    fireEvent.click(grantButton);

    expect(await screen.findByText('Granted Platform Team')).toBeVisible();
    expect(auditRequestCount()).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: 'LLM Credentials' }));
    expect(
      await screen.findByRole('heading', { name: 'OpenAI' }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Access' }));
    expect(await screen.findByRole('heading', { name: 'Teams' })).toBeVisible();

    isManager = false;
    fireEvent.click(screen.getByRole('button', { name: '새로고침' }));

    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Access' }),
      ).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByRole('heading', { name: 'OpenAI' }),
    ).toBeVisible();
    expect(auditRequestCount()).toBe(0);
  });

  it('manager 뱃지를 설정 제목과 같은 줄에 표시한다', async () => {
    isManager = true;
    render(<SettingsPage />);

    expect(
      await screen.findByRole('heading', { name: 'OpenAI' }),
    ).toBeVisible();

    const title = screen.getByRole('heading', { level: 1, name: '설정' });
    const badge = screen.getByText('관리자');

    expect(title.firstElementChild).toHaveClass('lucide-settings');
    expect(title.parentElement).toHaveClass('flex', 'items-center', 'gap-2');
    expect(badge.parentElement).toBe(title.parentElement);
    expect(badge).not.toHaveClass('mt-2');
  });

  const auditRequestCount = () =>
    fetchMock.mock.calls.filter(([input]) =>
      String(input).includes('/users/me/audit-logs'),
    ).length;

  const organizationRequestCount = () =>
    fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith('/api/v1/organizations'),
    ).length;
});
