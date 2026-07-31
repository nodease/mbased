import { useEffect, useState } from 'react';
import {
  Shield,
  Key,
  CheckCircle,
  AlertCircle,
  Loader2,
  Play,
} from 'lucide-react';
import {
  SUPPORTED_DB_TYPES,
  SupportedDbType,
  DBConfig,
} from '@/app/features/knowledge/types/DB';
import type { ConnectionTestResult } from '../../api/connectorApi';

// 타입 정의

interface Props {
  onChange: (config: DBConfig) => void;
  onTestConnection: (
    config: DBConfig,
  ) => Promise<Pick<ConnectionTestResult, 'success' | 'retryAfter'>>;
  initialConfig?: Partial<DBConfig>; // DB 연결 수정 원래 값 보여주기 용도
}

export default function DBConnectionForm({
  onChange,
  onTestConnection,
  initialConfig,
}: Props) {
  // 상태 관리
  const [loading, setLoading] = useState(false);
  const [retryAfter, setRetryAfter] = useState(0);
  const [testStatus, setTestStatus] = useState<'idle' | 'success' | 'error'>(
    'idle',
  );
  const [config, setConfig] = useState<DBConfig>({
    connectionName: initialConfig?.connectionName || '',
    type: initialConfig?.type || 'postgres',
    host: initialConfig?.host || '',
    port: initialConfig?.port || 5432,
    database: initialConfig?.database || '',
    username: initialConfig?.username || '',
    password: '',
    ssh: {
      enabled: initialConfig?.ssh?.enabled ?? false,
      host: initialConfig?.ssh?.host || '',
      port: initialConfig?.ssh?.port || 22,
      username: initialConfig?.ssh?.username || '',
      authType: initialConfig?.ssh?.authType || 'password',
      password: '',
      privateKey: '',
    },
  });

  useEffect(() => {
    if (retryAfter <= 0) return;
    const timer = window.setTimeout(
      () => setRetryAfter((seconds) => Math.max(0, seconds - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [retryAfter]);

  // 입력 핸들러 (중첩 객체 업데이트 유틸리티 필요)
  const handleChange = (field: string, value: any, isSsh = false) => {
    const newConfig = { ...config };

    if (isSsh) {
      newConfig.ssh = { ...config.ssh, [field]: value };
    } else {
      (newConfig as any)[field] = value;
    }

    setConfig(newConfig); // 로컬 상태 업데이트
    onChange(newConfig); // 부모 상태 업데이트
    setTestStatus('idle'); // 수정 시 테스트 상태 초기화
    return newConfig;
  };

  // 키 파일 읽기 핸들러
  const handleKeyFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        handleChange('privateKey', event.target?.result as string, true);
      };
      reader.readAsText(file);
    }
  };

  // 연결 테스트 핸들러
  const handleTest = async () => {
    setLoading(true);
    setTestStatus('idle');
    try {
      const result = await onTestConnection(config);
      setTestStatus(result.success ? 'success' : 'error');
      setRetryAfter(result.retryAfter ?? 0);
    } catch {
      setTestStatus('error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* 1. 기본 연결 정보 */}
      <div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
              연결 이름 (식별용) <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              placeholder="예: 운영 DB"
              value={config.connectionName}
              onChange={(e) => handleChange('connectionName', e.target.value)}
              maxLength={100}
              required
            />
          </div>

          <div>
            <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
              데이터베이스 타입
            </label>
            <select
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              value={config.type}
              onChange={(e) =>
                handleChange('type', e.target.value as SupportedDbType)
              }
            >
              {SUPPORTED_DB_TYPES.map((db) => (
                <option key={db.value} value={db.value} disabled={db.disabled}>
                  {db.label}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                호스트 이름
              </label>
              <input
                type="text"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                placeholder="db.example.com"
                value={config.host}
                onChange={(e) => handleChange('host', e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                포트
              </label>
              <input
                type="number"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                value={config.port}
                onChange={(e) => handleChange('port', Number(e.target.value))}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
              데이터베이스 이름
            </label>
            <input
              type="text"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              placeholder="postgres"
              value={config.database}
              onChange={(e) => handleChange('database', e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                계정 아이디
              </label>
              <input
                type="text"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                value={config.username}
                onChange={(e) => handleChange('username', e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                비밀번호
              </label>
              <input
                type="password"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                value={config.password}
                onChange={(e) => handleChange('password', e.target.value)}
              />
            </div>
          </div>
        </div>
      </div>

      {/* 2. SSH 터널링 섹션 */}
      <div className="pt-2">
        <div className="flex items-center mb-2">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              className="toggle checkbox-primary"
              checked={config.ssh.enabled}
              onChange={(e) => handleChange('enabled', e.target.checked, true)}
            />
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4" /> SSH 사용
            </div>
          </label>
        </div>

        {config.ssh.enabled && (
          <div className="bg-gray-50 dark:bg-gray-800/50 p-4 rounded-lg space-y-3 border border-gray-200 dark:border-gray-700">
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                  SSH 호스트 주소
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  placeholder="bastion.example.com"
                  value={config.ssh.host}
                  onChange={(e) => handleChange('host', e.target.value, true)}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                  SSH 포트
                </label>
                <input
                  type="number"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  value={config.ssh.port}
                  onChange={(e) =>
                    handleChange('port', Number(e.target.value), true)
                  }
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                SSH 계정 아이디
              </label>
              <input
                type="text"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                value={config.ssh.username}
                onChange={(e) => handleChange('username', e.target.value, true)}
              />
            </div>

            <div>
              <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                인증 방식
              </label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="authType"
                    checked={config.ssh.authType === 'password'}
                    onChange={() => handleChange('authType', 'password', true)}
                    className="text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-xs text-gray-700 dark:text-gray-300">
                    Password
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="authType"
                    checked={config.ssh.authType === 'key'}
                    onChange={() => handleChange('authType', 'key', true)}
                    className="text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-xs text-gray-700 dark:text-gray-300">
                    Private Key (RSA)
                  </span>
                </label>
              </div>
            </div>

            {config.ssh.authType === 'password' ? (
              <div>
                <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                  SSH 비밀번호
                </label>
                <input
                  type="password"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  value={config.ssh.password || ''}
                  onChange={(e) =>
                    handleChange('password', e.target.value, true)
                  }
                />
              </div>
            ) : (
              <div>
                <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
                  개인 키 파일 (Private Key)
                </label>
                <div className="flex items-center gap-2">
                  <label className="flex-1 cursor-pointer">
                    <div className="flex items-center justify-center w-full px-3 py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg hover:bg-white dark:hover:bg-gray-700 transition-colors bg-white dark:bg-gray-700">
                      <Key className="w-4 h-4 mr-2 text-gray-500" />
                      <span className="text-xs text-gray-500">
                        {config.ssh.privateKey ? '키 파일 로드됨' : '파일 선택'}
                      </span>
                    </div>
                    <input
                      type="file"
                      className="hidden"
                      onChange={handleKeyFileUpload}
                    />
                  </label>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 3. 테스트 버튼 */}
      <div className="pt-2">
        <button
          type="button"
          onClick={handleTest}
          disabled={loading || retryAfter > 0}
          className="w-full py-3 px-4 border-2 border-blue-100 dark:border-blue-900/30 hover:border-blue-500 dark:hover:border-blue-400 bg-blue-50 dark:bg-blue-900/10 text-blue-600 dark:text-blue-400 rounded-xl transition-all flex items-center justify-center gap-2 font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Play className="w-4 h-4 fill-current" />
          )}
          {retryAfter > 0 ? `${retryAfter}초 후 재시도` : '연결 테스트'}
        </button>

        {/* 상태 메시지 */}
        <div className="flex items-center justify-center gap-2 mt-3 min-h-[20px]">
          {testStatus === 'success' && (
            <span className="text-sm text-green-600 flex items-center gap-1.5 font-medium animate-in fade-in slide-in-from-top-1">
              <CheckCircle className="w-4 h-4" /> 연결 성공!
            </span>
          )}
          {testStatus === 'error' && (
            <span className="text-sm text-red-600 flex items-center gap-1.5 font-medium animate-in fade-in slide-in-from-top-1">
              <AlertCircle className="w-4 h-4" /> 연결 실패
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
