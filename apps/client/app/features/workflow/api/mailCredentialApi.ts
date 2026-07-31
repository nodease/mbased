import { apiClient } from '@/lib/apiClient';

export interface MailCredentialOption {
  id: string;
  credential_name: string;
  provider: 'gmail' | 'naver' | 'daum' | 'outlook' | 'custom';
  auth_type: 'app_password' | 'password' | 'oauth2';
  email_preview: string;
  status: 'active' | 'revoked';
}

export const mailCredentialApi = {
  async listAvailable(): Promise<MailCredentialOption[]> {
    const response =
      await apiClient.get<MailCredentialOption[]>('/mail/credentials');
    return response.data;
  },
  async startGoogleOAuth(
    credentialName: string,
  ): Promise<{ authorization_url: string }> {
    const response = await apiClient.post<{ authorization_url: string }>(
      '/mail/credentials/oauth/google/start',
      { credential_name: credentialName },
    );
    return response.data;
  },
};
