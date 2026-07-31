import { describe, expect, it } from 'vitest';

import {
  buildLoginRedirectPath,
  claimLoginRedirectPath,
  resolveSafeAuthReturnPath,
} from './authReturn';

describe('auth return path', () => {
  it('preserves an internal path with its query string', () => {
    const returnPath =
      '/modules/workflow-1/run?deploymentId=deployment-1';

    expect(resolveSafeAuthReturnPath(returnPath)).toBe(returnPath);
    expect(buildLoginRedirectPath(returnPath)).toBe(
      '/auth/login?next=%2Fmodules%2Fworkflow-1%2Frun%3FdeploymentId%3Ddeployment-1',
    );
  });

  it.each([
    undefined,
    '',
    'https://evil.example/steal',
    '//evil.example/steal',
    '/\\evil.example/steal',
    '/safe/../steal',
    '/safe/%2e%2e/steal',
    '/%255c%255cevil.example/steal',
    '/%25252f%25252fevil.example/steal',
    '/%2525252525252f%2525252525252fevil.example/steal',
    '/safe%0d%0aX-Test:%20unsafe',
    '/malformed%escape',
    '/%2e%2e//evil.example/steal',
    `/${'x'.repeat(2048)}`,
  ])('falls back to the dashboard for unsafe return path %s', (returnPath) => {
    expect(resolveSafeAuthReturnPath(returnPath)).toBe('/dashboard');
  });

  it('deduplicates competing login redirects for the same return path', () => {
    const returnPath = '/modules/deduplication-test/run';

    expect(claimLoginRedirectPath(returnPath, 10_000)).toBe(
      '/auth/login?next=%2Fmodules%2Fdeduplication-test%2Frun',
    );
    expect(claimLoginRedirectPath(returnPath, 11_999)).toBeNull();
    expect(claimLoginRedirectPath(returnPath, 12_000)).toBe(
      '/auth/login?next=%2Fmodules%2Fdeduplication-test%2Frun',
    );
  });

  it('does not suppress a redirect when the clock moves backwards', () => {
    const returnPath = '/modules/clock-change-test/run';

    expect(claimLoginRedirectPath(returnPath, 20_000)).not.toBeNull();
    expect(claimLoginRedirectPath(returnPath, 19_000)).not.toBeNull();
  });
});
