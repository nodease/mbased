import { Edge } from '@xyflow/react';
import { WorkflowDraftRequest } from '../types/Workflow';
import { Node } from '../types/Nodes';
import {
  DashboardStatsResponse,
  WorkflowResponse,
  WorkflowRun,
  WorkflowRunListResponse,
} from '../types/Api';
import { DeploymentResponse } from '../types/Deployment';

const now = new Date().toISOString();

export const mockWorkflowId = 'mock';
export const mockAppId = 'mock-app';

export const mockWorkflowResponse: WorkflowResponse = {
  id: mockWorkflowId,
  app_id: mockAppId,
  created_at: now,
  updated_at: now,
};

export const mockWorkflowDraft: WorkflowDraftRequest = {
  viewport: { x: 80, y: 60, zoom: 0.85 },
  nodes: [
    {
      id: 'mock-start',
      type: 'startNode',
      position: { x: 0, y: 120 },
      data: {
        title: '고객 문의 입력',
        triggerType: 'manual',
        variables: [
          {
            id: 'customer_message',
            name: 'customer_message',
            label: '고객 문의',
            type: 'paragraph',
            required: true,
            placeholder: '고객이 남긴 문의 내용을 붙여넣으세요.',
          },
          {
            id: 'priority',
            name: 'priority',
            label: '우선순위',
            type: 'select',
            required: false,
            options: [
              { label: '일반', value: 'normal' },
              { label: '긴급', value: 'urgent' },
            ],
          },
        ],
      },
    },
    {
      id: 'mock-llm',
      type: 'llmNode',
      position: { x: 360, y: 40 },
      data: {
        title: '문의 의도 분석',
        provider: 'openai',
        model_id: 'gpt-4.1-mini',
        fallback_model_id: '',
        system_prompt: '고객 문의를 읽고 의도, 감정, 다음 행동을 요약하세요.',
        user_prompt: '{{customer_message}}',
        assistant_prompt: '',
        referenced_variables: [
          { name: 'customer_message', value_selector: ['mock-start', 'customer_message'] },
        ],
        context_variable: '',
        parameters: {
          temperature: 0.3,
          top_p: 1,
          max_tokens: 1200,
          presence_penalty: 0,
          frequency_penalty: 0,
          stop: [],
        },
      },
    },
    {
      id: 'mock-condition',
      type: 'conditionNode',
      position: { x: 720, y: 120 },
      data: {
        title: '긴급 여부 분기',
        cases: [
          {
            id: 'urgent-case',
            case_name: '긴급',
            logical_operator: 'and',
            conditions: [
              {
                id: 'priority-check',
                variable_selector: ['mock-start', 'priority'],
                operator: 'equals',
                value: 'urgent',
              },
            ],
          },
        ],
      },
    },
    {
      id: 'mock-template',
      type: 'templateNode',
      position: { x: 1080, y: 20 },
      data: {
        title: '응답 초안 생성',
        template:
          '문의 요약:\n{{analysis}}\n\n고객에게 보낼 답변 초안을 정중한 톤으로 작성하세요.',
        variables: [
          { name: 'analysis', value_selector: ['mock-llm', 'text'] },
        ],
      },
    },
    {
      id: 'mock-answer',
      type: 'answerNode',
      position: { x: 1440, y: 120 },
      data: {
        title: '최종 응답',
        outputs: [
          { variable: 'reply', value_selector: ['mock-template', 'text'] },
        ],
      },
    },
  ] as Node[],
  edges: [
    {
      id: 'mock-start-to-llm',
      source: 'mock-start',
      target: 'mock-llm',
    },
    {
      id: 'mock-llm-to-condition',
      source: 'mock-llm',
      target: 'mock-condition',
    },
    {
      id: 'mock-condition-to-template',
      source: 'mock-condition',
      target: 'mock-template',
    },
    {
      id: 'mock-template-to-answer',
      source: 'mock-template',
      target: 'mock-answer',
    },
  ] as Edge[],
  features: {
    mockMode: true,
    noteNodes: [
      {
        id: 'mock-note',
        type: 'note',
        position: { x: 720, y: 380 },
        data: {
          title: 'UI/UX 작업 메모',
          content:
            '이 화면은 백엔드 없이 워크플로우 편집기를 확인하기 위한 mock 모드입니다.',
        },
      },
    ],
  },
  envVariables: [
    {
      id: 'mock-env-api-base',
      key: 'API_BASE_URL',
      value: 'https://api.example.com',
      type: 'string',
    },
  ],
  runtimeVariables: [
    {
      id: 'mock-runtime-user-id',
      key: 'user_id',
      name: '사용자 ID',
    },
  ],
};

