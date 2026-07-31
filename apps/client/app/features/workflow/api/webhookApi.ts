import axios from 'axios';
import { attachActiveOrganizationHeader } from '@/lib/activeOrganization';
import { attachCsrfProtection } from '@/lib/csrfToken';

const API_BASE_URL = '/api/v1';

// Axios 인스턴스 생성
const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
});

attachCsrfProtection(api);
attachActiveOrganizationHeader(api);

// 401 에러 인터셉터
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      console.warn('Authentication expired, redirecting to login...');
      window.location.href = '/auth/login';
    }
    return Promise.reject(error);
  },
);

export interface CaptureStatusResponse {
  status: 'waiting' | 'captured';
  capture_id?: string;
  expires_at?: string;
  payload?: unknown | null;
  payload_redacted?: boolean;
}

export interface CaptureStartResponse {
  status: 'waiting';
  capture_id: string;
  expires_at: string;
  message?: string;
}

export interface CaptureCancelResponse {
  status: 'cancelled';
}

export const webhookApi = {
  /**
   * 캡처 세션 시작
   */
  startCapture: async (urlSlug: string): Promise<CaptureStartResponse> => {
    const response = await api.get(`/hooks/${urlSlug}/capture/start`);
    return response.data;
  },

  /**
   * 캡처 상태 조회
   */
  getCaptureStatus: async (
    urlSlug: string,
    captureId: string,
  ): Promise<CaptureStatusResponse> => {
    const response = await api.get(`/hooks/${urlSlug}/capture/status`, {
      params: { capture_id: captureId },
    });
    return response.data;
  },

  /**
   * 캡처 세션 취소
   */
  cancelCapture: async (
    urlSlug: string,
    captureId: string,
  ): Promise<CaptureCancelResponse> => {
    const response = await api.post(`/hooks/${urlSlug}/capture/cancel`, null, {
      params: { capture_id: captureId },
    });
    return response.data;
  },
};
