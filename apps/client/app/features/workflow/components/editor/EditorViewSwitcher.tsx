import { Workflow, ScrollText, Activity } from 'lucide-react';

export type ViewMode = 'edit' | 'log' | 'monitoring';

interface EditorViewSwitcherProps {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
}

export default function EditorViewSwitcher({
  viewMode,
  onViewModeChange,
}: EditorViewSwitcherProps) {
  return (
    <div className="relative z-50 flex h-0 justify-center">
      <div className="absolute top-3 flex -translate-y-1/2 items-center rounded-lg border border-slate-200 bg-slate-100 p-1 shadow-sm">
        <button
          onClick={() => onViewModeChange('edit')}
          className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md transition-all ${
            viewMode === 'edit'
              ? 'bg-white text-slate-950 border border-slate-200 shadow-sm'
              : 'text-slate-500 hover:text-slate-950 hover:bg-slate-200/60 border border-transparent'
          }`}
        >
          <Workflow className="w-4 h-4" />
          <span>편집</span>
        </button>
        <button
          onClick={() => onViewModeChange('log')}
          className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md transition-all ${
            viewMode === 'log'
              ? 'bg-white text-slate-950 border border-slate-200 shadow-sm'
              : 'text-slate-500 hover:text-slate-950 hover:bg-slate-200/60 border border-transparent'
          }`}
        >
          <ScrollText className="w-4 h-4" />
          <span>로그</span>
        </button>
        <button
          onClick={() => onViewModeChange('monitoring')}
          className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md transition-all ${
            viewMode === 'monitoring'
              ? 'bg-white text-slate-950 border border-slate-200 shadow-sm'
              : 'text-slate-500 hover:text-slate-950 hover:bg-slate-200/60 border border-transparent'
          }`}
        >
          <Activity className="w-4 h-4" />
          <span>모니터링</span>
        </button>
      </div>
    </div>
  );
}
