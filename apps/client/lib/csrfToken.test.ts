import axios, {
  AxiosHeaders,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  attachCsrfProtection,
  csrfFetch,
  getCsrfToken,
  invalidateCsrfToken,
} from './csrfToken';

const csrfResponse = (token = 'csrf-token', expiresInMs = 600_000) =>
  new Response(
    JSON.stringify({
      token,
      expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    },
  );

beforeEach(() => {
  invalidateCsrfToken();
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('getCsrfToken', () => {
  it('single-flights concurrent bootstrap and stores the token only in memory', async () => {
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const first = getCsrfToken('organization-a');
    const second = getCsrfToken('organization-a');
    resolveFetch?.(csrfResponse());

    await expect(Promise.all([first, second])).resolves.toEqual([
      'csrf-token',
      'csrf-token',
    ]);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/csrf');
    const bootstrapHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(bootstrapHeaders.get('X-CSRF-Bootstrap')).toBe('1');
    expect(storageSpy).not.toHaveBeenCalled();
  });

  it('uses a new bootstrap after active organization scope changes', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(csrfResponse('organization-a-token'))
      .mockResolvedValueOnce(csrfResponse('organization-b-token'))
      .mockResolvedValueOnce(csrfResponse('organization-a-new-token'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getCsrfToken('organization-a')).resolves.toBe(
      'organization-a-token',
    );
    await expect(getCsrfToken('organization-b')).resolves.toBe(
      'organization-b-token',
    );
    await expect(getCsrfToken('organization-a')).resolves.toBe(
      'organization-a-new-token',
    );

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers);
    expect(secondHeaders.get('X-Organization-Id')).toBe('organization-b');
  });

  it('does not revive an in-flight token after lifecycle invalidation', async () => {
    let resolveStale: ((response: Response) => void) | undefined;
    const fetchMock = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveStale = resolve;
          }),
      )
      .mockResolvedValueOnce(csrfResponse('fresh-token'));
    vi.stubGlobal('fetch', fetchMock);

    const staleRequest = getCsrfToken('organization-a');
    invalidateCsrfToken();
    const freshRequest = getCsrfToken('organization-a');
    resolveStale?.(csrfResponse('stale-token'));

    await expect(staleRequest).rejects.toThrow('invalidated');
    await expect(freshRequest).resolves.toBe('fresh-token');
    await expect(getCsrfToken('organization-a')).resolves.toBe('fresh-token');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('retries bootstrap once after the Gateway clears an invalid auth cookie', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(csrfResponse('anonymous-recovery-token'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getCsrfToken(null)).resolves.toBe('anonymous-recovery-token');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('rejects malformed bootstrap responses without persisting raw values', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"token":42}', { status: 200 })),
    );

    await expect(getCsrfToken(null)).rejects.toThrow(
      'CSRF token bootstrap failed',
    );
    expect(window.localStorage.length).toBe(0);
  });
});

