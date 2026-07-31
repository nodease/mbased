'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertTriangle,
  CheckCircle2,
  Edit3,
  ExternalLink,
  Filter,
  LayoutGrid,
  List,
  Play,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Workflow,
} from 'lucide-react';

import CreateAppModal from '@/app/features/app/components/create-app-modal';
import EditAppModal from '@/app/features/app/components/edit-app-modal';
import { appApi, type App, type AppIcon } from '@/app/features/app/api/appApi';
import { BudgetStatusBadge } from '@/app/features/budget/components/BudgetStatusBadge';
import { budgetRunBlockMessage } from '@/app/features/budget/utils/budgetGuard';
import {
  budgetStatusLabel,
  type BudgetUsageStatus,
} from '@/app/features/budget/types';
import {
  moduleOperationsApi,
  type ModuleOperationRow,
  type ModuleOperationsListParams,
  type ModuleRunState,
} from '@/app/features/app/api/moduleOperationsApi';
import {
  buildModuleRunHref,
  canRunDeployedModule,
  getModuleRunDisabledReason,
} from '@/app/features/app/utils/moduleRunNavigation';
import { deploymentApiErrorMessage } from '@/app/features/workflow/utils/deploymentPreflightMessage';
import { AutomaticOptimizationManagementModal } from '@/app/features/workflow/components/deployment/AutomaticOptimizationManagementModal';
import { apiClient } from '@/lib/apiClient';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import type {
  DeploymentParameterOptimizationConfig,
  DeploymentParameterOptimizationSummary,
} from '@/app/features/workflow/types/Deployment';
import {
  DashboardPageHeader,
  DashboardPanel,
} from '@/app/features/dashboard/components/DashboardSurface';

type PermissionFilter = 'all' | 'executable' | 'editable' | 'manageable';
type DeploymentFilter = 'all' | 'active' | 'inactive' | 'undeployed';
type RunFilter = 'all' | 'running' | 'failed';
type OperationsViewMode = 'list' | 'grid';

const PAGE_SIZE = 100;
const OPERATIONS_VIEW_STORAGE_KEY = 'mymodule:operations-view';

const runLabels: Record<ModuleRunState, string> = {
  running: '실행 중',
  success: '정상',
  failed: '오류',
  not_started: '기록 없음',
  unavailable: '확인 필요',
};

const deploymentLabels: Record<DeploymentFilter, string> = {
  all: '전체',
  active: '배포 중',
  inactive: '배포 꺼짐',
  undeployed: '미배포',
};

const deploymentTone: Record<Exclude<DeploymentFilter, 'all'>, string> = {
  active: 'border-green-200 bg-green-50 text-green-700',
  inactive: 'border-amber-200 bg-amber-50 text-amber-700',
  undeployed: 'border-slate-200 bg-slate-50 text-slate-600',
};

const runTone: Record<ModuleRunState, string> = {
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  success: 'border-green-200 bg-green-50 text-green-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  not_started: 'border-slate-200 bg-slate-50 text-slate-600',
  unavailable: 'border-slate-200 bg-slate-50 text-slate-600',
};

