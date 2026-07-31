const AUTH_RETURN_ORIGIN = 'https://nodease.local';
const MAX_AUTH_RETURN_PATH_LENGTH = 2048;
const MAX_AUTH_RETURN_DECODE_PASSES = 5;
const AUTH_REDIRECT_DEDUPLICATION_MS = 2000;
const INVALID_PERCENT_ESCAPE = /%(?![0-9a-fA-F]{2})/;

let lastClaimedRedirect: { path: string; claimedAt: number } | null = null;

const containsControlCharacter = (value: string): boolean =>
  Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127;
  });

export const DEFAULT_AUTH_RETURN_PATH = '/dashboard';

export const resolveSafeAuthReturnPath = (
  returnPath: string | null | undefined,
): string => {
  if (!returnPath || !returnPath.startsWith('/')) {
    return DEFAULT_AUTH_RETURN_PATH;
  }
  if (
    returnPath.length > MAX_AUTH_RETURN_PATH_LENGTH ||
    INVALID_PERCENT_ESCAPE.test(returnPath)
  ) {
    return DEFAULT_AUTH_RETURN_PATH;
  }

  try {
    const candidates = [returnPath];
    let decoded = returnPath;
    let stabilized = false;
    for (
      let iteration = 0;
      iteration < MAX_AUTH_RETURN_DECODE_PASSES;
      iteration += 1
    ) {
      const decodedOnce = decodeURIComponent(decoded);
      if (decodedOnce === decoded) {
        stabilized = true;
        break;
      }
      candidates.push(decodedOnce);
      decoded = decodedOnce;
    }
    if (!stabilized && decodeURIComponent(decoded) !== decoded) {
      return DEFAULT_AUTH_RETURN_PATH;
    }

    for (const candidate of candidates) {
      if (
        !candidate.startsWith('/') ||
        candidate.startsWith('//') ||
        candidate.includes('\\') ||
        containsControlCharacter(candidate)
      ) {
        return DEFAULT_AUTH_RETURN_PATH;
      }
      const candidateUrl = new URL(candidate, AUTH_RETURN_ORIGIN);
      if (candidateUrl.origin !== AUTH_RETURN_ORIGIN) {
        return DEFAULT_AUTH_RETURN_PATH;
      }
      const candidatePath = candidate.split(/[?#]/, 1)[0];
      if (
        candidatePath
          .split('/')
          .some((segment) => segment === '.' || segment === '..')
      ) {
        return DEFAULT_AUTH_RETURN_PATH;
      }
    }

    const resolved = new URL(returnPath, AUTH_RETURN_ORIGIN);
    const normalized = `${resolved.pathname}${resolved.search}${resolved.hash}`;
    const verified = new URL(normalized, AUTH_RETURN_ORIGIN);
    if (verified.origin !== AUTH_RETURN_ORIGIN) {
      return DEFAULT_AUTH_RETURN_PATH;
    }
    return `${verified.pathname}${verified.search}${verified.hash}`;
  } catch {
    return DEFAULT_AUTH_RETURN_PATH;
  }
};

export const getCurrentAuthReturnPath = (): string => {
  if (typeof window === 'undefined') {
    return DEFAULT_AUTH_RETURN_PATH;
  }

  return resolveSafeAuthReturnPath(
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
  );
};

export const buildLoginRedirectPath = (
  returnPath: string | null | undefined,
): string =>
  `/auth/login?next=${encodeURIComponent(resolveSafeAuthReturnPath(returnPath))}`;

export const claimLoginRedirectPath = (
  returnPath: string | null | undefined,
  now: number = Date.now(),
): string | null => {
  const path = buildLoginRedirectPath(returnPath);
  if (
    lastClaimedRedirect?.path === path &&
    now >= lastClaimedRedirect.claimedAt &&
    now - lastClaimedRedirect.claimedAt < AUTH_REDIRECT_DEDUPLICATION_MS
  ) {
    return null;
  }
  lastClaimedRedirect = { path, claimedAt: now };
  return path;
};
