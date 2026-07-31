import { budgetStatusLabel, type BudgetUsageStatus } from '../types';

const statusTone: Record<BudgetUsageStatus, string> = {
  normal: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  at_risk: 'border-amber-200 bg-amber-50 text-amber-700',
  exceeded: 'border-red-200 bg-red-50 text-red-700',
};

const normalizeStatus = (status: string): BudgetUsageStatus => {
  if (status === 'at_risk' || status === 'exceeded') return status;
  return 'normal';
};

export function BudgetStatusBadge({
  status,
  usageRatio,
}: {
  status: BudgetUsageStatus | string;
  usageRatio?: number | null;
}) {
  const normalizedStatus = normalizeStatus(status);

  return (
    <span
      className={`inline-flex w-fit items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold ${statusTone[normalizedStatus]}`}
    >
      <span>{budgetStatusLabel[normalizedStatus]}</span>
      {usageRatio !== undefined && usageRatio !== null && (
        <span>{Math.round(usageRatio * 100)}%</span>
      )}
    </span>
  );
}
