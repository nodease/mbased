import { apiBaseUrl, publicApiClient } from '@/lib/apiClient';
import { invalidateCsrfToken } from '@/lib/csrfToken';
import { resolveSafeAuthReturnPath } from '@/lib/authReturn';
import {
  SignupRequest,
  SignupResponse,
  LoginRequest,
  LoginResponse,
} from '../types/auth';

export const authApi = {
  // 회원가입
  signup: async (data: SignupRequest): Promise<SignupResponse> => {
    const response = await publicApiClient.post('/auth/signup', data);
    invalidateCsrfToken();
    return response.data;
  },

  // 로그인
  login: async (data: LoginRequest): Promise<LoginResponse> => {
    const response = await publicApiClient.post('/auth/login', data);
    invalidateCsrfToken();
    return response.data;
  },

  // 로그아웃
  logout: async (): Promise<void> => {
    await publicApiClient.post('/auth/logout', {});
    invalidateCsrfToken();
  },

  // 현재 사용자 정보 조회
  me: async (): Promise<LoginResponse> => {
    const response = await publicApiClient.get('/auth/me');
    return response.data;
  },

  // 구글 OAuth 로그인
  googleLogin: (returnPath?: string | null) => {
    const safeReturnPath = resolveSafeAuthReturnPath(returnPath);
    invalidateCsrfToken();
    window.location.href = `${apiBaseUrl}/auth/google/login?next=${encodeURIComponent(safeReturnPath)}`;
  },
};