export const mockDashboardStats: DashboardStatsResponse = {
  summary: {
    totalRuns: 128,
    successRate: 93.8,
    avgDuration: 2.7,
    totalCost: 4.82,
    avgTokenPerRun: 1840,
    avgCostPerRun: 0.038,
  },
  runsOverTime: [
    { date: '06-19', count: 14, total_cost: 0.42, total_tokens: 22100 },
    { date: '06-20', count: 19, total_cost: 0.57, total_tokens: 30200 },
    { date: '06-21', count: 11, total_cost: 0.31, total_tokens: 17600 },
    { date: '06-22', count: 23, total_cost: 0.76, total_tokens: 41100 },
    { date: '06-23', count: 18, total_cost: 0.64, total_tokens: 33800 },
    { date: '06-24', count: 27, total_cost: 1.12, total_tokens: 52000 },
    { date: '06-25', count: 16, total_cost: 1.0, total_tokens: 38700 },
  ],
  minCostRuns: [
    {
      run_id: 'mock-run-low-1',
      started_at: '2026-06-25T01:10:00.000Z',
      total_tokens: 720,
      total_cost: 0.012,
    },
    {
      run_id: 'mock-run-low-2',
      started_at: '2026-06-25T03:35:00.000Z',
      total_tokens: 910,
      total_cost: 0.016,
    },
  ],
  maxCostRuns: [
    {
      run_id: 'mock-run-high-1',
      started_at: '2026-06-25T05:20:00.000Z',
      total_tokens: 7100,
      total_cost: 0.19,
    },
    {
      run_id: 'mock-run-high-2',
      started_at: '2026-06-24T10:45:00.000Z',
      total_tokens: 5800,
      total_cost: 0.16,
    },
  ],
  failureAnalysis: [
    {
      node_id: 'mock-llm',
      node_name: '문의 의도 분석',
      count: 5,
      reason: 'Provider timeout',
      rate: '3.9%',
    },
    {
      node_id: 'mock-template',
      node_name: '응답 초안 생성',
      count: 3,
      reason: 'Missing variable',
      rate: '2.3%',
    },
  ],
  recentFailures: [
    {
      run_id: 'mock-run-fail-1',
      failed_at: '2026-06-25T04:12:00.000Z',
      node_id: 'mock-llm',
      error_message: 'LLM provider timeout after 30s',
    },
    {
      run_id: 'mock-run-fail-2',
      failed_at: '2026-06-24T13:08:00.000Z',
      node_id: 'mock-template',
      error_message: 'analysis variable is empty',
    },
  ],
};

export const mockWorkflowRuns: WorkflowRunListResponse = {
  total: 3,
  items: [
    {
      id: 'mock-run-success-1',
      workflow_id: mockWorkflowId,
      user_id: 'mock-user',
      status: 'success',
      trigger_mode: 'manual',
      inputs: { customer_message: '배송 일정이 궁금합니다.', priority: 'normal' },
      outputs: { reply: '배송 일정 확인 방법을 안내했습니다.' },
      started_at: '2026-06-25T06:20:00.000Z',
      finished_at: '2026-06-25T06:20:03.000Z',
      duration: 3,
      workflow_version: 4,
      total_tokens: 1520,
      total_cost: 0.032,
    },
    {
      id: 'mock-run-fail-1',
      workflow_id: mockWorkflowId,
      user_id: 'mock-user',
      status: 'failed',
      trigger_mode: 'manual',
      inputs: { customer_message: '환불을 바로 처리해 주세요.', priority: 'urgent' },
      error_message: 'LLM provider timeout after 30s',
      started_at: '2026-06-25T04:12:00.000Z',
      finished_at: '2026-06-25T04:12:30.000Z',
      duration: 30,
      workflow_version: 4,
      total_tokens: 0,
      total_cost: 0,
    },
    {
      id: 'mock-run-success-2',
      workflow_id: mockWorkflowId,
      user_id: 'mock-user',
      status: 'success',
      trigger_mode: 'api',
      inputs: { customer_message: '영수증 재발급이 필요합니다.', priority: 'normal' },
      outputs: { reply: '영수증 재발급 절차를 안내했습니다.' },
      started_at: '2026-06-24T08:40:00.000Z',
      finished_at: '2026-06-24T08:40:02.000Z',
      duration: 2,
      workflow_version: 3,
      total_tokens: 1280,
      total_cost: 0.027,
    },
  ],
};

