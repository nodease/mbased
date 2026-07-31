import { describe, expect, it } from 'vitest';

import { resolvePublicApiBaseUrl } from './publicApiOrigin';

describe('resolvePublicApiBaseUrl', () => {
  it.each([undefined, '', '   '])(
    'uses the same-origin API path when the value is %p',
    (value) => {
      expect(resolvePublicApiBaseUrl(value, 'production')).toBe('/api/v1');
    },
  );

  it('accepts and canonicalizes an explicit public HTTPS origin', () => {
    expect(
      resolvePublicApiBaseUrl(
        ' https://API.NODEASE.EXAMPLE:443/ ',
        'production',
      ),
    ).toBe('https://api.nodease.example/api/v1');
  });

  it.each([
    'http://api.nodease.example',
    'http://localhost:8000',
    'https://localhost',
    'https://127.0.0.1',
    'https://10.0.0.10',
    'https://172.16.0.10',
    'https://192.168.0.10',
    'https://192.0.2.10',
    'https://198.51.100.10',
    'https://203.0.113.10',
    'https://169.254.169.254',
    'https://api-service',
    'https://api.internal',
    'https://api.local',
    'https://api.home.arpa',
    'https://gateway.default.svc',
    'https://gateway.default.svc.',
    'https://gateway.default.svc.cluster.local',
    'https://gateway.default.svc.cluster.local.',
  ])('rejects a non-public production origin: %s', (value) => {
    expect(() => resolvePublicApiBaseUrl(value, 'production')).toThrow(
      'NEXT_PUBLIC_API_URL',
    );
  });

  it.each([
    '//api.nodease.example',
    'https://user@api.nodease.example',
    'https://api.nodease.example/api/v1',
    'https://api.nodease.example?tenant=one',
    'https://api.nodease.example#fragment',
    'https://*.nodease.example',
    'https://api.nodease.example:99999',
    'https://api.nodease.example:not-a-port',
  ])('rejects a value that is not an origin: %s', (value) => {
    expect(() => resolvePublicApiBaseUrl(value, 'production')).toThrow(
      'NEXT_PUBLIC_API_URL',
    );
  });

  it.each([
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://[::1]:8000',
  ])('allows loopback HTTP during local development: %s', (value) => {
    expect(resolvePublicApiBaseUrl(value, 'development')).toBe(
      `${value}/api/v1`,
    );
  });

  it('does not relax private HTTPS origins during local development', () => {
    expect(() =>
      resolvePublicApiBaseUrl(
        'https://gateway.default.svc.cluster.local',
        'development',
      ),
    ).toThrow('NEXT_PUBLIC_API_URL');
  });

  it.each([
    'http://api.nodease.example',
    'http://gateway.default.svc.cluster.local',
    'http://10.0.0.10',
  ])('rejects non-loopback HTTP during local development: %s', (value) => {
    expect(() => resolvePublicApiBaseUrl(value, 'development')).toThrow(
      'NEXT_PUBLIC_API_URL',
    );
  });

  it('treats an unknown environment as production', () => {
    expect(() =>
      resolvePublicApiBaseUrl('http://localhost:8000', undefined),
    ).toThrow('NEXT_PUBLIC_API_URL');
  });
});
