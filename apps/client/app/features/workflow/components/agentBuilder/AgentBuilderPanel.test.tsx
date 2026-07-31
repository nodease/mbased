import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, within } from '@testing-library/react';
import { toast } from 'sonner';
import {
  AgentBuilderPanel,
  isAgentBuilderSetupCompleted,
} from './AgentBuilderPanel';
import { calculateAgentBuilderNodeFocusViewport } from './agentBuilderNodeFocus';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { agentBuilderApi } from '../../api/agentBuilderApi';
import type {
  AgentBuilderMessageResponse,
  AgentBuilderParameterGroup,
} from '../../api/agentBuilderApi';
import { workflowApi } from '../../api/workflowApi';
import type { Node } from '../../types/Workflow';
import type { LLMNode } from '../../types/Nodes';

const fitView = vi.fn();
const setCenter = vi.fn();
const setViewport = vi.fn();
const getNode = vi.fn();

vi.mock('@xyflow/react', async () => {
  const actual =
    await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react');
  return {
    ...actual,
    useReactFlow: () => ({
      getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
      fitView,
      setCenter,
      setViewport,
      getNode,
    }),
  };
});

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('../../api/agentBuilderApi', () => ({
  agentBuilderApi: {
    getModelOptions: vi.fn(),
    createSession: vi.fn(),
    getSession: vi.fn(),
    sendMessage: vi.fn(),
    cancelRequest: vi.fn(),
    acknowledgeMutation: vi.fn(),
    decideParameterTask: vi.fn(),
    selectKnowledge: vi.fn(),
    cancelParameterGroup: vi.fn(),
  },
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
    storeNodeSecret: vi.fn(),
  },
}));

const initialState = useWorkflowStore.getState();
const canonicalSaveResponse = (
  graphHash = 'b'.repeat(64),
  updatedAt = '2026-07-13T00:00:01Z',
) => ({
  status: 'success' as const,
  workflow_id: 'workflow-1',
  graph_hash: graphHash,
  updated_at: updatedAt,
});

const node = (id: string, type = 'startNode'): Node =>
  ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: { title: id, triggerType: 'manual', variables: [] },
  }) as Node;

const hierarchyKb = (
  kbHandle: string,
  safeLabel: string,
  score: number | null = null,
) => ({
  kb_handle: kbHandle,
  selection_key: kbHandle,
  safe_label: safeLabel,
  score,
  shared_collection_count: 1,
});

const mockKnowledgeSelection = (
  selectedCandidates: Array<{
    candidate_id: string;
    resolution_id: string;
    requirement_id: string;
  }>,
) => {
  vi.mocked(agentBuilderApi.selectKnowledge).mockResolvedValue({
    resolution_id: selectedCandidates[0]?.resolution_id ?? 'resolve-kb-1',
    selected_candidates: selectedCandidates,
    graph_mutation: {
      operation_id: 'operation-kb-selection',
      kind: 'initial_graph',
      status: 'pending_apply',
      workflow_id: 'workflow-old',
      base_graph_hash: 'a'.repeat(64),
      expected_workflow_updated_at: '2026-07-13T00:00:00Z',
      expected_result_graph_hash: 'b'.repeat(64),
      catalog_version: 3,
      operations: [{ op: 'add_node', node: node('answer', 'answerNode') }],
    },
  });
  vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
    canonicalSaveResponse(),
  );
  vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
    operation_id: 'operation-kb-selection',
    operation_status: 'acknowledged',
    graph_hash: 'b'.repeat(64),
    updated_at: '2026-07-13T00:00:01Z',
    parameter_group: null,
  });
};

