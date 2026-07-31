import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const workflowApiMock = vi.hoisted(() => ({
  getCostOptimizerLatestBaseline: vi.fn(),
  listCostOptimizerBaselines: vi.fn(),
}));

vi.mock('../../api/workflowApi', () => ({
  workflowApi: workflowApiMock,
}));

const comparableBaseline = {
  baseline_id: 'baseline-1',
  baseline_source: 'workflow_node_run',
  source_workflow_node_run_id: 'baseline-1',
  workflow_run_id: 'run-1',
  workflow_id: 'workflow-1',
  node_id: 'llm-triage',
  run_started_at: '2026-07-04T00:00:00Z',
  workflow_run_status: 'success',
  node_status: 'success',
  model: 'gpt-4.1-mini',
  cost: 0.0012,
  total_tokens: 420,
  latency_ms: 1800,
  input_available: true,
  output_available: true,
  usage_available: true,
  trace_available: true,
  compare_available: true,
  unavailable_reason: null,
  input_preview: 'billing escalation',
  output_preview: 'enterprise response',
  has_trace: true,
  downstream_compatibility: {
    state: 'compatible',
    label: '검증 가능',
    message: 'baseline downstream is compatible',
  },
};

const nonComparableBaseline = {
  ...comparableBaseline,
  baseline_id: 'baseline-2',
  input_available: false,
  compare_available: false,
  unavailable_reason: 'input_payload_unavailable',
  input_preview: '',
  output_preview: 'retained output',
};

const loadBaselineSelection = async () => {
  const path = '../../components/costOptimizer/CostOptimizerBaselineSelection';
  const module = await import(/* @vite-ignore */ path);
  return module.CostOptimizerBaselineSelection;
};

