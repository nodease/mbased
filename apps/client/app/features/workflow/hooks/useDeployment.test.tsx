import { act, renderHook } from '@testing-library/react';
import type { Edge } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import type { DeploymentParameterOptimizationConfig } from '@/app/features/workflow/types/Deployment';
import type { AppNode } from '@/app/features/workflow/types/Nodes';
import { useDeployment } from './useDeployment';

vi.mock('@/app/features/workflow/api/workflowApi', () => ({
  workflowApi: {
    createDeployment: vi.fn(),
    preflightDeployment: vi.fn(),
  },
}));

const mockedWorkflowApi = vi.mocked(workflowApi);
const initialStoreState = useWorkflowStore.getState();
const disabledParameterOptimization: DeploymentParameterOptimizationConfig = {
  enabled: false,
  node_ids: [],
  check_every_runs: 50,
  monthly_validation_budget_usd: 3,
};

const renderDeploymentHook = (nodes: AppNode[] = [], edges: Edge[] = []) =>
  renderHook(() =>
    useDeployment({
      nodes,
      edges,
      isSettingsOpen: false,
      toggleSettings: vi.fn(),
      isVersionHistoryOpen: false,
      toggleVersionHistory: vi.fn(),
      isTestPanelOpen: false,
      toggleTestPanel: vi.fn(),
      setSelectedNodeId: vi.fn(),
      setSelectedNodeType: vi.fn(),
    }),
  );

