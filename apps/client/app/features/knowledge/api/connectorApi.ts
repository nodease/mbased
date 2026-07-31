import { apiClient } from '@/lib/apiClient';
import { DBConfig } from '../types/DB';

const api = apiClient;

const getHttpStatus = (error: unknown): number | undefined => {
  if (typeof error !== 'object' || error === null) return undefined;
  const response = (error as { response?: { status?: unknown } }).response;
  return typeof response?.status === 'number' ? response.status : undefined;
};

const logConnectorApiFailure = (operation: string, error: unknown) => {
  console.warn('[connectorApi] request failed', {
    operation,
    status: getHttpStatus(error),
  });
};

const withStatus = (message: string, status?: number) =>
  status ? `${message} (HTTP ${status})` : message;

const SAFE_MESSAGE_BY_REASON_CODE: Record<string, string> = {
  'connector.connection_failed': 'DB 연결에 실패했습니다.',
  'connector.connection_timeout': 'DB 연결 확인 시간이 초과되었습니다.',
  'connector.target_not_allowed': '허용되지 않은 DB 연결 대상입니다.',
  'connector.ssh_probe_not_supported': 'SSH 연결 테스트는 지원하지 않습니다.',
  'connector.test_rate_limited': '연결 테스트 요청이 너무 많습니다.',
  'connector.test_busy': '연결 테스트 처리 용량이 사용 중입니다.',
  'connector.admission_unavailable':
    '연결 테스트 서비스를 일시적으로 사용할 수 없습니다.',
  'connector.test_payload_invalid':
    '연결 테스트 요청 형식이 올바르지 않습니다.',
  'connector.test_payload_timeout':
    '연결 테스트 요청 전송 시간이 초과되었습니다.',
  'connector.test_payload_too_large': '연결 테스트 요청 크기가 너무 큽니다.',
  'connector.test_media_type_not_supported':
    '연결 테스트 요청 형식이 지원되지 않습니다.',
  'connection_config.encrypt_failed': 'DB 연결 정보 암호화에 실패했습니다.',
};

const SAFE_BACKEND_MESSAGES = new Set([
  '데이터베이스 연결에 성공했습니다.',
  '데이터베이스 연결에 실패했습니다.',
  '연결 실패',
  '연결 정보가 안전하게 저장되었습니다.',
]);

const safeBackendMessage = (message: unknown, fallback: string): string => {
  if (typeof message !== 'string') return fallback;
  const trimmed = message.trim();
  return SAFE_BACKEND_MESSAGES.has(trimmed) ? trimmed : fallback;
};

const reasonCodeFromPayload = (payload: unknown): string | undefined => {
  if (typeof payload !== 'object' || payload === null) return undefined;
  const data = payload as {
    reason_code?: unknown;
    reasonCode?: unknown;
    detail?: unknown;
    error?: unknown;
  };
  if (
    typeof data.reason_code === 'string' &&
    data.reason_code in SAFE_MESSAGE_BY_REASON_CODE
  )
    return data.reason_code;
  if (
    typeof data.reasonCode === 'string' &&
    data.reasonCode in SAFE_MESSAGE_BY_REASON_CODE
  )
    return data.reasonCode;
  if (typeof data.detail === 'object' && data.detail !== null) {
    const detail = data.detail as {
      reason_code?: unknown;
      reasonCode?: unknown;
      error?: unknown;
    };
    if (
      typeof detail.reason_code === 'string' &&
      detail.reason_code in SAFE_MESSAGE_BY_REASON_CODE
    )
      return detail.reason_code;
    if (
      typeof detail.reasonCode === 'string' &&
      detail.reasonCode in SAFE_MESSAGE_BY_REASON_CODE
    )
      return detail.reasonCode;
    if (typeof detail.error === 'object' && detail.error !== null) {
      const code = (detail.error as { code?: unknown }).code;
      if (typeof code === 'string' && code in SAFE_MESSAGE_BY_REASON_CODE)
        return code;
    }
  }
  if (typeof data.error === 'object' && data.error !== null) {
    const code = (data.error as { code?: unknown }).code;
    if (typeof code === 'string' && code in SAFE_MESSAGE_BY_REASON_CODE)
      return code;
  }
  return undefined;
};

