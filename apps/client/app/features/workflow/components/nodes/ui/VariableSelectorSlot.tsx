import { useCallback, useEffect, useId } from 'react';

import { NodeOutputVariable } from '../../../utils/nodeVariablePorts';
import { cn } from '@/lib/utils';
import { useVariableInsertion } from './useVariableInsertion';
import { VariableInsertionTargetKind } from './variableInsertionContext';

const selectorFor = (output: NodeOutputVariable) => [
  output.sourceNodeId,
  output.outputId || output.key,
];

type VariableSelectorSlotProps = {
  value?: string[];
  selectedOutput?: NodeOutputVariable;
  label: string;
  placeholder?: string;
  sourceLabel?: string;
  className?: string;
  kind?: VariableInsertionTargetKind;
  onChange: (selector: string[], output: NodeOutputVariable) => void;
};

export function VariableSelectorSlot({
  value,
  selectedOutput,
  label,
  placeholder = '입력 변수 선택',
  sourceLabel,
  className,
  kind = 'selector',
  onChange,
}: VariableSelectorSlotProps) {
  const targetId = useId();
  const { activeTarget, setActiveTarget, clearMessage, registerTarget } =
    useVariableInsertion();
  const isActive = activeTarget?.id === targetId;
  const hasSelector = Array.isArray(value) && value.length >= 2;
  const fallbackLabel = hasSelector ? value?.[1] : '';
  const displayLabel = selectedOutput?.label || fallbackLabel || '';
  const displaySource = sourceLabel || selectedOutput?.sourceTitle || '';

  const activate = useCallback(() => {
    setActiveTarget({ id: targetId, kind, label });
    clearMessage();
  }, [clearMessage, kind, label, setActiveTarget, targetId]);

  const applyOutput = useCallback(
    (output: NodeOutputVariable) => {
      onChange(selectorFor(output), output);
      return true;
    },
    [onChange],
  );

  useEffect(
    () =>
      registerTarget(
        {
          id: targetId,
          kind,
          label,
        },
        applyOutput,
      ),
    [applyOutput, kind, label, registerTarget, targetId],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        activate();
      }}
      onFocus={activate}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        activate();
      }}
      className={cn(
        'flex min-h-9 w-full items-center rounded-md border border-dashed border-gray-200 bg-gray-50 px-2 py-1.5 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/60 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-100',
        isActive && 'border-blue-400 bg-blue-50/70 ring-2 ring-blue-100',
        className,
      )}
      title={`${label}: 좌측 입력 패널에서 변수를 클릭해 선택`}
      aria-label={`${label} 변수 선택`}
    >
      {displayLabel ? (
        <span className="inline-flex min-w-0 max-w-full flex-col gap-0.5">
          <span className="inline-flex max-w-full items-center rounded-md border border-blue-100 bg-blue-50 px-2 py-1 text-xs font-semibold text-blue-800 shadow-sm">
            <span className="truncate">{displayLabel}</span>
          </span>
          {displaySource && (
            <span className="truncate px-0.5 text-[10px] text-gray-500">
              {displaySource}
            </span>
          )}
        </span>
      ) : (
        <span className="text-xs font-medium text-gray-400">{placeholder}</span>
      )}
    </div>
  );
}