const formatDate = (value?: string) => {
  if (!value) return '-';
  return new Date(value).toLocaleDateString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const formatCurrency = (value?: number | null) => {
  if (value == null) return '-';

  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  })}`;
};

const formatValidationBudget = (value: number) => `$${value.toFixed(2)}`;

const displayAppIcon = (icon?: Pick<AppIcon, 'type' | 'content'>) => {
  const content = icon?.content?.trim();
  if (!content) return 'N';

  const pythonUnicodeEscape = /^\\U([0-9a-fA-F]{8})$/.exec(content);
  if (icon?.type === 'emoji' && pythonUnicodeEscape) {
    const codePoint = Number.parseInt(pythonUnicodeEscape[1], 16);
    if (codePoint <= 0x10ffff) return String.fromCodePoint(codePoint);
  }

  return content;
};

const automaticOptimizationStatusLabel = {
  collecting: '수집 중',
  ready: '점검 대기',
  paused: '일시 중지',
  budget_exhausted: '월 예산 도달',
  failed: '점검 실패',
  disabled: '미사용',
} as const;

function AutomaticOptimizationTableCell({
  summary,
  hasDeployment,
  canManage,
  isLoading,
  onManage,
}: {
  summary: ModuleOperationRow['automaticOptimization'];
  hasDeployment: boolean;
  canManage: boolean;
  isLoading: boolean;
  onManage: () => void;
}) {
  const isEnabled = Boolean(summary?.enabled);
  const status = summary?.status || 'disabled';
  const collectedRuns = summary?.collected_runs || 0;
  const checkEveryRuns = summary?.check_every_runs || 50;
  const spend = summary?.validation_spend_usd || 0;
  const monthlyBudget = summary?.monthly_validation_budget_usd || 3;
  const statusClassName = !isEnabled
    ? 'border-slate-200 bg-slate-50 text-slate-600'
    : status === 'budget_exhausted' || status === 'failed'
      ? 'border-amber-200 bg-amber-50 text-amber-700'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700';
  const statusLabel = hasDeployment
    ? automaticOptimizationStatusLabel[status]
    : '배포 후 설정';

  return (
    <div
      aria-label="자동 파라미터 최적화 요약"
      className="min-w-40 space-y-1.5"
    >
      <div className="flex items-center justify-between gap-2">
        <Badge
          className={
            hasDeployment
              ? statusClassName
              : 'border-slate-200 bg-slate-50 text-slate-600'
          }
        >
          {statusLabel}
        </Badge>
        <button
          type="button"
          aria-label="자동 최적화 설정"
          title="자동 최적화 설정"
          onClick={onManage}
          disabled={!hasDeployment || !canManage || isLoading}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:border-slate-100 disabled:text-slate-300"
        >
          {isLoading ? (
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <SlidersHorizontal className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      {hasDeployment && isEnabled ? (
        <>
          <p className="whitespace-nowrap text-xs font-medium text-slate-700">
            수집 {Math.min(collectedRuns, checkEveryRuns)} / {checkEveryRuns}회
          </p>
          <p className="whitespace-nowrap text-[11px] text-slate-500">
            월 검증 {formatValidationBudget(spend)} /{' '}
            {formatValidationBudget(monthlyBudget)}
          </p>
        </>
      ) : (
        <p className="whitespace-nowrap text-xs text-slate-500">
          {hasDeployment
            ? '설정에서 사용 여부를 변경합니다.'
            : '배포 후 설정할 수 있습니다.'}
        </p>
      )}
    </div>
  );
}

type CostOptimizationSignal = {
  monthlyCost: number | null;
  workflowExecutionCost: number | null;
  agentBuilderCost: number | null;
  trendPercent: number | null;
  usageDataComplete: boolean;
  unresolvedProviderCallCount: number;
  budgetUsageRatio: number | null;
  budgetStatus: BudgetUsageStatus | null;
};

const budgetStatusPresentation: Record<
  BudgetUsageStatus,
  { textClassName: string; barClassName: string }
> = {
  normal: {
    textClassName: 'text-emerald-600',
    barClassName: 'bg-emerald-500',
  },
  at_risk: {
    textClassName: 'text-amber-600',
    barClassName: 'bg-amber-500',
  },
  exceeded: {
    textClassName: 'text-red-600',
    barClassName: 'bg-red-500',
  },
};

const costSignalOf = (row: ModuleOperationRow): CostOptimizationSignal => {
  const metrics = row.app.operation_metrics;
  const monthlyCost =
    metrics?.projected_month_cost ?? metrics?.current_month_cost ?? null;
  const agentBuilderCost =
    metrics?.projected_month_agent_builder_cost ??
    metrics?.current_month_agent_builder_cost ??
    0;
  const workflowExecutionCost =
    metrics?.projected_month_workflow_execution_cost ??
    metrics?.current_month_workflow_execution_cost ??
    (monthlyCost == null ? null : Math.max(monthlyCost - agentBuilderCost, 0));
  const trendPercent = metrics?.trend_percent ?? null;
  const budgetUsageRatio = row.app.budget_status?.usage_ratio ?? null;
  const budgetStatus = row.app.budget_status?.status ?? null;

  return {
    monthlyCost,
    workflowExecutionCost,
    agentBuilderCost,
    trendPercent,
    usageDataComplete: metrics?.usage_data_complete ?? true,
    unresolvedProviderCallCount:
      metrics?.unresolved_provider_call_count ?? 0,
    budgetUsageRatio,
    budgetStatus,
  };
};

function ModuleProjectedCost({
  appName,
  deploymentState,
  costSignal,
  valueClassName,
}: {
  appName: string;
  deploymentState: ModuleOperationRow['deploymentState'];
  costSignal: CostOptimizationSignal;
  valueClassName: string;
}) {
  const hasNoCost =
    costSignal.monthlyCost == null || costSignal.monthlyCost === 0;

  return (
    <div>
      <p className={valueClassName}>{formatCurrency(costSignal.monthlyCost)}</p>
      <div
        className="mt-1 space-y-0.5 text-xs text-slate-500"
        aria-label={`${appName} 예상 비용 구성`}
      >
        <p>
          {deploymentState === 'undeployed'
            ? '테스트 실행'
            : '테스트/배포 실행'}{' '}
          {formatCurrency(costSignal.workflowExecutionCost)}
        </p>
        <p>Agent Builder {formatCurrency(costSignal.agentBuilderCost)}</p>
      </div>
      {hasNoCost && <p className="mt-1 text-xs text-slate-400">비용 없음</p>}
      {!costSignal.usageDataComplete && (
        <p className="mt-1 text-xs font-medium text-amber-700">
          미확정 {costSignal.unresolvedProviderCallCount}건
        </p>
      )}
    </div>
  );
}

type OrganizationResponse = {
  id: string;
  name: string;
  is_manager?: boolean;
};

const canEditApp = (row: ModuleOperationRow, isOrgManager: boolean) =>
  isOrgManager ||
  (row.permissionStatus === 'loaded' && Boolean(row.permission?.can_manage));

const canToggleDeployment = (row: ModuleOperationRow) =>
  row.permissionStatus === 'loaded' &&
  Boolean(row.permission?.can_deploy || row.permission?.can_manage) &&
  Boolean(row.deployment.deployment_id);

const canOpenModule = (row: ModuleOperationRow) =>
  row.permissionStatus === 'loaded' && Boolean(row.permission?.can_read);

const capabilityParamOf = (
  filter: PermissionFilter,
): ModuleOperationsListParams['capability'] => {
  if (filter === 'executable') return 'execute';
  if (filter === 'editable') return 'write';
  if (filter === 'manageable') return 'manage';
  return undefined;
};

export default function MyModulePage() {
  const router = useRouter();
  const [rows, setRows] = useState<ModuleOperationRow[]>([]);
  const [isOrgManager, setIsOrgManager] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [permissionFilter, setPermissionFilter] =
    useState<PermissionFilter>('all');
  const [deploymentFilter, setDeploymentFilter] =
    useState<DeploymentFilter>('all');
  const [runFilter, setRunFilter] = useState<RunFilter>('all');
  const [viewMode, setViewMode] = useState<OperationsViewMode>('grid');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [editingApp, setEditingApp] = useState<App | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState('');
  const [automaticOptimizationTarget, setAutomaticOptimizationTarget] =
    useState<ModuleOperationRow | null>(null);
  const [automaticOptimizationSummary, setAutomaticOptimizationSummary] =
    useState<DeploymentParameterOptimizationSummary | null>(null);
  const [
    isLoadingAutomaticOptimizationId,
    setIsLoadingAutomaticOptimizationId,
  ] = useState<string | null>(null);
  const requestSeqRef = useRef(0);

  const buildListParams = useCallback(
    (offset: number): ModuleOperationsListParams => {
      const trimmedQuery = debouncedSearchQuery.trim();
      return {
        q: trimmedQuery || undefined,
        capability: capabilityParamOf(permissionFilter),
        deployment_state:
          deploymentFilter === 'all' ? undefined : deploymentFilter,
        run_state: runFilter === 'all' ? undefined : runFilter,
        limit: PAGE_SIZE,
        offset,
      };
    },
    [debouncedSearchQuery, deploymentFilter, permissionFilter, runFilter],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, 300);

    return () => window.clearTimeout(timeoutId);
  }, [searchQuery]);

  useEffect(() => {
    try {
      const savedViewMode = window.localStorage.getItem(
        OPERATIONS_VIEW_STORAGE_KEY,
      );
      if (savedViewMode === 'list' || savedViewMode === 'grid') {
        setViewMode(savedViewMode);
      }
    } catch {
      // 저장소를 사용할 수 없는 환경에서는 기본 그리드 보기를 유지한다.
    }
  }, []);

  const handleViewModeChange = (nextViewMode: OperationsViewMode) => {
    setViewMode(nextViewMode);
    try {
      window.localStorage.setItem(OPERATIONS_VIEW_STORAGE_KEY, nextViewMode);
    } catch {
      // 보기 전환은 저장 실패와 관계없이 동작한다.
    }
  };

  const loadModules = useCallback(
    async ({ offset = 0, append = false } = {}) => {
      const requestSeq = requestSeqRef.current + 1;
      requestSeqRef.current = requestSeq;
      try {
        if (append) {
          setIsLoadingMore(true);
        } else {
          setIsLoading(true);
        }
        setError('');
        const data = await moduleOperationsApi.listModuleOperations(
          buildListParams(offset),
        );
        if (requestSeq !== requestSeqRef.current) return;
        setRows((currentRows) => (append ? [...currentRows, ...data] : data));
        setHasMore(data.length === PAGE_SIZE);

        try {
          const organizationResponse =
            await apiClient.get<OrganizationResponse>('/organizations/current');
          if (requestSeq !== requestSeqRef.current) return;
          setIsOrgManager(Boolean(organizationResponse.data.is_manager));
        } catch {
          if (requestSeq !== requestSeqRef.current) return;
          setIsOrgManager(false);
        }
      } catch {
        if (requestSeq !== requestSeqRef.current) return;
        setError('모듈 운영 현황을 불러오지 못했습니다.');
      } finally {
        if (requestSeq === requestSeqRef.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [buildListParams],
  );

  useEffect(() => {
    loadModules();
  }, [loadModules]);

  useEffect(() => {
    const handleOpenModal = () => {
      setIsCreateModalOpen(true);
    };

    window.addEventListener('openCreateAppModal', handleOpenModal);
    return () =>
      window.removeEventListener('openCreateAppModal', handleOpenModal);
  }, []);

  const unavailableCount = useMemo(
    () =>
      rows.filter(
        (row) =>
          row.dataQuality.permissionSourcesUnavailable ||
          row.dataQuality.latestRunUnavailable,
      ).length,
    [rows],
  );

  const handleModuleClick = (row: ModuleOperationRow) => {
    if (!canOpenModule(row)) return;
    const targetId = row.app.workflow_id || row.app.id;
    router.push(`/modules/${targetId}`);
  };

  const handleRunModule = (row: ModuleOperationRow) => {
    if (!canRunDeployedModule(row)) return;
    const href = buildModuleRunHref(row);
    if (!href) return;
    router.push(href);
  };

  const handleEditApp = async (row: ModuleOperationRow) => {
    if (!canEditApp(row, isOrgManager)) return;
    try {
      const app = await appApi.getApp(row.app.id);
      setEditingApp(app);
    } catch {
      alert('앱 정보를 불러오지 못했습니다.');
    }
  };

  const handleToggleDeployment = async (row: ModuleOperationRow) => {
    if (!canToggleDeployment(row) || !row.deployment.deployment_id) return;

    try {
      await appApi.toggleDeployment(row.deployment.deployment_id);
      loadModules();
    } catch (error) {
      alert(deploymentApiErrorMessage(error, '배포 상태 변경에 실패했습니다.'));
    }
  };

  const handleManageAutomaticOptimization = async (row: ModuleOperationRow) => {
    const deploymentId = row.deployment.deployment_id;
    if (!deploymentId) {
      alert('배포된 workflow에서만 자동 최적화를 관리할 수 있습니다.');
      return;
    }

    setIsLoadingAutomaticOptimizationId(row.app.id);
    try {
      const summary =
        await workflowApi.getDeploymentParameterOptimization(deploymentId);
      setAutomaticOptimizationSummary(summary);
      setAutomaticOptimizationTarget(row);
    } catch {
      alert('자동 최적화 설정을 불러오지 못했습니다.');
    } finally {
      setIsLoadingAutomaticOptimizationId(null);
    }
  };

  const handleSaveAutomaticOptimization = async (
    config: DeploymentParameterOptimizationConfig,
  ) => {
    if (!automaticOptimizationTarget?.deployment.deployment_id) return;

    try {
      const summary = await workflowApi.updateDeploymentParameterOptimization(
        automaticOptimizationTarget.deployment.deployment_id,
        config,
      );
      setRows((currentRows) =>
        currentRows.map((row) =>
          row.app.id === automaticOptimizationTarget.app.id
            ? { ...row, automaticOptimization: summary }
            : row,
        ),
      );
      setAutomaticOptimizationSummary(summary);
      setAutomaticOptimizationTarget(null);
    } catch {
      alert('자동 최적화 설정 저장에 실패했습니다.');
    }
  };

  return (
    <div className="min-h-full bg-white px-6 py-8">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <DashboardPageHeader
          icon={Workflow}
          title="워크플로우 목록"
          description="워크플로우 접근 권한, 배포 상태, 실행 흐름을 한 화면에서 확인합니다."
          meta={
            unavailableCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                일부 권한 출처 또는 실행 상태를 확인할 수 없어 확인 필요로
                표시됩니다.
              </span>
            )
          }
          action={
            <div className="flex items-center gap-2">
              <button
                onClick={() => loadModules()}
                className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100"
              >
                <RefreshCw className="h-4 w-4" />
                새로고침
              </button>
              <button
                onClick={() => setIsCreateModalOpen(true)}
                className="inline-flex h-10 items-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800"
              >
                <Plus className="h-4 w-4" />새 워크플로우
              </button>
            </div>
          }
        />

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">
            {error}
          </div>
        )}

        <DashboardPanel
          title="운영 현황"
          icon={SlidersHorizontal}
          aside={
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-slate-500 sm:inline">
                현재 로드 {rows.length}개{hasMore ? ' 이상' : ''}
              </span>
              <div
                className="inline-flex rounded-md border border-slate-200 bg-slate-50 p-1"
                aria-label="운영 현황 보기 방식"
              >
                <button
                  type="button"
                  aria-label="리스트 보기"
                  aria-pressed={viewMode === 'list'}
                  onClick={() => handleViewModeChange('list')}
                  className={`inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-semibold transition-colors ${
                    viewMode === 'list'
                      ? 'bg-white text-slate-950 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  <List className="h-4 w-4" />
                  리스트
                </button>
                <button
                  type="button"
                  aria-label="그리드 보기"
                  aria-pressed={viewMode === 'grid'}
                  onClick={() => handleViewModeChange('grid')}
                  className={`inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-semibold transition-colors ${
                    viewMode === 'grid'
                      ? 'bg-white text-slate-950 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  <LayoutGrid className="h-4 w-4" />
                  그리드
                </button>
              </div>
            </div>
          }
        >
          <div className="border-b border-slate-100 bg-white px-5 py-4">
            <div className="grid gap-3 lg:grid-cols-[1fr_auto_auto_auto]">
              <label className="relative block">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  placeholder="모듈명, 설명 검색"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  className="h-10 w-full rounded-md border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </label>

              <FilterSelect
                label="권한"
                value={permissionFilter}
                onChange={(value) =>
                  setPermissionFilter(value as PermissionFilter)
                }
                options={[
                  ['all', '전체 권한'],
                  ['executable', '실행 가능'],
                  ['editable', '워크플로우 수정 가능'],
                  ['manageable', '워크플로우 관리 가능'],
                ]}
              />
              <FilterSelect
                label="배포"
                value={deploymentFilter}
                onChange={(value) =>
                  setDeploymentFilter(value as DeploymentFilter)
                }
                options={[
                  ['all', '전체 배포'],
                  ['active', '배포 중'],
                  ['inactive', '배포 꺼짐'],
                  ['undeployed', '미배포'],
                ]}
              />
              <FilterSelect
                label="실행"
                value={runFilter}
                onChange={(value) => setRunFilter(value as RunFilter)}
                options={[
                  ['all', '전체 실행'],
                  ['running', '실행 중'],
                  ['failed', '오류만'],
                ]}
              />
            </div>
          </div>

          {isLoading ? (
            <ModuleListSkeleton viewMode={viewMode} />
          ) : rows.length === 0 ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-semibold text-slate-700">
                조건에 맞는 모듈이 없습니다.
              </p>
              <button
                onClick={() => {
                  setSearchQuery('');
                  setPermissionFilter('all');
                  setDeploymentFilter('all');
                  setRunFilter('all');
                }}
                className="mt-3 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
              >
                필터 초기화
              </button>
            </div>
          ) : (
            <div>
              {viewMode === 'list' ? (
                <div className="overflow-x-auto">
                  <table className="min-w-[1120px] table-fixed divide-y divide-slate-100">
                    <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-500">
                      <tr>
                        <th className="w-[20%] px-5 py-3">워크플로우</th>
                        <th className="w-[12%] px-4 py-3">월 예상 비용</th>
                        <th className="w-[10%] px-4 py-3">증가 추세</th>
                        <th className="w-[14%] px-4 py-3">예산 사용률</th>
                        <th className="w-[18%] px-4 py-3">자동 최적화</th>
                        <th className="w-[10%] px-4 py-3">상태</th>
                        <th className="w-[16%] px-5 py-3 text-right">작업</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 bg-white">
                      {rows.map((row) => (
                        <ModuleOperationTableRow
                          key={row.app.id}
                          row={row}
                          onRun={() => handleRunModule(row)}
                          onOpen={() => handleModuleClick(row)}
                          onEdit={() => handleEditApp(row)}
                          onToggleDeployment={() => handleToggleDeployment(row)}
                          onManageAutomaticOptimization={() =>
                            handleManageAutomaticOptimization(row)
                          }
                          isLoadingAutomaticOptimization={
                            isLoadingAutomaticOptimizationId === row.app.id
                          }
                          isOrgManager={isOrgManager}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="grid gap-x-4 gap-y-3 bg-slate-100/50 p-5 md:grid-cols-2 lg:grid-cols-3">
                  {rows.map((row) => (
                    <ModuleOperationGridCard
                      key={row.app.id}
                      row={row}
                      onRun={() => handleRunModule(row)}
                      onOpen={() => handleModuleClick(row)}
                      onEdit={() => handleEditApp(row)}
                      onToggleDeployment={() => handleToggleDeployment(row)}
                      isOrgManager={isOrgManager}
                    />
                  ))}
                </div>
              )}
              {hasMore && (
                <div className="border-t border-slate-100 bg-white px-5 py-4 text-center">
                  <button
                    onClick={() =>
                      loadModules({ offset: rows.length, append: true })
                    }
                    disabled={isLoadingMore}
                    className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <RefreshCw
                      className={`h-4 w-4 ${isLoadingMore ? 'animate-spin' : ''}`}
                    />
                    {isLoadingMore ? '불러오는 중' : '더 보기'}
                  </button>
                </div>
              )}
            </div>
          )}
        </DashboardPanel>
      </div>

      {isCreateModalOpen && (
        <CreateAppModal
          onClose={() => setIsCreateModalOpen(false)}
          onSuccess={() => {
            setIsCreateModalOpen(false);
            loadModules();
          }}
        />
      )}

      {editingApp && (
        <EditAppModal
          app={editingApp}
          onClose={() => setEditingApp(null)}
          onSuccess={() => {
            setEditingApp(null);
            loadModules();
          }}
        />
      )}

      {automaticOptimizationTarget && automaticOptimizationSummary && (
        <AutomaticOptimizationManagementModal
          workflowName={automaticOptimizationTarget.app.name}
          summary={automaticOptimizationSummary}
          onClose={() => {
            setAutomaticOptimizationTarget(null);
            setAutomaticOptimizationSummary(null);
          }}
          onSave={handleSaveAutomaticOptimization}
        />
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white pl-3 pr-2 text-sm font-medium text-slate-600 has-disabled:bg-slate-50 has-disabled:text-slate-400">
      <Filter className="h-4 w-4 text-slate-400" />
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="h-full bg-transparent pr-6 text-sm font-semibold text-slate-800 outline-none disabled:cursor-not-allowed disabled:text-slate-400"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
}

function ModuleOperationTableRow({
  row,
  onRun,
  onOpen,
  onEdit,
  onToggleDeployment,
  onManageAutomaticOptimization,
  isLoadingAutomaticOptimization,
  isOrgManager,
}: {
  row: ModuleOperationRow;
  onRun: () => void;
  onOpen: () => void;
  onEdit: () => void;
  onToggleDeployment: () => void;
  onManageAutomaticOptimization: () => void;
  isLoadingAutomaticOptimization: boolean;
  isOrgManager: boolean;
}) {
  const deploymentState = row.deploymentState;
  const canEdit = canEditApp(row, isOrgManager);
  const canToggle = canToggleDeployment(row);
  const runBlockMessage = budgetRunBlockMessage(row.app.budget_status);
  const runDisabledReason = getModuleRunDisabledReason(row);
  const costSignal = costSignalOf(row);
  const budgetPercent =
    costSignal.budgetUsageRatio == null
      ? null
      : Math.round(costSignal.budgetUsageRatio * 100);
  const budgetPresentation = costSignal.budgetStatus
    ? budgetStatusPresentation[costSignal.budgetStatus]
    : null;
  const trendPercent = costSignal.trendPercent;

  return (
    <tr className="text-sm text-slate-700 hover:bg-slate-50">
      <td className="px-5 py-4 align-top">
        <button
          onClick={onOpen}
          className="flex min-w-0 items-start gap-3 text-left"
        >
          <span
            className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg text-lg leading-none shadow-sm"
            style={{ backgroundColor: row.app.icon?.background_color }}
            aria-hidden="true"
          >
            {displayAppIcon(row.app.icon)}
          </span>
          <span className="min-w-0">
            <span className="block truncate font-semibold text-slate-950">
              {row.app.name}
            </span>
            <span className="mt-1 line-clamp-2 block text-xs text-slate-500">
              {row.app.description || '설명 없음'}
            </span>
            {row.app.budget_status && (
              <span className="mt-2 block">
                <BudgetStatusBadge
                  status={row.app.budget_status.status}
                  usageRatio={row.app.budget_status.usage_ratio}
                />
              </span>
            )}
            <span className="mt-2 block text-xs text-slate-400">
              마지막 수정 {formatDate(row.app.updated_at)}
            </span>
          </span>
        </button>
      </td>
      <td className="px-4 py-4 align-top">
        <ModuleProjectedCost
          appName={row.app.name}
          deploymentState={row.deploymentState}
          costSignal={costSignal}
          valueClassName="font-semibold text-slate-950"
        />
      </td>
      <td className="px-4 py-4 align-top">
        {row.deploymentState === 'active' && trendPercent != null ? (
          <Badge
            className={
              trendPercent >= 20
                ? 'border-red-200 bg-red-50 text-red-700'
                : trendPercent >= 10
                  ? 'border-amber-200 bg-amber-50 text-amber-700'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700'
            }
          >
            {trendPercent > 0 ? '+' : ''}
            {Math.round(trendPercent)}%
          </Badge>
        ) : row.deploymentState === 'active' ? (
          <span className="text-xs font-medium text-slate-400">
            비교 데이터 없음
          </span>
        ) : (
          <span className="text-xs font-medium text-slate-400">-</span>
        )}
      </td>
      <td className="px-4 py-4 align-top">
        {row.deploymentState === 'active' && budgetPercent != null ? (
          <div className="min-w-28">
            <div className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-700">
              <span>{budgetPercent}%</span>
              <span className={budgetPresentation?.textClassName}>
                {costSignal.budgetStatus
                  ? budgetStatusLabel[costSignal.budgetStatus]
                  : '확인 필요'}
              </span>
            </div>
            <div className="mt-2 h-2 rounded-full bg-slate-100">
              <div
                className={`h-2 rounded-full ${
                  budgetPresentation?.barClassName ?? 'bg-slate-300'
                }`}
                style={{ width: `${Math.min(budgetPercent, 100)}%` }}
              />
            </div>
          </div>
        ) : row.deploymentState === 'active' ? (
          <span className="text-xs font-medium text-slate-400">예산 없음</span>
        ) : (
          <span className="text-xs font-medium text-slate-400">-</span>
        )}
      </td>
      <td className="px-4 py-4 align-top">
        <AutomaticOptimizationTableCell
          summary={row.automaticOptimization}
          hasDeployment={Boolean(row.deployment.deployment_id)}
          canManage={canToggle}
          isLoading={isLoadingAutomaticOptimization}
          onManage={onManageAutomaticOptimization}
        />
      </td>
      <td className="px-4 py-4 align-top">
        <div className="flex flex-col gap-2">
          <Badge className={deploymentTone[deploymentState]}>
            {deploymentLabels[deploymentState]}
          </Badge>
          {row.latestRun.state !== 'success' && (
            <Badge className={runTone[row.latestRun.state]}>
              {runLabels[row.latestRun.state]}
            </Badge>
          )}
          {runBlockMessage && (
            <span
              title={runBlockMessage}
              className="w-fit rounded bg-red-100 px-1 text-[10px] font-bold text-red-700"
            >
              실행 차단
            </span>
          )}
        </div>
      </td>
      <td className="px-5 py-4 align-top">
        <div className="flex justify-end gap-2">
          <IconButton
            label={runDisabledReason || '배포 실행'}
            onClick={onRun}
            disabled={Boolean(runDisabledReason)}
          >
            <Play className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={canOpenModule(row) ? '편집기 열기' : '조회 권한 확인 필요'}
            onClick={onOpen}
            disabled={!canOpenModule(row)}
          >
            <ExternalLink className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={canEdit ? '앱 설정 수정' : '앱 설정 관리 권한 필요'}
            onClick={onEdit}
            disabled={!canEdit}
          >
            <Edit3 className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={
              canToggle
                ? '배포 상태 변경'
                : row.deployment.deployment_id
                  ? '배포 권한 필요'
                  : '배포 없음'
            }
            onClick={onToggleDeployment}
            disabled={!canToggle}
          >
            <CheckCircle2 className="h-4 w-4" />
          </IconButton>
        </div>
      </td>
    </tr>
  );
}

function ModuleOperationGridCard({
  row,
  onRun,
  onOpen,
  onEdit,
  onToggleDeployment,
  isOrgManager,
}: {
  row: ModuleOperationRow;
  onRun: () => void;
  onOpen: () => void;
  onEdit: () => void;
  onToggleDeployment: () => void;
  isOrgManager: boolean;
}) {
  const deploymentState = row.deploymentState;
  const canEdit = canEditApp(row, isOrgManager);
  const canToggle = canToggleDeployment(row);
  const runBlockMessage = budgetRunBlockMessage(row.app.budget_status);
  const runDisabledReason = getModuleRunDisabledReason(row);

  return (
    <article className="flex h-full min-h-72 flex-col overflow-hidden rounded-3xl bg-white shadow-lg shadow-slate-300/40 transition-all hover:-translate-y-0.5 hover:shadow-xl">
      <div className="relative px-6 pb-5 pt-6">
        <button
          type="button"
          onClick={onOpen}
          disabled={!canOpenModule(row)}
          className="block w-full text-left disabled:cursor-not-allowed"
        >
          <span
            className="grid h-12 w-12 place-items-center overflow-hidden rounded-full border border-slate-200 text-xl leading-none shadow-sm"
            style={{ backgroundColor: row.app.icon?.background_color }}
            aria-hidden="true"
          >
            {displayAppIcon(row.app.icon)}
          </span>
          <span className="mt-5 block min-w-0 pr-2">
            <h3 className="line-clamp-2 text-xl font-bold leading-7 text-slate-950">
              {row.app.name}
            </h3>
            <span className="mt-1 block text-xs text-slate-400">
              마지막 수정 {formatDate(row.app.updated_at)}
            </span>
          </span>
          <span className="mt-4 min-h-10 line-clamp-2 block text-sm leading-5 text-slate-500">
            {row.app.description || '설명 없음'}
          </span>
        </button>
        <div className="absolute right-6 top-6">
          <Badge className={deploymentTone[deploymentState]}>
            {deploymentLabels[deploymentState]}
          </Badge>
        </div>
        {(row.latestRun.state !== 'success' || runBlockMessage) && (
          <div className="mt-4 flex min-w-0 flex-wrap gap-2">
            {row.latestRun.state !== 'success' && (
              <Badge className={runTone[row.latestRun.state]}>
                {runLabels[row.latestRun.state]}
              </Badge>
            )}
            {runBlockMessage && (
              <span
                title={runBlockMessage}
                className="inline-flex items-center rounded bg-red-100 px-2 py-1 text-[10px] font-bold text-red-700"
              >
                실행 차단
              </span>
            )}
          </div>
        )}
      </div>

      <footer className="mx-6 mt-auto flex items-center justify-end border-t border-slate-200 py-5">
        <div className="flex shrink-0 gap-1.5">
          <IconButton
            label={runDisabledReason || '배포 실행'}
            onClick={onRun}
            disabled={Boolean(runDisabledReason)}
          >
            <Play className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={canOpenModule(row) ? '편집기 열기' : '조회 권한 확인 필요'}
            onClick={onOpen}
            disabled={!canOpenModule(row)}
          >
            <ExternalLink className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={canEdit ? '앱 설정 수정' : '앱 설정 관리 권한 필요'}
            onClick={onEdit}
            disabled={!canEdit}
          >
            <Edit3 className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={
              canToggle
                ? '배포 상태 변경'
                : row.deployment.deployment_id
                  ? '배포 권한 필요'
                  : '배포 없음'
            }
            onClick={onToggleDeployment}
            disabled={!canToggle}
          >
            <CheckCircle2 className="h-4 w-4" />
          </IconButton>
        </div>
      </footer>
    </article>
  );
}

function Badge({
  children,
  className,
}: {
  children: React.ReactNode;
  className: string;
}) {
  return (
    <span
      className={`inline-flex w-fit items-center rounded-md border px-2 py-1 text-xs font-semibold ${className}`}
    >
      {children}
    </span>
  );
}

function IconButton({
  label,
  disabled,
  children,
  onClick,
}: {
  label: string;
  disabled?: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="grid h-9 w-9 place-items-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-300"
    >
      {children}
    </button>
  );
}

function ModuleListSkeleton({ viewMode }: { viewMode: OperationsViewMode }) {
  if (viewMode === 'grid') {
    return (
      <div className="grid gap-x-4 gap-y-3 bg-white p-5 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <div
            key={index}
            className="h-72 animate-pulse rounded-3xl bg-white shadow-lg shadow-slate-300/40"
          />
        ))}
      </div>
    );
  }

  return (
    <div className="divide-y divide-slate-100">
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className="grid grid-cols-6 gap-4 px-5 py-4">
          <div className="col-span-2 h-12 animate-pulse rounded bg-slate-100" />
          <div className="h-8 animate-pulse rounded bg-slate-100" />
          <div className="h-8 animate-pulse rounded bg-slate-100" />
          <div className="h-8 animate-pulse rounded bg-slate-100" />
          <div className="h-8 animate-pulse rounded bg-slate-100" />
        </div>
      ))}
    </div>
  );
}
