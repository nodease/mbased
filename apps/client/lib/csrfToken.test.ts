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
    const fetchMock = vi.fn(
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
    expect(storageSpy).not.toHaveBeenCalled();
  });

  it('uses a new bootstrap after active organization scope changes', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(csrfResponse('organization-a-token'))
      .mockResolvedValueOnce(csrfResponse('organization-b-token'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getCsrfToken('organization-a')).resolves.toBe(
      'organization-a-token',
    );
    await expect(getCsrfToken('organization-b')).resolves.toBe(
      'organization-b-token',
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers);
    expect(secondHeaders.get('X-Organization-Id')).toBe('organization-b');
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
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => csrfResponse('scoped-token')),
    );
    const seen: InternalAxiosRequestConfig[] = [];
    const adapter: AxiosAdapter = async (config) => {
      seen.push(config);
      return success(config);
    };
    const client = axios.create({ adapter, withCredentials: true });
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
});
