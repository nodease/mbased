'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { toast } from 'sonner';

import {
  appApi,
  type AppAuthSecretStatus,
} from '@/app/features/app/api/appApi';

interface AppAuthSecretControlProps {
  appId: string;
  issuedSecret?: IssuedAppAuthSecret | null;
  onSecretAvailable?: (secret: IssuedAppAuthSecret | null) => void;
  onReadinessChange?: (readiness: AppAuthSecretReadiness) => void;
}

export interface IssuedAppAuthSecret {
  value: string;
  version: number;
}

export type AppAuthSecretReadiness =
  | 'checking'
  | 'ready'
  | 'secret_required'
  | 'lifecycle_unavailable'
  | 'status_unavailable';

function readinessOf(status: AppAuthSecretStatus): AppAuthSecretReadiness {
  if (status.configured) return 'ready';
  return status.rotation_enabled ? 'secret_required' : 'lifecycle_unavailable';
}

export function AppAuthSecretControl({
  appId,
  issuedSecret: controlledIssuedSecret,
  onSecretAvailable,
  onReadinessChange,
}: AppAuthSecretControlProps) {
  const [status, setStatus] = useState<AppAuthSecretStatus | null>(null);
  const [localIssuedSecret, setLocalIssuedSecret] =
    useState<IssuedAppAuthSecret | null>(null);
  const [showSecret, setShowSecret] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [revokeImmediately, setRevokeImmediately] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rotating, setRotating] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const controlledIssuedSecretRef = useRef(controlledIssuedSecret);
  const isIssuedSecretControlled = controlledIssuedSecret !== undefined;
  const issuedSecret = isIssuedSecretControlled
    ? controlledIssuedSecret
    : localIssuedSecret;
  const issuedSecretValue =
    issuedSecret && status?.version === issuedSecret.version
      ? issuedSecret.value
      : null;

  const updateIssuedSecret = useCallback(
    (secret: IssuedAppAuthSecret | null) => {
      if (!isIssuedSecretControlled) {
        setLocalIssuedSecret(secret);
      }
      onSecretAvailable?.(secret);
    },
    [isIssuedSecretControlled, onSecretAvailable],
  );

  useEffect(() => {
    controlledIssuedSecretRef.current = controlledIssuedSecret;
  }, [controlledIssuedSecret]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setUnavailable(false);
    setStatus(null);
    onReadinessChange?.('checking');
    if (!isIssuedSecretControlled) {
      updateIssuedSecret(null);
    }

    void appApi
      .getAuthSecretStatus(appId)
      .then((nextStatus) => {
        if (active) {
          const currentControlledIssuedSecret =
            controlledIssuedSecretRef.current;
          if (
            currentControlledIssuedSecret &&
            currentControlledIssuedSecret.version !== nextStatus.version
          ) {
            updateIssuedSecret(null);
            setShowSecret(false);
            setConfirming(false);
          }
          setStatus(nextStatus);
          onReadinessChange?.(readinessOf(nextStatus));
        }
      })
      .catch(() => {
        if (active) {
          setUnavailable(true);
          onReadinessChange?.('status_unavailable');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [
    appId,
    isIssuedSecretControlled,
    onReadinessChange,
    updateIssuedSecret,
  ]);

  const beginRotation = () => {
    setRevokeImmediately(false);
    setConfirming(true);
  };

  const rotateSecret = async () => {
    if (!status || rotating) return;
    const requestedVersion = status.version;
    setRotating(true);
    try {
      const result = await appApi.rotateAuthSecret(appId, {
        expected_version: requestedVersion,
        revoke_previous_immediately: revokeImmediately,
      });
      const nextStatus: AppAuthSecretStatus = {
        configured: true,
        version: result.version,
        rotation_enabled: true,
        rotated_at: result.rotated_at,
        previous_grace_active: result.previous_grace_active,
        previous_valid_until: result.previous_valid_until,
      };
      setStatus(nextStatus);
      onReadinessChange?.('ready');
      updateIssuedSecret({ value: result.secret, version: result.version });
      setShowSecret(true);
      setConfirming(false);
      toast.success('새 App secret이 발급되었습니다.');
    } catch {
      toast.error('Secret 상태가 변경되었습니다. 상태를 새로 확인해주세요.');
      try {
        const nextStatus = await appApi.getAuthSecretStatus(appId);
        setStatus(nextStatus);
        setUnavailable(false);
        onReadinessChange?.(readinessOf(nextStatus));
        if (nextStatus.version !== requestedVersion) {
          updateIssuedSecret(null);
          setShowSecret(false);
          setConfirming(false);
        }
      } catch {
        setUnavailable(true);
        onReadinessChange?.('status_unavailable');
      }
    } finally {
      setRotating(false);
    }
  };

  const copyText = async (value: string, message: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(message);
    } catch {
      toast.error('클립보드에 복사하지 못했습니다.');
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-10 items-center gap-2 text-xs text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Secret 상태 확인 중
      </div>
    );
  }

  if (unavailable || !status) {
    return (
      <p className="text-xs text-gray-500">Secret 상태를 확인할 수 없습니다.</p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
            <KeyRound className="h-3.5 w-3.5" />
            App Secret
          </div>
          <p className="mt-1 text-xs text-gray-500">
            {status.configured
              ? `발급됨 · 버전 ${status.version}`
              : '아직 발급되지 않음'}
          </p>
        </div>
        {status.rotation_enabled && !confirming && (
          <button
            type="button"
            onClick={beginRotation}
            className="inline-flex h-8 items-center gap-1.5 rounded border border-gray-300 bg-white px-2.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {status.configured ? '교체' : '발급'}
          </button>
        )}
      </div>

      {!status.rotation_enabled && (
        <p className="text-xs text-gray-500" role="status">
          Gateway 전환이 완료된 후 Secret을 발급할 수 있습니다.
        </p>
      )}

      {status.previous_grace_active && status.previous_valid_until && (
        <p className="text-xs text-amber-700" role="status">
          이전 secret은 전환 유예시간 동안만 사용할 수 있습니다.
        </p>
      )}

      {confirming && (
        <div className="space-y-3 border-l-2 border-amber-300 pl-3">
          <div className="flex gap-2 text-xs leading-5 text-gray-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <span>
              {status.configured
                ? '기본 교체는 이전 secret을 최대 5분 허용합니다.'
                : '원문은 발급 직후 한 번만 확인할 수 있습니다.'}
            </span>
          </div>
          {status.configured && (
            <label className="flex cursor-pointer items-start gap-2 text-xs text-gray-700">
              <input
                type="checkbox"
                checked={revokeImmediately}
                onChange={(event) => setRevokeImmediately(event.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              이전 secret 즉시 폐기
            </label>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={rotateSecret}
              disabled={rotating}
              className="inline-flex h-8 items-center gap-1.5 rounded bg-gray-900 px-3 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {rotating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {status.configured ? '교체 확인' : '발급 확인'}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={rotating}
              className="h-8 rounded border border-gray-300 bg-white px-3 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              취소
            </button>
          </div>
        </div>
      )}

      {issuedSecretValue && (
        <div className="space-y-2" role="status">
          <p className="text-xs font-medium text-red-700">
            지금 보관하세요. 이 값을 다시 조회할 수 없습니다.
          </p>
          <div className="flex items-center gap-1.5">
            <input
              type={showSecret ? 'text' : 'password'}
              value={issuedSecretValue}
              readOnly
              aria-label="새 App secret"
              className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2.5 py-2 font-mono text-xs text-gray-700"
            />
            <button
              type="button"
              onClick={() => setShowSecret((value) => !value)}
              className="rounded border border-gray-300 p-2 text-gray-600 hover:bg-gray-50"
              title={showSecret ? 'Secret 숨기기' : 'Secret 보기'}
            >
              {showSecret ? (
                <EyeOff className="h-3.5 w-3.5" />
              ) : (
                <Eye className="h-3.5 w-3.5" />
              )}
            </button>
            <button
              type="button"
              onClick={() =>
                void copyText(issuedSecretValue, 'Secret을 복사했습니다.')
              }
              className="rounded border border-gray-300 p-2 text-gray-600 hover:bg-gray-50"
              title="Secret 복사"
            >
              <Copy className="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            type="button"
            onClick={() =>
              void copyText(
                `Authorization: Bearer ${issuedSecretValue}`,
                'Authorization 헤더를 복사했습니다.',
              )
            }
            className="inline-flex h-8 items-center gap-1.5 rounded border border-gray-300 bg-white px-2.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            <Copy className="h-3.5 w-3.5" />
            Authorization 헤더 복사
          </button>
        </div>
      )}
    </div>
  );
}
