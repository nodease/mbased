import { describe, expect, it } from 'vitest';
import {
  formatCost,
  formatLatency,
  formatTokens,
  isTestExecutionActionDisabled,
  readCost,
  readNodeFinishExecutionSummary,
  readWorkflowFinishExecutionSummary,
  readTokenUsage,
  summarizeWorkflowExecution,
} from '../utils/testExecutionSummary';

describe('workflow test cases: 실행 편의성', () => {
  it('usage.total_tokens가 있으면 해당 토큰 수를 읽는다', () => {
    expect(readTokenUsage({ usage: { total_tokens: 123 } })).toBe(123);
  });

  it('prompt_tokens와 completion_tokens가 있으면 합산 토큰 수를 읽는다', () => {
    expect(
      readTokenUsage({
        usage: { prompt_tokens: 40, completion_tokens: 60 },
      }),
    ).toBe(100);
  });

  it('cost 또는 usage.total_cost가 있으면 비용을 읽는다', () => {
    expect(readCost({ cost: 0.0012 })).toBe(0.0012);
    expect(readCost({ usage: { total_cost: 0.0024 } })).toBe(0.0024);
  });

  it('토큰 또는 비용 정보가 없으면 표시값은 dash가 된다', () => {
    expect(readTokenUsage({ usage: {} })).toBeUndefined();
    expect(readCost({ usage: {} })).toBeUndefined();
    expect(formatTokens(undefined)).toBe('-');
    expect(formatCost(undefined)).toBe('-');
  });

  it('node_start와 node_finish 수신 시각 차이를 ms 또는 s 단위로 표시한다', () => {
    expect(formatLatency(530)).toBe('530ms');
    expect(formatLatency(5200)).toBe('5.2s');
  });

  it('node_finish 표준 필드가 있으면 output usage보다 우선해서 읽는다', () => {
    const summary = readNodeFinishExecutionSummary(
      {
        output: {
          usage: {
            total_tokens: 10,
            total_cost: 0.99,
          },
          cost: 0.88,
        },
        latency_ms: 3571,
        total_tokens: 361,
        total_cost: 0.001964,
      },
      9999,
    );

    expect(summary).toEqual({
      latencyMs: 3571,
      totalTokens: 361,
      totalCost: 0.001964,
    });
  });

  it('node_finish 표준 필드가 없으면 기존 output usage/cost와 수신 시각 fallback을 사용한다', () => {
    const summary = readNodeFinishExecutionSummary(
      {
        output: {
          usage: {
            prompt_tokens: 40,
            completion_tokens: 60,
            total_cost: 0.0024,
          },
        },
      },
      530,
    );

    expect(summary).toEqual({
      latencyMs: 530,
      totalTokens: 100,
      totalCost: 0.0024,
    });
  });

  it('node_finish malformed usage와 비정상 표준 필드는 표시 요약에서 제외한다', () => {
    const summary = readNodeFinishExecutionSummary(
      {
        output: {
          usage: {
            prompt_tokens: 40,
            completion_tokens: 2,
            total_tokens: 'secret-like-token-count',
            total_cost: 'secret-like-cost',
          },
          cost: 'secret-like-direct-cost',
        },
        latency_ms: 'secret-like-latency',
        total_tokens: 'secret-like-total',
        total_cost: 'secret-like-cost',
      },
      250,
    );

    expect(summary).toEqual({
      latencyMs: 250,
      totalTokens: 42,
      totalCost: undefined,
    });
    expect(JSON.stringify(summary)).not.toContain('secret-like');
  });

  it('전체 테스트 실행 완료 시 전체 시간, 비용, 토큰 사용량을 계산한다', () => {
    const summary = summarizeWorkflowExecution(
      [
        {
          nodeId: 'llm-1',
          status: 'success',
          latencyMs: 120,
          totalTokens: 100,
          cost: 0.001,
        },
        {
          nodeId: 'llm-2',
          status: 'success',
          latencyMs: 180,
          totalTokens: 250,
          cost: 0.002,
        },
      ],
      1000,
      1450,
      {
        duration: 0.34,
        total_tokens: 8420,
        total_cost: 0.0842,
      },
    );

    expect(summary).toEqual({
      serverDurationMs: 340,
      screenCompletionDurationMs: 450,
      totalLatencyMs: 450,
      totalTokens: 8420,
      totalCost: 0.0842,
    });
  });

  it('전체 요약에서 화면 완료 시간은 startedAt/finishedAt 차이로 계산한다', () => {
    const summary = summarizeWorkflowExecution(
      [
        {
          nodeId: 'llm-1',
          status: 'success',
          latencyMs: 3400,
          totalTokens: 361,
          cost: 0.001964,
        },
      ],
      1000,
      9600,
    );

    expect(summary.screenCompletionDurationMs).toBe(8600);
    expect(formatLatency(summary.screenCompletionDurationMs)).toBe('8.6s');
  });

  it('workflow_finish summary가 있으면 서버 실행 시간, 전체 토큰, 전체 비용을 우선 사용한다', () => {
    const summary = readWorkflowFinishExecutionSummary({
      duration: 3.4,
      total_tokens: 8420,
      total_cost: 0.0842,
    });

    expect(summary).toEqual({
      serverDurationMs: 3400,
      totalTokens: 8420,
      totalCost: 0.0842,
    });
    expect(formatLatency(summary.serverDurationMs)).toBe('3.4s');
  });

  it('workflow_finish duration이 음수이면 0ms로 보정하고 malformed usage는 버린다', () => {
    const summary = readWorkflowFinishExecutionSummary({
      duration: -3.4,
      total_tokens: 'secret-like-total',
      total_cost: 'secret-like-cost',
    });

    expect(summary).toEqual({
      serverDurationMs: 0,
      totalTokens: undefined,
      totalCost: undefined,
    });
    expect(JSON.stringify(summary)).not.toContain('secret-like');
  });

  it('workflow_finish 서버 실행 시간이 없으면 노드별 latency 합산을 fallback으로 사용한다', () => {
    const summary = summarizeWorkflowExecution(
      [
        {
          nodeId: 'start',
          status: 'success',
          latencyMs: 516,
        },
        {
          nodeId: 'llm',
          status: 'success',
          latencyMs: 1600,
          totalTokens: 165,
          cost: 0.000113,
        },
      ],
      1000,
      5800,
      {},
    );

    expect(summary.serverDurationMs).toBe(2116);
    expect(formatLatency(summary.serverDurationMs)).toBe('2.1s');
    expect(formatLatency(summary.screenCompletionDurationMs)).toBe('4.8s');
  });

  it('서버 실행 시간과 노드별 latency가 모두 없으면 서버 실행 시간은 dash로 표시한다', () => {
    const summary = summarizeWorkflowExecution([], 1000, 9600, {});

    expect(summary.serverDurationMs).toBeUndefined();
    expect(formatLatency(summary.serverDurationMs)).toBe('-');
    expect(formatLatency(summary.screenCompletionDurationMs)).toBe('8.6s');
  });

  it('테스트 실행 중이거나 준비 중이면 실행 액션은 비활성화된다', () => {
    expect(
      isTestExecutionActionDisabled({
        isExecuting: true,
        isUploading: false,
        isPreparing: false,
        canExecute: true,
      }),
    ).toBe(true);
    expect(
      isTestExecutionActionDisabled({
        isExecuting: false,
        isUploading: false,
        isPreparing: true,
        canExecute: true,
      }),
    ).toBe(true);
  });

  it('실행 권한이 없으면 테스트 실행 액션은 비활성화된다', () => {
    expect(
      isTestExecutionActionDisabled({
        isExecuting: false,
        isUploading: false,
        isPreparing: false,
        canExecute: false,
      }),
    ).toBe(true);
  });

  it('실행 중도 준비 중도 아니고 권한이 있으면 테스트 실행 액션은 활성화된다', () => {
    expect(
      isTestExecutionActionDisabled({
        isExecuting: false,
        isUploading: false,
        isPreparing: false,
        canExecute: true,
      }),
    ).toBe(false);
  });

  it.todo('권한 없는 사용자의 테스트 실행 요청은 403으로 거부된다');
  it.todo('scope 밖 workflow 테스트 실행 요청은 404로 처리된다');
  it.todo(
    '기존 테스트 실행 스트리밍 API가 node_start/node_finish/workflow_finish 이벤트를 반환한다',
  );
  it.todo('캔버스에는 별도 테스트 실행 요약 패널이 표시되지 않는다');
  it.todo(
    '프론트에서 테스트 버튼이 disabled여도 API 직접 호출 권한 검증은 Gateway에서 유지된다',
  );
});
