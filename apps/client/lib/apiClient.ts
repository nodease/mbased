import axios from 'axios';
import { attachActiveOrganizationHeader } from './activeOrganization';
import { attachCsrfProtection } from './csrfToken';
import { claimLoginRedirectPath, getCurrentAuthReturnPath } from './authReturn';
import { resolvePublicApiBaseUrl } from './publicApiOrigin';

export const apiBaseUrl = resolvePublicApiBaseUrl(
  process.env.NEXT_PUBLIC_API_URL,
  process.env.NODE_ENV,
);

const createApiClient = () =>
  axios.create({
    baseURL: apiBaseUrl,
    withCredentials: true,
  });

const attachAuthRedirectInterceptor = (
  client: ReturnType<typeof createApiClient>,
) => {
  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response?.status === 401) {
        // 로그인/회원가입 페이지에서는 리다이렉트하지 않음 (에러 메시지를 보여주기 위해)
        if (
          typeof window !== 'undefined' &&
          !window.location.pathname.startsWith('/auth') &&
          window.location.pathname !== '/'
        ) {
          const redirectPath = claimLoginRedirectPath(
            getCurrentAuthReturnPath(),
          );
          if (redirectPath) window.location.href = redirectPath;
        }
      }
      return Promise.reject(error);
    },
  );
};

export const publicApiClient = createApiClient();

export const apiClient = createApiClient();

// Axios request interceptors run last-in-first-out. Register CSRF first so the
// active organization header exists before the scoped token is bootstrapped.
attachCsrfProtection(publicApiClient);
attachCsrfProtection(apiClient);
attachActiveOrganizationHeader(apiClient);

attachAuthRedirectInterceptor(publicApiClient);
attachAuthRedirectInterceptor(apiClient);
