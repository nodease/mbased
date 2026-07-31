'use client';

import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';
import { Database, DollarSign, Users, type LucideIcon } from 'lucide-react';
import { adminApi } from '../api/adminApi';
import type {
  AdminBudgetSummary,
  AdminOrganizationSummary,
} from '../types/AdminUsage';

// 비용은 표시 직전에만 USD 2자리로 반올림한다 (FR-015).
const formatCost = (value: number) => `$${value.toFixed(2)}`;

type AdminSummaryCardsProps = {
  members: {
    active: number;
    invited: number;
    suspended: number;
    removed: number;
  };
  teams: {
    active: number;
    assignments: number;
  };
  credentials: {
    active: number;
    providers: number;
  };
  knowledgeBases: number;
};

type SummaryGroupCardProps = {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
};

function SummaryGroupCard({
  title,
  icon: Icon,
  children,
}: SummaryGroupCardProps) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        <Icon className="h-5 w-5 shrink-0 text-blue-600" aria-hidden="true" />
      </div>
      {children}
    </article>
  );
}

type SummaryMetricLinkProps = {
  label: string;
  value: number;
  description: string;
  href: string;
  ariaLabel: string;
};

function SummaryMetricLink({
  label,
  value,
  description,
  href,
  ariaLabel,
}: SummaryMetricLinkProps) {
  return (
    <Link
      href={href}
      aria-label={ariaLabel}
      className="min-w-0 rounded-md px-2 py-1.5 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
    >
      <p className="truncate text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-950">{value}</p>
      <p className="mt-1 truncate text-xs text-slate-500">{description}</p>
    </Link>
  );
}

type BudgetStatusProps = {
  budget: AdminBudgetSummary | null | undefined;
  failed: boolean;
};

function BudgetStatus({ budget, failed }: BudgetStatusProps) {
  if (failed) return null;

  if (budget === undefined) {
    return <span className="text-xs text-slate-500">예산 집계 중</span>;
  }

  if (budget === null) {
    return (
      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
        예산 미설정
      </span>
    );
  }

  const riskCount = budget.at_risk_count + budget.exceeded_count;

  return (
    <div className="flex items-baseline gap-1 text-slate-950">
      <span className="text-lg font-semibold">{riskCount}개</span>
      <span className="text-xs font-semibold">위험</span>
    </div>
  );
}

export function AdminSummaryCards({
  members,
  teams,
  credentials,
  knowledgeBases,
}: AdminSummaryCardsProps) {
  const [summary, setSummary] = useState<AdminOrganizationSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getOrganizationSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const workflowExecutionCost = summary
    ? (summary.workflow_execution_cost ??
      Math.max(summary.total_cost - (summary.agent_builder_cost ?? 0), 0))
    : null;

  return (
    <section aria-label="관리 요약" className="grid gap-3 lg:grid-cols-3">
      <Link
        href="/dashboard/admin?tab=usage"
        aria-label="이번 달 비용과 예산 비용 탭에서 확인"
        className="rounded-lg border border-slate-200 bg-white p-5 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-700">비용·예산</h2>
          <DollarSign
            className="h-5 w-5 shrink-0 text-blue-600"
            aria-hidden="true"
          />
        </div>

        <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-2xl font-semibold text-slate-950">
            {summary
              ? formatCost(summary.total_cost)
              : failed
                ? '-'
                : '집계 중'}
          </p>
          <BudgetStatus budget={summary?.budget} failed={failed} />
        </div>

        {failed ? (
          <p className="mt-2 text-xs text-slate-500">
            요약을 불러오지 못했습니다
          </p>
        ) : summary ? (
          <div className="mt-2 space-y-1 text-xs text-slate-500">
            <p>{summary.month} · USD · KST 달력 월 기준</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-600">
              <span>
                워크플로 실행 {formatCost(workflowExecutionCost ?? 0)}
              </span>
              <span>
                Agent Builder {formatCost(summary.agent_builder_cost ?? 0)}
              </span>
            </div>
            {!summary.usage_data_complete && (
              <p className="font-medium text-amber-700">
                비용 미확정 provider 호출{' '}
                {summary.unresolved_provider_call_count}건
              </p>
            )}
            {summary.budget && (
              <div
                className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-slate-700"
                aria-label="예산 위험 상태 요약"
              >
                <span className="inline-flex items-center gap-1.5">
                  <span
                    data-testid="budget-at-risk-dot"
                    aria-hidden="true"
                    className="h-2 w-2 rounded-full bg-amber-400"
                  />
                  예산 임박 {summary.budget.at_risk_count}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 rounded-full bg-red-500"
                  />
                  예산 초과 {summary.budget.exceeded_count}
                </span>
              </div>
            )}
          </div>
        ) : null}
      </Link>

      <SummaryGroupCard title="조직 구성" icon={Users}>
        <div className="mt-3 grid grid-cols-2 divide-x divide-slate-200">
          <SummaryMetricLink
            label="활성 멤버"
            value={members.active}
            description={`초대 ${members.invited} · 정지 ${members.suspended} · 제거 ${members.removed}`}
            href="/dashboard/admin?tab=organization-structure&view=members"
            ariaLabel="활성 멤버 조직 구성 멤버 보기에서 확인"
          />
          <SummaryMetricLink
            label="활성 팀"
            value={teams.active}
            description={`팀 배정 ${teams.assignments}건`}
            href="/dashboard/admin?tab=organization-structure&view=teams"
            ariaLabel="활성 팀 조직 구성 팀 보기에서 확인"
          />
        </div>
      </SummaryGroupCard>

      <SummaryGroupCard title="운영 리소스" icon={Database}>
        <div className="mt-3 grid grid-cols-2 divide-x divide-slate-200">
          <SummaryMetricLink
            label="LLM Credentials"
            value={credentials.active}
            description={`${credentials.providers}개 provider 기준`}
            href="/dashboard/admin?tab=credentials"
            ariaLabel="LLM Credentials 탭에서 확인"
          />
          <SummaryMetricLink
            label="지식 기반"
            value={knowledgeBases}
            description="팀/사용자 권한 관리 가능"
            href="/dashboard/admin?tab=knowledge"
            ariaLabel="지식 기반 탭에서 확인"
          />
        </div>
      </SummaryGroupCard>
    </section>
  );
}
