import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LogDetail } from './LogDetail';
import { workflowApi } from '../../api/workflowApi';
import type {
  LLMTrace,
  WorkflowNodeRun,
  WorkflowRun,
} from '@/app/features/workflow/types/Api';

vi.mock('../../api/workflowApi', () => ({
  workflowApi: {
    getWorkflowRunLlmTraces: vi.fn(),
  },
}));

const createRun = (id: string, workflowId = 'workflow-1'): WorkflowRun => ({
  id,
  workflow_id: workflowId,
  user_id: 'user-1',
  status: 'success',
  trigger_mode: 'manual',
  started_at: '2026-06-27T00:00:00Z',
  duration: 1,
  node_runs: [
    {
      id: `${id}-node-run`,
      node_id: `${id}-node`,
      node_type: 'llmNode',
      status: 'success',
      started_at: '2026-06-27T00:00:00Z',
      outputs: {
        model: 'legacy-model',
        cost: 0.001,
        usage: {
          prompt_tokens: 1,
          completion_tokens: 2,
          total_tokens: 3,
        },
      },
    },
  ],
});

const createTrace = (runId: string): LLMTrace => ({
  id: `${runId}-trace`,
  workflow_id: 'workflow-1',
  workflow_run_id: runId,
  node_id: `${runId}-node`,
  model_id: 'model-1',
  model_name: 'gpt-trace-model',
  provider: 'openai',
  credential_id: 'credential-1',
  prompt_tokens: 10,
  completion_tokens: 20,
  total_tokens: 30,
  total_cost: 0.02,
  latency_ms: 123,
  status: 'success',
  created_at: '2026-06-27T00:00:01Z',
});

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
});

describe('LogDetail', () => {
  it('배포 실행 LLM trace에서 사용자가 이해할 수 있는 모델 선택 근거를 보여준다', async () => {
    vi.mocked(workflowApi.getWorkflowRunLlmTraces).mockResolvedValue({
      total: 0,
      limit: 500,
      offset: 0,
      items: [],
    });
    const run = createRun('run-routing');
    const llmNodeRun = run.node_runs?.[0] as WorkflowNodeRun;
    llmNodeRun.outputs = {
      ...llmNodeRun.outputs,
      model: 'gpt-4.1',
      metadata: {
        model_routing: {
          judge: {
            reason_short: '복수 근거 종합',
            candidate_model_count: 3,
          },
        },
      },
    };
    llmNodeRun.trace_metadata = {
      llm: {
        strategy_id: 'judge_bootstrap_incremental_v1',
        selected_model: 'gpt-4.1',
        fallback_model: 'gpt-4.1-mini',
        decision_source: 'runtime_judge',
        reason_code: 'judge_bootstrap_required',
        policy_version: 'routing-policy-v10',
        input_length_bucket: 'long',
        output_format: 'json',
        schema_required: true,
        knowledge_enabled: true,
        decision_factors: {
          learning_mode: 'judge_first',
          judged_request_count: 8,
          local_confidence_threshold: 0.78,
        },
        judge: {
          model: 'gpt-4.1-mini',
          confidence: 0.84,
          reason_code: 'structured_reasoning_required',
          cost: 0.00013,
        },
        judge_called: true,
      },
    };

    await act(async () => {
      render(<LogDetail run={run} />);
    });

    const routingDetails = screen
      .getByText('모델 선택 결과')
      .closest('section');
    expect(routingDetails).not.toBeNull();
    const routing = within(routingDetails as HTMLElement);
    expect(routing.getByText('gpt-4.1')).toBeInTheDocument();
    expect(
      routing.getByText('Judge 실행 성공'),
    ).toBeInTheDocument();
    expect(routing.getByText('Judge 모델')).toBeInTheDocument();
    expect(routing.getByText('gpt-4.1-mini')).toBeInTheDocument();
    expect(routing.getByText('판단 확신도')).toBeInTheDocument();
    expect(routing.getByText('84.0%')).toBeInTheDocument();
    expect(routing.getByText('Judge 비용')).toBeInTheDocument();
    expect(routing.getByText('$0.000130')).toBeInTheDocument();
    expect(routing.getByText('복수 근거 종합')).toBeInTheDocument();
    expect(routing.getByText('3개')).toBeInTheDocument();
    expect(routing.getByText(/학습 방식:/)).toBeInTheDocument();
    expect(routing.getByText(/Judge 학습 중/)).toBeInTheDocument();
    expect(routing.getByText(/기준 78.0%/)).toBeInTheDocument();
  });

  it('run 전환 시 이전 LLM trace state를 즉시 초기화한다', async () => {
    let resolveSecondTrace: (
      value: Awaited<ReturnType<typeof workflowApi.getWorkflowRunLlmTraces>>,
    ) => void = () => undefined;

    vi.mocked(workflowApi.getWorkflowRunLlmTraces)
      .mockResolvedValueOnce({
        total: 1,
        limit: 500,
        offset: 0,
        items: [createTrace('run-1')],
      })
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecondTrace = resolve;
        }),
      );

    const { rerender } = render(<LogDetail run={createRun('run-1')} />);

    await waitFor(() => {
      expect(screen.getAllByText('gpt-trace-model').length).toBeGreaterThan(0);
    });

    rerender(<LogDetail run={{ ...createRun('run-2'), node_runs: [] }} />);

    await waitFor(() => {
      expect(screen.queryByText('gpt-trace-model')).not.toBeInTheDocument();
    });
    expect(
      screen.getByText('LLM trace를 불러오는 중입니다...'),
    ).toBeInTheDocument();

    await act(async () => {
      resolveSecondTrace({
        total: 0,
        limit: 500,
        offset: 0,
        items: [],
      });
    });
  });
});