describe('attachCsrfProtection', () => {
  const success = (
    config: InternalAxiosRequestConfig,
  ): AxiosResponse<Record<string, boolean>> => ({
    data: { ok: true },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  });

  it('attaches a token to unsafe requests after organization headers resolve', async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(async () => csrfResponse('scoped-token'));
    vi.stubGlobal('fetch', fetchMock);
    const seen: InternalAxiosRequestConfig[] = [];
    const adapter: AxiosAdapter = async (config) => {
      seen.push(config);
      return success(config);
    };
    const client = axios.create({
      adapter,
      baseURL: 'https://api.nodease.example/api/v1',
      withCredentials: true,
    });
    attachCsrfProtection(client);
    client.interceptors.request.use((config) => {
      const headers = AxiosHeaders.from(config.headers);
      headers.set('X-Organization-Id', 'organization-a');
      config.headers = headers;
      return config;
    });

    await client.post('/protected', { value: 1 });

    expect(AxiosHeaders.from(seen[0]?.headers).get('X-CSRF-Token')).toBe(
      'scoped-token',
    );
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      'https://api.nodease.example/api/v1/auth/csrf',
    );
  });

  it('refreshes at most once for replay-safe requests', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(csrfResponse('first-token'))
        .mockResolvedValueOnce(csrfResponse('second-token')),
    );
    let attempts = 0;
    const adapter: AxiosAdapter = async (config) => {
      attempts += 1;
      if (attempts === 1) {
        return Promise.reject({
          isAxiosError: true,
          config,
          response: {
            status: 403,
            data: {
              error: { code: 'auth.csrf_validation_failed' },
            },
          },
        });
      }
      return success(config);
    };
    const client = axios.create({ adapter, withCredentials: true });
    attachCsrfProtection(client);

    await expect(client.put('/protected', { value: 1 })).resolves.toMatchObject(
      {
        status: 200,
      },
    );
    expect(attempts).toBe(2);
  });

  it('coalesces one refresh when concurrent replay-safe requests reject the same token', async () => {
    let resolveRefresh: ((response: Response) => void) | undefined;
    let markRefreshStarted: (() => void) | undefined;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(csrfResponse('expired-token'))
      .mockImplementationOnce(() => {
        markRefreshStarted?.();
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      });
    vi.stubGlobal('fetch', fetchMock);
    let markSecondExpiredAttempt: (() => void) | undefined;
    const secondExpiredAttempt = new Promise<void>((resolve) => {
      markSecondExpiredAttempt = resolve;
    });
    const seenTokens: string[] = [];
    const adapter: AxiosAdapter = async (config) => {
      const token = String(
        AxiosHeaders.from(config.headers).get('X-CSRF-Token'),
      );
      seenTokens.push(token);
      if (token === 'expired-token') {
        if (config.url?.endsWith('/b')) {
          await refreshStarted;
          markSecondExpiredAttempt?.();
        }
        return Promise.reject({
          isAxiosError: true,
          config,
          response: {
            status: 403,
            data: {
              error: { code: 'auth.csrf_validation_failed' },
            },
          },
        });
      }
      return success(config);
    };
    const client = axios.create({ adapter, withCredentials: true });
    attachCsrfProtection(client);

    const requests = Promise.all([
      client.put('/protected/a', { value: 1 }),
      client.delete('/protected/b'),
    ]);
    await secondExpiredAttempt;
    await new Promise((resolve) => setTimeout(resolve, 0));
    resolveRefresh?.(csrfResponse('refreshed-token'));

    await expect(requests).resolves.toHaveLength(2);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(seenTokens.filter((token) => token === 'expired-token')).toHaveLength(
      2,
    );
    expect(
      seenTokens.filter((token) => token === 'refreshed-token'),
    ).toHaveLength(2);
  });

  it('does not automatically replay non-idempotent requests', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => csrfResponse()),
    );
    let attempts = 0;
    const adapter: AxiosAdapter = async (config) => {
      attempts += 1;
      return Promise.reject({
        isAxiosError: true,
        config,
        response: {
          status: 403,
          data: {
            error: { code: 'auth.csrf_validation_failed' },
          },
        },
      });
    };
    const client = axios.create({ adapter, withCredentials: true });
    attachCsrfProtection(client);

    await expect(client.post('/protected', { value: 1 })).rejects.toBeTruthy();
    expect(attempts).toBe(1);
  });
});

describe('csrfFetch', () => {
  it('attaches the scoped token to a protected direct fetch', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(csrfResponse('direct-fetch-token'))
      .mockResolvedValueOnce(new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await csrfFetch('/api/v1/protected', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-Id': 'organization-a',
      },
      body: '{"value":1}',
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const requestHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers);
    expect(requestHeaders.get('X-CSRF-Token')).toBe('direct-fetch-token');
    expect(requestHeaders.get('X-Organization-Id')).toBe('organization-a');
    expect(fetchMock.mock.calls[1]?.[1]?.credentials).toBe('include');
  });

  it('partitions bootstrap cookies by the actual mutation origin', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(csrfResponse('web-origin-token'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(csrfResponse('api-origin-token'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await csrfFetch('/api/v1/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    await csrfFetch('https://api.nodease.example/api/v1/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/csrf');
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      'https://api.nodease.example/api/v1/auth/csrf',
    );
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('X-CSRF-Token'),
    ).toBe('web-origin-token');
    expect(
      new Headers(fetchMock.mock.calls[3]?.[1]?.headers).get('X-CSRF-Token'),
    ).toBe('api-origin-token');
  });
});
