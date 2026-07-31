import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MyModulePage from '@/app/dashboard/mymodule/page';

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
}));

vi.mock('@/app/features/app/api/moduleOperationsApi', () => ({
  moduleOperationsApi: {
    listModuleOperations: vi.fn(),
    getModuleOperationsCostSummary: vi.fn(),
  },
}));

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
  },
}));

vi.mock('@/app/features/workflow/api/workflowApi', () => ({
  workflowApi: {
    getDeploymentParameterOptimization: vi.fn(),
  },
}));

const { moduleOperationsApi } = await import(
  '@/app/features/app/api/moduleOperationsApi'
);
const { apiClient } = await import('@/lib/apiClient');
const { workflowApi } = await import('@/app/features/workflow/api/workflowApi');

describe('FR-014 워크플로우 화면 자동 최적화 관리', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.removeItem('mymodule:operations-view');
    vi.mocked(
      moduleOperationsApi.getModuleOperationsCostSummary,
    ).mockResolvedValue({
      active_workflow_count: 1,
      projected_month_cost: 20,
      projected_month_workflow_execution_cost: 14,
      projected_month_agent_builder_cost: 6,
    } as never);
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValue([
      {
        app: {
          id: 'app-1',
          name: '예산 위험 티켓 처리',
          workflow_id: 'workflow-1',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
          budget_status: { status: 'at_risk', usage_ratio: 0.95 },
          operation_metrics: {
            current_month_cost: 10,
            current_month_workflow_execution_cost: 7,
            current_month_agent_builder_cost: 3,
            projected_month_cost: 20,
            projected_month_workflow_execution_cost: 14,
            projected_month_agent_builder_cost: 6,
            previous_month_cost: 8,
          },
        },
        deployment: { state: 'active', deployment_id: 'deployment-1' },
        deploymentState: 'active',
        latestRun: { state: 'success' },
        permissionStatus: 'loaded',
        permission: { can_deploy: true },
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { id: 'org-1', name: '데모 조직', is_manager: true },
    } as never);
    vi.mocked(
      workflowApi.getDeploymentParameterOptimization,
    ).mockResolvedValue({
      enabled: true,
      status: 'collecting',
      node_ids: ['llm-triage'],
      node_count: 1,
      collected_runs: 12,
      check_every_runs: 50,
      validation_spend_usd: 0,
      monthly_validation_budget_usd: 3,
    } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it('배포된 workflow의 자동 최적화 관리는 해당 배포 설정을 불러온다', async () => {
    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    fireEvent.click(
      await screen.findByRole('button', { name: '자동 최적화 설정' }),
    );

    await waitFor(() => {
      expect(workflowApi.getDeploymentParameterOptimization).toHaveBeenCalledWith(
        'deployment-1',
      );
    });

    const dialog = await screen.findByRole('dialog', {
      name: '자동 최적화 관리',
    });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText('예산 위험 티켓 처리')).toBeInTheDocument();
    expect(routerMock.push).not.toHaveBeenCalled();
  });

  it('그리드에서 자동 최적화 상세를 숨기고 리스트에서 요약을 표시한다', async () => {
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValueOnce([
      {
        app: {
          id: 'app-1',
          name: '예산 위험 티켓 처리',
          workflow_id: 'workflow-1',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
          operation_metrics: {
            current_month_cost: 10,
            current_month_workflow_execution_cost: 7,
            current_month_agent_builder_cost: 3,
            projected_month_cost: 20,
            projected_month_workflow_execution_cost: 14,
            projected_month_agent_builder_cost: 6,
            previous_month_cost: 8,
          },
        },
        deployment: { state: 'active', deployment_id: 'deployment-1' },
        deploymentState: 'active',
        automaticOptimization: {
          enabled: true,
          status: 'collecting',
          node_count: 1,
          collected_runs: 12,
          check_every_runs: 50,
          validation_spend_usd: 0.25,
          monthly_validation_budget_usd: 3,
        },
        latestRun: { state: 'success' },
        permissionStatus: 'loaded',
        permission: { can_deploy: true },
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);

    render(<MyModulePage />);

    expect(
      screen.queryByLabelText('자동 파라미터 최적화 상태'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('자동 파라미터 최적화')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '리스트 보기' }));

    expect(
      await screen.findByLabelText('자동 파라미터 최적화 요약'),
    ).toBeVisible();
  });

  it('리스트 보기에서는 자동 최적화를 짧은 상태 요약과 설정 아이콘으로 표시한다', async () => {
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValueOnce([
      {
        app: {
          id: 'app-1',
          name: '예산 위험 티켓 처리',
          workflow_id: 'workflow-1',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
        },
        deployment: { state: 'active', deployment_id: 'deployment-1' },
        deploymentState: 'active',
        automaticOptimization: {
          enabled: true,
          status: 'collecting',
          node_count: 1,
          collected_runs: 12,
          check_every_runs: 50,
          validation_spend_usd: 0.25,
          monthly_validation_budget_usd: 3,
        },
        latestRun: { state: 'success' },
        permissionStatus: 'loaded',
        permission: { can_deploy: true },
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);

    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    const summary = await screen.findByLabelText('자동 파라미터 최적화 요약');
    expect(summary).toHaveTextContent('수집 중');
    expect(summary).toHaveTextContent('수집 12 / 50회');
    expect(summary).toHaveTextContent('월 검증 $0.25 / $3.00');
    expect(
      within(summary).getByRole('button', { name: '자동 최적화 설정' }),
    ).toBeEnabled();
    expect(summary.querySelector('progress')).not.toBeInTheDocument();
  });

  it('자동 최적화를 사용하지 않으면 꺼진 상태와 설정 행동을 표시한다', async () => {
    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    const optimization = await screen.findByLabelText('자동 파라미터 최적화 요약');
    expect(optimization).toHaveTextContent('미사용');
    expect(
      within(optimization).getByRole('button', { name: '자동 최적화 설정' }),
    ).toBeEnabled();
  });

  it('Python Unicode escape로 저장된 앱 이모지를 안전하게 표시한다', async () => {
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValueOnce([
      {
        app: {
          id: 'app-icon',
          name: '아이콘 복원 워크플로우',
          icon: {
            type: 'emoji',
            content: '\\U0001f9ea',
            background_color: '#E0F2FE',
          },
          workflow_id: 'workflow-icon',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
        },
        deployment: { state: 'undeployed' },
        deploymentState: 'undeployed',
        latestRun: { state: 'not_started' },
        permissionStatus: 'loaded',
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);

    render(<MyModulePage />);

    expect(await screen.findByText('🧪')).toBeInTheDocument();
  });

  it('예산 사용률 숫자가 아닌 API status로 운영 목록의 위험 상태를 표시한다', async () => {
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValueOnce([
      {
        app: {
          id: 'app-1',
          name: '서버 상태 기준 워크플로우',
          workflow_id: 'workflow-1',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
          budget_status: { status: 'normal', usage_ratio: 0.81 },
          operation_metrics: {
            current_month_cost: 10,
            current_month_workflow_execution_cost: 7,
            current_month_agent_builder_cost: 3,
            projected_month_cost: 20,
            projected_month_workflow_execution_cost: 14,
            projected_month_agent_builder_cost: 6,
            previous_month_cost: 8,
          },
        },
        deployment: { state: 'active', deployment_id: 'deployment-1' },
        deploymentState: 'active',
        latestRun: { state: 'success' },
        permissionStatus: 'loaded',
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);

    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    const usage = (await screen.findAllByText('81%')).at(-1)!;
    expect(usage.parentElement).toHaveTextContent('정상');
    expect(usage.parentElement).not.toHaveTextContent('위험');
  });

  it('월 예상 총비용 아래에 테스트/배포 실행과 Agent Builder 비용을 구분한다', async () => {
    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    const breakdown = await screen.findByLabelText(
      '예산 위험 티켓 처리 예상 비용 구성',
    );
    expect(breakdown).toHaveTextContent('테스트/배포 실행 $14.000');
    expect(breakdown).toHaveTextContent('Agent Builder $6.000');
  });

  it('미배포 워크플로우도 테스트 실행과 Agent Builder 월 예상 비용을 표시한다', async () => {
    vi.mocked(moduleOperationsApi.listModuleOperations).mockResolvedValueOnce([
      {
        app: {
          id: 'app-undeployed',
          name: '배포 전 워크플로우',
          workflow_id: 'workflow-undeployed',
          created_at: '2026-07-11T00:00:00.000Z',
          updated_at: '2026-07-11T00:00:00.000Z',
          operation_metrics: {
            current_month_cost: 3,
            current_month_workflow_execution_cost: 2,
            current_month_agent_builder_cost: 1,
            projected_month_cost: 6,
            projected_month_workflow_execution_cost: 4,
            projected_month_agent_builder_cost: 2,
            previous_month_cost: 0,
          },
        },
        deployment: { state: 'undeployed' },
        deploymentState: 'undeployed',
        latestRun: { state: 'not_started' },
        permissionStatus: 'loaded',
        permissionSources: [],
        dataQuality: {
          permissionSourcesUnavailable: false,
          latestRunUnavailable: false,
        },
      },
    ] as never);

    render(<MyModulePage />);
    fireEvent.click(await screen.findByRole('button', { name: '리스트 보기' }));

    const breakdown = await screen.findByLabelText(
      '배포 전 워크플로우 예상 비용 구성',
    );
    expect(breakdown.parentElement).toHaveTextContent('$6.000');
    expect(breakdown).toHaveTextContent('테스트 실행 $4.000');
    expect(breakdown).toHaveTextContent('Agent Builder $2.000');
    expect(screen.queryByText('배포 후 표시')).not.toBeInTheDocument();
    const optimization = screen.getByLabelText('자동 파라미터 최적화 요약');
    expect(optimization).toHaveTextContent('배포 후 설정');
    expect(optimization).toHaveTextContent('배포 후 설정할 수 있습니다.');
    expect(
      within(optimization).getByRole('button', { name: '자동 최적화 설정' }),
    ).toBeDisabled();
  });
});
