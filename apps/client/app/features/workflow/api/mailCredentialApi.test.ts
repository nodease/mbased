import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
  },
}));

import { apiClient } from '@/lib/apiClient';
import { mailCredentialApi } from './mailCredentialApi';

describe('mailCredentialApi', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('safe Mail credential option 목록만 반환한다', async () => {
    const options = [
      {
        id: 'credential-1',
        credential_name: '업무 메일',
        provider: 'gmail' as const,
        email_preview: 'm***@example.com',
        status: 'active' as const,
      },
    ];
    vi.mocked(apiClient.get).mockResolvedValue({ data: options });

    await expect(mailCredentialApi.listAvailable()).resolves.toEqual(options);
    expect(apiClient.get).toHaveBeenCalledWith('/mail/credentials');
    expect(options[0]).not.toHaveProperty('password');
    expect(options[0]).not.toHaveProperty('secret');
    expect(options[0]).not.toHaveProperty('encrypted_secret');
    expect(options[0]).not.toHaveProperty('imap_host');
  });
});
