import { NextRequest, NextResponse } from 'next/server';

const CONTRACT_VERSION = 'deployment_browser_access.v1';
const DISABLED_FRAME_ANCESTORS = "frame-ancestors 'none'";
const MAX_PARENT_ORIGINS = 20;
const MAX_ORIGIN_BYTES = 512;
const MAX_CSP_BYTES = 4096;
const GATEWAY_TIMEOUT_MS = 1_000;
const EMBED_CHAT_PATH = /^\/embed\/chat\/([a-z0-9-]{1,255})\/?$/;
const DEVELOPMENT_ENVIRONMENTS = new Set([
  'development',
  'dev',
  'test',
  'testing',
]);

type BrowserAccessProjection = {
  contract_version: typeof CONTRACT_VERSION;
  deployment_version: number;
  embedding: {
    enabled: boolean;
    frame_ancestors: string[];
  };
};

const hasExactKeys = (value: object, keys: string[]) => {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const byteLength = (value: string) => new TextEncoder().encode(value).length;

const isDevelopmentEnvironment = () =>
  DEVELOPMENT_ENVIRONMENTS.has(
    (process.env.NODE_ENV || '').trim().toLowerCase(),
  );

const isCanonicalOrigin = (origin: string) => {
  if (!origin || byteLength(origin) > MAX_ORIGIN_BYTES) return false;

  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return false;
  }

  if (
    parsed.origin !== origin ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash
  ) {
    return false;
  }

  if (parsed.protocol === 'https:') return true;
  if (parsed.protocol !== 'http:' || !isDevelopmentEnvironment()) return false;

  return ['localhost', '127.0.0.1', '[::1]'].includes(
    parsed.hostname.toLowerCase(),
  );
};

const parseProjection = (value: unknown): BrowserAccessProjection | null => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'contract_version',
      'deployment_version',
      'embedding',
    ]) ||
    value.contract_version !== CONTRACT_VERSION ||
    !Number.isInteger(value.deployment_version) ||
    Number(value.deployment_version) < 1 ||
    !isRecord(value.embedding) ||
    !hasExactKeys(value.embedding, ['enabled', 'frame_ancestors']) ||
    typeof value.embedding.enabled !== 'boolean' ||
    !Array.isArray(value.embedding.frame_ancestors)
  ) {
    return null;
  }

  const origins = value.embedding.frame_ancestors;
  if (
    origins.length > MAX_PARENT_ORIGINS ||
    !origins.every((origin): origin is string => typeof origin === 'string') ||
    origins.some((origin) => !isCanonicalOrigin(origin)) ||
    new Set(origins).size !== origins.length ||
    origins.some(
      (origin, index) => index > 0 && origins[index - 1] >= origin,
    ) ||
    (value.embedding.enabled ? origins.length === 0 : origins.length !== 0)
  ) {
    return null;
  }

  return value as BrowserAccessProjection;
};

const normalizeGatewayUrl = (value: string) =>
  value.replace(/\/+$/, '').replace(/\/api\/v1$/i, '');

const gatewayBaseUrl = () =>
  normalizeGatewayUrl(process.env.API_URL?.trim() || 'http://127.0.0.1:8000');

const fetchFrameAncestors = async (slug: string): Promise<string> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), GATEWAY_TIMEOUT_MS);

  try {
    const response = await fetch(
      `${gatewayBaseUrl()}/api/v1/deployments/public/${slug}/browser-access`,
      {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      },
    );
    if (!response.ok) return DISABLED_FRAME_ANCESTORS;

    const projection = parseProjection(await response.json());
    if (!projection?.embedding.enabled) return DISABLED_FRAME_ANCESTORS;

    const csp = `frame-ancestors ${projection.embedding.frame_ancestors.join(' ')}`;
    return byteLength(csp) <= MAX_CSP_BYTES ? csp : DISABLED_FRAME_ANCESTORS;
  } catch {
    return DISABLED_FRAME_ANCESTORS;
  } finally {
    clearTimeout(timeout);
  }
};

export async function proxy(request: NextRequest) {
  const response = NextResponse.next();
  response.headers.set('Cache-Control', 'no-store');

  const match = EMBED_CHAT_PATH.exec(request.nextUrl.pathname);
  const csp = match
    ? await fetchFrameAncestors(match[1])
    : DISABLED_FRAME_ANCESTORS;
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  matcher: '/embed/:path*',
};