const responsePayload = (error: unknown): unknown => {
  if (typeof error !== 'object' || error === null) return undefined;
  return (error as { response?: { data?: unknown } }).response?.data;
};

const retryAfterFromError = (error: unknown): number | undefined => {
  if (typeof error !== 'object' || error === null) return undefined;
  const headers = (error as { response?: { headers?: unknown } }).response
    ?.headers;
  if (typeof headers !== 'object' || headers === null) return undefined;
  const candidate = headers as {
    get?: (name: string) => unknown;
    'retry-after'?: unknown;
    'Retry-After'?: unknown;
  };
  const rawValue =
    typeof candidate.get === 'function'
      ? candidate.get('retry-after')
      : (candidate['retry-after'] ?? candidate['Retry-After']);
  if (typeof rawValue !== 'string' || !/^\d+$/.test(rawValue)) return undefined;
  return Math.max(1, Math.min(60, Number(rawValue)));
};

const safeFailureMessage = (
  fallback: string,
  options: { status?: number; reasonCode?: string } = {},
) => {
  const { status, reasonCode } = options;
  return withStatus(
    (reasonCode && SAFE_MESSAGE_BY_REASON_CODE[reasonCode]) || fallback,
    status,
  );
};

type ConnectorCreationResult = {
  id: string;
  success: boolean;
  message: string;
  status?: number;
  reasonCode?: string;
};

export type ConnectionTestResult = {
  success: boolean;
  message: string;
  status?: number;
  reasonCode?: string;
  retryAfter?: number;
};

type DBConnectionDetailPayload = {
  connection_name?: unknown;
  type?: unknown;
  host?: unknown;
  port?: unknown;
  database?: unknown;
  username?: unknown;
  ssh?: unknown;
};

const requiredString = (value: unknown): string => {
  if (typeof value !== 'string') throw new Error('Invalid connector detail');
  return value;
};

const requiredPort = (value: unknown): number => {
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < 1 ||
    value > 65535
  )
    throw new Error('Invalid connector detail');
  return value;
};

const normalizeConnectionDetails = (payload: unknown): DBConfig => {
  if (typeof payload !== 'object' || payload === null)
    throw new Error('Invalid connector detail');

  const detail = payload as DBConnectionDetailPayload;
  if (detail.type !== 'postgres' && detail.type !== 'mysql')
    throw new Error('Invalid connector detail');

  const rawSsh =
    typeof detail.ssh === 'object' && detail.ssh !== null
      ? (detail.ssh as {
          enabled?: unknown;
          host?: unknown;
          port?: unknown;
          username?: unknown;
          auth_type?: unknown;
        })
      : null;
  const sshEnabled = rawSsh?.enabled === true;

  return {
    connectionName: requiredString(detail.connection_name),
    type: detail.type,
    host: requiredString(detail.host),
    port: requiredPort(detail.port),
    database: requiredString(detail.database),
    username: requiredString(detail.username),
    password: '',
    ssh: {
      enabled: sshEnabled,
      host: sshEnabled ? requiredString(rawSsh?.host) : '',
      port: sshEnabled ? requiredPort(rawSsh?.port) : 22,
      username: sshEnabled ? requiredString(rawSsh?.username) : '',
      authType: rawSsh?.auth_type === 'key' ? 'key' : 'password',
      password: '',
      privateKey: '',
    },
  };
};

type ConnectorDeletionResult = {
  success: boolean;
  status?: number;
};

