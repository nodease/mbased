'use client';

import { Loader2 } from 'lucide-react';

import {
  AppAuthSecretControl,
  type AppAuthSecretReadiness,
  type IssuedAppAuthSecret,
} from '@/app/features/app/components/AppAuthSecretControl';

import type {
  DeploymentBrowserAccessPolicy,
  DeploymentType,
} from '../../types/Deployment';
import { buildBrowserAccessPolicyDraft } from '../../utils/browserAccessPolicy';
import { BrowserAccessPolicyEditor } from './BrowserAccessPolicyEditor';

interface InputStepProps {
  appId?: string;
  issuedSecret: IssuedAppAuthSecret | null;
  onSecretAvailable: (secret: IssuedAppAuthSecret | null) => void;
  appAuthSecretReadiness: AppAuthSecretReadiness;
  onAppAuthSecretReadinessChange: (readiness: AppAuthSecretReadiness) => void;
  deploymentType: DeploymentType;
  deploymentTypeLabel: string;
  description: string;
  onDescriptionChange: (value: string) => void;
  llmNodes: Array<{ id: string; title: string }>;
  conversationHistoryConsumerNodeId: string;
  onConversationHistoryConsumerNodeIdChange: (nodeId: string) => void;
  embeddingEnabled: boolean;
  parentOrigins: string[];
  onEmbeddingEnabledChange: (enabled: boolean) => void;
  onParentOriginChange: (index: number, value: string) => void;
  onAddParentOrigin: () => void;
  onRemoveParentOrigin: (index: number) => void;
  onCancel: () => void;
  onSubmit: (policy?: DeploymentBrowserAccessPolicy) => void;
  isDeploying: boolean;
  submitLabel?: string;
}

export function InputStep({
  appId,
  issuedSecret,
  onSecretAvailable,
  appAuthSecretReadiness,
  onAppAuthSecretReadinessChange,
  deploymentType,
  deploymentTypeLabel,
  description,
  onDescriptionChange,
  llmNodes,
  conversationHistoryConsumerNodeId,
  onConversationHistoryConsumerNodeIdChange,
  embeddingEnabled,
  parentOrigins,
  onEmbeddingEnabledChange,
  onParentOriginChange,
  onAddParentOrigin,
  onRemoveParentOrigin,
  onCancel,
  onSubmit,
  isDeploying,
  submitLabel = '배포',
}: InputStepProps) {
  const supportsEmbeddingPolicy = ['chatbot', 'widget'].includes(
    deploymentType,
  );
  const requiresAppAuthSecret = ['api', 'webhook'].includes(deploymentType);
  const appAuthSecretBlocked =
    requiresAppAuthSecret && (!appId || appAuthSecretReadiness !== 'ready');
  const conversationConsumerBlocked =
    deploymentType === 'chatbot' && !conversationHistoryConsumerNodeId;
  const policyResult = supportsEmbeddingPolicy
    ? buildBrowserAccessPolicyDraft(embeddingEnabled, parentOrigins)
    : null;
  const validationError = policyResult?.error || null;

  return (
    <>
      <div className="border-b border-gray-200 px-6 py-4">
        <h2 className="text-xl font-semibold text-gray-800">
          {deploymentTypeLabel} 배포
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          현재 워크플로우를 배포하여 사용할 수 있게 만듭니다.
        </p>
      </div>

      <div className="max-h-[65vh] space-y-6 overflow-y-auto p-6">
        {requiresAppAuthSecret && appId && (
          <AppAuthSecretControl
            appId={appId}
            issuedSecret={issuedSecret}
            onSecretAvailable={onSecretAvailable}
            onReadinessChange={onAppAuthSecretReadinessChange}
          />
        )}

        {requiresAppAuthSecret && !appId && (
          <p className="text-xs text-gray-500" role="status">
            App 정보를 확인할 수 없어 Secret 준비를 진행할 수 없습니다.
          </p>
        )}

        <div>
          <label
            className="mb-2 block text-sm font-medium text-gray-700"
            htmlFor="deployment-description"
          >
            배포 설명 (선택)
          </label>
          <textarea
            id="deployment-description"
            className="h-28 w-full resize-none rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="이번 배포에 대한 설명"
            value={description}
            onChange={(event) => onDescriptionChange(event.target.value)}
            disabled={isDeploying}
          />
        </div>

        {deploymentType === 'chatbot' && (
          <div>
            <label
              className="mb-2 block text-sm font-medium text-gray-700"
              htmlFor="public-chat-history-consumer"
            >
              대화 기록을 사용할 LLM 노드
            </label>
            <select
              id="public-chat-history-consumer"
              className="w-full rounded-md border border-gray-300 px-3 py-2"
              value={conversationHistoryConsumerNodeId}
              onChange={(event) =>
                onConversationHistoryConsumerNodeIdChange(event.target.value)
              }
              disabled={isDeploying}
            >
              <option value="">LLM 노드를 선택하세요</option>
              {llmNodes.map((node) => (
                <option key={node.id} value={node.id}>
                  {node.title}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-gray-500">
              이전 대화는 선택한 노드 한 곳에만 전달됩니다.
            </p>
          </div>
        )}

        {supportsEmbeddingPolicy && (
          <BrowserAccessPolicyEditor
            headingId="deployment-browser-access-heading"
            enabled={embeddingEnabled}
            parentOrigins={parentOrigins}
            validationError={validationError}
            disabled={isDeploying}
            onEnabledChange={onEmbeddingEnabledChange}
            onParentOriginChange={onParentOriginChange}
            onAddParentOrigin={onAddParentOrigin}
            onRemoveParentOrigin={onRemoveParentOrigin}
          />
        )}
      </div>

      <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">
        <button
          type="button"
          onClick={onCancel}
          disabled={isDeploying}
          className="rounded-md bg-gray-100 px-4 py-2 text-gray-700 transition-colors hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          취소
        </button>
        <button
          type="button"
          onClick={() => onSubmit(policyResult?.policy || undefined)}
          disabled={
            isDeploying ||
            Boolean(validationError) ||
            appAuthSecretBlocked ||
            conversationConsumerBlocked
          }
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-400"
        >
          {isDeploying && <Loader2 className="h-4 w-4 animate-spin" />}
          {isDeploying ? '배포 중...' : submitLabel}
        </button>
      </div>
    </>
  );
}
