import type { OrganizationAuthState } from '../types/Organization';

const organizationAuthMeta: Record<
  OrganizationAuthState,
  { label: string; className: string }
> = {
  manager: {
    label: '관리자',
    className: 'border-blue-200 bg-blue-50 text-blue-700',
  },
  member: {
    label: '멤버',
    className: 'border-gray-200 bg-gray-50 text-gray-600',
  },
};

type OrganizationAuthBadgeProps = {
  state: OrganizationAuthState;
  className?: string;
};

export function OrganizationAuthBadge({
  state,
  className = '',
}: OrganizationAuthBadgeProps) {
  const meta = organizationAuthMeta[state];

  return (
    <span
      className={`inline-flex w-fit items-center rounded-md border px-2 py-0.5 text-xs font-medium ${meta.className} ${className}`}
    >
      {meta.label}
    </span>
  );
}
