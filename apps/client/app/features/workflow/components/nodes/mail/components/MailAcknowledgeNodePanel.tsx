import { Plus, Trash2 } from 'lucide-react';

import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { MailAcknowledgeNodeData } from '../../../../types/Nodes';
import { getNodeOutputVariables } from '../../../../utils/nodeVariablePorts';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import { VariableSelectorSlot } from '../../ui/VariableSelectorSlot';

export function MailAcknowledgeNodePanel({
  nodeId,
  data,
}: {
  nodeId: string;
  data: MailAcknowledgeNodeData;
}) {
  const { nodes, updateNodeData } = useWorkflowStore();
  const effectSelectors = data.required_effect_ref_selectors.length
    ? data.required_effect_ref_selectors
    : [[]];
  const selectedOutput = (selector: string[]) => {
    const node = nodes.find((candidate) => candidate.id === selector?.[0]);
    return node
      ? getNodeOutputVariables(node).find(
          (output) =>
            output.key === selector?.[1] || output.outputId === selector?.[1],
        )
      : undefined;
  };

  return (
    <CollapsibleSection title="처리 완료 조건" defaultOpen showDivider>
      <div className="flex flex-col gap-3">
        <VariableSelectorSlot
          value={data.processing_ref_selector}
          selectedOutput={selectedOutput(data.processing_ref_selector)}
          label="Mail processing reference"
          onChange={(selector) =>
            updateNodeData(nodeId, { processing_ref_selector: selector })
          }
        />
        {effectSelectors.map((effectSelector, index) => (
          <div
            key={`required-effect-${index}`}
            className="flex items-end gap-2"
          >
            <div className="min-w-0 flex-1">
              <VariableSelectorSlot
                value={effectSelector}
                selectedOutput={selectedOutput(effectSelector)}
                label={`필수 Draft effect ${index + 1}`}
                onChange={(selector) => {
                  const next = [...data.required_effect_ref_selectors];
                  next[index] = selector;
                  updateNodeData(nodeId, {
                    required_effect_ref_selectors: next,
                  });
                }}
              />
            </div>
            <button
              type="button"
              title="필수 effect 제거"
              aria-label={`필수 Draft effect ${index + 1} 제거`}
              onClick={() =>
                updateNodeData(nodeId, {
                  required_effect_ref_selectors:
                    data.required_effect_ref_selectors.filter(
                      (_selector, selectorIndex) => selectorIndex !== index,
                    ),
                })
              }
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() =>
            updateNodeData(nodeId, {
              required_effect_ref_selectors: [
                ...data.required_effect_ref_selectors,
                [],
              ],
            })
          }
          className="flex h-8 items-center justify-center gap-1 rounded border border-gray-300 px-3 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          필수 effect 추가
        </button>
        {data.required_effect_ref_selectors.length === 0 && (
          <ValidationAlert message="완료를 확인할 필수 effect를 연결해주세요." />
        )}
        <p className="text-[11px] text-gray-500">
          연결된 모든 필수 작업이 성공한 경우에만 원본 메일을 읽음 처리합니다.
        </p>
      </div>
    </CollapsibleSection>
  );
}
