import { describe, expect, it } from 'vitest';

import {
  browserAccessApiErrorMessage,
  buildBrowserAccessPolicyDraft,
  disabledBrowserAccessPolicy,
} from './browserAccessPolicy';

describe('browser access policy form adapter', () => {
  it('builds the canonical disabled contract without origins', () => {
    expect(
      buildBrowserAccessPolicyDraft(false, ['https://ignored.example']),
    ).toEqual({ policy: disabledBrowserAccessPolicy(), error: null });
  });

  it('preserves provisional user origins for server-side canonicalization', () => {
    expect(
      buildBrowserAccessPolicyDraft(
        true,
        [' https://EXAMPLE.com:443 ', 'https://portal.example.com'],
        'production',
      ),
    ).toEqual({
      policy: {
        contract_version: 'deployment_browser_access.v1',
        embedding: {
          enabled: true,
          parent_origins: [
            'https://EXAMPLE.com:443',
            'https://portal.example.com',
          ],
        },
      },
      error: null,
    });
  });

  it.each([
    [[], '1개 이상'],
    [['https://example.com/path'], '경로'],
    [['https://user@example.com'], '인증 정보'],
    [['not-an-origin'], '유효한 origin'],
    [['http://example.com'], 'HTTPS'],
    [['https://example.com', 'https://example.com:443'], '중복된 부모 origin'],
  ])('rejects invalid enabled inputs %#', (origins, message) => {
    const result = buildBrowserAccessPolicyDraft(true, origins, 'production');

    expect(result.policy).toBeNull();
    expect(result.error).toContain(message);
  });

  it('allows exact loopback HTTP origins only for development', () => {
    expect(
      buildBrowserAccessPolicyDraft(
        true,
        ['http://localhost:3000', 'http://127.0.0.1:3001', 'http://[::1]:3002'],
        'development',
      ).error,
    ).toBeNull();
    expect(
      buildBrowserAccessPolicyDraft(
        true,
        ['http://dev.localhost:3000'],
        'development',
      ).error,
    ).toContain('HTTPS');
  });

  it('maps fixed server codes without exposing raw response details', () => {
    const error = {
      response: {
        data: {
          detail: {
            code: 'deployment.browser_access.invalid_origin',
            message: 'raw upstream detail',
          },
        },
      },
    };

    expect(browserAccessApiErrorMessage(error)).toBe(
      '유효한 HTTPS origin을 입력하세요.',
    );
  });
});
