import type { MembershipState } from '../types/Organization';

const memberStateMeta: Record<
  MembershipState,
  { label: string; className: string }
> = {
  invited: {
    label: '초대 중',
    className: 'border-amber-200 bg-amber-50 text-amber-700',
  },
  active: {
    label: '활성',
    className: 'border-green-200 bg-green-50 text-green-700',
  },
  suspended: {
    label: '정지',
    className: 'border-red-200 bg-red-50 text-red-700',
  },
  removed: {
    label: '제거됨',
    className: 'border-gray-200 bg-gray-50 text-gray-600',
  },
};

type MemberStateBadgeProps = {
  state: MembershipState;
  className?: string;
};

export function MemberStateBadge({
  state,
  className = '',
}: MemberStateBadgeProps) {
  const meta = memberStateMeta[state];

  return (
    <span
      className={`inline-flex w-fit items-center rounded-md border px-2 py-0.5 text-xs font-medium ${meta.className} ${className}`}
    >
      {meta.label}
    </span>
  );
}
