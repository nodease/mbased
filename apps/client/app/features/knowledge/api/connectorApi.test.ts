import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    delete: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { connectorApi } from './connectorApi';
import { apiClient } from '@/lib/apiClient';
import type { DBConfig } from '../types/DB';

const dbConfig: DBConfig = {
  connectionName: 'demo-db',
  type: 'postgres',
  host: 'db.internal',
  port: 5432,
  database: 'demo',
  username: 'demo-user',
  password: 'placeholder-password',
  ssh: {
    enabled: true,
    host: 'bastion.internal',
    port: 22,
    username: 'ssh-user',
    authType: 'key',
    password: 'placeholder-ssh-password',
    privateKey: 'placeholder-private-key',
  },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('connectorApi safe failure handling', () => {
  it('trims the connection name before create and test requests', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({
        data: { id: 'connection-1', success: true },
      })
      .mockResolvedValueOnce({
        data: { success: true },
      });
    const config = { ...dbConfig, connectionName: '  demo-db  ' };

    await connectorApi.createConnector(config);
    await connectorApi.testConnection(config);

    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/connectors',
      expect.objectContaining({ connection_name: 'demo-db' }),
    );
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/connectors/test',
      expect.objectContaining({ connection_name: 'demo-db' }),
    );
  });

  it('normalizes successful connector creation responses to safe fields', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'connection-1',
        success: true,
        message: 'raw-backend-success-message-should-not-be-shown',
        host: 'raw-host-should-not-be-returned',
      },
    });

    const result = await connectorApi.createConnector(dbConfig);

    expect(result).toEqual({
      id: 'connection-1',
      success: true,
      message: 'DB 연결 정보가 저장되었습니다.',
    });
    expect(result).not.toHaveProperty('host');
  });

  it('does not log raw createConnector errors or return raw response details', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 500,
        data: { detail: 'raw-response-payload-should-not-be-logged' },
      },
      config: {
        data: 'request-config-should-not-be-logged',
      },
    };
    vi.mocked(apiClient.post).mockRejectedValueOnce(error);

    const result = await connectorApi.createConnector(dbConfig);

    expect(result).toEqual({
      id: '',
      success: false,
      message: 'DB 연결 정보 저장에 실패했습니다. (HTTP 500)',
      status: 500,
    });
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'createConnector',
      status: 500,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('maps safe connector failure reason codes without exposing raw details', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 400,
        data: {
          detail: {
            reason_code: 'connector.connection_failed',
            diagnostic: 'raw-diagnostic-should-not-be-logged',
          },
        },
      },
    };
    vi.mocked(apiClient.post).mockRejectedValueOnce(error);

    const result = await connectorApi.createConnector(dbConfig);

    expect(result).toEqual({
      id: '',
      success: false,
      message: 'DB 연결에 실패했습니다. (HTTP 400)',
      status: 400,
      reasonCode: 'connector.connection_failed',
    });
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'createConnector',
      status: 400,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('drops unknown connector reason codes from client results', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 400,
        data: {
          detail: {
            reason_code: 'raw.internal.debug_code',
            diagnostic: 'raw-diagnostic-should-not-be-logged',
          },
        },
      },
    };
    vi.mocked(apiClient.post).mockRejectedValueOnce(error);

    const result = await connectorApi.createConnector(dbConfig);

    expect(result).toEqual({
      id: '',
      success: false,
      message: 'DB 연결 정보 저장에 실패했습니다. (HTTP 400)',
      status: 400,
    });
    expect(result).not.toHaveProperty('reasonCode');
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'createConnector',
      status: 400,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('does not expose backend messages from failed connection tests', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        success: false,
        message: 'raw-response-payload-should-not-be-shown',
      },
    });

    const result = await connectorApi.testConnection(dbConfig);

    expect(result).toEqual({
      success: false,
      message: 'DB 연결에 실패했습니다.',
    });
  });

  it('deletes a request-owned connector without returning backend payloads', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce({
      data: { diagnostic: 'not-returned' },
    });

    const result = await connectorApi.deleteConnector('connection-1');

    expect(apiClient.delete).toHaveBeenCalledWith('/connectors/connection-1');
    expect(result).toEqual({ success: true });
  });

  it('reports connector cleanup failure without logging raw response details', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 503,
        data: { detail: 'raw-response-payload-should-not-be-logged' },
      },
    };
    vi.mocked(apiClient.delete).mockRejectedValueOnce(error);

    const result = await connectorApi.deleteConnector('connection-1');

    expect(result).toEqual({ success: false, status: 503 });
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'deleteConnector',
      status: 503,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('normalizes successful connection test responses to a safe message', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        success: true,
        message: 'raw-backend-success-message-should-not-be-shown',
      },
    });

    const result = await connectorApi.testConnection(dbConfig);

    expect(result).toEqual({
      success: true,
      message: 'DB 연결 테스트 성공',
    });
  });

  it('does not log raw testConnection errors or return raw response details', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 403,
        data: { message: 'raw-response-payload-should-not-be-logged' },
      },
      config: {
        data: 'request-config-should-not-be-logged',
      },
    };
    vi.mocked(apiClient.post).mockRejectedValueOnce(error);

    const result = await connectorApi.testConnection(dbConfig);

    expect(result).toEqual({
      success: false,
      message: 'DB 연결 테스트 중 오류가 발생했습니다. (HTTP 403)',
      status: 403,
    });
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'testConnection',
      status: 403,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('maps standard error envelopes and bounds Retry-After cooldown', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(apiClient.post).mockRejectedValueOnce({
      response: {
        status: 429,
        headers: { 'retry-after': '999' },
        data: {
          error: {
            code: 'connector.test_rate_limited',
            message: 'raw-backend-message',
            details: { target: 'raw-target' },
          },
        },
      },
    });

    const result = await connectorApi.testConnection(dbConfig);

    expect(result).toEqual({
      success: false,
      message: '연결 테스트 요청이 너무 많습니다. (HTTP 429)',
      status: 429,
      reasonCode: 'connector.test_rate_limited',
      retryAfter: 60,
    });
    expect(warnSpy).toHaveBeenCalledWith('[connectorApi] request failed', {
      operation: 'testConnection',
      status: 429,
    });
  });

  it.each(['-1', '1.5', 'date-value', ''])(
    'ignores malformed Retry-After value %s',
    async (retryAfter) => {
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      vi.mocked(apiClient.post).mockRejectedValueOnce({
        response: {
          status: 429,
          headers: { 'retry-after': retryAfter },
          data: { error: { code: 'connector.test_busy' } },
        },
      });

      const result = await connectorApi.testConnection(dbConfig);

      expect(result.retryAfter).toBeUndefined();
      expect(result.reasonCode).toBe('connector.test_busy');
    },
  );

  it('raises a zero Retry-After value to the one-second minimum', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(apiClient.post).mockRejectedValueOnce({
      response: {
        status: 429,
        headers: { 'retry-after': '0' },
        data: { error: { code: 'connector.test_busy' } },
      },
    });

    const result = await connectorApi.testConnection(dbConfig);

    expect(result.retryAfter).toBe(1);
  });

  it('normalizes snake-case connection details before opening the edit form', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        id: 'connection-1',
        connection_name: 'existing-db',
        type: 'postgres',
        host: 'db.internal',
        port: 5432,
        database: 'app',
        username: 'app-user',
        ssh: {
          enabled: true,
          host: 'bastion.internal',
          port: 22,
          username: 'ssh-user',
          auth_type: 'key',
        },
      },
    });

    const result = await connectorApi.getConnectionDetails('connection-1');

    expect(result).toEqual({
      connectionName: 'existing-db',
      type: 'postgres',
      host: 'db.internal',
      port: 5432,
      database: 'app',
      username: 'app-user',
      password: '',
      ssh: {
        enabled: true,
        host: 'bastion.internal',
        port: 22,
        username: 'ssh-user',
        authType: 'key',
        password: '',
        privateKey: '',
      },
    });
    expect(result).not.toHaveProperty('connection_name');
    expect(result.ssh).not.toHaveProperty('auth_type');
  });

  it('does not pass raw backend fields through successful HTTP failures', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        success: false,
        message: 'raw-driver-message',
        reason_code: 'connector.target_not_allowed',
        host: 'raw-target',
      },
    });

    const result = await connectorApi.testConnection(dbConfig);

    expect(result).toEqual({
      success: false,
      message: '허용되지 않은 DB 연결 대상입니다.',
      reasonCode: 'connector.target_not_allowed',
    });
    expect(result).not.toHaveProperty('host');
  });
});
