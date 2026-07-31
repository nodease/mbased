import { useMemo, useCallback } from 'react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { VariableExtractionNodeData } from '../../../../types/Nodes';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { getNodeOutputVariables } from '../../../../utils/nodeVariablePorts';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { VariableSelectorSlot } from '../../ui/VariableSelectorSlot';
import { JsonExtractionMappingControl } from './JsonExtractionMappingControl';

interface VariableExtractionNodePanelProps {
  nodeId: string;
  data: VariableExtractionNodeData;
}

export function VariableExtractionNodePanel({
  nodeId,
  data,
}: VariableExtractionNodePanelProps) {
  const { updateNodeData, nodes, edges } = useWorkflowStore();

  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );

  const sourceSelector = data.source_selector || [];
  const selectedNodeId = sourceSelector[0] || '';

  const selectedNode = upstreamNodes.find((n) => n.id === selectedNodeId);
  const selectedOutput = selectedNode
    ? getNodeOutputVariables(selectedNode).find(
        (output) =>
          output.key === sourceSelector[1] ||
          output.outputId === sourceSelector[1],
      )
    : undefined;

  const handleSourceChange = useCallback(
    (selector: string[]) => {
      updateNodeData(nodeId, { source_selector: selector });
    },
    [nodeId, updateNodeData],
  );

  const handleAddMapping = useCallback(() => {
    updateNodeData(nodeId, {
      mappings: [...(data.mappings || []), { name: '', json_path: '' }],
    });
  }, [data.mappings, nodeId, updateNodeData]);

  const handleUpdateMapping = useCallback(
    (index: number, key: 'name' | 'json_path', value: string) => {
      const nextMappings = [...(data.mappings || [])];
      nextMappings[index] = { ...nextMappings[index], [key]: value };
      updateNodeData(nodeId, { mappings: nextMappings });
    },
    [data.mappings, nodeId, updateNodeData],
  );

  const handleRemoveMapping = useCallback(
    (index: number) => {
      const nextMappings = [...(data.mappings || [])];
      nextMappings.splice(index, 1);
      updateNodeData(nodeId, { mappings: nextMappings });
    },
    [data.mappings, nodeId, updateNodeData],
  );

  return (
    <div className="flex flex-col gap-4">
      <CollapsibleSection title="입력 데이터" showDivider>
        <div className="flex flex-col gap-3">
          <p className="text-xs text-gray-500">
            추출할 JSON 출력을 선택하세요.
          </p>
          <VariableSelectorSlot
            value={sourceSelector}
            selectedOutput={selectedOutput}
            label="추출할 입력 데이터"
            placeholder="입력 변수 클릭"
            kind="selector"
            onChange={handleSourceChange}
          />
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="데이터 필터 설정" showDivider>
        <JsonExtractionMappingControl
          mappings={data.mappings || []}
          onUpdate={handleUpdateMapping}
          onAdd={handleAddMapping}
          onRemove={handleRemoveMapping}
          title=""
          description="💡 입력 데이터에서 필요한 값만 골라냅니다. 가져올 데이터의 키(Key)를 입력하세요. (예: result, user.name) 추출된 값은 이 변수명으로 다른 노드에서 사용할 수 있습니다."
        />
      </CollapsibleSection>
    </div>
  );
}