describe('FR-002 Cost Optimizer baseline 선택', () => {
  beforeEach(() => {
    workflowApiMock.getCostOptimizerLatestBaseline.mockResolvedValue({
      baseline: comparableBaseline,
    });
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 2,
      limit: 20,
      offset: 0,
      items: [comparableBaseline, nonComparableBaseline],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('baseline 선택 화면은 최신 자동 선택 없이 실행 로그 목록을 바로 제공한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ limit: 20, offset: 0 }),
      );
    });

    expect(
      screen.queryByRole('button', { name: /최신 실행 로그로 비교하기/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('searchbox', { name: /baseline 검색/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: 'gpt-4.1-mini' }),
    ).toBeInTheDocument();
  });

  it('baseline preview는 긴 값과 여러 JSON field를 임의로 줄이지 않고 표시한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();
    const longMessage =
      'enterprise 고객의 정산 파일이 다시 생성되지 않아 월말 마감이 지연되고 있으며 담당자가 다운로드 위치와 재처리 방법을 확인해야 합니다.';
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
        ...comparableBaseline,
        input_preview: JSON.stringify({
          message: longMessage,
          customerTier: 'enterprise',
          region: 'KR',
          channel: 'slack',
          severity: 'high',
        }),
        output_preview: JSON.stringify({
          approvalRequired: false,
          replyDraft: '정산 파일 재생성 방법과 다운로드 위치를 안내합니다.',
          nextAction: 'notify-customer',
          owner: 'billing-ops',
          confidence: 0.91,
        }),
        },
      ],
    });

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(new RegExp(longMessage))).toBeInTheDocument();
    expect(screen.getByText('severity')).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
    expect(screen.getByText('confidence')).toBeInTheDocument();
    expect(screen.getByText('0.91')).toBeInTheDocument();
    expect(screen.queryByText(/\.{3}/)).not.toBeInTheDocument();
  });

  it('baseline preview는 LLM text 안의 JSON 문자열도 viewer로 풀어서 표시한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
        ...comparableBaseline,
        output_preview: JSON.stringify({
          cost: 0.0012,
          text: JSON.stringify({
            approvalRequired: false,
            mailDraft: '고객에게 정산 파일 재생성 방법을 안내합니다.',
          }),
          model: 'gpt-4.1',
        }),
        },
      ],
    });

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText('approvalRequired')).toBeInTheDocument();
    expect(screen.getByText('false')).toBeInTheDocument();
    expect(screen.getByText('mailDraft')).toBeInTheDocument();
    expect(
      screen.getByText('고객에게 정산 파일 재생성 방법을 안내합니다.'),
    ).toBeInTheDocument();
  });

  it('baseline preview는 잘린 output_preview보다 보존된 output payload를 우선 표시한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
        ...comparableBaseline,
        output_preview: '{"cost":0.0012,"text":"{\\"approvalRequired\\":false',
        output: {
          cost: 0.0012,
          text: JSON.stringify({
            approvalRequired: false,
            mailDraft: 'preview가 잘려도 이 전체 답변 초안을 보여줘야 합니다.',
          }),
        },
        },
      ],
    });

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText('mailDraft')).toBeInTheDocument();
    expect(
      screen.getByText('preview가 잘려도 이 전체 답변 초안을 보여줘야 합니다.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/approvalRequired\\":false/)).not.toBeInTheDocument();
  });

  it('이전 실행 로그 picker는 baseline row의 핵심 정보를 표시한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: /최신 실행 로그로 비교하기/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /닫기/i }),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ limit: 20, offset: 0 }),
      );
    });
    expect(
      screen.getByRole('option', { name: 'gpt-4.1-mini' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/420/)).toBeInTheDocument();
    expect(screen.getByText(/1.8s/)).toBeInTheDocument();
    expect(screen.getByText(/0.0012/)).toBeInTheDocument();
    expect(screen.getByText(/billing escalation/)).toBeInTheDocument();
    expect(screen.getByText(/enterprise response/)).toBeInTheDocument();
    expect(screen.getByText(/검증 가능/)).toBeInTheDocument();
  });

  it('비교 불가 baseline row는 표시하되 선택해도 baseline selected를 호출하지 않는다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();
    const onBaselineSelected = vi.fn();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={onBaselineSelected}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText(/비교 불가/);
    fireEvent.click(screen.getByText(/retained output/));

    expect(onBaselineSelected).not.toHaveBeenCalled();
    expect(
      screen.getByText(/입력 기록이 보관 기간 만료 또는 보안 정책/i),
    ).toBeInTheDocument();
  });

  it('picker 검색과 필터는 baseline list API query로 전달된다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByRole('searchbox'), {
      target: { value: 'billing' },
    });

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ q: 'billing', model: undefined }),
      );
    });

    fireEvent.change(screen.getByRole('combobox', { name: '모델' }), {
      target: { value: 'gpt-4.1-mini' },
    });

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ q: 'billing', model: 'gpt-4.1-mini' }),
      );
    });

    fireEvent.change(screen.getByLabelText(/비교 가능 여부/i), {
      target: { value: 'true' },
    });
    fireEvent.change(screen.getByLabelText(/정렬/i), {
      target: { value: 'latency_desc' },
    });
    fireEvent.change(screen.getByLabelText(/시작일/i), {
      target: { value: '2026-07-01' },
    });
    fireEvent.change(screen.getByLabelText(/종료일/i), {
      target: { value: '2026-07-04' },
    });

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({
          q: 'billing',
          model: 'gpt-4.1-mini',
          compare_available: true,
          sort: 'latency_desc',
          date_from: '2026-07-01T00:00:00',
          date_to: '2026-07-04T23:59:59',
        }),
      );
    });
  });

  it('picker 모델 필터는 API가 반환한 baseline 모델만 옵션으로 제공한다', async () => {
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 2,
      limit: 20,
      offset: 0,
      items: [
        comparableBaseline,
        {
          ...comparableBaseline,
          baseline_id: 'baseline-claude',
          model: 'claude-sonnet-4-5',
        },
      ],
    });
    const CostOptimizerBaselineSelection = await loadBaselineSelection();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const modelFilter = await screen.findByLabelText(/모델/i);
    expect(
      screen.getByRole('option', { name: 'gpt-4.1-mini' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: 'claude-sonnet-4-5' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'GPT mini' }),
    ).not.toBeInTheDocument();

    fireEvent.change(modelFilter, {
      target: { value: 'claude-sonnet-4-5' },
    });

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ model: 'claude-sonnet-4-5' }),
      );
    });
  });

  it('output schema title이 있으면 preview field label로 사용한다', async () => {
    const CostOptimizerBaselineSelection = await loadBaselineSelection();
    workflowApiMock.listCostOptimizerBaselines.mockResolvedValue({
      total: 1,
      limit: 20,
      offset: 0,
      items: [
        {
          ...comparableBaseline,
          output_preview: JSON.stringify({ reply_draft: '고객에게 안내합니다.' }),
          node_options: {
            output_format: {
              type: 'json',
              schema: {
                properties: {
                  reply_draft: { title: '고객 답변 초안', type: 'string' },
                },
              },
            },
          },
        },
      ],
    });

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText('고객 답변 초안')).toBeInTheDocument();
    expect(screen.queryByText('reply_draft')).not.toBeInTheDocument();
  });

  it('picker는 더 보기로 다음 offset을 요청하고 기존 row 뒤에 append한다', async () => {
    workflowApiMock.listCostOptimizerBaselines
      .mockResolvedValueOnce({
        total: 3,
        limit: 20,
        offset: 0,
        items: [comparableBaseline, nonComparableBaseline],
      })
      .mockResolvedValueOnce({
        total: 3,
        limit: 20,
        offset: 2,
        items: [
          {
            ...comparableBaseline,
            baseline_id: 'baseline-3',
            model: 'gpt-4.1',
            input_preview: 'third input',
            output_preview: 'third output',
          },
        ],
      });
    const CostOptimizerBaselineSelection = await loadBaselineSelection();

    render(
      <CostOptimizerBaselineSelection
        workflowId="workflow-1"
        nodeId="llm-triage"
        onBaselineSelected={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await screen.findByText(/총 3개 중 2개 표시/);
    fireEvent.click(screen.getByRole('button', { name: /더 보기/i }));

    await waitFor(() => {
      expect(workflowApiMock.listCostOptimizerBaselines).toHaveBeenLastCalledWith(
        'workflow-1',
        'llm-triage',
        expect.objectContaining({ limit: 20, offset: 2 }),
      );
    });
    expect(screen.getByText(/third input/)).toBeInTheDocument();
    expect(screen.getByText(/총 3개 중 3개 표시/)).toBeInTheDocument();
  });
});
