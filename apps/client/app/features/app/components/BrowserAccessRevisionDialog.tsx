'use client';

import { Loader2, X } from 'lucide-react';
import { useMemo, useState } from 'react';

import { BrowserAccessPolicyEditor } from '../../workflow/components/deployment/BrowserAccessPolicyEditor';
import {
  browserAccessApiErrorMessage,
  buildBrowserAccessPolicyDraft,
} from '../../workflow/utils/browserAccessPolicy';
import { appApi, type Deployment } from '../api/appApi';

interface BrowserAccessRevisionDialogProps {
  deployment: Deployment;
  onClose: () => void;
  onCreated: (revision: Deployment) => void;
}

export function BrowserAccessRevisionDialog({
  deployment,
  onClose,
  onCreated,
}: BrowserAccessRevisionDialogProps) {
  const currentEmbedding = deployment.browser_access_policy?.embedding;
  const [enabled, setEnabled] = useState(Boolean(currentEmbedding?.enabled));
  const [parentOrigins, setParentOrigins] = useState<string[]>(
    currentEmbedding?.parent_origins.length
      ? [...currentEmbedding.parent_origins]
      : [''],
  );
  const [isSaving, setIsSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const draft = useMemo(
    () => buildBrowserAccessPolicyDraft(enabled, parentOrigins),
    [enabled, parentOrigins],
  );

  const handleSave = async () => {
    if (!draft.policy || isSaving) return;
    setIsSaving(true);
    setServerError(null);
    try {
      const revision = await appApi.createBrowserAccessRevision(deployment.id, {
        browser_access_policy: draft.policy,
        is_active: false,
      });
      setIsSaving(false);
      onCreated(revision);
    } catch (error) {
      setServerError(browserAccessApiErrorMessage(error));
      setIsSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="브라우저 접근 정책 새 버전"
      onClick={() => !isSaving && onClose()}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              브라우저 접근 정책 새 버전
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              원본 v{deployment.version} · 새 버전은 비활성 상태
            </p>
          </div>
          <button
            type="button"
            title="닫기"
            aria-label="브라우저 접근 정책 닫기"
            disabled={isSaving}
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-md text-gray-500 hover:bg-gray-100 disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="overflow-y-auto px-6 py-5">
          <BrowserAccessPolicyEditor
            headingId="revision-browser-access-heading"
            enabled={enabled}
            parentOrigins={parentOrigins}
            validationError={draft.error || serverError}
            disabled={isSaving}
            onEnabledChange={(nextEnabled) => {
              setEnabled(nextEnabled);
              setServerError(null);
              if (nextEnabled && parentOrigins.length === 0) {
                setParentOrigins(['']);
              }
            }}
            onParentOriginChange={(index, value) => {
              setServerError(null);
              setParentOrigins((current) =>
                current.map((origin, originIndex) =>
                  originIndex === index ? value : origin,
                ),
              );
            }}
            onAddParentOrigin={() => {
              setServerError(null);
              setParentOrigins((current) => [...current, '']);
            }}
            onRemoveParentOrigin={(index) => {
              setServerError(null);
              setParentOrigins((current) =>
                current.length === 1
                  ? ['']
                  : current.filter((_, originIndex) => originIndex !== index),
              );
            }}
          />
        </div>

        <footer className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">
          <button
            type="button"
            disabled={isSaving}
            onClick={onClose}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="button"
            disabled={isSaving || !draft.policy}
            onClick={handleSave}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-400"
          >
            {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}새 비활성
            버전 만들기
          </button>
        </footer>
      </div>
    </div>
  );
}