export const mockWorkflowRunDetail: WorkflowRun = {
  ...mockWorkflowRuns.items[0],
  node_runs: [
    {
      id: 'mock-node-run-start',
      node_id: 'mock-start',
      node_type: 'startNode',
      status: 'success',
      inputs: {},
      outputs: { customer_message: '배송 일정이 궁금합니다.', priority: 'normal' },
      started_at: '2026-06-25T06:20:00.000Z',
      finished_at: '2026-06-25T06:20:00.000Z',
    },
    {
      id: 'mock-node-run-llm',
      node_id: 'mock-llm',
      node_type: 'llmNode',
      status: 'success',
      inputs: { customer_message: '배송 일정이 궁금합니다.' },
      outputs: { text: '고객은 배송 일정 확인을 원합니다.' },
      started_at: '2026-06-25T06:20:00.000Z',
      finished_at: '2026-06-25T06:20:02.000Z',
    },
  ],
};

export const mockDeployments: DeploymentResponse[] = [];

const readMockInputValue = (
  userInput: Record<string, unknown> | FormData | undefined,
  key: string,
  fallback: string,
) => {
  if (!userInput) return fallback;
  if (typeof FormData !== 'undefined' && userInput instanceof FormData) {
    const value = userInput.get(key);
    return typeof value === 'string' && value.trim() ? value : fallback;
  }

  const value = (userInput as Record<string, unknown>)[key];
  return typeof value === 'string' && value.trim() ? value : fallback;
};

export const createMockWorkflowExecuteResult = (
  userInput?: Record<string, unknown> | FormData,
) => {
  const customerMessage = readMockInputValue(
    userInput,
    'customer_message',
    '배송 일정이 궁금합니다.',
  );
  const priority = readMockInputValue(userInput, 'priority', 'normal');
  const isUrgent = priority === 'urgent';
  const llmText = isUrgent
    ? `고객은 "${customerMessage}" 문의에 대해 긴급한 처리를 원합니다.`
    : `고객은 "${customerMessage}" 문의에 대해 차분한 안내를 원합니다.`;
  const templateText = isUrgent
    ? `안녕하세요. 남겨주신 문의 "${customerMessage}"는 긴급 건으로 확인했습니다. 담당자가 우선 처리하겠습니다.`
    : `안녕하세요. 남겨주신 문의 "${customerMessage}"를 확인했습니다. 담당자가 순차적으로 안내드리겠습니다.`;

  return {
    'mock-start': {
      customer_message: customerMessage,
      priority,
    },
    'mock-llm': {
      text: llmText,
    },
    'mock-condition': {
      selected_case: isUrgent ? 'urgent' : 'default',
      matched: isUrgent,
    },
    'mock-template': {
      text: templateText,
    },
    'mock-answer': {
      reply: templateText,
    },
  };
};

export const mockWorkflowExecuteResult = createMockWorkflowExecuteResult();

type MockWorkflowStreamEvent =
  | {
      type: 'node_start';
      data: { node_id: string; node_type: string };
    }
  | {
      type: 'node_finish';
      data: {
        node_id: string;
        node_type: string;
        output: Record<string, unknown>;
      };
    }
  | {
      type: 'workflow_finish';
      data: typeof mockWorkflowExecuteResult;
    };

export const createMockWorkflowStreamEvents = (
  userInput?: Record<string, unknown> | FormData,
) => {
  const result = createMockWorkflowExecuteResult(userInput);

  return [
  {
    type: 'node_start',
    data: { node_id: 'mock-start', node_type: 'startNode' },
  },
  {
    type: 'node_finish',
    data: {
      node_id: 'mock-start',
      node_type: 'startNode',
      output: result['mock-start'],
    },
  },
  {
    type: 'node_start',
    data: { node_id: 'mock-llm', node_type: 'llmNode' },
  },
  {
    type: 'node_finish',
    data: {
      node_id: 'mock-llm',
      node_type: 'llmNode',
      output: result['mock-llm'],
    },
  },
  {
    type: 'node_start',
    data: { node_id: 'mock-condition', node_type: 'conditionNode' },
  },
  {
    type: 'node_finish',
    data: {
      node_id: 'mock-condition',
      node_type: 'conditionNode',
      output: result['mock-condition'],
    },
  },
  {
    type: 'node_start',
    data: { node_id: 'mock-template', node_type: 'templateNode' },
  },
  {
    type: 'node_finish',
    data: {
      node_id: 'mock-template',
      node_type: 'templateNode',
      output: result['mock-template'],
    },
  },
  {
    type: 'node_start',
    data: { node_id: 'mock-answer', node_type: 'answerNode' },
  },
  {
    type: 'node_finish',
    data: {
      node_id: 'mock-answer',
      node_type: 'answerNode',
      output: result['mock-answer'],
    },
  },
  {
    type: 'workflow_finish',
    data: result,
  },
] satisfies MockWorkflowStreamEvent[];
};

export const mockWorkflowStreamEvents = createMockWorkflowStreamEvents();
