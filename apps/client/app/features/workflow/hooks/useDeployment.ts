import { useState, useCallback } from 'react';
import type { Edge } from '@xyflow/react';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import type { DeploymentResult } from '../components/deployment/types';
import type { AppNode } from '../types/Nodes';
import type {
  DeploymentBrowserAccessPolicy,
  DeploymentParameterOptimizationConfig,
  DeploymentType,
  PublicChatConversationConfig,
} from '../types/Deployment';
import { disabledBrowserAccessPolicy } from '../utils/browserAccessPolicy';
import {
  deploymentApiErrorMessage,
  formatDeploymentPreflightMessage,
} from '../utils/deploymentPreflightMessage';

interface UseDeploymentProps {
  nodes: AppNode[]; // 시작 노드 타입 확인 및 graph_snapshot용
  edges: Edge[];
  isSettingsOpen: boolean;
  toggleSettings: () => void;
  isVersionHistoryOpen: boolean;
  toggleVersionHistory: () => void;
  isTestPanelOpen: boolean;
  toggleTestPanel: () => void;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedNodeType: (type: string | null) => void;
}

export function useDeployment({
  nodes,
  edges,
  isSettingsOpen,
  toggleSettings,
  isVersionHistoryOpen,
  toggleVersionHistory,
  isTestPanelOpen,
  toggleTestPanel,
  setSelectedNodeId,
  setSelectedNodeType,
}: UseDeploymentProps) {
  const [showDeployFlowModal, setShowDeployFlowModal] = useState(false);
  const [showDeployDropdown, setShowDeployDropdown] = useState(false);
  const [deploymentType, setDeploymentType] = useState<DeploymentType>('api');

  const { workflows, activeWorkflowId } = useWorkflowStore();
  const activeWorkflow = workflows.find((w) => w.id === activeWorkflowId);

  // 배포 드롭다운 토글 시 다른 패널 닫기
  const toggleDeployDropdown = useCallback(() => {
    if (!showDeployDropdown) {
      // 열릴 때 다른거 다 닫기
      if (isSettingsOpen) toggleSettings();
      if (isVersionHistoryOpen) toggleVersionHistory();
      if (isTestPanelOpen) toggleTestPanel();
      setSelectedNodeId(null);
      setSelectedNodeType(null);
    }
    setShowDeployDropdown((prev) => !prev);
  }, [
    showDeployDropdown,
    isSettingsOpen,
    toggleSettings,
    isVersionHistoryOpen,
    toggleVersionHistory,
    isTestPanelOpen,
    toggleTestPanel,
    setSelectedNodeId,
    setSelectedNodeType,
  ]);

  const handlePublishAsRestAPI = useCallback(() => {
    setDeploymentType('api');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsWebApp = useCallback(() => {
    setDeploymentType('webapp');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsWidget = useCallback(() => {
    setDeploymentType('widget');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsChatbot = useCallback(() => {
    setDeploymentType('chatbot');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsInternalChatbot = useCallback(() => {
    setDeploymentType('internal_chatbot');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsWorkflowNode = useCallback(() => {
    setDeploymentType('workflow_node');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsSchedule = useCallback(() => {
    setDeploymentType('schedule');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handlePublishAsWebhook = useCallback(() => {
    setDeploymentType('webhook');
    setShowDeployFlowModal(true);
    setShowDeployDropdown(false);
  }, []);

  const handleDeploy = useCallback(
    async (
      description: string,
      parameterOptimization: DeploymentParameterOptimizationConfig,
      browserAccessPolicy?: DeploymentBrowserAccessPolicy,
      publicConversation?: PublicChatConversationConfig,
    ): Promise<DeploymentResult> => {
      try {
        if (!activeWorkflow?.appId) {
          throw new Error('App ID를 찾을 수 없습니다.');
        }

        const supportsEmbeddingPolicy = ['chatbot', 'widget'].includes(
          deploymentType,
        );
        const requestedBrowserAccessPolicy = supportsEmbeddingPolicy
          ? browserAccessPolicy || disabledBrowserAccessPolicy()
          : undefined;
        const deploymentConfig = publicConversation
          ? { public_conversation: publicConversation }
          : {};
        const graphSnapshot = { nodes, edges };
        const preflight = await workflowApi.preflightDeployment({
          app_id: activeWorkflow.appId,
          description,
          type: deploymentType,
          config: deploymentConfig,
          parameter_optimization: parameterOptimization,
          is_active: true,
          graph_snapshot: graphSnapshot,
          ...(requestedBrowserAccessPolicy
            ? { browser_access_policy: requestedBrowserAccessPolicy }
            : {}),
        });
        if (preflight.status === 'blocked') {
          return {
            success: false,
            message: formatDeploymentPreflightMessage(preflight),
          };
        }
        const preflightWarning =
          preflight.status === 'warning'
            ? formatDeploymentPreflightMessage(preflight)
            : undefined;
        const normalizedBrowserAccessPolicy = supportsEmbeddingPolicy
          ? preflight.normalized_browser_access_policy
          : undefined;
        if (supportsEmbeddingPolicy && !normalizedBrowserAccessPolicy) {
          return {
            success: false,
            message: '서버에서 브라우저 접근 정책을 확인하지 못했습니다.',
          };
        }

        const response = await workflowApi.createDeployment({
          app_id: activeWorkflow.appId,
          description,
          type: deploymentType,
          config: deploymentConfig,
          parameter_optimization: parameterOptimization,
          is_active: true,
          graph_snapshot: graphSnapshot,
          ...(normalizedBrowserAccessPolicy
            ? { browser_access_policy: normalizedBrowserAccessPolicy }
            : {}),
        });

        useWorkflowStore.getState().notifyDeploymentComplete();

        const result: DeploymentResult = {
          success: true,
          deploymentId: response.id,
          appId: response.app_id,
          url_slug: response.url_slug ?? null,
          version: response.version,
          input_schema: response.input_schema ?? null,
          output_schema: response.output_schema ?? null,
          graph_snapshot: graphSnapshot, // webhook trigger 감지용
          message: preflightWarning,
          browser_access_policy:
            response.browser_access_policy ?? normalizedBrowserAccessPolicy,
        };

        if (deploymentType === 'webapp') {
          result.webAppUrl = `${window.location.origin}/shared/${response.url_slug}`;
        } else if (deploymentType === 'chatbot') {
          if (response.url_slug) {
            // 공개 챗봇 링크는 무인증 public-only RAG 경계를 사용한다.
            result.webAppUrl = `${window.location.origin}/embed/chat/${response.url_slug}`;
            if (normalizedBrowserAccessPolicy?.embedding.enabled) {
              result.embedUrl = result.webAppUrl;
            }
          }
        } else if (deploymentType === 'internal_chatbot') {
          if (activeWorkflow.id) {
            result.internalRunUrl = `${window.location.origin}/modules/${activeWorkflow.id}/run?deploymentId=${response.id}`;
          }
        } else if (deploymentType === 'widget') {
          if (response.url_slug) {
            result.webAppUrl = `${window.location.origin}/embed/chat/${response.url_slug}`;
            if (normalizedBrowserAccessPolicy?.embedding.enabled) {
              result.embedUrl = result.webAppUrl;
            }
          }
        } else if (deploymentType === 'workflow_node') {
          result.isWorkflowNode = true;
        } else if (deploymentType === 'schedule') {
          // schedule 노드에서 cron expression, timezone 추출
          const scheduleNode = nodes.find((n) => n.type === 'scheduleTrigger');
          if (scheduleNode) {
            const data = asRecord(scheduleNode.data);
            result.cronExpression =
              stringValue(data.cronExpression) ||
              stringValue(data.cron_expression);
            result.timezone =
              stringValue(data.timezone) ||
              stringValue(data.time_zone) ||
              'Asia/Seoul';
          }
        }

        return result;
      } catch (error: unknown) {
        return {
          success: false,
          message: deploymentApiErrorMessage(error),
        };
      }
    },
    [deploymentType, activeWorkflow?.appId, activeWorkflow?.id, nodes, edges],
  );

  return {
    showDeployFlowModal,
    setShowDeployFlowModal,
    showDeployDropdown,
    setShowDeployDropdown,
    deploymentType,
    toggleDeployDropdown,
    handlePublishAsRestAPI,
    handlePublishAsWebApp,
    handlePublishAsWidget,
    handlePublishAsChatbot,
    handlePublishAsInternalChatbot,
    handlePublishAsWorkflowNode,
    handlePublishAsSchedule,
    handlePublishAsWebhook,
    handleDeploy,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}
