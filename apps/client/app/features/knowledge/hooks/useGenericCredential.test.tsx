import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useGenericCredential } from './useGenericCredential';

vi.mock('@/lib/activeOrganization', () => ({
  activeOrganizationHeaders: (organizationId?: string | null) =>
    organizationId ? { 'X-Organization-Id': organizationId } : {},
  getStoredActiveOrganizationId: () =>
    '11111111-1111-4111-8111-111111111111',
}));

describe('useGenericCredential organization scope', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('credential 목록 요청에 active organization header를 전송한다', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ id: 'provider-1', name: 'openai' }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 'credential-1',
            provider_id: 'provider-1',
            is_valid: true,
          },
        ],
      });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGenericCredential('openai'));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.hasKey).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/v1/llm/credentials',
      {
        credentials: 'include',
        headers: {
          'X-Organization-Id':
            '11111111-1111-4111-8111-111111111111',
        },
      },
    );
  });
});
