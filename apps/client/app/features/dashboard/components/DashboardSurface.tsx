import type { ReactNode } from 'react';
import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';

type DashboardPageHeaderProps = {
  icon: LucideIcon;
  title: string;
  description: string;
  meta?: ReactNode;
  badge?: ReactNode;
  action?: ReactNode;
};

export function DashboardPageHeader({
  icon,
  title,
  description,
  meta,
  badge,
  action,
}: DashboardPageHeaderProps) {
  return (
    <header className="border-b border-slate-200 pb-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DashboardTitle icon={icon} title={title} />
            {badge}
          </div>
          <p className="mt-1 text-sm font-medium text-slate-700">
            {description}
          </p>
          {meta && <div className="mt-2">{meta}</div>}
        </div>
        {action}
      </div>
    </header>
  );
}

type DashboardTitleProps = {
  icon: LucideIcon;
  title: string;
  className?: string;
};

export function DashboardTitle({
  icon: Icon,
  title,
  className = 'text-2xl font-bold text-slate-950',
}: DashboardTitleProps) {
  return (
    <h1 className={`flex items-center gap-2 ${className}`}>
      <Icon aria-hidden="true" className="h-6 w-6 shrink-0" />
      {title}
    </h1>
  );
}

type DashboardSummaryCardProps = {
  label: string;
  value: string | number;
  icon: LucideIcon;
  iconClassName?: string;
  valueClassName?: string;
  descriptionClassName?: string;
  description?: ReactNode;
  href?: string;
  linkAriaLabel?: string;
};

export function DashboardSummaryCard({
  label,
  value,
  icon: Icon,
  iconClassName = 'text-blue-600',
  valueClassName = 'mt-1 text-lg',
  descriptionClassName = 'mt-1 text-xs',
  description,
  href,
  linkAriaLabel,
}: DashboardSummaryCardProps) {
  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p
            className={`truncate font-semibold text-slate-950 ${valueClassName}`}
          >
            {value}
          </p>
          {description && (
            <div className={`text-slate-500 ${descriptionClassName}`}>
              {description}
            </div>
          )}
        </div>
        <Icon className={`h-5 w-5 shrink-0 ${iconClassName}`} />
      </div>
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        aria-label={linkAriaLabel || `${label} 상세 보기`}
        className="rounded-lg border border-slate-200 bg-white p-5 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
      >
        {content}
      </Link>
    );
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      {content}
    </div>
  );
}

type DashboardPanelProps = {
  title: string;
  icon?: LucideIcon;
  aside?: ReactNode;
  children: ReactNode;
};

export function DashboardPanel({
  title,
  icon: Icon,
  aside,
  children,
}: DashboardPanelProps) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
          {Icon && <Icon className="h-4 w-4 text-blue-600" />}
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
