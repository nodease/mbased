import {
  afterEach,
  describe,
  expect,
  it,
  vi,
  type MockedFunction,
} from 'vitest';

import { POST } from './route';

const getFetchInit = (fetchMock: MockedFunction<typeof fetch>): RequestInit => {
  const init = fetchMock.mock.calls[0]?.[1];
  if (!init) throw new Error('Expected fetch to receive request options');
  return init;
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const callRoute = (request: Request, workflowId = 'workflow-1') =>
  POST(request as Parameters<typeof POST>[0], {
    params: Promise.resolve({ workflowId }),
  });

describe('workflow stream proxy route', () => {
  it('uses server-only API_URL and forwards safe context headers', async () => {
    vi.stubEnv('API_URL', 'http://localhost:8001/api/v1/');
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://public.example');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      Promise.resolve(new Response('data: {}\n\n')),
    );
    vi.stubGlobal('fetch', fetchMock);

    const response = await callRoute(
      new Request('http://localhost:3000/stream-api/workflows/workflow-1', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Cookie: 'auth_token=session; csrf_token=stale',
          Origin: 'http://localhost:3000',
          'Sec-Fetch-Site': 'same-origin',
          'X-CSRF-Token': 'v1.123.nonce.signature',
          'X-Organization-Id': 'org-1',
          'X-Request-Id': 'request-1',
          'X-Correlation-Id': 'corr-1',
          Authorization: 'Bearer should-not-forward',
        },
        body: JSON.stringify({ inputs: { question: 'hello' } }),
      }),
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8001/api/v1/workflows/workflow-1/stream',
      expect.objectContaining({ method: 'POST' }),
    );
    const init = getFetchInit(fetchMock);
    const headers = new Headers(init.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('Cookie')).toBe(
      'auth_token=session; csrf_token=v1.123.nonce.signature',
    );
    expect(headers.get('Origin')).toBe('http://localhost:3000');
    expect(headers.get('Sec-Fetch-Site')).toBe('same-origin');
    expect(headers.get('X-CSRF-Token')).toBe('v1.123.nonce.signature');
    expect(headers.get('X-Organization-Id')).toBe('org-1');
    expect(headers.get('X-Request-Id')).toBe('request-1');
    expect(headers.get('X-Correlation-Id')).toBe('corr-1');
    expect(headers.has('Authorization')).toBe(false);
    expect(init.body).toBe(JSON.stringify({ inputs: { question: 'hello' } }));
  });

  it('proxies FormData without overriding the multipart Content-Type', async () => {
    vi.stubEnv('API_URL', 'http://localhost:8000');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      Promise.resolve(new Response('data: {}\n\n')),
    );
    vi.stubGlobal('fetch', fetchMock);
    const formData = new FormData();
    formData.set('inputs', JSON.stringify({ question: 'hello' }));

    await callRoute(
      new Request('http://localhost:3000/stream-api/workflows/workflow-1', {
        method: 'POST',
        headers: {
          'X-Organization-Id': 'org-1',
        },
        body: formData,
      }),
    );

    const init = getFetchInit(fetchMock);
    const headers = new Headers(init.headers);
    expect(headers.has('Content-Type')).toBe(false);
    expect(headers.get('X-Organization-Id')).toBe('org-1');
    expect((init.body as FormData).get('inputs')).toBe(
      JSON.stringify({ question: 'hello' }),
    );
  });

  it('prefers API_URL over NEXT_PUBLIC_API_URL', async () => {
    vi.stubEnv('API_URL', 'http://gateway.internal:8000/');
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8001');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      Promise.resolve(new Response('data: {}\n\n')),
    );
    vi.stubGlobal('fetch', fetchMock);

    await callRoute(
      new Request('http://localhost:3000/stream-api/workflows/workflow-2', {
        method: 'POST',
        body: JSON.stringify({ inputs: {} }),
      }),
      'workflow-2',
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'http://gateway.internal:8000/api/v1/workflows/workflow-2/stream',
      expect.any(Object),
    );
  });

  it('fails closed when production API_URL is missing', async () => {
    vi.stubEnv('API_URL', undefined);
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.nodease.example');
    vi.stubEnv('NODE_ENV', 'production');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      callRoute(
        new Request('http://localhost:3000/stream-api/workflows/workflow-3', {
          method: 'POST',
          body: JSON.stringify({ inputs: {} }),
        }),
        'workflow-3',
      ),
    ).rejects.toThrow('API_URL');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('uses the local Gateway fallback outside production', async () => {
    vi.stubEnv('API_URL', undefined);
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.nodease.example');
    vi.stubEnv('NODE_ENV', 'development');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      Promise.resolve(new Response('data: {}\n\n')),
    );
    vi.stubGlobal('fetch', fetchMock);

    await callRoute(
      new Request('http://localhost:3000/stream-api/workflows/workflow-3', {
        method: 'POST',
        body: JSON.stringify({ inputs: {} }),
      }),
      'workflow-3',
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/workflows/workflow-3/stream',
      expect.any(Object),
    );
  });

  it('returns structured json when the backend error body is plain text', async () => {
    vi.stubEnv('API_URL', 'http://localhost:8000');
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        Promise.resolve(new Response('upstream unavailable', { status: 503 })),
      ),
    );

    const response = await callRoute(
      new Request('http://localhost:3000/stream-api/workflows/workflow-1', {
        method: 'POST',
        body: JSON.stringify({ inputs: {} }),
      }),
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      detail: 'upstream unavailable',
    });
  });
});
