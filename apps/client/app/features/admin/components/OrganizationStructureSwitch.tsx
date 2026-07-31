import type { OrganizationStructureView } from '../utils/adminUrlState';

type OrganizationStructureSwitchProps = {
  view: OrganizationStructureView;
  memberCount: number;
  teamCount: number;
  onChange: (view: OrganizationStructureView) => void;
};

export function OrganizationStructureSwitch({
  view,
  memberCount,
  teamCount,
  onChange,
}: OrganizationStructureSwitchProps) {
  const items: Array<{
    key: OrganizationStructureView;
    label: string;
    count: number;
  }> = [
    { key: 'members', label: '멤버', count: memberCount },
    { key: 'teams', label: '팀', count: teamCount },
  ];

  return (
    <div
      className="inline-grid w-fit grid-cols-2 gap-1 rounded-xl border border-slate-200 bg-slate-100 p-1"
      role="group"
      aria-label="조직 구성 보기"
    >
      {items.map((item) => {
        const selected = view === item.key;
        return (
          <button
            key={item.key}
            type="button"
            aria-label={`${item.label} ${item.count}`}
            aria-pressed={selected}
            onClick={() => {
              if (!selected) onChange(item.key);
            }}
            className={`flex min-w-0 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${
              selected
                ? 'bg-white text-slate-950 shadow-sm ring-1 ring-slate-200'
                : 'text-slate-500 hover:bg-slate-200/70 hover:text-slate-800'
            }`}
          >
            <span>{item.label}</span>
            <span
              className={`rounded-full px-2 py-0.5 text-xs ${
                selected
                  ? 'bg-slate-950 text-white'
                  : 'bg-slate-200 text-slate-600'
              }`}
            >
              {item.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
