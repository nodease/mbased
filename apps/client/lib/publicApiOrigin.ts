const LOCAL_ENVIRONMENTS = new Set(['development', 'local', 'test']);
const INTERNAL_HOST_SUFFIXES = [
  '.cluster.local',
  '.home.arpa',
  '.internal',
  '.local',
  '.svc',
];

const configurationError = () =>
  new Error(
    'NEXT_PUBLIC_API_URL must be an origin-only public HTTPS URL in production',
  );

const normalizedHostname = (hostname: string) =>
  hostname
    .trim()
    .toLowerCase()
    .replace(/^\[(.*)\]$/, '$1')
    .replace(/\.+$/, '');

const isLoopbackHostname = (hostname: string) => {
  const host = normalizedHostname(hostname);
  if (host === 'localhost' || host.endsWith('.localhost') || host === '::1') {
    return true;
  }
  const octets = host.split('.').map(Number);
  return (
    octets.length === 4 &&
    octets.every(
      (value) => Number.isInteger(value) && value >= 0 && value <= 255,
    ) &&
    octets[0] === 127
  );
};

const isPrivateOrInternalHostname = (hostname: string) => {
  const host = normalizedHostname(hostname);
  if (
    isLoopbackHostname(host) ||
    host === '0.0.0.0' ||
    INTERNAL_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))
  ) {
    return true;
  }

  const octets = host.split('.').map(Number);
  if (
    octets.length !== 4 ||
    !octets.every(
      (value) => Number.isInteger(value) && value >= 0 && value <= 255,
    )
  ) {
    // Literal IPv6 and single-label DNS names are not accepted as browser
    // production origins. Public deployments must use a routable DNS name.
    return host.includes(':') || !host.includes('.');
  }

  const [first, second] = octets;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    (first === 100 && second >= 64 && second <= 127) ||
    (first === 169 && second === 254) ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 0) ||
    (first === 192 && second === 168) ||
    (first === 198 && (second === 18 || second === 19)) ||
    (first === 198 && second === 51 && octets[2] === 100) ||
    (first === 203 && second === 0 && octets[2] === 113) ||
    first >= 224
  );
};

export const resolvePublicApiBaseUrl = (
  rawValue: string | undefined,
  nodeEnv: string | undefined,
) => {
  const value = rawValue?.trim();
  if (!value) return '/api/v1';

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw configurationError();
  }

  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    !parsed.hostname ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash ||
    parsed.hostname.includes('*') ||
    parsed.hostname.endsWith('.')
  ) {
    throw configurationError();
  }

  const environment = nodeEnv?.trim().toLowerCase() ?? '';
  const isLocalEnvironment = LOCAL_ENVIRONMENTS.has(environment);
  if (parsed.protocol === 'http:' && !isLocalEnvironment) {
    throw configurationError();
  }
  if (parsed.protocol === 'http:' && !isLoopbackHostname(parsed.hostname)) {
    throw configurationError();
  }
  const isDevelopmentLoopbackHttp =
    isLocalEnvironment &&
    parsed.protocol === 'http:' &&
    isLoopbackHostname(parsed.hostname);
  if (
    isPrivateOrInternalHostname(parsed.hostname) &&
    !isDevelopmentLoopbackHttp
  ) {
    throw configurationError();
  }

  return `${parsed.origin}/api/v1`;
};
