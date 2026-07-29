import { beforeEach, describe, expect, it, vi } from 'vitest';

const postMock = vi.hoisted(() => vi.fn());
const invalidateMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/apiClient', () => ({
  apiBaseUrl: '/api/v1',
  publicApiClient: {
    post: postMock,
    get: vi.fn(),
  },
}));

vi.mock('@/lib/csrfToken', () => ({
  invalidateCsrfToken: invalidateMock,
}));

import { authApi } from './authApi';

describe('authApi CSRF lifecycle', () => {
  beforeEach(() => {
    postMock.mockReset();
    invalidateMock.mockReset();
  });

  it.each([
    {
      action: () =>
        authApi.signup({
          email: 'user@example.com',
          password: 'safe-password',
          name: 'User',
        }),
      endpoint: '/auth/signup',
    },
    {
      action: () =>
        authApi.login({
          email: 'user@example.com',
          password: 'safe-password',
        }),
      endpoint: '/auth/login',
    },
  ])(
    'invalidates the pre-auth token after $endpoint succeeds',
    async ({ action, endpoint }) => {
      postMock.mockResolvedValueOnce({ data: { ok: true } });

      await action();

      expect(postMock).toHaveBeenCalledWith(endpoint, expect.any(Object));
      expect(invalidateMock).toHaveBeenCalledOnce();
    },
  );

  it('invalidates the authenticated token after logout succeeds', async () => {
    postMock.mockResolvedValueOnce({ data: undefined });

    await authApi.logout();

    expect(postMock).toHaveBeenCalledWith('/auth/logout', {});
    expect(invalidateMock).toHaveBeenCalledOnce();
  });

  it('keeps the cached token when the auth mutation fails', async () => {
    postMock.mockRejectedValueOnce(new Error('network failure'));

    await expect(
      authApi.login({
        email: 'user@example.com',
        password: 'safe-password',
      }),
    ).rejects.toThrow('network failure');
    expect(invalidateMock).not.toHaveBeenCalled();
  });
});
