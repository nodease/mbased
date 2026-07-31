'use client';

import { Activity, ArrowLeft, BarChart3, ScrollText } from 'lucide-react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';

import { LogTab } from '@/app/features/workflow/components/editor/tabs/LogTab';
import { MonitoringTab } from '@/app/features/workflow/components/editor/tabs/MonitoringTab';
import { useWorkflowAppSync } from '@/app/features/workflow/hooks/useWorkflowAppSync';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { copyTestExecutionLocationQueryParams } from '@/app/features/workflow/utils/testExecutionLocation';

type ReportTab = 'logs' | 'monitoring';
type ReportTabButtonProps = {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
};

const getReportTab = (value: string | null): ReportTab =>
  value === 'monitoring' ? 'monitoring' : 'logs';

function ReportTabButton({
  active,
  icon,
  label,
  onClick,
}: ReportTabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative flex h-11 items-center gap-2 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 focus-visible:ring-offset-2 ${
        active
          ? 'text-slate-950 after:absolute after:bottom-[-1px] after:left-0 after:h-0.5 after:w-full after:bg-slate-950'
          : 'text-slate-500 hover:text-slate-900'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

export default function WorkflowReportPage() {
  useWorkflowAppSync();

  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const workflowId = String(params.id || '');
  const activeTab = getReportTab(searchParams.get('tab'));
  const runId = searchParams.get('runId');
  const projectApp = useWorkflowStore((state) => state.projectApp);

  const navigateToTab = (tab: ReportTab, nextRunId?: string) => {
    const query = new URLSearchParams();
    query.set('tab', tab);
    if (nextRunId) query.set('runId', nextRunId);
    copyTestExecutionLocationQueryParams(searchParams, query);
    router.push(`/modules/${workflowId}/report?${query.toString()}`);
  };

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-5">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={() => {
              const query = new URLSearchParams();
              copyTestExecutionLocationQueryParams(searchParams, query);
              const suffix = query.size > 0 ? `?${query.toString()}` : '';
              router.push(`/modules/${workflowId}${suffix}`);
            }}
            className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-600 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950"
          >
            <ArrowLeft className="h-4 w-4" />
            워크플로우 편집
          </button>
          <div className="h-6 w-px bg-slate-200" />
          <div className="flex min-w-0 items-center gap-2">
            <BarChart3 className="h-5 w-5 shrink-0 text-slate-500" />
            <div className="min-w-0">
              <h1 className="truncate text-base font-black text-slate-950">
                {projectApp?.name || '이름 없는 모듈'} 보고
              </h1>
              <p className="truncate text-xs font-medium text-slate-500">
                실행 로그와 운영 지표를 확인합니다.
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="mx-auto flex w-full max-w-7xl shrink-0 items-center px-6 pt-5">
          <div className="flex w-full items-center gap-7 border-b border-slate-200">
            <ReportTabButton
              active={activeTab === 'logs'}
              icon={<ScrollText className="h-4 w-4" />}
              label="로그"
              onClick={() => navigateToTab('logs')}
            />
            <ReportTabButton
              active={activeTab === 'monitoring'}
              icon={<Activity className="h-4 w-4" />}
              label="모니터링"
              onClick={() => navigateToTab('monitoring')}
            />
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">
          {activeTab === 'logs' ? (
            <LogTab workflowId={workflowId} initialRunId={runId} />
          ) : (
            <MonitoringTab
              workflowId={workflowId}
              onNavigateToLog={(targetRunId) =>
                navigateToTab('logs', targetRunId)
              }
            />
          )}
        </div>
      </main>
    </div>
  );
}