export const connectorApi = {
  /**
   * DB 연결 정보 저장 및 Connector 생성 요청
   *
   * @param config - 사용자가 입력한 DB 연결 정보
   * @returns 생성된 커넥터 정보
   */
  createConnector: async (
    config: DBConfig,
  ): Promise<ConnectorCreationResult> => {
    try {
      const payload = {
        connection_name: config.connectionName.trim(),
        type: config.type,
        host: config.host,
        port: config.port,
        database: config.database,
        username: config.username,
        password: config.password,
        ssh: config.ssh?.enabled
          ? {
              enabled: true,
              host: config.ssh.host,
              port: config.ssh.port,
              username: config.ssh.username,
              auth_type: config.ssh.authType === 'key' ? 'key' : 'password',
              password: config.ssh.password,
              private_key: config.ssh.privateKey,
            }
          : null,
      };

      const response = await api.post('/connectors', payload);
      const id = typeof response.data?.id === 'string' ? response.data.id : '';
      const success = response.data?.success === true && Boolean(id);
      const reasonCode = reasonCodeFromPayload(response.data);
      if (!success) {
        return {
          id: '',
          success: false,
          message: safeFailureMessage('DB 연결 정보 저장에 실패했습니다.', {
            reasonCode,
          }),
          ...(reasonCode ? { reasonCode } : {}),
        };
      }
      return {
        id,
        success: true,
        message: safeBackendMessage(
          response.data?.message,
          'DB 연결 정보가 저장되었습니다.',
        ),
      };
    } catch (error) {
      const status = getHttpStatus(error);
      const reasonCode = reasonCodeFromPayload(responsePayload(error));
      logConnectorApiFailure('createConnector', error);
      return {
        id: '',
        success: false,
        message: safeFailureMessage('DB 연결 정보 저장에 실패했습니다.', {
          status,
          reasonCode,
        }),
        ...(status ? { status } : {}),
        ...(reasonCode ? { reasonCode } : {}),
      };
    }
  },

  /** Best-effort cleanup for a request-owned connector that was never linked. */
  deleteConnector: async (
    connectionId: string,
  ): Promise<ConnectorDeletionResult> => {
    try {
      await api.delete(`/connectors/${connectionId}`);
      return { success: true };
    } catch (error) {
      const status = getHttpStatus(error);
      logConnectorApiFailure('deleteConnector', error);
      return {
        success: false,
        ...(status ? { status } : {}),
      };
    }
  },

  /**
   * DB 연결 테스트
   * @param config - DB 연결 정보
   * @returns 성공 여부 및 메시지
   */
  testConnection: async (config: DBConfig): Promise<ConnectionTestResult> => {
    try {
      const payload = {
        connection_name: config.connectionName.trim(),
        type: config.type,
        host: config.host,
        port: config.port,
        database: config.database,
        username: config.username,
        password: config.password,
        ssh: config.ssh?.enabled
          ? {
              enabled: true,
              host: config.ssh.host,
              port: config.ssh.port,
              username: config.ssh.username,
              auth_type: config.ssh.authType === 'key' ? 'key' : 'password',
              password: config.ssh.password,
              private_key: config.ssh.privateKey,
            }
          : null,
      };

      const response = await api.post('/connectors/test', payload);
      const success = response.data?.success === true;
      const reasonCode = reasonCodeFromPayload(response.data);
      return {
        success,
        message: success
          ? safeBackendMessage(response.data?.message, 'DB 연결 테스트 성공')
          : safeFailureMessage('DB 연결에 실패했습니다.', { reasonCode }),
        ...(reasonCode ? { reasonCode } : {}),
      };
    } catch (error) {
      const status = getHttpStatus(error);
      const reasonCode = reasonCodeFromPayload(responsePayload(error));
      const retryAfter =
        status === 429 ? retryAfterFromError(error) : undefined;
      logConnectorApiFailure('testConnection', error);
      return {
        success: false,
        message: safeFailureMessage('DB 연결 테스트 중 오류가 발생했습니다.', {
          status,
          reasonCode,
        }),
        ...(status ? { status } : {}),
        ...(reasonCode ? { reasonCode } : {}),
        ...(retryAfter ? { retryAfter } : {}),
      };
    }
  },

  getSchema: async (connectionId: string): Promise<any> => {
    const response = await api.get(`/connectors/${connectionId}/schema`);
    return response.data;
  },

  /**
   * DB 연결 상세 정보 조회 (비밀번호 제외)
   *
   * @param connectedId - 연결 ID
   * @returns 저장된 DB연결 정보
   */
  getConnectionDetails: async (connectionId: string): Promise<DBConfig> => {
    const response = await api.get(`/connectors/${connectionId}`);
    return normalizeConnectionDetails(response.data);
  },
};
