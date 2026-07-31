import { NextRequest } from 'next/server';
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
  type MockedFunction,
} from 'vitest';

import { proxy } from './proxy';

const enabledProjection = {
  contract_version: 'deployment_browser_access.v1',
  deployment_version: 4,
  embedding: {
    enabled: true,
    frame_ancestors: [
      'https://admin.example.com',
      'https://portal.example.com',
    ],
  },
};

const callProxy = (path = '/embed/chat/app-a1b2c3d4') =>
  proxy(
    new NextRequest(`https://nodease.example${path}`, {
      headers: {
        Origin: 'https://untrusted.example',
        Referer: 'https://untrusted.example/page',
        Host: 'spoofed.example',
      },
    }),
  );

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('embed browser-access proxy', () => {
  it('sets the exact deployment CSP from the configured Gateway projection', async () => {
    vi.stubEnv('API_URL', 'http://gateway.internal:8000/api/v1/');
    vi.stubEnv('NODE_ENV', 'production');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      jsonResponse(enabledProjection),
    );
    vi.stubGlobal('fetch', fetchMock);

    const response = await callProxy(
      '/embed/chat/app-a1b2c3d4?origin=https://attacker.example',
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'http://gateway.internal:8000/api/v1/deployments/public/app-a1b2c3d4/browser-access',
      expect.objectContaining({ cache: 'no-store' }),
    );
    expect(response.headers.get('Content-Security-Policy')).toBe(
      'frame-ancestors https://admin.example.com https://portal.example.com',
    );
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.has('X-Frame-Options')).toBe(false);
    expect(response.headers.has('Access-Control-Allow-Origin')).toBe(false);
  });

  it('uses the disabled CSP when the active policy is disabled', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({
          ...enabledProjection,
          embedding: { enabled: false, frame_ancestors: [] },
        }),
      ),
    );

    const response = await callProxy();

    expect(response.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
  });

  it.each([
    ['unknown contract', { ...enabledProjection, contract_version: 'v2' }],
    ['invalid version', { ...enabledProjection, deployment_version: 0 }],
    [
      'wildcard source',
      {
        ...enabledProjection,
        embedding: { enabled: true, frame_ancestors: ['*'] },
      },
    ],
    [
      'path-bearing source',
      {
        ...enabledProjection,
        embedding: {
          enabled: true,
          frame_ancestors: ['https://portal.example.com/path'],
        },
      },
    ],
    [
      'non-canonical source',
      {
        ...enabledProjection,
        embedding: {
          enabled: true,
          frame_ancestors: ['https://PORTAL.example.com'],
        },
      },
    ],
    [
      'production HTTP source',
      {
        ...enabledProjection,
        embedding: {
          enabled: true,
          frame_ancestors: ['http://localhost:3001'],
        },
      },
    ],
    [
      'unsorted sources',
      {
        ...enabledProjection,
        embedding: {
          enabled: true,
          frame_ancestors: ['https://z.example.com', 'https://a.example.com'],
        },
      },
    ],
    [
      'disabled policy with sources',
      {
        ...enabledProjection,
        embedding: {
          enabled: false,
          frame_ancestors: ['https://portal.example.com'],
        },
      },
    ],
    ['extra projection field', { ...enabledProjection, organization_id: 'x' }],
  ])('fails closed for a malformed %s projection', async (_name, body) => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () => jsonResponse(body)),
    );

    const response = await callProxy();

    expect(response.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
  });

  it.each([404, 409, 500])(
    'fails closed when the Gateway returns %s',
    async (status) => {
      vi.stubGlobal(
        'fetch',
        vi.fn<typeof fetch>(async () =>
          jsonResponse({ detail: 'hidden' }, status),
        ),
      );

      const response = await callProxy();

      expect(response.headers.get('Content-Security-Policy')).toBe(
        "frame-ancestors 'none'",
      );
    },
  );

  it('fails closed when the Gateway lookup rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () => {
        throw new Error('unavailable');
      }),
    );

    const response = await callProxy();

    expect(response.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
  });

  it('aborts a stalled Gateway lookup after one second and fails closed', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(
        async (_input, init) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    );

    const pendingResponse = callProxy();
    await vi.advanceTimersByTimeAsync(1_001);
    const response = await pendingResponse;

    expect(response.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
  });

  it('allows exact loopback HTTP origins only in development', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({
          ...enabledProjection,
          embedding: {
            enabled: true,
            frame_ancestors: [
              'http://127.0.0.1:3001',
              'http://[::1]:3002',
              'http://localhost:3000',
            ],
          },
        }),
      ),
    );

    const response = await callProxy();

    expect(response.headers.get('Content-Security-Policy')).toBe(
      'frame-ancestors http://127.0.0.1:3001 http://[::1]:3002 http://localhost:3000',
    );
  });

  it('does not query Gateway for unknown embed paths or unsafe slugs', async () => {
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      jsonResponse(enabledProjection),
    );
    vi.stubGlobal('fetch', fetchMock);

    const unknown = await callProxy('/embed/preview/app-a1b2c3d4');
    const unsafe = await callProxy('/embed/chat/app-a1b2c3d4%2Fother');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(unknown.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
    expect(unsafe.headers.get('Content-Security-Policy')).toBe(
      "frame-ancestors 'none'",
    );
  });
});