describe('AgentBuilderPanel', () => {
  it('parameter group만 끝나도 backend request/history boundary가 끝나기 전에는 완료가 아니다', () => {
    const completedGroup = {
      group_id: 'group-completed-locally',
      status: 'completed',
      tasks: [],
    } satisfies AgentBuilderParameterGroup;

    expect(
      isAgentBuilderSetupCompleted('parameter_configuration', completedGroup),
    ).toBe(false);
    expect(
      isAgentBuilderSetupCompleted('graph_mutation_ready', completedGroup),
    ).toBe(false);
    expect(
      isAgentBuilderSetupCompleted('clarification_required', completedGroup),
    ).toBe(false);
    expect(isAgentBuilderSetupCompleted('completed', completedGroup)).toBe(
      true,
    );
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.sessionStorage.clear();
    useWorkflowStore.setState(initialState, true);
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSaveResponse(),
    );
    vi.mocked(agentBuilderApi.getModelOptions).mockResolvedValue([
      {
        provider_name: 'openai',
        unavailable_reason: null,
        options: [
          {
            model: {
              id: 'model-openai',
              model_id_for_api_call: 'gpt-5.5',
              name: 'GPT-5.5',
              provider_name: 'openai',
            },
            credential: {
              id: 'credential-openai',
              credential_name: 'OpenAI Main',
            },
            relation_priority: 0,
          },
          {
            model: {
              id: 'model-openai-pro',
              model_id_for_api_call: 'gpt-5.5-pro',
              name: 'GPT-5.5 Pro',
              provider_name: 'openai',
            },
            credential: {
              id: 'credential-openai',
              credential_name: 'OpenAI Main',
            },
            relation_priority: 0,
          },
        ],
      },
      {
        provider_name: 'anthropic',
        unavailable_reason: null,
        options: [
          {
            model: {
              id: 'model-anthropic',
              model_id_for_api_call: 'claude-sonnet-5',
              name: 'Claude Sonnet 5',
              provider_name: 'anthropic',
            },
            credential: {
              id: 'credential-anthropic',
              credential_name: 'Anthropic Main',
            },
            relation_priority: 0,
          },
        ],
      },
      {
        provider_name: 'google',
        unavailable_reason: 'no_authorized_model',
        options: [],
      },
      {
        provider_name: 'llamaparse',
        unavailable_reason: 'chat_model_not_supported',
        options: [],
      },
    ]);
  });

  it('Agent Builder 패널을 최소화했다가 다시 펼친다', async () => {
    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    const launcher = screen.getByLabelText('Agent Builder 열기');
    expect(launcher).toHaveClass('pointer-events-auto');
    expect(launcher.parentElement).toHaveClass('pointer-events-none');

    fireEvent.click(launcher);
    await screen.findByRole('button', {
      name: /Agent Builder 모델: GPT-5.5$/,
    });
    expect(screen.getByText('Agent Builder')).toBeInTheDocument();
    expect(screen.getByLabelText('Agent Builder panel')).toHaveClass(
      'pointer-events-auto',
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 최소화'));
    expect(screen.queryByText('Agent Builder')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Agent Builder 펼치기').parentElement).toHaveClass(
      'pointer-events-none',
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 펼치기'));
    expect(screen.getByText('Agent Builder')).toBeInTheDocument();
  });

  it('mobile에서는 viewport 안에 두고 desktop에서는 기본 화면 절반 너비로 표시한다', async () => {
    const canvasShortcut = vi.fn();
    window.addEventListener('keydown', canvasShortcut);
    try {
      render(
        <AgentBuilderPanel
          workflowId="workflow-1"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );

      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
      await screen.findByRole('button', {
        name: /Agent Builder 모델: GPT-5.5$/,
      });
      const panel = screen.getByText('Agent Builder').closest('section');
      expect(panel?.className).toContain('w-full');
      expect(panel?.parentElement).toHaveClass('agent-builder-panel-shell');
      expect(
        panel?.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('clamp(360px, 50vw, calc(100vw - 40px))');
      expect(panel?.className).toContain('h-[calc(100dvh-7.75rem)]');
      expect(panel?.parentElement?.className).toContain('bottom-5');

      const button = screen.getByLabelText('Agent Builder 최소화');
      const shortcut = new KeyboardEvent('keydown', {
        key: 'z',
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      });
      button.dispatchEvent(shortcut);

      expect(shortcut.defaultPrevented).toBe(false);
      expect(canvasShortcut).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', canvasShortcut);
    }
  });

  it('desktop 초기 너비에도 최소·최대 제한과 separator 접근성 범위를 적용한다', async () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 700,
    });
    try {
      render(
        <AgentBuilderPanel
          workflowId="workflow-1"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );

      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
      const panel = await screen.findByLabelText('Agent Builder panel');
      const resizeHandle = screen.getByLabelText('Agent Builder 너비 조절');

      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('clamp(360px, 50vw, calc(100vw - 40px))');
      expect(resizeHandle).toHaveAttribute('aria-valuenow', '360');
      expect(resizeHandle).toHaveAttribute('aria-valuemin', '360');
      expect(resizeHandle).toHaveAttribute('aria-valuemax', '660');
      expect(resizeHandle).toHaveAttribute('aria-valuetext', '360픽셀');
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        value: originalInnerWidth,
      });
    }
  });

  it('desktop에서 왼쪽 경계를 드래그해 panel 너비를 조절한다', async () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1200,
    });
    try {
      render(
        <AgentBuilderPanel
          workflowId="workflow-1"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );

      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
      const panel = await screen.findByLabelText('Agent Builder panel');
      vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({
        x: 600,
        y: 100,
        top: 100,
        right: 1200,
        bottom: 800,
        left: 600,
        width: 600,
        height: 700,
        toJSON: () => ({}),
      });

      const resizeHandle = screen.getByLabelText('Agent Builder 너비 조절');
      fireEvent.pointerDown(resizeHandle, { clientX: 600, pointerId: 1 });
      fireEvent.pointerMove(window, { clientX: 500, pointerId: 1 });

      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('700px');
      expect(document.body.style.cursor).toBe('col-resize');

      fireEvent.pointerMove(window, { clientX: -1000, pointerId: 1 });
      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('1160px');

      fireEvent.pointerMove(window, { clientX: 2000, pointerId: 1 });
      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('360px');
      expect(resizeHandle).toHaveAttribute('aria-valuenow', '360');
      expect(resizeHandle).toHaveAttribute('aria-valuemin', '360');
      expect(resizeHandle).toHaveAttribute('aria-valuemax', '1160');

      fireEvent.pointerUp(window, { pointerId: 1 });
      expect(document.body.style.cursor).toBe('');
      expect(document.body.style.userSelect).toBe('');

      fireEvent.keyDown(resizeHandle, { key: 'End' });
      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('1160px');

      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        value: 800,
      });
      fireEvent(window, new Event('resize'));
      expect(
        panel.parentElement?.style.getPropertyValue(
          '--agent-builder-panel-width',
        ),
      ).toBe('760px');
      expect(resizeHandle).toHaveAttribute('aria-valuemax', '760');
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        value: originalInnerWidth,
      });
    }
  });

  it('Knowledge 또는 parameter 설정 중에도 새 자연어 요청 입력을 막지 않는다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-1',
      'session-active-setup',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-active-setup',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'parameter_configuration',
      messages: [],
      parameter_group: {
        group_id: 'group-active-setup',
        status: 'active',
        tasks: [
          {
            task_id: 'task-active-setup',
            group_id: 'group-active-setup',
            step_id: 'step-llm',
            node_id: 'llm',
            node_type: 'llmNode',
            parameter_key: 'model',
            label: 'Model',
            input_type: 'text',
            required: true,
            defer_policy: 'forbidden',
            status: 'active',
            task_version: 1,
            stable_order: 0,
            resolution_source: null,
            reason: 'LLM 응답을 생성합니다.',
            input_guidance: '모델을 입력하세요.',
          },
        ],
      },
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    const input = await screen.findByRole('textbox');
    expect(input).not.toBeDisabled();
    fireEvent.change(input, { target: { value: '입력 출력 노드 생성' } });
    expect(
      screen.getByLabelText('Agent Builder 요청 보내기'),
    ).not.toBeDisabled();
  });

  it('panel을 제외한 mobile canvas에서 node와 겹치지 않는 최대 동적 zoom을 계산한다', () => {
    const viewport = calculateAgentBuilderNodeFocusViewport({
      canvasRect: {
        left: 0,
        top: 0,
        right: 360,
        bottom: 720,
        width: 360,
        height: 720,
      },
      panelRect: {
        left: 8,
        top: 200,
        right: 352,
        bottom: 700,
        width: 344,
        height: 500,
      },
      node: { x: 400, y: 300, width: 200, height: 100 },
      padding: 24,
      maxZoom: 1.6,
    });

    const nodeTop = 300 * viewport.zoom + viewport.y;
    const nodeBottom = 400 * viewport.zoom + viewport.y;
    expect(viewport.zoom).toBeCloseTo(1.52);
    expect(nodeTop).toBeGreaterThanOrEqual(24);
    expect(nodeBottom).toBeLessThanOrEqual(176);
  });

  it('첫 completed-boundary Undo presentation은 panel을 열고 최소화를 해제해 node를 선택·focus·scroll하되 input focus를 빼앗지 않는다', async () => {
    const canvasNode = node('answer', 'answerNode');
    const reopenedGroup: AgentBuilderParameterGroup = {
      group_id: 'group-reopened',
      status: 'active',
      tasks: [
        {
          task_id: 'task-reopened',
          group_id: 'group-reopened',
          step_id: 'step-answer',
          node_id: 'answer',
          node_type: 'answerNode',
          parameter_key: 'prompt',
          label: 'Prompt',
          input_type: 'text',
          required: true,
          defer_policy: 'forbidden',
          status: 'active',
          task_version: 4,
          stable_order: 3,
          resolution_source: 'catalog_default',
          reason: '응답 문구를 설정합니다.',
          input_guidance: '문구를 입력하세요.',
          node_label: 'Answer',
        },
      ],
    };
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-1',
      'session-reopened',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-reopened',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [],
      parameter_group: null,
    });
    getNode.mockReturnValue({
      ...canvasNode,
      position: { x: 400, y: 300 },
      measured: { width: 200, height: 100 },
    });
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        if (this.classList.contains('react-flow')) {
          return {
            x: 0,
            y: 0,
            left: 0,
            top: 0,
            right: 1000,
            bottom: 800,
            width: 1000,
            height: 800,
            toJSON: () => ({}),
          } as DOMRect;
        }
        if (this.getAttribute('aria-label') === 'Agent Builder panel') {
          return {
            x: 620,
            y: 200,
            left: 620,
            top: 200,
            right: 1000,
            bottom: 800,
            width: 380,
            height: 600,
            toJSON: () => ({}),
          } as DOMRect;
        }
        return {
          x: 0,
          y: 0,
          left: 0,
          top: 0,
          right: 0,
          bottom: 0,
          width: 0,
          height: 0,
          toJSON: () => ({}),
        } as DOMRect;
      });
    useWorkflowStore.setState({ nodes: [canvasNode], edges: [] });
    try {
      render(
        <>
          <button type="button">Canvas command target</button>
          <div className="react-flow" />
          <AgentBuilderPanel
            workflowId="workflow-1"
            appId="app-1"
            nodes={[canvasNode]}
            edges={[]}
            hasUnsavedChanges={false}
          />
        </>,
      );

      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
      await waitFor(() =>
        expect(agentBuilderApi.getSession).toHaveBeenCalledWith(
          'session-reopened',
        ),
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 최소화'));
      const canvasTarget = screen.getByRole('button', {
        name: 'Canvas command target',
      });
      canvasTarget.focus();

      act(() => {
        useWorkflowStore.setState({
          recoveredAgentBuilderParameterGroup: {
            sessionId: 'session-reopened',
            parameterGroup: reopenedGroup,
          },
          agentBuilderHistoryNotice:
            '마지막 재편집 가능 설정 항목을 다시 열었습니다.',
        });
      });

      expect(await screen.findByLabelText('Agent Builder panel')).toBeVisible();
      await waitFor(() => {
        expect(useWorkflowStore.getState().nodes[0].selected).toBe(true);
        expect(setViewport).toHaveBeenCalled();
        expect(scrollIntoView).toHaveBeenCalled();
      });
      expect(canvasTarget).toHaveFocus();
      expect(screen.getByLabelText('Prompt')).not.toHaveFocus();
      expect(
        screen.getByText('마지막 재편집 가능 설정 항목을 다시 열었습니다.'),
      ).toHaveAttribute('aria-live', 'polite');
    } finally {
      rectSpy.mockRestore();
    }
  });

  it('credential 허용 모델을 provider 순서로 표시하고 선택 모델을 요청에 포함한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-model',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-model',
      status: 'unsupported',
      clarification_questions: [],
      clarification_options: [],
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    await screen.findByRole('button', { name: /Agent Builder 모델: GPT-5.5$/ });
    fireEvent.click(
      screen.getByRole('button', { name: /Agent Builder 모델: GPT-5.5$/ }),
    );

    expect(screen.getByTestId('agent-builder-model-menu')).toHaveClass(
      'w-[190px]',
      'max-h-[264px]',
      'overflow-y-auto',
    );
    expect(
      screen
        .getAllByTestId('agent-builder-model-provider')
        .map((item) => item.textContent),
    ).toEqual(['openai', 'anthropic', 'google', 'llamaparse']);
    fireEvent.click(screen.getByRole('button', { name: /Claude Sonnet 5/ }));

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledWith(
        'session-model',
        expect.objectContaining({
          intentModelSelection: {
            credentialId: 'credential-anthropic',
            modelId: 'model-anthropic',
          },
        }),
      );
    });
  });

  it('structure-only direct mutation을 preview 없이 editor에 적용하고 저장한다', async () => {
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [],
      edges: [],
      undoStack: [],
      redoStack: [],
    });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-direct',
      status: 'graph_mutation_ready',
      graph_mutation: {
        operation_id: 'operation-direct',
        kind: 'initial_graph',
        status: 'pending_apply',
        generation_mode: 'structure_only',
        workflow_id: 'workflow-1',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        catalog_version: 3,
        affected_node_ids: ['start', 'answer'],
        operations: [
          { op: 'add_node', node: node('start') },
          { op: 'add_node', node: node('answer', 'answerNode') },
          {
            op: 'add_edge',
            edge: { id: 'e1', source: 'start', target: 'answer' },
          },
        ],
      },
      parameter_group: null,
      clarification_questions: [],
      clarification_options: [],
      warnings: [],
    });
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValueOnce({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-1',
      graph_hash: 'a'.repeat(64),
      updated_at: '2026-07-12T00:00:00Z',
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSaveResponse('b'.repeat(64), '2026-07-13T00:00:00Z'),
    );
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-direct',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:00Z',
      parameter_group: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    await screen.findByRole('button', {
      name: /Agent Builder 모델: GPT-5.5$/,
    });
    fireEvent.click(screen.getByRole('button', { name: '구조만 생성' }));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledWith(
        'session-direct',
        expect.objectContaining({ generationMode: 'structure_only' }),
      );
      expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledWith(
        'session-direct',
        expect.objectContaining({ operationId: 'operation-direct' }),
      );
    });
    expect(useWorkflowStore.getState().nodes.map((item) => item.id)).toEqual([
      'start',
      'answer',
    ]);
    expect(screen.queryByText('도안 생성 미리보기')).not.toBeInTheDocument();
  });

  it('structure-only 결과는 새로고침 뒤에도 safe envelope의 affected node로 routing 안내를 복구한다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-1',
      'session-routing-restore',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-routing-restore',
      workflow_id: 'workflow-1',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [
        {
          kind: 'assistant',
          request_id: 'request-routing-restore',
          response: {
            request_id: 'request-routing-restore',
            status: 'completed',
            structured_plan: null,
            clarification_questions: [],
            clarification_options: [],
            validation_result: null,
            warnings: [],
          },
        },
      ],
      active_graph_mutation: {
        operation_id: 'operation-routing-restore',
        status: 'acknowledged',
        affected_node_ids: ['llm-routing-restore'],
      },
      parameter_group: null,
      pending_request: null,
    });

    const restoredRoutingNode = {
      id: 'llm-routing-restore',
      type: 'llmNode',
      position: { x: 0, y: 0 },
      data: {
        title: '복구된 LLM',
        provider: 'openai',
        model_id: 'gpt-5.5',
        referenced_variables: [],
        parameters: {},
        auto_model_routing: false,
      },
    } satisfies LLMNode;

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[restoredRoutingNode]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    expect(
      await screen.findByTestId('agent-builder-routing-guidance'),
    ).toHaveTextContent('복구된 LLM Routing 설정으로 이동');
  });

  it('direct-edit는 legacy clarification KB 후보를 설정 카드로 표시하지 않는다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-no-legacy-kb',
      workflow_id: 'workflow-1',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-no-legacy-kb',
      status: 'clarification_required',
      structured_plan: null,
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          type: 'knowledge_base',
          candidate_id: 'legacy-kb-candidate',
          resolution_id: 'legacy-kb-resolution',
          requirement_id: 'legacy-kb-requirement',
          safe_label: 'Legacy Knowledge 후보',
        },
      ],
      validation_result: null,
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '사내 문서를 참고해줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await screen.findByText('clarification_required');
    expect(
      screen.queryByRole('checkbox', { name: 'Legacy Knowledge 후보' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('agent-builder-kb-candidate-list'),
    ).not.toBeInTheDocument();
  });

  it('새 요청이 실패하면 이전 요청의 완료 ParameterTask를 현재 설정 결과에 섞지 않는다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-1',
      'session-old-completed-task',
    );
    const completedGroup: AgentBuilderParameterGroup = {
      group_id: 'group-old-completed-task',
      status: 'completed',
      tasks: [
        {
          task_id: 'task-old-completed-task',
          group_id: 'group-old-completed-task',
          step_id: 'step-old-completed-task',
          node_id: 'node-old-completed-task',
          node_type: 'answerNode',
          parameter_key: 'prompt',
          label: '이전 응답 문구',
          input_type: 'text',
          required: true,
          defer_policy: 'forbidden',
          status: 'completed',
          task_version: 1,
          stable_order: 1,
          resolution_source: 'catalog_default',
          reason: '이전 요청의 설정입니다.',
          input_guidance: '이전 문구입니다.',
          node_label: '이전 요청 노드',
        },
      ],
    };
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-old-completed-task',
      workflow_id: 'workflow-1',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [
        {
          kind: 'assistant',
          request_id: 'request-old-completed-task',
          response: {
            request_id: 'request-old-completed-task',
            status: 'completed',
            structured_plan: null,
            clarification_questions: [],
            clarification_options: [],
            validation_result: null,
            warnings: [],
          },
        },
      ],
      active_graph_mutation: null,
      parameter_group: completedGroup,
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-new-failed',
      status: 'failed',
      structured_plan: null,
      clarification_questions: [],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    await screen.findByText('이전 요청 노드');

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '실패할 새 요청' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await screen.findByText('failed');
    expect(screen.queryByText('이전 요청 노드')).not.toBeInTheDocument();
    expect(screen.queryByText('이전 응답 문구')).not.toBeInTheDocument();
  });

  it.each(['agent_builder_card', 'llm_node_editor'] as const)(
    'after-graph Knowledge 선택은 %s에서 전용 endpoint를 사용하고 message를 재호출하지 않는다',
    async (selectionSource) => {
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [],
      edges: [],
      undoStack: [],
      redoStack: [],
    });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct-kb',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-direct-kb',
      status: 'graph_mutation_ready',
      graph_mutation: {
        operation_id: 'operation-initial-kb',
        kind: 'initial_graph',
        status: 'pending_apply',
        generation_mode: 'configure_and_generate',
        workflow_id: 'workflow-1',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-12T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        catalog_version: 3,
        affected_node_ids: ['start', 'llm', 'answer'],
        operations: [
          { op: 'add_node', node: node('start') },
          { op: 'add_node', node: node('llm', 'llmNode') },
          { op: 'add_node', node: node('answer', 'answerNode') },
          {
            op: 'add_edge',
            edge: { id: 'e1', source: 'start', target: 'llm' },
          },
          {
            op: 'add_edge',
            edge: { id: 'e2', source: 'llm', target: 'answer' },
          },
        ],
      },
      parameter_group: null,
      knowledge_resolution: {
        resolution_id: 'res-kb-1',
        target_node_id: 'llm',
        timing: 'after_graph',
        required: true,
        candidates: [],
        collections: [
          {
            collection_handle: 'collection-safe-1',
            safe_label: '사내 정책 Collection',
            score: 0.84,
            children: [
              {
                kb_handle: 'safe-rec-1',
                selection_key: 'kb-selection-1',
                safe_label: '휴가 정책',
                score: 0.7,
                shared_collection_count: 1,
              },
            ],
          },
        ],
        ungrouped_kbs: [],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      warnings: [],
    });
    vi.mocked(agentBuilderApi.selectKnowledge).mockResolvedValue({
      resolution_id: 'res-kb-1',
      selected_candidates: [],
      selected_collection_handles: ['collection-safe-1'],
      selected_kb_handles: ['safe-rec-1'],
      graph_mutation: {
        operation_id: 'operation-bind-kb',
        kind: 'knowledge_binding',
        status: 'pending_apply',
        generation_mode: 'configure_and_generate',
        workflow_id: 'workflow-1',
        base_graph_hash: 'b'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        expected_result_graph_hash: 'c'.repeat(64),
        catalog_version: 3,
        affected_node_ids: ['llm'],
        operations: [
          {
            op: 'replace_node_data',
            node_id: 'llm',
            data: {
              title: 'llm',
              knowledgeBases: [{ id: 'kb-1', name: '휴가 정책' }],
              knowledgeCollections: [
                { id: 'collection-1', safeLabel: '사내 정책 Collection' },
              ],
            },
          },
        ],
      },
    });
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockResolvedValueOnce(
        canonicalSaveResponse('b'.repeat(64), '2026-07-13T00:00:00Z'),
      )
      .mockResolvedValueOnce(
        canonicalSaveResponse('c'.repeat(64), '2026-07-13T00:00:01Z'),
      );
    vi.mocked(workflowApi.getDraftWorkflow)
      .mockResolvedValueOnce({
        nodes: [],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'a'.repeat(64),
        updated_at: '2026-07-12T00:00:00Z',
      })
      .mockResolvedValueOnce({
        nodes: [
          node('start'),
          node('llm', 'llmNode'),
          node('answer', 'answerNode'),
        ],
        edges: [
          { id: 'e1', source: 'start', target: 'llm' },
          { id: 'e2', source: 'llm', target: 'answer' },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
        workflow_id: 'workflow-1',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
      });
    vi.mocked(agentBuilderApi.acknowledgeMutation)
      .mockResolvedValueOnce({
        operation_id: 'operation-initial-kb',
        operation_status: 'acknowledged',
        graph_hash: 'b'.repeat(64),
        updated_at: '2026-07-13T00:00:00Z',
        parameter_group: null,
      })
      .mockResolvedValueOnce({
        operation_id: 'operation-bind-kb',
        operation_status: 'acknowledged',
        graph_hash: 'c'.repeat(64),
        updated_at: '2026-07-13T00:00:01Z',
        parameter_group: {
          group_id: 'group-kb',
          status: 'completed',
          tasks: [
            {
              task_id: 'task-kb',
              group_id: 'group-kb',
              step_id: 'step-llm',
              node_id: 'llm',
              node_type: 'llmNode',
              parameter_key: 'knowledgeBases',
              label: 'Knowledge Bases',
              input_type: 'resource_ref',
              required: false,
              defer_policy: 'allow_unresolved',
              status: 'completed',
              task_version: 2,
              stable_order: 0,
              resolution_source: 'user_request',
              reason: '사내 문서 기반 답변에 필요합니다.',
              input_guidance: '사용할 Knowledge Base를 선택하세요.',
            },
          ],
          } satisfies AgentBuilderParameterGroup,
      });
    vi.mocked(agentBuilderApi.getSession)
      .mockResolvedValueOnce({
        session_id: 'session-direct-kb',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'clarification_required',
        messages: [],
        active_graph_mutation: {
          operation_id: 'operation-initial-kb',
          status: 'acknowledged',
          result_graph_hash: 'b'.repeat(64),
          saved_workflow_updated_at: '2026-07-13T00:00:00Z',
        },
        parameter_group: null,
        pending_request: null,
      })
      .mockResolvedValueOnce({
        session_id: 'session-direct-kb',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'completed',
        messages: [],
        active_graph_mutation: {
          operation_id: 'operation-bind-kb',
          status: 'acknowledged',
          result_graph_hash: 'c'.repeat(64),
          saved_workflow_updated_at: '2026-07-13T00:00:01Z',
        },
        parameter_group: {
          group_id: 'group-kb',
          status: 'completed',
          tasks: [],
        },
        pending_request: null,
      });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책을 참고해 답변하는 워크플로우를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));
    const afterGraphSetup = await screen.findByTestId('workflow-result-group');
    expect(
      await within(afterGraphSetup).findByText('Graph 생성 후 Knowledge'),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox', { name: '휴가 정책' })).toHaveLength(
      1,
    );
    if (selectionSource === 'agent_builder_card') {
      fireEvent.click(
        within(afterGraphSetup).getByRole('checkbox', {
          name: '사내 정책 Collection',
        }),
      );
      expect(
        within(afterGraphSetup).getByRole('checkbox', { name: '휴가 정책' }),
      ).toBeChecked();
      const applySelectionButton = screen.getByRole('button', {
        name: '선택 적용',
      });
      await waitFor(() => expect(applySelectionButton).toBeEnabled());
      fireEvent.click(applySelectionButton);
    } else {
      const detail = {
        nodeId: 'llm',
        knowledgeBases: [{ id: 'kb-1', name: '휴가 정책' }],
        knowledgeCollections: [
          { id: 'collection-1', safeLabel: '사내 정책 Collection' },
        ],
        handled: false,
      };
      window.dispatchEvent(
        new CustomEvent('agent-builder:knowledge-selection-from-node', {
          detail,
        }),
      );
      expect(detail.handled).toBe(true);
    }

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-direct-kb',
        selectionSource === 'agent_builder_card'
          ? {
              resolutionId: 'res-kb-1',
              selectedCandidates: [],
              selectedCollectionHandles: ['collection-safe-1'],
              selectedKbHandles: ['safe-rec-1'],
            }
          : {
              resolutionId: 'res-kb-1',
              selectedCandidates: [],
              editorTargetNodeId: 'llm',
              selectedKnowledgeBaseIds: ['kb-1'],
              selectedKnowledgeCollectionIds: ['collection-1'],
            },
      );
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
      expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
      expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText('1 / 1 완료')).toBeInTheDocument();
    },
  );

  it('before-graph KB 선택도 message를 재전송하지 않고 전용 endpoint로 해결한다', async () => {
    useWorkflowStore.setState({ activeWorkflowId: 'workflow-1' });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-before-graph-kb',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValueOnce({
      request_id: 'request-before-graph-kb',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'res-before-1',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-before-1',
            resolution_id: 'res-before-1',
            requirement_id: 'kr-before-1',
            safe_label: '사내 문서',
          },
        ],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-rec-before-1', '사내 문서')],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          type: 'knowledge_base',
          candidate_id: 'safe-rec-before-1',
          resolution_id: 'res-before-1',
          requirement_id: 'kr-before-1',
          label: '사내 문서',
        },
      ],
      warnings: [],
    });
    vi.mocked(agentBuilderApi.selectKnowledge).mockResolvedValue({
      resolution_id: 'res-before-1',
      selected_candidates: [
        {
          candidate_id: 'safe-rec-before-1',
          resolution_id: 'res-before-1',
          requirement_id: 'kr-before-1',
        },
      ],
      graph_mutation: {
        operation_id: 'operation-before-graph-kb',
        kind: 'initial_graph',
        status: 'pending_apply',
        generation_mode: 'configure_and_generate',
        workflow_id: 'workflow-1',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        catalog_version: 3,
        affected_node_ids: ['start', 'llm', 'answer'],
        operations: [
          { op: 'add_node', node: node('start') },
          { op: 'add_node', node: node('llm', 'llmNode') },
          { op: 'add_node', node: node('answer', 'answerNode') },
          {
            op: 'add_edge',
            edge: { id: 'e1', source: 'start', target: 'llm' },
          },
          {
            op: 'add_edge',
            edge: { id: 'e2', source: 'llm', target: 'answer' },
          },
        ],
      },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSaveResponse(),
    );
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-before-graph-kb',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
      parameter_group: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));
    const beforeGraphSetup = await screen.findByTestId('workflow-result-group');
    expect(
      await within(beforeGraphSetup).findByText('Graph 생성 전 Knowledge'),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox', { name: '사내 문서' })).toHaveLength(
      1,
    );
    fireEvent.click(
      within(beforeGraphSetup).getByRole('checkbox', { name: '사내 문서' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-before-graph-kb',
        {
          resolutionId: 'res-before-1',
          selectedCandidates: [],
          selectedCollectionHandles: [],
          selectedKbHandles: ['safe-rec-before-1'],
        },
      );
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.queryByRole('checkbox', { name: '사내 문서' })).toBeNull();
    });
  });

  it('사용 가능한 intent model이 없으면 hidden fallback 없이 요청을 차단한다', async () => {
    vi.mocked(agentBuilderApi.getModelOptions).mockResolvedValue([
      {
        provider_name: 'openai',
        unavailable_reason: 'no_authorized_model',
        options: [],
      },
      {
        provider_name: 'anthropic',
        unavailable_reason: 'no_authorized_model',
        options: [],
      },
      {
        provider_name: 'google',
        unavailable_reason: 'no_authorized_model',
        options: [],
      },
      {
        provider_name: 'llamaparse',
        unavailable_reason: 'chat_model_not_supported',
        options: [],
      },
    ]);

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    await screen.findByRole('button', {
      name: /Agent Builder 모델: 선택 필요/,
    });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(agentBuilderApi.getModelOptions).toHaveBeenCalled();
    });
    expect(agentBuilderApi.createSession).not.toHaveBeenCalled();
    expect(agentBuilderApi.sendMessage).not.toHaveBeenCalled();
  });

  it('입력창에서 Enter를 누르면 Agent Builder 요청을 보낸다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-enter',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-enter',
      status: 'clarification_required',
      structured_plan: null,
      clarification_questions: ['추가 정보를 알려주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'LLM 가동' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledWith(
        'session-enter',
        {
          message: 'LLM 가동',
          workflowId: 'workflow-old',
          appId: 'app-1',
          selectedNodeId: undefined,
          selectedEdgeId: undefined,
          intentModelSelection: {
            credentialId: 'credential-openai',
            modelId: 'model-openai',
          },
          generationMode: 'configure_and_generate',
        },
      );
    });
  });

  it('연속 Enter 입력은 하나의 요청과 사용자 메시지만 만든다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-submit-lock',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    let resolveRequest!: (value: AgentBuilderMessageResponse) => void;
    vi.mocked(agentBuilderApi.sendMessage).mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '중복 없이 생성' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
    expect(
      within(screen.getByTestId('agent-builder-conversation')).getAllByText(
        '중복 없이 생성',
      ),
    ).toHaveLength(1);
    await act(async () => {
      resolveRequest({
        request_id: 'request-submit-lock',
        status: 'unsupported',
        structured_plan: null,
        clarification_questions: [],
        clarification_options: [],
        warnings: [],
      });
      await Promise.resolve();
    });
  });

  it('전송한 사용자 입력을 대화 영역에 남긴다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-user-message',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-user-message',
      status: 'clarification_required',
      structured_plan: null,
      clarification_questions: ['추가 정보를 알려주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Summarize input and send to Slack' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalled();
    });
    expect(screen.getByText('Summarize input and send to Slack')).toBeTruthy();
  });

  it('같은 workflow의 app metadata가 채워져도 열린 패널과 대화를 유지한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-app-hydration',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-app-hydration',
      status: 'clarification_required',
      structured_plan: null,
      clarification_questions: ['추가 정보를 알려주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });

    const { rerender } = render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId={null}
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Keep this conversation after save' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    await screen.findByText('Keep this conversation after save');
    rerender(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    expect(screen.getByText('Keep this conversation after save')).toBeTruthy();
    expect(screen.getByRole('textbox')).toBeTruthy();
  });

  it('workflow target clarification에서 node를 선택해 같은 수정 요청을 재전송한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-target',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage)
      .mockResolvedValueOnce({
        request_id: 'request-target-clarification',
        status: 'clarification_required',
        structured_plan: null,
        clarification_questions: ['수정 대상 node를 선택해주세요.'],
        clarification_options: [
          {
            type: 'workflow_node',
            node_id: 'github-read-1',
            node_type: 'githubNode',
            label: 'GitHub PR 조회 1',
          },
          {
            type: 'workflow_node',
            node_id: 'github-read-2',
            node_type: 'githubNode',
            label: 'GitHub PR 조회 2',
          },
        ],
        validation_result: {
          valid: false,
          issues: [
            {
              code: 'WORKFLOW_TARGET_UNRESOLVED',
              message: '기존 workflow 수정 대상을 확인해야 합니다.',
            },
          ],
        },
        warnings: [],
      })
      .mockResolvedValueOnce({
        request_id: 'request-target-resolved',
        status: 'draft_ready',
        structured_plan: null,
        clarification_questions: [],
        clarification_options: [],
        validation_result: { valid: true, issues: [] },
        warnings: [],
      });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'github 노드 뒤에 LLM 노드를 추가해줘' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    const targetButton = await screen.findByRole('button', {
      name: /GitHub PR 조회 2/,
    });
    expect(screen.queryByTestId('agent-builder-kb-candidate-list')).toBeNull();
    fireEvent.click(targetButton);

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenLastCalledWith(
        'session-target',
        expect.objectContaining({
          message: 'github 노드 뒤에 LLM 노드를 추가해줘',
          selectedNodeId: 'github-read-2',
        }),
      );
    });
  });

  it('Knowledge Base clarification 후보를 선택 가능한 정보로 표시한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-options',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-options',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-options',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-1',
            resolution_id: 'resolve-kb-options',
            safe_label: '휴가 정책',
            score: 0.7,
            reason_category: 'topic_keyword_match',
          },
          {
            candidate_id: 'safe-rec-2',
            resolution_id: 'resolve-kb-options',
            safe_label: '인사 정책',
            score: 0.66,
            reason_category: 'metadata_match',
          },
          {
            candidate_id: 'safe-rec-3',
            resolution_id: 'resolve-kb-options',
            safe_label: '총무 정책',
            score: 0.58,
            reason_category: 'metadata_match',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          candidate_id: 'safe-rec-1',
          label: '휴가 정책',
          confidence: 'high',
          score: 0.7,
          reason_category: 'topic_keyword_match',
        },
        {
          candidate_id: 'safe-rec-2',
          label: '인사 정책',
          confidence: 'high',
          score: 0.66,
          reason_category: 'metadata_match',
        },
        {
          candidate_id: 'safe-rec-3',
          label: '총무 정책',
          confidence: 'medium',
          score: 0.58,
          reason_category: 'metadata_match',
        },
        {
          type: 'no_knowledge_base',
          candidate_id: '__agent_builder_no_kb__',
          label: 'Knowledge Base 없이 생성',
          confidence: 'user_choice',
          score: null,
          reason_category: 'user_selected_no_kb',
        },
      ],
      validation_result: null,
      warnings: ['Knowledge Base 후보가 비슷해 자동 선택하지 않았습니다.'],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책을 찾아서 요약해줘' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    expect(await screen.findByText('휴가 정책')).toBeTruthy();
    expect(screen.getByText('인사 정책')).toBeTruthy();
    expect(screen.getByText('총무 정책')).toBeTruthy();
    expect(
      screen.queryByRole('checkbox', { name: 'Knowledge Base 없이 생성' }),
    ).toBeNull();
    expect(screen.getByText('0.70')).toBeTruthy();
    expect(screen.getByText('0.66')).toBeTruthy();
    const candidateList = screen.getByTestId('agent-builder-kb-candidate-list');
    const scrollList = Array.from(candidateList.querySelectorAll('div')).find(
      (element) => element.className.includes('max-h-[156px]'),
    );
    expect(scrollList?.className).toContain('max-h-[156px]');
    expect(scrollList?.className).toContain('overflow-y-auto');
  });

  it('새 요청과 응답이 추가되면 대화 영역을 맨 아래로 스크롤한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-scroll',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-scroll',
      status: 'draft_ready',
      structured_plan: null,
      clarification_questions: [],
      clarification_options: [],
      validation_result: { valid: true, issues: [] },
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    const conversation = screen.getByTestId('agent-builder-conversation');
    Object.defineProperty(conversation, 'scrollHeight', {
      configurable: true,
      value: 480,
    });

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '새 workflow를 만들어줘' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalled();
      expect(conversation.scrollTop).toBe(480);
    });
  });

  it('Knowledge Base 후보를 선택한 뒤 safe handle로 도안 생성을 요청한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-confirm',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValueOnce({
      request_id: 'request-kb-clarify',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-1',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-1',
            resolution_id: 'resolve-kb-1',
            requirement_id: 'kr-1',
            safe_label: '휴가 정책',
            score: 0.7,
            reason_category: 'topic_keyword_match',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          candidate_id: 'safe-rec-1',
          resolution_id: 'resolve-kb-1',
          requirement_id: 'kr-1',
          label: '휴가 정책',
          confidence: 'high',
          score: 0.7,
          reason_category: 'topic_keyword_match',
        },
      ],
      validation_result: null,
      warnings: [],
    });
    mockKnowledgeSelection([
      {
        candidate_id: 'safe-rec-1',
        resolution_id: 'resolve-kb-1',
        requirement_id: 'kr-1',
      },
    ]);

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Find the policy knowledge base and summarize it' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    fireEvent.click(await screen.findByRole('checkbox', { name: '휴가 정책' }));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge Base로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-kb-confirm',
        {
          resolutionId: 'resolve-kb-1',
          selectedCandidates: [
            {
              candidate_id: 'safe-rec-1',
              resolution_id: 'resolve-kb-1',
              requirement_id: 'kr-1',
            },
          ],
        },
      );
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
  });

  it('KB 선택으로 도안을 만든 뒤에는 빈 입력으로 같은 요청을 다시 제출할 수 없다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-no-repeat',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-no-repeat-clarify',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-1',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-1',
            resolution_id: 'resolve-kb-1',
            requirement_id: 'kr-1',
            safe_label: '휴가 정책',
            score: 0.7,
            reason_category: 'topic_keyword_match',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          candidate_id: 'safe-rec-1',
          resolution_id: 'resolve-kb-1',
          requirement_id: 'kr-1',
          label: '휴가 정책',
          confidence: 'high',
          score: 0.7,
          reason_category: 'topic_keyword_match',
        },
      ],
      validation_result: null,
      warnings: [],
    });
    mockKnowledgeSelection([
      {
        candidate_id: 'safe-rec-1',
        resolution_id: 'resolve-kb-1',
        requirement_id: 'kr-1',
      },
    ]);

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책을 찾아서 요약해줘' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    fireEvent.click(await screen.findByRole('checkbox', { name: '휴가 정책' }));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge Base로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledTimes(1);
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByLabelText('Agent Builder 요청 보내기')).toBeDisabled();
  });

  it('Knowledge Base 후보를 여러 개 선택해 도안 생성을 요청한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-multi',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-multi-clarify',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-1',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-1',
            resolution_id: 'resolve-kb-1',
            requirement_id: 'kr-1',
            safe_label: '휴가 정책',
            score: 0.7,
            reason_category: 'topic_keyword_match',
          },
          {
            candidate_id: 'safe-rec-2',
            resolution_id: 'resolve-kb-1',
            requirement_id: 'kr-1',
            safe_label: '인사 정책',
            score: 0.66,
            reason_category: 'metadata_match',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          candidate_id: 'safe-rec-1',
          resolution_id: 'resolve-kb-1',
          requirement_id: 'kr-1',
          label: '휴가 정책',
          confidence: 'high',
          score: 0.7,
          reason_category: 'topic_keyword_match',
        },
        {
          candidate_id: 'safe-rec-2',
          resolution_id: 'resolve-kb-1',
          requirement_id: 'kr-1',
          label: '인사 정책',
          confidence: 'high',
          score: 0.66,
          reason_category: 'metadata_match',
        },
      ],
      validation_result: null,
      warnings: [],
    });
    mockKnowledgeSelection([
      {
        candidate_id: 'safe-rec-1',
        resolution_id: 'resolve-kb-1',
        requirement_id: 'kr-1',
      },
      {
        candidate_id: 'safe-rec-2',
        resolution_id: 'resolve-kb-1',
        requirement_id: 'kr-1',
      },
    ]);

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Find the policy knowledge base and summarize it' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    fireEvent.click(await screen.findByRole('checkbox', { name: '휴가 정책' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '인사 정책' }));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge Base로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-kb-multi',
        {
          resolutionId: 'resolve-kb-1',
          selectedCandidates: [
            {
              candidate_id: 'safe-rec-1',
              resolution_id: 'resolve-kb-1',
              requirement_id: 'kr-1',
            },
            {
              candidate_id: 'safe-rec-2',
              resolution_id: 'resolve-kb-1',
              requirement_id: 'kr-1',
            },
          ],
        },
      );
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
  });

  it('Knowledge Base 후보를 선택하지 않으면 빈 선택으로 도안 생성을 요청한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-none',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-none-clarify',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-1',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-1',
            resolution_id: 'resolve-kb-1',
            requirement_id: 'kr-1',
            safe_label: '휴가 정책',
            score: 0.7,
            reason_category: 'topic_keyword_match',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          type: 'knowledge_base',
          candidate_id: 'safe-rec-1',
          resolution_id: 'resolve-kb-1',
          requirement_id: 'kr-1',
          label: '휴가 정책',
          safe_label: '휴가 정책',
          confidence: 'high',
          score: 0.7,
          reason_category: 'topic_keyword_match',
          threshold_result: 'clarification_required',
          runtime_availability: 'available',
        },
      ],
      validation_result: null,
      warnings: [],
    });
    mockKnowledgeSelection([]);

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Find the policy knowledge base and summarize it' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), {
      key: 'Enter',
      code: 'Enter',
    });

    expect(
      await screen.findByRole('checkbox', { name: '휴가 정책' }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole('button', { name: 'Knowledge Base 없이 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-kb-none',
        { resolutionId: 'resolve-kb-1', selectedCandidates: [] },
      );
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    });
  });

  it('direct_edit_v1 empty Knowledge candidates use the top-level resolution id and ignore legacy fallback options', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-empty-direct',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-empty-direct',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-empty-direct',
        timing: 'before_graph',
        required: true,
        candidates: [],
        collections: [],
        ungrouped_kbs: [],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [
        {
          candidate_id: 'legacy-candidate-must-not-render',
          resolution_id: 'legacy-resolution',
          requirement_id: 'legacy-requirement',
          label: 'Legacy KB 후보',
          reason_category: 'topic_keyword_match',
        },
      ],
      validation_result: null,
      warnings: [],
    });
    vi.mocked(agentBuilderApi.selectKnowledge).mockResolvedValue({
      resolution_id: 'resolve-kb-empty-direct',
      selected_candidates: [],
      graph_mutation: {
        operation_id: 'operation-kb-empty-direct',
        kind: 'initial_graph',
        status: 'pending_apply',
        workflow_id: 'workflow-old',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        catalog_version: 3,
        operations: [{ op: 'add_node', node: node('answer', 'answerNode') }],
      },
    });
    vi.mocked(workflowApi.syncDraftWorkflow).mockResolvedValue(
      canonicalSaveResponse('b'.repeat(64), '2026-07-13T00:00:01Z'),
    );
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-kb-empty-direct',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
      parameter_group: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Knowledge 없이 응답 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    expect(
      await screen.findByRole('button', { name: 'Knowledge Base 없이 생성' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('checkbox', { name: 'Legacy KB 후보' }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: 'Knowledge Base 없이 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-kb-empty-direct',
        {
          resolutionId: 'resolve-kb-empty-direct',
          selectedCandidates: [],
          selectedCollectionHandles: [],
          selectedKbHandles: [],
          selectedKnowledgeBaseIds: undefined,
          selectedKnowledgeCollectionIds: undefined,
          editorTargetNodeId: undefined,
        },
      );
    });
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
  });

  it('Knowledge save failure reads the canonical session and keeps the selected card retryable', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-failure',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    const response: AgentBuilderMessageResponse = {
      request_id: 'request-kb-failure',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-failure',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-failure',
            resolution_id: 'resolve-kb-failure',
            requirement_id: 'kr-failure',
            safe_label: '휴가 정책',
            reason_category: 'topic_keyword_match',
          },
        ],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-rec-failure', '휴가 정책')],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    };
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue(response);
    vi.mocked(agentBuilderApi.selectKnowledge).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-kb-failure',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'clarification_required',
      messages: [
        {
          kind: 'assistant',
          request_id: response.request_id,
          response: {
            ...response,
            knowledge_resolution: {
              ...response.knowledge_resolution!,
              candidates: [
                {
                  candidate_id: 'safe-rec-failure',
                  resolution_id: 'resolve-kb-failure',
                  requirement_id: 'kr-failure',
                  safe_label: '휴가 정책 canonical',
                  reason_category: 'topic_keyword_match',
                },
              ],
              collections: [],
              ungrouped_kbs: [
                hierarchyKb('safe-rec-failure', '휴가 정책 canonical'),
              ],
              selected: [],
            },
          },
        },
      ],
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책으로 답변하는 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    const checkbox = await screen.findByRole('checkbox', { name: '휴가 정책' });
    fireEvent.click(checkbox);
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.getSession).toHaveBeenCalledWith(
        'session-kb-failure',
      );
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Knowledge Base 선택을 적용하지 못했습니다. 현재 선택은 유지됩니다. 다시 시도해주세요.',
    );
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole('checkbox', { name: '휴가 정책 canonical' }),
    ).toBeChecked();
    expect(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    ).toBeEnabled();
  });

  it('refreshes the same Knowledge card when the submitted hierarchy is stale', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-stale-refresh',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    const response: AgentBuilderMessageResponse = {
      request_id: 'request-kb-stale-refresh',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-stale-refresh',
        timing: 'before_graph',
        required: true,
        candidates: [],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-rec-stale', '이전 휴가 정책')],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    };
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue(response);
    vi.mocked(agentBuilderApi.selectKnowledge).mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 409,
        data: { detail: { code: 'knowledge_selection_stale' } },
      },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-kb-stale-refresh',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'clarification_required',
      messages: [
        {
          kind: 'assistant',
          request_id: response.request_id,
          response: {
            ...response,
            knowledge_resolution: {
              ...response.knowledge_resolution!,
              ungrouped_kbs: [
                hierarchyKb('safe-rec-refreshed', '최신 휴가 정책'),
              ],
              selected: [],
            },
          },
        },
      ],
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책으로 답변하는 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    fireEvent.click(
      await screen.findByRole('checkbox', { name: '이전 휴가 정책' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.getSession).toHaveBeenCalledWith(
        'session-kb-stale-refresh',
      );
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Knowledge 후보가 변경되어 최신 목록으로 갱신했습니다. 다시 선택해주세요.',
    );
    expect(
      screen.queryByRole('checkbox', { name: '이전 휴가 정책' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: '최신 휴가 정책' }),
    ).not.toBeChecked();
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
  });

  it('ambiguous Knowledge selection reconciles completed canonical session and closes the stale card', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-completed-reconcile',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    const response: AgentBuilderMessageResponse = {
      request_id: 'request-kb-completed-reconcile',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-completed-reconcile',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-completed-reconcile',
            resolution_id: 'resolve-kb-completed-reconcile',
            requirement_id: 'kr-completed-reconcile',
            safe_label: '휴가 정책',
          },
        ],
        collections: [],
        ungrouped_kbs: [
          hierarchyKb('safe-rec-completed-reconcile', '휴가 정책'),
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    };
    const completedResponse: AgentBuilderMessageResponse = {
      ...response,
      status: 'completed',
      knowledge_resolution: {
        ...response.knowledge_resolution!,
        selected: [
          {
            candidate_id: 'safe-rec-completed-reconcile',
            safe_label: '휴가 정책',
          },
        ],
      },
      clarification_questions: [],
    };
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue(response);
    vi.mocked(agentBuilderApi.selectKnowledge).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-kb-completed-reconcile',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [
        {
          kind: 'assistant',
          request_id: completedResponse.request_id,
          response: completedResponse,
        },
      ],
      parameter_group: null,
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책으로 답변하는 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));
    fireEvent.click(await screen.findByRole('checkbox', { name: '휴가 정책' }));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    expect(await screen.findByText('Knowledge 설정 완료')).toBeInTheDocument();
    expect(
      screen.queryByRole('checkbox', { name: '휴가 정책' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Workflow 생성 완료')).toBeInTheDocument();
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('Knowledge Base 선택을 적용하지 못했습니다'),
    );
  });

  it('ambiguous Knowledge selection locks the card while canonical session is pending acknowledgement', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-pending-ack',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    const response: AgentBuilderMessageResponse = {
      request_id: 'request-kb-pending-ack',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-pending-ack',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-pending-ack',
            resolution_id: 'resolve-kb-pending-ack',
            requirement_id: 'kr-pending-ack',
            safe_label: '휴가 정책',
          },
        ],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-rec-pending-ack', '휴가 정책')],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    };
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue(response);
    vi.mocked(agentBuilderApi.selectKnowledge).mockRejectedValue(
      new Error('response lost'),
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-kb-pending-ack',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'graph_mutation_ready',
      messages: [
        {
          kind: 'assistant',
          request_id: response.request_id,
          response,
        },
      ],
      active_graph_mutation: {
        operation_id: 'operation-kb-pending-ack',
        status: 'pending_ack',
        result_graph_hash: 'b'.repeat(64),
        saved_workflow_updated_at: '2026-07-13T00:00:01Z',
      },
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책으로 답변하는 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));
    fireEvent.click(await screen.findByRole('checkbox', { name: '휴가 정책' }));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('Workflow 확인 중'),
    );
    expect(screen.getByRole('checkbox', { name: '휴가 정책' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    ).toBeDisabled();
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('Knowledge Base 선택을 적용하지 못했습니다'),
    );
  });

  it('Knowledge candidate reasons use Korean allowlisted labels and hide unknown codes', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-reasons',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-reasons',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-reasons',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-known',
            resolution_id: 'resolve-kb-reasons',
            requirement_id: 'kr-reasons',
            safe_label: '휴가 정책',
            reason_category: 'topic_keyword_match',
          },
          {
            candidate_id: 'safe-rec-unknown',
            resolution_id: 'resolve-kb-reasons',
            requirement_id: 'kr-reasons',
            safe_label: '비공개 정책',
            reason_category: 'internal_raw_code',
            reason: 'raw reason must not render',
          },
        ],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책으로 답변하는 workflow를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    expect(await screen.findByText('휴가 정책')).toBeInTheDocument();
    expect(screen.getByText('요청 주제와 일치')).toBeInTheDocument();
    expect(screen.getByText('비공개 정책')).toBeInTheDocument();
    expect(screen.queryByText('internal_raw_code')).not.toBeInTheDocument();
    expect(
      screen.queryByText('raw reason must not render'),
    ).not.toBeInTheDocument();
  });

  it('direct_edit_v1 Knowledge는 knowledge_resolution candidates와 전용 selection endpoint만 사용하고 저장 전 대화 결과를 추가하지 않는다', async () => {
    useWorkflowStore.setState({ activeWorkflowId: 'workflow-old' });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-kb-direct',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.sendMessage).mockResolvedValue({
      request_id: 'request-kb-direct',
      status: 'clarification_required',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-direct',
        timing: 'before_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-rec-direct',
            resolution_id: 'resolve-kb-direct',
            requirement_id: 'kr-direct',
            safe_label: '휴가 정책 v1',
            score: 0.91,
            reason_category: 'topic_keyword_match',
          },
        ],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-rec-direct', '휴가 정책 v1', 0.91)],
        selected: [],
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    });
    vi.mocked(agentBuilderApi.selectKnowledge).mockResolvedValue({
      resolution_id: 'resolve-kb-direct',
      selected_candidates: [
        {
          candidate_id: 'safe-rec-direct',
          resolution_id: 'resolve-kb-direct',
          requirement_id: 'kr-direct',
        },
      ],
      graph_mutation: {
        operation_id: 'operation-kb-direct',
        kind: 'initial_graph',
        status: 'pending_apply',
        workflow_id: 'workflow-old',
        base_graph_hash: 'a'.repeat(64),
        expected_workflow_updated_at: '2026-07-13T00:00:00Z',
        expected_result_graph_hash: 'b'.repeat(64),
        catalog_version: 3,
        operations: [{ op: 'add_node', node: node('answer', 'answerNode') }],
      },
    });
    vi.mocked(workflowApi.syncDraftWorkflow)
      .mockRejectedValueOnce(new Error('save failed'))
      .mockRejectedValueOnce(new Error('save retry failed'))
      .mockResolvedValueOnce(
        canonicalSaveResponse('b'.repeat(64), '2026-07-13T00:00:01Z'),
      );
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockResolvedValue({
      operation_id: 'operation-kb-direct',
      operation_status: 'acknowledged',
      graph_hash: 'b'.repeat(64),
      updated_at: '2026-07-13T00:00:01Z',
      parameter_group: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '휴가 정책을 찾아서 요약해줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    fireEvent.click(
      await screen.findByRole('checkbox', { name: '휴가 정책 v1' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledWith(
        'session-kb-direct',
        {
          resolutionId: 'resolve-kb-direct',
          selectedCandidates: [],
          selectedCollectionHandles: [],
          selectedKbHandles: ['safe-rec-direct'],
        },
      );
      expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(2);
    });
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(
        screen.getByRole('checkbox', { name: '휴가 정책 v1' }),
      ).toBeChecked();
      expect(
        screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
      ).toBeEnabled();
    });
    expect(screen.queryByText(/Knowledge Base 선택:/)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    await waitFor(() => {
      expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText(/Knowledge Base 선택:/)).toHaveTextContent(
      '휴가 정책 v1',
    );
    expect(agentBuilderApi.selectKnowledge).toHaveBeenCalledTimes(2);
    expect(workflowApi.syncDraftWorkflow).toHaveBeenCalledTimes(3);
    expect(agentBuilderApi.acknowledgeMutation).toHaveBeenCalledTimes(1);
  });

  it('session restore는 redacted 사용자 메시지와 assistant 응답을 함께 복원한다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow-old:app-1',
      'session-restore',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-restore',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      status: 'active',
      messages: [
        {
          kind: 'user',
          request_id: 'request-restore',
          content: '휴가 정책을 찾아서 요약해줘',
          redacted: true,
        },
        {
          kind: 'assistant',
          request_id: 'request-restore',
          response: {
            request_id: 'request-restore',
            status: 'clarification_required',
            structured_plan: null,
            clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
            clarification_options: [],
            validation_result: null,
            warnings: [],
          },
        },
      ],
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    expect(await screen.findByText('휴가 정책을 찾아서 요약해줘')).toBeTruthy();
    expect(
      screen.getByText('사용할 Knowledge Base를 선택해주세요.'),
    ).toBeTruthy();
  });

  it('복구된 planning 응답을 polling 결과의 같은 request terminal 응답으로 교체한다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-old',
      'session-pending-poll',
    );
    vi.mocked(agentBuilderApi.getSession)
      .mockResolvedValueOnce({
        session_id: 'session-pending-poll',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1',
        status: 'planning',
        messages: [
          {
            kind: 'user',
            request_id: 'request-pending-poll',
            content: '웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘',
          },
          {
            kind: 'assistant',
            request_id: 'request-pending-poll',
            response: {
              request_id: 'request-pending-poll',
              status: 'planning',
              clarification_questions: [],
              clarification_options: [],
              warnings: [],
            },
          },
        ],
        pending_request: {
          request_id: 'request-pending-poll',
          status: 'planning',
        },
      })
      .mockResolvedValue({
        session_id: 'session-pending-poll',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1',
        status: 'failed',
        messages: [
          {
            kind: 'user',
            request_id: 'request-pending-poll',
            content: '웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘',
          },
          {
            kind: 'assistant',
            request_id: 'request-pending-poll',
            response: {
              request_id: 'request-pending-poll',
              status: 'failed',
              clarification_questions: [],
              clarification_options: [],
              warnings: ['요청을 안전한 workflow 구조로 변환하지 못했습니다.'],
            },
          },
        ],
        pending_request: null,
      });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );

    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    expect(await screen.findByText('planning')).toBeInTheDocument();
    expect(
      await screen.findByText('failed', {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText('planning')).toBeNull();
    expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(2);
  });

  it('session restore의 pending acknowledgement는 acknowledgement를 재전송하지 않는다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow-old:app-1',
      'session-pending-ack',
    );
    let sessionReadCount = 0;
    vi.mocked(agentBuilderApi.getSession).mockImplementation(async () => {
      sessionReadCount += 1;
      if (sessionReadCount === 1) {
        return {
          session_id: 'session-pending-ack',
          workflow_id: 'workflow-old',
          protocol_version: 'direct_edit_v1',
          status: 'graph_mutation_ready',
          messages: [],
          active_graph_mutation: {
            operation_id: 'operation-pending-ack',
            status: 'pending_ack',
            result_graph_hash: 'd'.repeat(64),
            saved_workflow_updated_at: '2026-07-13T00:00:02Z',
          },
        };
      }
      return {
        session_id: 'session-pending-ack',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1',
        status: 'completed',
        messages: [],
        active_graph_mutation: null,
        parameter_group: {
          group_id: 'group-pending-ack',
          status: 'completed',
          tasks: [],
        },
      };
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    await waitFor(() =>
      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(1),
    );
    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
  });

  it('later canonical session reconciliation completes the persisted local history boundary', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-old',
      'session-acknowledged-recovery',
    );
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-old',
      undoStack: [
        {
          nodes: [],
          edges: [],
          agentBuilderOperation: {
            operationId: 'operation-acknowledged-recovery',
            resultGraphHash: 'f'.repeat(64),
            workflowUpdatedAt: '2026-07-15T00:00:03Z',
            sessionId: 'session-acknowledged-recovery',
          },
          agentBuilderHistory: {
            sessionId: 'session-acknowledged-recovery',
            latestOperationId: 'operation-acknowledged-recovery',
            acknowledged: false,
            completionEligible: false,
            parameterGroup: null,
            lastManuallyConfiguredTaskId: null,
            presentation: 'none',
          },
        },
      ],
    });
    vi.mocked(workflowApi.getDraftWorkflow).mockResolvedValue({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      workflow_id: 'workflow-old',
      graph_hash: 'f'.repeat(64),
      updated_at: '2026-07-15T00:00:03Z',
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-acknowledged-recovery',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'completed',
      messages: [],
      active_graph_mutation: {
        operation_id: 'operation-acknowledged-recovery',
        status: 'acknowledged',
        result_graph_hash: 'f'.repeat(64),
        saved_workflow_updated_at: '2026-07-15T00:00:03Z',
      },
      parameter_group: {
        group_id: 'group-acknowledged-recovery',
        status: 'completed',
        tasks: [],
      },
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    await waitFor(() => {
      const boundary = useWorkflowStore.getState().undoStack.at(-1);
      expect(boundary?.agentBuilderHistory?.acknowledged).toBe(true);
      expect(boundary?.agentBuilderHistory?.completionEligible).toBe(true);
    });
    expect(workflowApi.getDraftWorkflow).toHaveBeenCalledWith('workflow-old');
    expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
  });

  it('pending_ack Knowledge 선택은 새로고침 뒤 선택값을 유지하고 확인 중으로 잠근다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-old',
      'session-kb-pending-recovery',
    );
    const response: AgentBuilderMessageResponse = {
      request_id: 'request-kb-pending-recovery',
      status: 'graph_mutation_ready',
      structured_plan: null,
      knowledge_resolution: {
        resolution_id: 'resolve-kb-pending-recovery',
        timing: 'after_graph',
        required: true,
        candidates: [
          {
            candidate_id: 'safe-kb-pending-recovery',
            resolution_id: 'resolve-kb-pending-recovery',
            requirement_id: 'kr-pending-recovery',
            safe_label: '휴가 정책',
          },
        ],
        collections: [],
        ungrouped_kbs: [hierarchyKb('safe-kb-pending-recovery', '휴가 정책')],
        selected: [
          {
            candidate_id: 'safe-kb-pending-recovery',
            safe_label: '휴가 정책',
          },
        ],
        selected_collection_handles: [],
        selected_kb_handles: ['safe-kb-pending-recovery'],
        selection_status: 'pending_ack',
      },
      clarification_questions: ['사용할 Knowledge Base를 선택해주세요.'],
      clarification_options: [],
      validation_result: null,
      warnings: [],
    };
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-kb-pending-recovery',
      workflow_id: 'workflow-old',
      app_id: 'app-1',
      protocol_version: 'direct_edit_v1',
      status: 'graph_mutation_ready',
      messages: [
        {
          kind: 'assistant',
          request_id: response.request_id,
          response,
        },
      ],
      active_graph_mutation: {
        operation_id: 'operation-kb-pending-recovery',
        status: 'pending_ack',
        result_graph_hash: 'c'.repeat(64),
        saved_workflow_updated_at: '2026-07-14T00:00:00Z',
      },
      pending_request: null,
    });
    vi.mocked(agentBuilderApi.acknowledgeMutation).mockRejectedValue(
      new Error('acknowledgement unavailable'),
    );

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    expect(await screen.findByText('Workflow 확인 중')).toBeInTheDocument();
    const checkbox = screen.getByRole('checkbox', { name: '휴가 정책' });
    expect(checkbox).toBeChecked();
    expect(checkbox).toBeDisabled();
    expect(screen.getByRole('button', { name: '선택 적용' })).toBeDisabled();
    expect(agentBuilderApi.selectKnowledge).not.toHaveBeenCalled();
  });

  it('legacy stale_protocol session을 제거하고 새 direct-edit session으로 교체한다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-old',
      'session-legacy',
    );
    window.localStorage.setItem(
      'agent-builder:workflow-old:app-1',
      'session-legacy',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-legacy',
      workflow_id: 'workflow-old',
      protocol_version: null,
      status: 'stale_protocol',
      messages: [],
    });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct-new',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    await waitFor(() => {
      expect(agentBuilderApi.createSession).toHaveBeenCalledWith({
        workflowId: 'workflow-old',
        appId: 'app-1',
      });
    });
    expect(
      window.localStorage.getItem('agent-builder:workflow:workflow-old'),
    ).toBe('session-direct-new');
    expect(
      window.localStorage.getItem('agent-builder:workflow-old:app-1'),
    ).toBeNull();
  });

  it('legacy stale_protocol session은 안전한 대화 이력을 read-only로 남기고 direct session을 한 번만 만든다', async () => {
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-old',
      'session-legacy-safe',
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-legacy-safe',
      workflow_id: 'workflow-old',
      protocol_version: null,
      status: 'stale_protocol',
      messages: [
        {
          kind: 'user',
          request_id: 'request-legacy',
          content: '기존 미적용 workflow 초안',
          redacted: true,
        },
        {
          kind: 'assistant',
          request_id: 'request-legacy',
          response: {
            request_id: 'request-legacy',
            status: 'stale_protocol',
            clarification_questions: [],
            clarification_options: [],
            warnings: ['이전 Agent Builder 결과는 다시 적용할 수 없습니다.'],
          },
        },
      ],
    });
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct-safe',
      workflow_id: 'workflow-old',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-old"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    expect(
      await screen.findByText('기존 미적용 workflow 초안'),
    ).toBeInTheDocument();
    expect(screen.getByText('stale_protocol')).toBeInTheDocument();
    await waitFor(() => {
      expect(agentBuilderApi.createSession).toHaveBeenCalledTimes(1);
    });
    expect(
      window.localStorage.getItem('agent-builder:workflow:workflow-old'),
    ).toBe('session-direct-safe');
  });

  it('message 전송 중 stale_protocol이면 새 session으로 한 번 재시도한다', async () => {
    vi.mocked(agentBuilderApi.createSession)
      .mockResolvedValueOnce({
        session_id: 'session-stale',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'active',
        messages: [],
      })
      .mockResolvedValueOnce({
        session_id: 'session-recovered',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'active',
        messages: [],
      });
    vi.mocked(agentBuilderApi.sendMessage)
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: { status: 409, data: { detail: 'stale_protocol' } },
      })
      .mockResolvedValueOnce({
        request_id: 'request-recovered',
        status: 'unsupported',
        clarification_questions: [],
        clarification_options: [],
        warnings: [],
      });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(2);
    });
    expect(agentBuilderApi.sendMessage).toHaveBeenNthCalledWith(
      1,
      'session-stale',
      expect.any(Object),
    );
    expect(agentBuilderApi.sendMessage).toHaveBeenNthCalledWith(
      2,
      'session-recovered',
      expect.any(Object),
    );
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('요청에 실패'),
    );
  });

  it('새 session 재시도도 stale_protocol이면 원인을 구분해 표시한다', async () => {
    vi.mocked(agentBuilderApi.createSession)
      .mockResolvedValueOnce({
        session_id: 'session-stale',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'active',
        messages: [],
      })
      .mockResolvedValueOnce({
        session_id: 'session-recovered',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'active',
        messages: [],
      });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'stale_protocol' } },
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        '이전 Agent Builder 세션을 새 세션으로 전환하지 못했습니다. 다시 시도해주세요.',
      );
    });
    expect(agentBuilderApi.sendMessage).toHaveBeenCalledTimes(2);
  });

  it('client에서 감지한 stale_graph도 최신 상태 안내로 표시한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue(
      Object.assign(new Error('stale_graph'), { code: 'stale_graph' }),
    );

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'workflow가 변경되어 요청을 적용할 수 없습니다. 최신 상태를 확인해주세요.',
      );
    });
  });

  it('저장 대기 중 workflow가 바뀌면 이전 작업을 적용하지 않았다고 안내한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue(
      Object.assign(new Error('workflow_context_changed'), {
        code: 'workflow_context_changed',
      }),
    );

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'Workflow가 전환되어 이전 Agent Builder 작업을 적용하지 않았습니다.',
      );
    });
  });

  it('Gateway 502를 일반 요청 실패로 숨기지 않는다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue({
      isAxiosError: true,
      response: { status: 502, data: { detail: 'Bad Gateway' } },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '입력과 응답 노드를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'Agent Builder 서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요. (HTTP 502)',
      );
    });
  });

  it('legacy Knowledge 선택 입력은 전용 선택 화면 안내로 표시한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-direct',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 422,
        data: {
          detail: {
            code: 'invalid_request',
            message:
              '현재 Agent Builder에서는 대화 메시지로 Knowledge Base 선택을 제출할 수 없습니다.',
          },
        },
      },
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '이 Knowledge Base를 선택할게' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        '현재 Agent Builder에서는 대화 메시지로 Knowledge Base 선택을 제출할 수 없습니다. 표시된 Knowledge Base 선택 화면에서 선택해주세요.',
      );
    });
  });

  it('session 조회 5xx는 저장된 세션을 보존하고 세 번 확인 뒤 수동 재시도를 제공한다', async () => {
    vi.useFakeTimers();
    try {
      window.localStorage.setItem(
        'agent-builder:workflow:workflow-old',
        'session-recovery-502',
      );
      vi.mocked(agentBuilderApi.getSession)
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        })
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        })
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        })
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        })
        .mockResolvedValue({
          session_id: 'session-recovery-502',
          workflow_id: 'workflow-old',
          protocol_version: 'direct_edit_v1',
          status: 'active',
          messages: [],
        });

      render(
        <AgentBuilderPanel
          workflowId="workflow-old"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(4);
      expect(
        window.localStorage.getItem('agent-builder:workflow:workflow-old'),
      ).toBe('session-recovery-502');
      expect(screen.getByText('결과 확인 필요')).toBeInTheDocument();

      fireEvent.click(
        screen.getByRole('button', { name: 'Agent Builder 상태 다시 확인' }),
      );
      await act(async () => {
        await Promise.resolve();
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(5);
      expect(screen.queryByText('결과 확인 필요')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('pending polling 연결이 끊기면 planning 표시를 멈추고 결과 확인 상태로 전환한다', async () => {
    vi.useFakeTimers();
    try {
      window.localStorage.setItem(
        'agent-builder:workflow:workflow-old',
        'session-pending-disconnected',
      );
      const planningSession = {
        session_id: 'session-pending-disconnected',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1' as const,
        status: 'planning',
        messages: [
          {
            kind: 'assistant' as const,
            request_id: 'request-pending-disconnected',
            response: {
              request_id: 'request-pending-disconnected',
              status: 'planning' as const,
              clarification_questions: [],
              clarification_options: [],
              warnings: [],
            },
          },
        ],
        pending_request: {
          request_id: 'request-pending-disconnected',
          status: 'planning',
          created_at: new Date().toISOString(),
        },
      };
      vi.mocked(agentBuilderApi.getSession)
        .mockResolvedValueOnce(planningSession)
        .mockRejectedValue({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        });

      render(
        <AgentBuilderPanel
          workflowId="workflow-old"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByText('planning')).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
        await vi.advanceTimersByTimeAsync(2000);
        await vi.advanceTimersByTimeAsync(4000);
      });

      expect(screen.getByText('결과 확인 필요')).toBeInTheDocument();
      expect(screen.queryByText('planning')).toBeNull();
      expect(screen.queryByText('Workflow 계획 중')).toBeNull();
      expect(
        screen.queryByText('Agent Builder 요청이 진행 중입니다.'),
      ).toBeNull();
      expect(
        window.localStorage.getItem('agent-builder:workflow:workflow-old'),
      ).toBe('session-pending-disconnected');
    } finally {
      vi.useRealTimers();
    }
  });

  it('정상 planning polling 뒤 단발성 연결 실패는 연속 실패로 누적하지 않고 복구한다', async () => {
    vi.useFakeTimers();
    try {
      window.localStorage.setItem(
        'agent-builder:workflow:workflow-old',
        'session-pending-transient-failure',
      );
      const planningSession = {
        session_id: 'session-pending-transient-failure',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1' as const,
        status: 'planning',
        messages: [
          {
            kind: 'assistant' as const,
            request_id: 'request-pending-transient-failure',
            response: {
              request_id: 'request-pending-transient-failure',
              status: 'planning' as const,
              clarification_questions: [],
              clarification_options: [],
              warnings: [],
            },
          },
        ],
        pending_request: {
          request_id: 'request-pending-transient-failure',
          status: 'planning',
        },
      };
      const terminalSession = {
        ...planningSession,
        status: 'failed',
        messages: [
          {
            kind: 'assistant' as const,
            request_id: 'request-pending-transient-failure',
            response: {
              request_id: 'request-pending-transient-failure',
              status: 'failed' as const,
              clarification_questions: [],
              clarification_options: [],
              warnings: ['요청 처리에 실패했습니다.'],
            },
          },
        ],
        pending_request: null,
      };
      vi.mocked(agentBuilderApi.getSession)
        .mockResolvedValueOnce(planningSession)
        .mockResolvedValueOnce(planningSession)
        .mockResolvedValueOnce(planningSession)
        .mockResolvedValueOnce(planningSession)
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Bad Gateway' } },
        })
        .mockResolvedValue(terminalSession);

      render(
        <AgentBuilderPanel
          workflowId="workflow-old"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4_000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(5);
      expect(screen.queryByText('결과 확인 필요')).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(6);
      expect(screen.getByText('failed')).toBeInTheDocument();
      expect(screen.queryByText('결과 확인 필요')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('planning session은 60초까지 정상 polling하고 이후 장기 처리 상태에서 terminal 결과를 복구한다', async () => {
    vi.useFakeTimers();
    try {
      window.localStorage.setItem(
        'agent-builder:workflow:workflow-old',
        'session-planning-recovery',
      );
      const planningSession = {
        session_id: 'session-planning-recovery',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1' as const,
        status: 'planning',
        messages: [
          {
            kind: 'assistant' as const,
            request_id: 'request-planning-recovery',
            response: {
              request_id: 'request-planning-recovery',
              status: 'planning' as const,
              clarification_questions: [],
              clarification_options: [],
              warnings: [],
            },
          },
        ],
        pending_request: {
          request_id: 'request-planning-recovery',
          status: 'planning',
        },
      };
      vi.mocked(agentBuilderApi.getSession).mockResolvedValue(planningSession);

      render(
        <AgentBuilderPanel
          workflowId="workflow-old"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(4);
      expect(screen.queryByText('결과 확인 필요')).toBeNull();
      expect(screen.queryByText('평소보다 오래 걸리고 있습니다')).toBeNull();
      expect(
        window.localStorage.getItem('agent-builder:workflow:workflow-old'),
      ).toBe('session-planning-recovery');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(55_000);
      });
      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(15);
      expect(
        screen.getByText('평소보다 오래 걸리고 있습니다'),
      ).toBeInTheDocument();
      expect(screen.queryByText('결과 확인 필요')).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(170_000);
      });
      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(32);
      expect(screen.queryByText('결과 확인 필요')).toBeNull();

      vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
        ...planningSession,
        status: 'failed',
        messages: [
          {
            kind: 'assistant',
            request_id: 'request-planning-recovery',
            response: {
              request_id: 'request-planning-recovery',
              status: 'failed',
              clarification_questions: [],
              clarification_options: [],
              validation_result: {
                valid: false,
                issues: [
                  {
                    code: 'REQUEST_PROCESSING_TIMEOUT',
                    message: '처리 제한시간이 지나 요청을 종료했습니다.',
                  },
                ],
              },
              warnings: [],
            },
          },
        ],
        pending_request: null,
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8_000);
      });

      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(33);
      expect(screen.getByText('failed')).toBeInTheDocument();
      expect(screen.queryByText('결과 확인 필요')).toBeNull();
      expect(screen.queryByText('평소보다 오래 걸리고 있습니다')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('acknowledgement 확인은 canonical session만 세 번 조회한 뒤 결과 확인 필요 상태로 멈춘다', async () => {
    vi.useFakeTimers();
    try {
      window.localStorage.setItem(
        'agent-builder:workflow:workflow-old',
        'session-ack-recovery',
      );
      vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
        session_id: 'session-ack-recovery',
        workflow_id: 'workflow-old',
        protocol_version: 'direct_edit_v1',
        status: 'graph_mutation_ready',
        messages: [],
        active_graph_mutation: {
          operation_id: 'operation-ack-recovery',
          status: 'pending_ack',
          result_graph_hash: 'e'.repeat(64),
          saved_workflow_updated_at: '2026-07-15T00:00:00Z',
        },
      });
      render(
        <AgentBuilderPanel
          workflowId="workflow-old"
          appId="app-1"
          nodes={[]}
          edges={[]}
          hasUnsavedChanges={false}
        />,
      );
      fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });

      expect(agentBuilderApi.acknowledgeMutation).not.toHaveBeenCalled();
      expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(4);
      expect(screen.getByText('결과 확인 필요')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('Gateway timeout 뒤 서버의 processing request를 복구해 terminal 상태까지 polling한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-timeout-recovery',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue({
      isAxiosError: true,
      response: { status: 504, data: { detail: 'Gateway Timeout' } },
    });
    vi.mocked(agentBuilderApi.getSession)
      .mockResolvedValueOnce({
        session_id: 'session-timeout-recovery',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'planning',
        messages: [
          {
            kind: 'assistant',
            request_id: 'request-timeout-recovery',
            response: {
              request_id: 'request-timeout-recovery',
              status: 'planning',
              clarification_questions: [],
              clarification_options: [],
              warnings: [],
            },
          },
        ],
        pending_request: {
          request_id: 'request-timeout-recovery',
          status: 'planning',
        },
      })
      .mockResolvedValue({
        session_id: 'session-timeout-recovery',
        workflow_id: 'workflow-1',
        protocol_version: 'direct_edit_v1',
        status: 'failed',
        messages: [
          {
            kind: 'assistant',
            request_id: 'request-timeout-recovery',
            response: {
              request_id: 'request-timeout-recovery',
              status: 'failed',
              clarification_questions: [],
              clarification_options: [],
              warnings: ['요청 처리에 실패했습니다.'],
            },
          },
        ],
        pending_request: null,
      });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    expect(
      await screen.findByText('failed', {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(agentBuilderApi.getSession).toHaveBeenCalledTimes(2);
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('HTTP 504'),
    );
  });

  it('응답 유실 뒤 pending_request 없이 terminal session만 남아도 canonical 결과를 복구한다', async () => {
    vi.mocked(agentBuilderApi.createSession).mockResolvedValue({
      session_id: 'session-terminal-recovery',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'active',
      messages: [],
    });
    vi.mocked(agentBuilderApi.sendMessage).mockRejectedValue({
      isAxiosError: true,
      response: { status: 504, data: { detail: 'Gateway Timeout' } },
    });
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: 'session-terminal-recovery',
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'failed',
      messages: [
        {
          kind: 'assistant',
          request_id: 'request-terminal-recovery',
          response: {
            request_id: 'request-terminal-recovery',
            status: 'failed',
            clarification_questions: [],
            clarification_options: [],
            warnings: ['요청 처리에 실패했습니다.'],
          },
        },
      ],
      pending_request: null,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘' },
    });
    fireEvent.click(screen.getByLabelText('Agent Builder 요청 보내기'));

    expect(
      await screen.findByText('failed', {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('요청 처리에 실패했습니다.')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('HTTP 504'),
    );
  });

  it.each([
    {
      caseId: 'slack-bot-token',
      nodeId: 'slack',
      nodeType: 'slackPostNode',
      parameterKey: 'bot_token',
      label: 'Bot Token',
      nodeData: { title: 'Slack', authConfig: {} },
      expectedData: {
        authConfig: { token: 'workflow-node-secret://00000000-0000-4000-8000-000000000001' },
      },
    },
    {
      caseId: 'slack-webhook-url',
      nodeId: 'slack',
      nodeType: 'slackPostNode',
      parameterKey: 'url',
      label: 'Webhook URL',
      nodeData: { title: 'Slack' },
      expectedData: {
        url: 'workflow-node-secret://00000000-0000-4000-8000-000000000001',
      },
    },
    {
      caseId: 'github-api-token',
      nodeId: 'github',
      nodeType: 'githubNode',
      parameterKey: 'api_token',
      label: 'GitHub API Token',
      nodeData: { title: 'GitHub' },
      expectedData: {
        api_token: 'workflow-node-secret://00000000-0000-4000-8000-000000000001',
      },
    },
  ])(
    'sends $caseId through the workflow editor bridge',
    async ({
      caseId,
      nodeId,
      nodeType,
      parameterKey,
      label,
      nodeData,
      expectedData,
    }) => {
    const secretGroup: AgentBuilderParameterGroup = {
      group_id: `group-${caseId}`,
      status: 'active',
      tasks: [
        {
          task_id: `task-${caseId}`,
          group_id: `group-${caseId}`,
          step_id: `step-${caseId}`,
          node_id: nodeId,
          node_type: nodeType,
          parameter_key: parameterKey,
          label,
          input_type: 'secret',
          required: true,
          defer_policy: 'forbidden',
          status: 'active',
          task_version: 1,
          stable_order: 0,
          resolution_source: null,
          sensitivity: 'secret_forbidden',
          reason: 'Slack API 연결에 사용할 token입니다.',
          input_guidance: '새 값을 입력하면 기존 값을 교체합니다.',
        },
      ],
    };
    const targetNode = {
      ...node(nodeId, nodeType),
      data: nodeData,
    } as Node;
    window.localStorage.setItem(
      'agent-builder:workflow:workflow-1',
      `session-${caseId}`,
    );
    vi.mocked(agentBuilderApi.getSession).mockResolvedValue({
      session_id: `session-${caseId}`,
      workflow_id: 'workflow-1',
      protocol_version: 'direct_edit_v1',
      status: 'parameter_configuration',
      messages: [
        {
          kind: 'assistant',
          request_id: 'request-secret-bridge',
          response: {
            request_id: 'request-secret-bridge',
            status: 'graph_mutation_ready',
            clarification_questions: [],
            clarification_options: [],
            warnings: [],
          },
        },
      ],
      parameter_group: secretGroup,
    });
    useWorkflowStore.setState({
      activeWorkflowId: 'workflow-1',
      nodes: [targetNode],
      edges: [],
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
    vi.mocked(workflowApi.storeNodeSecret).mockResolvedValue({
      secret_reference:
        'workflow-node-secret://00000000-0000-4000-8000-000000000001',
      configured: true,
    });

    render(
      <AgentBuilderPanel
        workflowId="workflow-1"
        appId="app-1"
        nodes={[targetNode]}
        edges={[]}
        hasUnsavedChanges={false}
      />,
    );
    fireEvent.click(screen.getByLabelText('Agent Builder 열기'));

    const input = await screen.findByLabelText(label);
    fireEvent.change(input, { target: { value: 'new-masked-input' } });
    fireEvent.click(screen.getByRole('button', { name: '적용' }));

    await waitFor(() => {
      const stored = useWorkflowStore.getState().nodes[0]?.data;
      expect(stored).toMatchObject(expectedData);
      expect(JSON.stringify(stored)).not.toContain('new-masked-input');
    });
    expect(workflowApi.storeNodeSecret).toHaveBeenCalledWith('workflow-1', {
      node_id: nodeId,
      node_type: nodeType,
      parameter_key: parameterKey,
      secret_value: 'new-masked-input',
    });
    expect(agentBuilderApi.decideParameterTask).not.toHaveBeenCalled();
    },
  );
});
