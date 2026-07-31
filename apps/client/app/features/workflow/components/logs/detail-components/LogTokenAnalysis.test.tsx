import { cleanup, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { LogTokenAnalysis } from './LogTokenAnalysis';
import type { LLMTrace, WorkflowRun } from '@/app/features/workflow/types/Api';

const baseRun: WorkflowRun = {
  id: 'run-1',
  workflow_id: 'workflow-1',
  user_id: 'user-1',
  status: 'success',
  trigger_mode: 'manual',
  started_at: '2026-06-27T00:00:00Z',
  node_runs: [
    {
      id: 'node-run-1',
      node_id: 'llm-node-1',
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
};

const trace: LLMTrace = {
  id: 'trace-1',
  workflow_id: 'workflow-1',
  workflow_run_id: 'run-1',
  node_id: 'llm-node-1',
  model_id: 'model-1',
  model_name: 'gpt-4o-mini',
  provider: 'openai',
  credential_id: 'credential-1',
  prompt_tokens: 10,
  completion_tokens: 20,
  total_tokens: 30,
  total_cost: 0.02,
  latency_ms: 123,
  status: 'success',
  created_at: '2026-06-27T00:00:01Z',
};

beforeEach(() => {
  cleanup();
});

describe('LogTokenAnalysis', () => {
  it('LLM trace가 있으면 node output usage보다 trace row를 우선 표시한다', () => {
    render(<LogTokenAnalysis run={baseRun} llmTraces={[trace]} />);

    expect(screen.getAllByText('30 tks')).toHaveLength(2);
    expect(screen.getAllByText('gpt-4o-mini')).toHaveLength(2);
    expect(screen.getAllByText(/\$0\.02000/)).toHaveLength(2);
    expect(screen.getByText(/123ms/)).toBeInTheDocument();
    expect(screen.queryByText('legacy-model')).not.toBeInTheDocument();
  });

  it('trace 조회에 실패하면 안내와 함께 legacy usage fallback을 표시한다', () => {
    render(<LogTokenAnalysis run={baseRun} error />);

    expect(
      screen.getByText(/LLM trace 조회에 실패해 실행 로그에 포함된 usage/),
    ).toBeInTheDocument();
    expect(screen.getAllByText('3 tks')).toHaveLength(2);
    expect(screen.getAllByText('legacy-model')).toHaveLength(2);
  });

  it('표시할 usage가 없고 loading 중이면 loading 상태를 표시한다', () => {
    render(<LogTokenAnalysis run={{ ...baseRun, node_runs: [] }} loading />);

    expect(screen.getByText('LLM trace를 불러오는 중입니다...')).toBeInTheDocument();
  });

  it('0-token failed trace도 숨기지 않고 상태를 표시한다', () => {
    render(
      <LogTokenAnalysis
        run={baseRun}
        llmTraces={[
          {
            ...trace,
            id: 'trace-failed',
            prompt_tokens: 0,
            completion_tokens: 0,
            total_tokens: 0,
            total_cost: 0,
            latency_ms: 0,
            status: 'failed',
          },
        ]}
      />,
    );

    expect(screen.getAllByText('0 tks')).toHaveLength(2);
    expect(screen.getByText(/\$0\.00000 · 0ms · failed/)).toBeInTheDocument();
    expect(screen.queryByText('legacy-model')).not.toBeInTheDocument();
  });

  it('노드별 막대는 모델 합계가 아니라 노드 최대 토큰 기준으로 계산한다', () => {
    const { container } = render(
      <LogTokenAnalysis
        run={baseRun}
        llmTraces={[
          trace,
          {
            ...trace,
            id: 'trace-2',
            node_id: 'llm-node-2',
            prompt_tokens: 5,
            completion_tokens: 5,
            total_tokens: 10,
            total_cost: 0.01,
          },
        ]}
      />,
    );

    const nodeBars = container.querySelectorAll('.bg-amber-400');
    expect(nodeBars[0]).toHaveStyle({ width: '100%' });
  });
});
