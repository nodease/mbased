import {
  AxiosHeaders,
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios';

import { getStoredActiveOrganizationId } from './activeOrganization';
import { ACTIVE_ORGANIZATION_CHANGED_EVENT } from './activeOrganizationEvent';
import { resolvePublicApiBaseUrl } from './publicApiOrigin';

const CSRF_HEADER_NAME = 'X-CSRF-Token';
const CSRF_BOOTSTRAP_HEADER_NAME = 'X-CSRF-Bootstrap';
const ORGANIZATION_HEADER_NAME = 'X-Organization-Id';
const CSRF_FAILURE_CODE = 'auth.csrf_validation_failed';
const EXPIRY_SKEW_MS = 30_000;
const MAX_TOKEN_LENGTH = 256;
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const REPLAY_SAFE_METHODS = new Set(['PUT', 'DELETE']);

const apiBaseUrl = resolvePublicApiBaseUrl(
  process.env.NEXT_PUBLIC_API_URL,
  process.env.NODE_ENV,
);

type CachedToken = {
  token: string;
  expiresAtMs: number;
  scope: string;
};

type BootstrapTarget = {
  cacheKey: string;
  url: string;
};

type RetryableRequestConfig = InternalAxiosRequestConfig & {
  _csrfRetried?: boolean;
};

type InFlightBootstrap = {
  generation: number;
  scope: string;
  promise: Promise<string>;
};

const cachedTokenByOrigin = new Map<string, CachedToken>();
const inFlightByOrigin = new Map<string, InFlightBootstrap>();
let cacheGeneration = 0;

const normalizeScope = (organizationId?: string | null) =>
  organizationId?.trim() || '';

const sameOriginBootstrapTarget = (): BootstrapTarget => ({
  cacheKey:
    typeof window !== 'undefined' ? window.location.origin : 'same-origin',
  url: '/api/v1/auth/csrf',
});

const absoluteBootstrapTarget = (value: string): BootstrapTarget | null => {
  try {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol)) return null;
    return {
      cacheKey: parsed.origin,
      url: `${parsed.origin}/api/v1/auth/csrf`,
    };
  } catch {
    return null;
  }
};

const requestTargetValue = (target: RequestInfo | URL): string =>
  typeof target === 'string'
    ? target
    : target instanceof URL
      ? target.toString()
      : target.url;

const resolveBootstrapTarget = (
  requestTarget?: RequestInfo | URL,
): BootstrapTarget => {
  if (requestTarget !== undefined) {
    return (
      absoluteBootstrapTarget(requestTargetValue(requestTarget)) ??
      sameOriginBootstrapTarget()
    );
  }
  return absoluteBootstrapTarget(apiBaseUrl) ?? sameOriginBootstrapTarget();
};

const axiosRequestTarget = (
  config: InternalAxiosRequestConfig,
): string | undefined => {
  if (config.url && absoluteBootstrapTarget(config.url)) return config.url;
  return config.baseURL ?? config.url;
};

const isUnsafeMethod = (method?: string) =>
  UNSAFE_METHODS.has((method || 'GET').toUpperCase());

const isFixedCsrfFailure = (error: AxiosError) => {
  const data = error.response?.data;
  if (typeof data !== 'object' || data === null || !('error' in data)) {
    return false;
  }
  const envelope = (data as { error?: unknown }).error;
  return (
    typeof envelope === 'object' &&
    envelope !== null &&
    'code' in envelope &&
    (envelope as { code?: unknown }).code === CSRF_FAILURE_CODE
  );
};

const isReplaySafe = (config: InternalAxiosRequestConfig) => {
  const method = (config.method || 'GET').toUpperCase();
  if (REPLAY_SAFE_METHODS.has(method)) return true;
  const headers = AxiosHeaders.from(config.headers);
  return headers.has('Idempotency-Key') || headers.has('X-Idempotency-Key');
};

const parseBootstrapResponse = async (
  response: Response,
  scope: string,
): Promise<CachedToken> => {
  if (!response.ok) throw new Error('CSRF token bootstrap failed');
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error('CSRF token bootstrap failed');
  }
  if (
    typeof payload !== 'object' ||
    payload === null ||
    !('token' in payload) ||
    !('expires_at' in payload)
  ) {
    throw new Error('CSRF token bootstrap failed');
  }
  const token = (payload as { token?: unknown }).token;
  const expiresAt = (payload as { expires_at?: unknown }).expires_at;
  const expiresAtMs =
    typeof expiresAt === 'string' ? Date.parse(expiresAt) : Number.NaN;
  if (
    typeof token !== 'string' ||
    token.length === 0 ||
    token.length > MAX_TOKEN_LENGTH ||
    !Number.isFinite(expiresAtMs) ||
    expiresAtMs <= Date.now() + EXPIRY_SKEW_MS
  ) {
    throw new Error('CSRF token bootstrap failed');
  }
  return { token, expiresAtMs, scope };
};

export const invalidateCsrfToken = () => {
  cacheGeneration += 1;
  cachedTokenByOrigin.clear();
};

