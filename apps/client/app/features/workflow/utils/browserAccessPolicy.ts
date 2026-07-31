import type { DeploymentBrowserAccessPolicy } from '../types/Deployment';

export const BROWSER_ACCESS_CONTRACT_VERSION =
  'deployment_browser_access.v1' as const;
export const MAX_BROWSER_PARENT_ORIGINS = 20;

const MAX_RAW_ORIGIN_BYTES = 512;
const DEVELOPMENT_ENVIRONMENTS = new Set([
  'development',
  'dev',
  'test',
  'testing',
]);

export type BrowserAccessPolicyDraftResult =
  | { policy: DeploymentBrowserAccessPolicy; error: null }
  | { policy: null; error: string };

export const disabledBrowserAccessPolicy =
  (): DeploymentBrowserAccessPolicy => ({
    contract_version: BROWSER_ACCESS_CONTRACT_VERSION,
    embedding: { enabled: false, parent_origins: [] },
  });

export const buildBrowserAccessPolicyDraft = (
  enabled: boolean,
  originInputs: string[],
  environment = process.env.NODE_ENV,
): BrowserAccessPolicyDraftResult => {
  if (!enabled) {
    return { policy: disabledBrowserAccessPolicy(), error: null };
  }

  const origins = originInputs.map((origin) => origin.trim()).filter(Boolean);
  if (origins.length === 0) {
    return { policy: null, error: '허용할 부모 origin을 1개 이상 입력하세요.' };
  }
  if (origins.length > MAX_BROWSER_PARENT_ORIGINS) {
    return {
      policy: null,
      error: `부모 origin은 최대 ${MAX_BROWSER_PARENT_ORIGINS}개까지 입력할 수 있습니다.`,
    };
  }

  const provisionalOrigins = new Set<string>();
  for (const origin of origins) {
    if (new TextEncoder().encode(origin).length > MAX_RAW_ORIGIN_BYTES) {
      return {
        policy: null,
        error: '각 부모 origin은 512 bytes 이하여야 합니다.',
      };
    }

    let parsed: URL;
    try {
      parsed = new URL(origin);
    } catch {
      return { policy: null, error: '유효한 origin 형식으로 입력하세요.' };
    }

    if (
      !['http:', 'https:'].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password ||
      parsed.pathname !== '/' ||
      parsed.search ||
      parsed.hash
    ) {
      return {
        policy: null,
        error: 'origin에는 경로, 인증 정보, query를 넣을 수 없습니다.',
      };
    }

    if (
      parsed.protocol === 'http:' &&
      !isAllowedDevelopmentHttp(parsed, environment)
    ) {
      return {
        policy: null,
        error: '운영 환경의 부모 origin은 HTTPS여야 합니다.',
      };
    }

    if (provisionalOrigins.has(parsed.origin)) {
      return { policy: null, error: '중복된 부모 origin을 제거하세요.' };
    }
    provisionalOrigins.add(parsed.origin);
  }

  return {
    policy: {
      contract_version: BROWSER_ACCESS_CONTRACT_VERSION,
      embedding: { enabled: true, parent_origins: origins },
    },
    error: null,
  };
};

export const browserAccessApiErrorMessage = (
  error: unknown,
  fallback = '브라우저 접근 정책을 저장하지 못했습니다.',
) => {
  const code = extractErrorCode(error);
  const messages: Record<string, string> = {
    'deployment.browser_access.not_supported':
      '공개 챗봇과 위젯 배포만 부모 origin 정책을 지원합니다.',
    'deployment.browser_access.unsupported_contract_version':
      '지원하지 않는 브라우저 접근 정책 버전입니다.',
    'deployment.browser_access.origins_required':
      '허용할 부모 origin을 1개 이상 입력하세요.',
    'deployment.browser_access.origin_limit_exceeded':
      '부모 origin은 최대 20개까지 입력할 수 있습니다.',
    'deployment.browser_access.invalid_origin':
      '유효한 HTTPS origin을 입력하세요.',
    'deployment.browser_access.duplicate_origin':
      '중복된 부모 origin을 제거하세요.',
    'deployment.browser_access.header_limit_exceeded':
      '허용 origin 목록이 보안 헤더 크기 제한을 초과했습니다.',
  };
  return (code && messages[code]) || fallback;
};

const isAllowedDevelopmentHttp = (url: URL, environment?: string) =>
  DEVELOPMENT_ENVIRONMENTS.has((environment || '').trim().toLowerCase()) &&
  ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname.toLowerCase());

const extractErrorCode = (error: unknown): string | null => {
  if (!error || typeof error !== 'object') return null;
  const response = (error as { response?: { data?: unknown } }).response;
  const data = response?.data;
  if (!data || typeof data !== 'object') return null;
  const detail = (data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== 'object') return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' ? code : null;
};
