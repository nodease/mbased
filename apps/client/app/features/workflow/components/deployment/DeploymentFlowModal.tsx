'use client';

import { useState, useEffect } from 'react';
import {
  DeploymentOptimizationNode,
  DeploymentResult,
  DeploymentStep,
} from './types';
import type {
  DeploymentBrowserAccessPolicy,
  DeploymentType,
  DeploymentParameterOptimizationConfig,
  PublicChatConversationConfig,
} from '../../types/Deployment';
import { InputStep } from './InputStep';
import { SuccessStep } from './SuccessStep';
import { ErrorStep } from './ErrorStep';
import type {
  AppAuthSecretReadiness,
  IssuedAppAuthSecret,
} from '@/app/features/app/components/AppAuthSecretControl';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  appId?: string;
  deploymentType: DeploymentType;
  llmNodes: DeploymentOptimizationNode[];
  onDeploy: (
    description: string,
    parameterOptimization: DeploymentParameterOptimizationConfig,
    browserAccessPolicy?: DeploymentBrowserAccessPolicy,
    publicConversation?: PublicChatConversationConfig,
  ) => Promise<DeploymentResult>;
}

// ========== Main Component ==========

export function DeploymentFlowModal({
  isOpen,
  onClose,
  appId,
  deploymentType,
  llmNodes,
  onDeploy,
}: Props) {
  const [currentStep, setCurrentStep] = useState<DeploymentStep>('input');
  const [description, setDescription] = useState('');
  const [deploymentResult, setDeploymentResult] =
    useState<DeploymentResult | null>(null);
  const [issuedSecret, setIssuedSecret] = useState<IssuedAppAuthSecret | null>(
    null,
  );
  const [appAuthSecretReadiness, setAppAuthSecretReadiness] =
    useState<AppAuthSecretReadiness>('checking');
  const [isDeploying, setIsDeploying] = useState(false);
  const [embeddingEnabled, setEmbeddingEnabled] = useState(false);
  const [parentOrigins, setParentOrigins] = useState<string[]>(['']);
  const [
    conversationHistoryConsumerNodeId,
    setConversationHistoryConsumerNodeId,
  ] = useState('');
  const [parameterOptimization, setParameterOptimization] =
    useState<DeploymentParameterOptimizationConfig>({
      enabled: false,
      node_ids: [],
      check_every_runs: 50,
      monthly_validation_budget_usd: 3,
    });

  useEffect(() => {
    setIssuedSecret(null);
    setAppAuthSecretReadiness('checking');
  }, [appId, deploymentType, isOpen]);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setCurrentStep('input');
      setDescription('');
      setParameterOptimization({
        enabled: false,
        node_ids: llmNodes.map((node) => node.id),
        check_every_runs: 50,
        monthly_validation_budget_usd: 3,
      });
      setDeploymentResult(null);
      setEmbeddingEnabled(false);
      setParentOrigins(['']);
      setConversationHistoryConsumerNodeId(
        llmNodes.length === 1 ? llmNodes[0].id : '',
      );
    }
  }, [isOpen, llmNodes]);

  // Handle ESC key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        if (isDeploying) {
          // Confirm before closing during deployment
          if (confirm('배포가 진행 중입니다. 정말 닫으시겠습니까?')) {
            onClose();
          }
        } else {
          onClose();
        }
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, isDeploying, onClose]);

  // Handle deployment submission
  const handleSubmit = async (
    nextBrowserAccessPolicy?: DeploymentBrowserAccessPolicy,
  ) => {
    setIsDeploying(true);

    try {
      const publicConversation: PublicChatConversationConfig | undefined =
        deploymentType === 'chatbot' && conversationHistoryConsumerNodeId
          ? {
              contract_version: 'public_chat_conversation.v1',
              history_consumer: {
                node_id: conversationHistoryConsumerNodeId,
                container_path: [],
              },
            }
          : undefined;
      const result = publicConversation
        ? await onDeploy(
            description,
            parameterOptimization,
            nextBrowserAccessPolicy,
            publicConversation,
          )
        : nextBrowserAccessPolicy
          ? await onDeploy(
              description,
              parameterOptimization,
              nextBrowserAccessPolicy,
            )
          : await onDeploy(description, parameterOptimization);
      setDeploymentResult(result);

      if (result.success) {
        setCurrentStep('success');
      } else {
        setCurrentStep('error');
      }
    } catch (error: unknown) {
      setDeploymentResult({
        success: false,
        message:
          error instanceof Error
            ? error.message
            : '알 수 없는 오류가 발생했습니다.',
      });
      setCurrentStep('error');
    } finally {
      setIsDeploying(false);
    }
  };

  // Handle retry on error
  const handleRetry = () => {
    setCurrentStep('input');
    setDeploymentResult(null);
  };

  const handleInputSubmit = (
    nextBrowserAccessPolicy?: DeploymentBrowserAccessPolicy,
  ) => {
    void handleSubmit(nextBrowserAccessPolicy);
  };

  if (!isOpen) return null;

  // Get deployment type display name
  const getDeploymentTypeName = () => {
    switch (deploymentType) {
      case 'api':
        return 'REST API';
      case 'webapp':
        return '웹 앱';
      case 'widget':
        return '웹사이트 위젯';
      case 'chatbot':
        return '공개 챗봇';
      case 'internal_chatbot':
        return '내부 챗봇';
      case 'workflow_node':
        return '서브 모듈';
      case 'mcp':
        return 'MCP';
      case 'schedule':
        return '알람';
      case 'webhook':
        return '웹훅';
      default:
        return '배포';
    }
  };

  const stepNumber = currentStep === 'input' ? 1 : 2;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="배포"
      data-canvas-shortcut-scope="blocked"
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100]"
    >
      <div
        className={`relative bg-white rounded-lg shadow-xl w-full mx-4 flex flex-col overflow-hidden ${
          (deploymentType === 'api' || deploymentType === 'webhook') &&
          currentStep === 'success'
            ? 'max-w-6xl'
            : 'max-w-lg'
        }`}
      >
        {/* Compact Step Indicator - Top Right Corner */}
        <div className="absolute top-4 right-4 z-10">
          <div className="bg-blue-50 border border-blue-200 rounded-full px-3 py-1 flex items-center gap-2">
            <span className="text-xs font-semibold text-blue-700">
              {stepNumber}/2
            </span>
            <div className="flex items-center gap-1">
              {['input', 'result'].map((step, index) => (
                <div
                  key={step}
                  className={`h-1.5 w-1.5 rounded-full ${
                    index < stepNumber ? 'bg-blue-600' : 'bg-gray-300'
                  }`}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Content - render based on current step */}
        <div className="flex-1 transition-all duration-300">
          {currentStep === 'input' && (
            <InputStep
              appId={appId}
              issuedSecret={issuedSecret}
              onSecretAvailable={setIssuedSecret}
              appAuthSecretReadiness={appAuthSecretReadiness}
              onAppAuthSecretReadinessChange={setAppAuthSecretReadiness}
              deploymentType={deploymentType}
              deploymentTypeLabel={getDeploymentTypeName()}
              description={description}
              onDescriptionChange={setDescription}
              llmNodes={llmNodes}
              conversationHistoryConsumerNodeId={
                conversationHistoryConsumerNodeId
              }
              onConversationHistoryConsumerNodeIdChange={
                setConversationHistoryConsumerNodeId
              }
              embeddingEnabled={embeddingEnabled}
              parentOrigins={parentOrigins}
              onEmbeddingEnabledChange={(enabled) => {
                setEmbeddingEnabled(enabled);
                if (enabled && parentOrigins.length === 0) {
                  setParentOrigins(['']);
                }
              }}
              onParentOriginChange={(index, value) =>
                setParentOrigins((current) =>
                  current.map((origin, originIndex) =>
                    originIndex === index ? value : origin,
                  ),
                )
              }
              onAddParentOrigin={() =>
                setParentOrigins((current) => [...current, ''])
              }
              onRemoveParentOrigin={(index) =>
                setParentOrigins((current) =>
                  current.length === 1
                    ? ['']
                    : current.filter((_, originIndex) => originIndex !== index),
                )
              }
              onCancel={onClose}
              onSubmit={handleInputSubmit}
              isDeploying={isDeploying}
              submitLabel="배포"
            />
          )}

          {currentStep === 'success' && deploymentResult && (
            <SuccessStep
              result={deploymentResult}
              deploymentType={deploymentType}
              issuedSecret={issuedSecret}
              onSecretAvailable={setIssuedSecret}
              onClose={onClose}
            />
          )}

          {currentStep === 'error' && deploymentResult && (
            <ErrorStep
              message={
                deploymentResult.message || '배포 중 오류가 발생했습니다.'
              }
              onRetry={handleRetry}
              onClose={onClose}
            />
          )}
        </div>
      </div>
    </div>
  );
}