export const getCsrfToken = async (
  organizationId?: string | null,
  requestTarget?: RequestInfo | URL,
): Promise<string> => {
  const scope = normalizeScope(organizationId);
  const target = resolveBootstrapTarget(requestTarget);
  const generation = cacheGeneration;
  const cachedToken = cachedTokenByOrigin.get(target.cacheKey);
  if (
    cachedToken &&
    cachedToken.scope === scope &&
    cachedToken.expiresAtMs > Date.now() + EXPIRY_SKEW_MS
  ) {
    return cachedToken.token;
  }

  const existing = inFlightByOrigin.get(target.cacheKey);
  if (
    existing &&
    existing.generation === generation &&
    existing.scope === scope
  ) {
    return existing.promise;
  }

  const predecessor = existing?.promise.catch(() => undefined);
  const bootstrapWork = async () => {
    if (predecessor) await predecessor;
    if (generation !== cacheGeneration) {
      throw new Error('CSRF token bootstrap was invalidated');
    }
    const headers = new Headers({
      Accept: 'application/json',
      [CSRF_BOOTSTRAP_HEADER_NAME]: '1',
    });
    if (scope) headers.set(ORGANIZATION_HEADER_NAME, scope);
    const requestToken = () =>
      fetch(target.url, {
        method: 'GET',
        headers,
        credentials: 'include',
        cache: 'no-store',
      });
    let response = await requestToken();
    // The Gateway clears an invalid HttpOnly auth cookie on the first 401.
    // Retry once so the browser can establish an anonymous pre-auth binding.
    if (response.status === 401) response = await requestToken();
    const parsed = await parseBootstrapResponse(response, scope);
    if (generation !== cacheGeneration) {
      throw new Error('CSRF token bootstrap was invalidated');
    }
    cachedTokenByOrigin.set(target.cacheKey, parsed);
    return parsed.token;
  };

  const bootstrap = bootstrapWork().finally(() => {
    if (inFlightByOrigin.get(target.cacheKey)?.promise === bootstrap) {
      inFlightByOrigin.delete(target.cacheKey);
    }
  });
  inFlightByOrigin.set(target.cacheKey, {
    generation,
    scope,
    promise: bootstrap,
  });
  return bootstrap;
};

export const attachCsrfProtection = (client: AxiosInstance) => {
  client.interceptors.request.use(async (config) => {
    if (!isUnsafeMethod(config.method)) return config;
    const headers = AxiosHeaders.from(config.headers);
    const organizationId = headers.get(ORGANIZATION_HEADER_NAME);
    const token = await getCsrfToken(
      typeof organizationId === 'string' ? organizationId : null,
      axiosRequestTarget(config),
    );
    headers.set(CSRF_HEADER_NAME, token);
    config.headers = headers;
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    async (rawError: unknown) => {
      const error = rawError as AxiosError;
      if (!isFixedCsrfFailure(error)) return Promise.reject(rawError);

      invalidateCsrfToken();
      const config = error.config as RetryableRequestConfig | undefined;
      if (!config || config._csrfRetried || !isReplaySafe(config)) {
        return Promise.reject(rawError);
      }
      config._csrfRetried = true;
      return client.request(config);
    },
  );
};

const responseHasFixedCsrfFailure = async (response: Response) => {
  if (response.status !== 403) return false;
  try {
    const payload = await response.clone().json();
    return payload?.error?.code === CSRF_FAILURE_CODE;
  } catch {
    return false;
  }
};

const fetchReplaySafe = (method: string, headers: Headers) =>
  REPLAY_SAFE_METHODS.has(method) ||
  headers.has('Idempotency-Key') ||
  headers.has('X-Idempotency-Key');

export const csrfFetch = async (
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> => {
  const method = (init.method || 'GET').toUpperCase();
  if (!UNSAFE_METHODS.has(method)) return fetch(input, init);

  const send = async () => {
    const headers = new Headers(init.headers);
    const organizationId =
      headers.get(ORGANIZATION_HEADER_NAME) ?? getStoredActiveOrganizationId();
    if (organizationId && !headers.has(ORGANIZATION_HEADER_NAME)) {
      headers.set(ORGANIZATION_HEADER_NAME, organizationId);
    }
    headers.set(CSRF_HEADER_NAME, await getCsrfToken(organizationId, input));
    return fetch(input, {
      ...init,
      method,
      headers,
      credentials: init.credentials ?? 'include',
    });
  };

  const firstResponse = await send();
  if (!(await responseHasFixedCsrfFailure(firstResponse))) {
    return firstResponse;
  }

  invalidateCsrfToken();
  const headers = new Headers(init.headers);
  if (!fetchReplaySafe(method, headers)) return firstResponse;
  return send();
};

if (typeof window !== 'undefined') {
  if (ACTIVE_ORGANIZATION_CHANGED_EVENT) {
    window.addEventListener(
      ACTIVE_ORGANIZATION_CHANGED_EVENT,
      invalidateCsrfToken,
    );
  }
}
