import { Copy } from 'lucide-react';
import type { MouseEvent } from 'react';

type AuditReferenceDisplayProps = {
  label: string;
  id: string;
  copyLabel: string;
};

export function AuditReferenceDisplay({
  label,
  id,
  copyLabel,
}: AuditReferenceDisplayProps) {
  const copyId = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (navigator.clipboard) {
      void navigator.clipboard.writeText(id).catch(() => undefined);
    }
  };

  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-slate-800">{label}</span>
      <span className="flex min-w-0 items-center gap-1 text-[11px] text-slate-500">
        <code className="truncate">{id}</code>
        <button
          type="button"
          onClick={copyId}
          onKeyDown={(event) => event.stopPropagation()}
          className="shrink-0 rounded p-0.5 hover:bg-slate-200 hover:text-slate-800"
          aria-label={copyLabel}
          title={copyLabel}
        >
          <Copy className="h-3 w-3" />
        </button>
      </span>
    </span>
  );
}