describe('useDeployment', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkflowStore.setState(initialStoreState, true);
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      workflows: [
        {
          id: 'workflow-1',
          appId: 'app-1',
          nodes: [],
          edges: [],
          features: {},
        },
      ],
    });
    mockedWorkflowApi.createDeployment.mockResolvedValue({
      id: 'deployment-1',
      app_id: 'app-1',
      version: 3,
      type: 'chatbot',
      url_slug: 'onboarding-bot',
      is_active: true,
      created_by: 'user-1',
      created_at: '2026-07-08T00:00:00Z',
      graph_snapshot: {},
      input_schema: null,
      output_schema: null,
    });
    mockedWorkflowApi.preflightDeployment.mockResolvedValue({
      status: 'passed',
      audience: 'anonymous_public',
      safe_summary: {
        blocked_reason: null,
        affected_node_count: 0,
        affected_kb_count_bucket: '0',
        affected_collection_count_bucket: '0',
        candidate_budget_limited: false,
      },
      required_actions: [],
      warnings: [],
      nodes: [],
      normalized_browser_access_policy: {
        contract_version: 'deployment_browser_access.v1',
        embedding: { enabled: false, parent_origins: [] },
      },
    });
  });

  it('public chatbot deployment returns only the anonymous public link', async () => {
    const { result } = renderDeploymentHook();

    act(() => {
      result.current.handlePublishAsChatbot();
    });

    let deploymentResult: Awaited<
      ReturnType<typeof result.current.handleDeploy>
    >;
    await act(async () => {
      deploymentResult = await result.current.handleDeploy(
        '사내 문서 질문 응답 봇',
        disabledParameterOptimization,
        undefined,
        {
          contract_version: 'public_chat_conversation.v1',
          history_consumer: { node_id: 'answer', container_path: [] },
        },
      );
    });

    expect(mockedWorkflowApi.createDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        app_id: 'app-1',
        type: 'chatbot',
        is_active: true,
        browser_access_policy: {
          contract_version: 'deployment_browser_access.v1',
          embedding: { enabled: false, parent_origins: [] },
        },
        config: {
          public_conversation: {
            contract_version: 'public_chat_conversation.v1',
            history_consumer: { node_id: 'answer', container_path: [] },
          },
        },
      }),
    );
    expect(deploymentResult!.webAppUrl).toContain('/embed/chat/onboarding-bot');
    expect(deploymentResult!.internalRunUrl).toBeUndefined();
  });

  it('uses the current unsaved graph for public preflight and deployment', async () => {
    const nodes = [
      {
        id: 'answer',
        type: 'llmNode',
        position: { x: 320, y: 40 },
        data: { title: 'Answer' },
      },
    ] as AppNode[];
    const edges: Edge[] = [
      {
        id: 'start-answer',
        source: 'start',
        target: 'answer',
      },
    ];
    const { result } = renderDeploymentHook(nodes, edges);
    act(() => result.current.handlePublishAsChatbot());

    await act(async () => {
      await result.current.handleDeploy(
        'unsaved public chatbot',
        disabledParameterOptimization,
        undefined,
        {
          contract_version: 'public_chat_conversation.v1',
          history_consumer: { node_id: 'answer', container_path: [] },
        },
      );
    });

    const expectedSnapshot = { nodes, edges };
    expect(mockedWorkflowApi.preflightDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        graph_snapshot: expectedSnapshot,
      }),
    );
    expect(mockedWorkflowApi.createDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        graph_snapshot: expectedSnapshot,
      }),
    );
  });

  it('uses the server-normalized policy for the create request', async () => {
    mockedWorkflowApi.preflightDeployment.mockResolvedValueOnce({
      status: 'passed',
      audience: 'anonymous_public',
      safe_summary: {
        blocked_reason: null,
        affected_node_count: 0,
        affected_kb_count_bucket: '0',
      },
      required_actions: [],
      warnings: [],
      nodes: [],
      normalized_browser_access_policy: {
        contract_version: 'deployment_browser_access.v1',
        embedding: {
          enabled: true,
          parent_origins: ['https://example.com'],
        },
      },
    });
    const { result } = renderDeploymentHook();
    act(() => result.current.handlePublishAsChatbot());

    await act(async () => {
      await result.current.handleDeploy(
        'canonical policy',
        disabledParameterOptimization,
        {
          contract_version: 'deployment_browser_access.v1',
          embedding: {
            enabled: true,
            parent_origins: ['https://EXAMPLE.com:443'],
          },
        },
      );
    });

    expect(mockedWorkflowApi.preflightDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        browser_access_policy: {
          contract_version: 'deployment_browser_access.v1',
          embedding: {
            enabled: true,
            parent_origins: ['https://EXAMPLE.com:443'],
          },
        },
      }),
    );
    expect(mockedWorkflowApi.createDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        browser_access_policy: {
          contract_version: 'deployment_browser_access.v1',
          embedding: {
            enabled: true,
            parent_origins: ['https://example.com'],
          },
        },
      }),
    );
  });

  it('internal chatbot deployment returns only the authenticated run link', async () => {
    mockedWorkflowApi.createDeployment.mockResolvedValueOnce({
      id: 'deployment-1',
      app_id: 'app-1',
      version: 3,
      type: 'internal_chatbot',
      url_slug: 'onboarding-bot',
      is_active: true,
      created_by: 'user-1',
      created_at: '2026-07-08T00:00:00Z',
      graph_snapshot: {},
      input_schema: null,
      output_schema: null,
    });
    const { result } = renderDeploymentHook();

    act(() => {
      result.current.handlePublishAsInternalChatbot();
    });

    let deploymentResult: Awaited<
      ReturnType<typeof result.current.handleDeploy>
    >;
    await act(async () => {
      deploymentResult = await result.current.handleDeploy(
        '사내 문서 질문 응답 봇',
        disabledParameterOptimization,
      );
    });

    expect(mockedWorkflowApi.preflightDeployment).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'internal_chatbot' }),
    );
    expect(mockedWorkflowApi.createDeployment).toHaveBeenCalledWith(
      expect.objectContaining({
        app_id: 'app-1',
        type: 'internal_chatbot',
        is_active: true,
      }),
    );
    expect(deploymentResult!.webAppUrl).toBeUndefined();
    expect(deploymentResult!.internalRunUrl).toContain(
      '/modules/workflow-1/run?deploymentId=deployment-1',
    );
  });

  it('blocked preflight stops active deployment creation', async () => {
    mockedWorkflowApi.preflightDeployment.mockResolvedValueOnce({
      status: 'blocked',
      audience: 'anonymous_public',
      safe_summary: {
        blocked_reason: 'private_kb_requires_execution_subject',
        affected_node_count: 1,
        affected_kb_count_bucket: '1',
        affected_collection_count_bucket: '0',
        candidate_budget_limited: false,
      },
      required_actions: [
        {
          action: 'remove_private_kb_or_use_authenticated_run',
          label: 'Private KB를 제거하거나 인증 실행 경로를 사용하세요',
        },
      ],
      warnings: [],
      nodes: [],
    });
    const { result } = renderDeploymentHook();

    let deploymentResult: Awaited<
      ReturnType<typeof result.current.handleDeploy>
    >;
    await act(async () => {
      deploymentResult = await result.current.handleDeploy(
        'private kb',
        disabledParameterOptimization,
      );
    });

    expect(deploymentResult!.success).toBe(false);
    expect(deploymentResult!.message).toContain(
      'private_kb_requires_execution_subject',
    );
    expect(mockedWorkflowApi.createDeployment).not.toHaveBeenCalled();
  });

  it('warning preflight deploys and returns a safe warning message', async () => {
    mockedWorkflowApi.preflightDeployment.mockResolvedValueOnce({
      status: 'warning',
      audience: 'anonymous_public',
      safe_summary: {
        blocked_reason: 'knowledge_candidate_budget_limited',
        affected_node_count: 1,
        affected_kb_count_bucket: '0',
        affected_collection_count_bucket: '1',
        candidate_budget_limited: true,
      },
      required_actions: [
        {
          action: 'review_knowledge_candidate_selection',
          label: 'KB와 Collection 선택을 검토하세요',
        },
      ],
      warnings: ['knowledge_candidate_budget_limited'],
      nodes: [],
    });
    const { result } = renderDeploymentHook();

    let deploymentResult: Awaited<
      ReturnType<typeof result.current.handleDeploy>
    >;
    await act(async () => {
      deploymentResult = await result.current.handleDeploy(
        'candidate warning',
        disabledParameterOptimization,
      );
    });

    expect(deploymentResult!.success).toBe(true);
    expect(deploymentResult!.message).toContain('실행 준비 검사 경고');
    expect(deploymentResult!.message).toContain('최대 후보 수');
    expect(mockedWorkflowApi.createDeployment).toHaveBeenCalledTimes(1);
  });
});
