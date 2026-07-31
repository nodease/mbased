'use client';

import { Plus, Trash2 } from 'lucide-react';

import { MAX_BROWSER_PARENT_ORIGINS } from '../../utils/browserAccessPolicy';

interface BrowserAccessPolicyEditorProps {
  enabled: boolean;
  parentOrigins: string[];
  validationError?: string | null;
  disabled?: boolean;
  headingId?: string;
  onEnabledChange: (enabled: boolean) => void;
  onParentOriginChange: (index: number, value: string) => void;
  onAddParentOrigin: () => void;
  onRemoveParentOrigin: (index: number) => void;
}

export function BrowserAccessPolicyEditor({
  enabled,
  parentOrigins,
  validationError,
  disabled = false,
  headingId = 'browser-access-policy-heading',
  onEnabledChange,
  onParentOriginChange,
  onAddParentOrigin,
  onRemoveParentOrigin,
}: BrowserAccessPolicyEditorProps) {
  return (
    <section
      className="border-t border-gray-200 pt-5"
      aria-labelledby={headingId}
    >
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 id={headingId} className="text-sm font-semibold text-gray-900">
            외부 사이트 삽입
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {enabled
              ? '허용 origin만 iframe 표시 가능'
              : 'iframe 표시 차단'}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="외부 사이트에 삽입 허용"
          disabled={disabled}
          onClick={() => onEnabledChange(!enabled)}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
            enabled ? 'bg-blue-600' : 'bg-gray-300'
          } disabled:cursor-not-allowed disabled:opacity-60`}
        >
          <span
            className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
              enabled ? 'translate-x-5' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>

      {enabled && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium text-gray-700">
              허용 부모 origin
            </label>
            <span className="text-xs text-gray-500">
              {parentOrigins.length}/{MAX_BROWSER_PARENT_ORIGINS}
            </span>
          </div>
          {parentOrigins.map((origin, index) => (
            <div className="flex items-center gap-2" key={index}>
              <input
                aria-label={`부모 origin ${index + 1}`}
                type="url"
                value={origin}
                disabled={disabled}
                onChange={(event) =>
                  onParentOriginChange(index, event.target.value)
                }
                placeholder="https://portal.example.com"
                className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                type="button"
                title="origin 제거"
                aria-label={`부모 origin ${index + 1} 제거`}
                disabled={disabled}
                onClick={() => onRemoveParentOrigin(index)}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-md text-gray-500 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={
              disabled || parentOrigins.length >= MAX_BROWSER_PARENT_ORIGINS
            }
            onClick={onAddParentOrigin}
            className="inline-flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:text-gray-400"
          >
            <Plus className="h-4 w-4" />
            origin 추가
          </button>
        </div>
      )}
      {validationError && (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {validationError}
        </p>
      )}
    </section>
  );
}
