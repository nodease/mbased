import { useCallback } from 'react';
import { LoopNodeData } from '../../../../types/Nodes';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { ReferencedVariablesControl } from '../../ui/ReferencedVariablesControl';
import { RoundedSelect } from '../../../ui/RoundedSelect';

interface LoopNodePanelProps {
  nodeId: string;
  data: LoopNodeData;
}

export function LoopNodePanel({ nodeId, data }: LoopNodePanelProps) {
  const { updateNodeData, nodes, edges } = useWorkflowStore();

  const upstreamNodes = getUpstreamNodes(nodeId, nodes, edges);

  const handleUpdateData = useCallback(
    (key: keyof LoopNodeData, value: any) => {
      updateNodeData(nodeId, { [key]: value });
    },
    [nodeId, updateNodeData],
  );

  // Input Mapping Handlers
  const handleAddInput = () => {
    handleUpdateData('inputs', [
      ...(data.inputs || []),
      { name: '', value_selector: [] },
    ]);
  };

  const handleRemoveInput = (index: number) => {
    const newInputs = [...(data.inputs || [])];
    newInputs.splice(index, 1);
    handleUpdateData('inputs', newInputs);
  };

  const handleUpdateInput = (
    index: number,
    field: 'name' | 'value_selector',
    value: any,
  ) => {
    const newInputs = [...(data.inputs || [])];
    newInputs[index] = { ...newInputs[index], [field]: value };
    handleUpdateData('inputs', newInputs);
  };

  // Output Mapping Handlers
  const handleAddOutput = () => {
    handleUpdateData('outputs', [
      ...(data.outputs || []),
      { name: '', value_selector: [] },
    ]);
  };

  const handleRemoveOutput = (index: number) => {
    const newOutputs = [...(data.outputs || [])];
    newOutputs.splice(index, 1);
    handleUpdateData('outputs', newOutputs);
  };

  const handleUpdateOutput = (
    index: number,
    field: 'name' | 'value_selector',
    value: any,
  ) => {
    const newOutputs = [...(data.outputs || [])];
    newOutputs[index] = { ...newOutputs[index], [field]: value };
    handleUpdateData('outputs', newOutputs);
  };

  return (
    <div className="flex flex-col gap-2">
      {/* 1. Configuration Section (Settings) */}
      <CollapsibleSection title="설정" showDivider>
        <div className="flex flex-col gap-4">
          {/* Inputs */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-gray-700 flex items-center gap-1">
                입력 변수 (선택사항)
              </label>
            </div>

            <div className="mb-2 p-3 bg-blue-50 border border-blue-200 rounded-md">
              <p className="text-xs text-blue-800 leading-relaxed">
                💡 <strong>하이브리드 접근:</strong>
                <br />
                • 비워두면 모든 외부 변수가 자동으로 접근 가능합니다
                <br />• 템플릿 문법:{' '}
                <code className="bg-blue-100 px-1 rounded">{`{{node_id.variable}}`}</code>
                <br />• Loop 특수 변수:{' '}
                <code className="bg-blue-100 px-1 rounded">{`{{loop.item}}`}</code>
                ,{' '}
                <code className="bg-blue-100 px-1 rounded">{`{{loop.index}}`}</code>
              </p>
            </div>

            <ReferencedVariablesControl
              variables={data.inputs || []}
              upstreamNodes={upstreamNodes}
              onUpdate={handleUpdateInput}
              onAdd={handleAddInput}
              onRemove={handleRemoveInput}
              title=""
              description="명시적으로 매핑할 변수만 추가하세요. 비워두면 모든 변수가 자동 전달됩니다."
              showAddButton={true}
              showRemoveButton={true}
              showItemLabel={false}
              placeholder="변수 이름 (예: items)"
            />
          </div>

          {/* Outputs */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-gray-700 flex items-center gap-1">
                출력 변수
              </label>
            </div>
            {/* Note: Outputs conceptually should map internal variables, but for now reusing the control. 
                 The user prompts imply mapped values. Ideally this should select from Internal Nodes, but we use upstreamNodes for consistency or reuse. 
                 If the user meant "Define Output Names", this selector might be confusing if they cant select anything. 
                 For now, I'll pass upstreamNodes (maybe they want to pass-through?), but typically Loop Output aggregates Loop Body results.
             */}
            <ReferencedVariablesControl
              variables={data.outputs || []}
              upstreamNodes={upstreamNodes} // Ideally should be internal nodes?
              onUpdate={handleUpdateOutput}
              onAdd={handleAddOutput}
              onRemove={handleRemoveOutput}
              title=""
              description="루프 실행 결과로 수집할 변수를 정의하세요."
              showAddButton={true}
              showRemoveButton={true}
              showItemLabel={false}
              placeholder="출력 변수 이름"
            />
          </div>

          {/* Error Strategy */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-semibold text-gray-700">
              오류 응답 방법
            </label>
            <RoundedSelect
              value={data.error_strategy || 'end'}
              onChange={(val) => handleUpdateData('error_strategy', val)}
              options={[
                { label: '종료', value: 'end' },
                { label: '건너뛰고 계속 진행', value: 'continue' },
              ]}
              placeholder="오류 처리 방식"
              className="px-2 py-1.5 text-xs bg-gray-50"
            />
          </div>
        </div>
      </CollapsibleSection>
    </div>
  );
}
