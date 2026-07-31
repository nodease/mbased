import { NodeOutputVariable } from '../../utils/nodeVariablePorts';
import { cn } from '@/lib/utils';
import { CollapsibleSection } from './ui/CollapsibleSection';

const TYPE_BADGE_CLASS: Record<string, string> = {
  string: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  number: 'border-blue-200 bg-blue-50 text-blue-700',
  boolean: 'border-violet-200 bg-violet-50 text-violet-700',
  object: 'border-amber-200 bg-amber-50 text-amber-700',
  array: 'border-pink-200 bg-pink-50 text-pink-700',
  null: 'border-gray-200 bg-gray-50 text-gray-600',
  unknown: 'border-slate-200 bg-slate-50 text-slate-600',
};

type NodeOutputsSectionProps = {
  outputs: NodeOutputVariable[];
  className?: string;
  listClassName?: string;
  hideHeader?: boolean;
  editingOutputKey: string | null;
  draftOutputLabel: string;
  onDraftOutputLabelChange: (value: string) => void;
  onStartOutputLabelEdit: (
    event: React.MouseEvent<HTMLDivElement>,
    output: NodeOutputVariable,
  ) => void;
  onSaveOutputLabelEdit: () => void;
  onCancelOutputLabelEdit: () => void;
  outputLabelInputRef: React.RefObject<HTMLInputElement | null>;
};

export const NodeOutputsSection = ({
  outputs,
  className,
  listClassName,
  hideHeader = false,
  editingOutputKey,
  draftOutputLabel,
  onDraftOutputLabelChange,
  onStartOutputLabelEdit,
  onSaveOutputLabelEdit,
  onCancelOutputLabelEdit,
  outputLabelInputRef,
}: NodeOutputsSectionProps) => {
  if (outputs.length === 0) return null;

  const outputList = (
    <div
      className={cn(
        'flex max-h-64 min-h-0 flex-col gap-1.5 overflow-y-auto pr-1',
        listClassName,
      )}
    >
      {outputs.map((output) => (
        <div
          key={`${output.sourceNodeId}-${output.key}`}
          onDoubleClick={(event) => onStartOutputLabelEdit(event, output)}
          className="group/output rounded-md border border-gray-200 bg-white px-2.5 py-2 shadow-sm transition-colors hover:border-blue-200 hover:bg-blue-50/30"
          title="라벨을 더블클릭해 수정하세요."
        >
          <div className="flex min-w-0 items-center gap-2">
            {editingOutputKey === output.key ? (
              <input
                ref={outputLabelInputRef}
                value={draftOutputLabel}
                onChange={(event) =>
                  onDraftOutputLabelChange(event.target.value)
                }
                onBlur={onCancelOutputLabelEdit}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    onSaveOutputLabelEdit();
                  }
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    onCancelOutputLabelEdit();
                  }
                }}
                onClick={(event) => event.stopPropagation()}
                onDoubleClick={(event) => event.stopPropagation()}
                className="min-w-0 flex-1 rounded border border-blue-300 bg-white px-1.5 py-1 text-xs font-semibold text-gray-900 outline-none ring-2 ring-blue-100"
                aria-label="출력변수 라벨 수정"
              />
            ) : (
              <div className="min-w-0 flex-1 truncate text-xs font-semibold text-gray-900">
                {output.label || output.key}
              </div>
            )}
            <code className="shrink-0 rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600">
              {output.key}
            </code>
            <span
              className={cn(
                'shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-bold uppercase',
                TYPE_BADGE_CLASS[output.dataType] || TYPE_BADGE_CLASS.unknown,
              )}
            >
              {output.dataType}
            </span>
          </div>
          {output.description && (
            <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-gray-500">
              {output.description}
            </p>
          )}
        </div>
      ))}
    </div>
  );

  return (
    <div
      className={cn(
        'nodrag nowheel mt-4 flex flex-col border-t border-gray-100 pt-3',
        className,
      )}
    >
      {hideHeader ? (
        outputList
      ) : (
        <CollapsibleSection
          title={
            <span className="flex items-center gap-2">
              <span>출력변수</span>
              <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500">
                {outputs.length}
              </span>
            </span>
          }
          showDivider={false}
        >
          {outputList}
        </CollapsibleSection>
      )}
    </div>
  );
};
