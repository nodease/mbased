import React, { useEffect, useState } from 'react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import {
  WorkflowNodeData,
  WorkflowVariable,
  WorkflowNodeInput,
} from '../../../../types/Nodes';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { getNodeOutputVariables } from '../../../../utils/nodeVariablePorts';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { VariableSelectorSlot } from '../../ui/VariableSelectorSlot';

interface WorkflowNodePanelProps {
  nodeId: string;
  data: WorkflowNodeData;
}

export const WorkflowNodePanel: React.FC<WorkflowNodePanelProps> = ({
  nodeId,
  data,
}) => {
  const { nodes, updateNodeData } = useWorkflowStore();
  const [targetVariables, setTargetVariables] = useState<WorkflowVariable[]>(
    [],
  );
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 1. 대상 워크플로우 정보 가져오기 (배포된 버전 기준)
  useEffect(() => {
    const loadTargetDeployment = async () => {
      // deployment_id가 있으면 우선 사용
      const targetDeploymentId = data.deployment_id;

      if (!targetDeploymentId) {
        // deployment_id가 없으면 workflowId로 fallback 시도하지 않음 (버전 불일치 위험)
        // 하지만 기존 데이터 호환성을 위해 경고만 표시하거나,
        // workflowId가 있으면 최신 draft라도 보여줄지는 기획적 결정.
        // 여기서는 배포 ID가 필수라고 가정하고 에러 처리.
        // (단, 마이그레이션 과도기라면 workflowId로 draft 조회하는 로직을 남겨둘 수도 있음.
        //  일단 사용자가 "불러온 노드"라고 했으므로 deployment_id가 있을 것임)
        return;
      }

      setIsLoading(true);
      setError(null);
      try {
        const deployment = await workflowApi.getDeployment(targetDeploymentId);

        if (deployment) {
          // 1. Output Schema 처리
          const outputKeys =
            deployment.output_schema?.outputs?.map((o) => o.variable) || [];

          // 변경사항이 있다면 outputs 업데이트
          if (JSON.stringify(data.outputs) !== JSON.stringify(outputKeys)) {
            updateNodeData(nodeId, { outputs: outputKeys });
          }

          // 2. Input Schema 처리 -> targetVariables
          if (deployment.input_schema?.variables) {
            // InputVariable -> WorkflowVariable 변환
            const mappedVars: WorkflowVariable[] =
              deployment.input_schema.variables.map((v, idx) => ({
                id: v.name || `var-${idx}`,
                name: v.name,
                label: v.label || v.name,
                type: (v.type as WorkflowVariable['type']) || 'text',
                required: true, // 배포된 입력은 기본적으로 required라고 가정하거나, 스키마에 required 필드 추가 필요
              }));
            setTargetVariables(mappedVars);
          } else {
            setTargetVariables([]);
          }
        }
      } catch {
        setError('배포 정보를 불러오지 못했습니다.');
      } finally {
        setIsLoading(false);
      }
    };

    loadTargetDeployment();
  }, [data.deployment_id, data.outputs, nodeId, updateNodeData]);

  // 2. 선택자(Selector) 업데이트 처리
  const handleSelectorUpdate = (targetVarName: string, selector: string[]) => {
    const currentInputs = [...(data.inputs || [])];
    const existingIdx = currentInputs.findIndex(
      (i) => i.name === targetVarName,
    );

    let newInput: WorkflowNodeInput;

    if (existingIdx !== -1) {
      newInput = { ...currentInputs[existingIdx], value_selector: selector };
      currentInputs[existingIdx] = newInput;
    } else {
      newInput = {
        name: targetVarName,
        value_selector: selector,
      };
      currentInputs.push(newInput);
    }

    updateNodeData(nodeId, { inputs: currentInputs });
  };

  if (isLoading) {
    return (
      <div className="p-4 text-sm text-gray-500">
        Loading target workflow info...
      </div>
    );
  }

  if (error) {
    return <div className="p-4 text-sm text-red-500">{error}</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <CollapsibleSection title="Input Parameters">
        <div className="flex flex-col gap-2">
          <p className="text-xs text-gray-500 mb-2">
            대상 워크플로우의 <b>입력 노드</b>에 정의된 변수에 값을 전달합니다.
          </p>

          {targetVariables.length === 0 ? (
            <div className="text-center text-xs text-gray-400 py-4 border border-dashed border-gray-300 rounded">
              입력 변수가 없는 워크플로우입니다.
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {targetVariables.map((targetVar) => {
                // 현재 매핑 찾기
                const mapping = data.inputs?.find(
                  (i) => i.name === targetVar.name,
                );
                const selectedNodeId = mapping?.value_selector?.[0] || '';
                const selectedNode = nodes.find((n) => n.id === selectedNodeId);
                const selectedOutput = selectedNode
                  ? getNodeOutputVariables(selectedNode).find(
                      (output) =>
                        output.key === mapping?.value_selector?.[1] ||
                        output.outputId === mapping?.value_selector?.[1],
                    )
                  : undefined;

                return (
                  <div
                    key={targetVar.id}
                    className="flex flex-col gap-2 rounded border border-gray-200 bg-gray-50 p-2"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold text-gray-700">
                          {targetVar.name}
                        </span>
                        <span className="text-[10px] text-gray-500 px-1.5 py-0.5 bg-gray-200 rounded-full">
                          {targetVar.type}
                        </span>
                      </div>
                      {targetVar.required && (
                        <span className="text-[10px] text-red-500">
                          *Required
                        </span>
                      )}
                    </div>

                    <VariableSelectorSlot
                      value={mapping?.value_selector}
                      selectedOutput={selectedOutput}
                      label={`${targetVar.label || targetVar.name} 입력`}
                      placeholder="입력 변수 클릭"
                      kind="mapping"
                      onChange={(selector) =>
                        handleSelectorUpdate(targetVar.name, selector)
                      }
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </CollapsibleSection>
    </div>
  );
};
