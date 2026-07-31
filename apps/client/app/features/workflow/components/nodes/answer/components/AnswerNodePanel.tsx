import { useCallback, useMemo } from 'react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { AnswerNodeData, AnswerNodeOutput } from '../../../../types/Nodes';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import {
  NodeOutputVariable,
  getNodeOutputVariables,
} from '../../../../utils/nodeVariablePorts';
import { ArrowRight, Plus, Trash2 } from 'lucide-react';
import { VariableSelectorSlot } from '../../ui/VariableSelectorSlot';

interface AnswerNodePanelProps {
  nodeId: string;
  data: AnswerNodeData;
}

const selectorFor = (output: NodeOutputVariable) => [
  output.sourceNodeId,
  output.key,
];

const selectorEquals = (
  selector: string[] | undefined,
  output: NodeOutputVariable,
) =>
  Array.isArray(selector) &&
  selector[0] === output.sourceNodeId &&
  (selector[1] === output.key || selector[1] === output.outputId);

const toSafeVariableName = (value: string) => {
  const normalized = value.trim().replace(/[^\w]/g, '_');
  return normalized || 'result';
};

const RETURN_KEY_PATTERN = /^[a-z][a-z0-9_]*$/;
const RETURN_KEY_MAX_LENGTH = 32;

const getReturnKeyError = (
  value: string,
  outputs: AnswerNodeOutput[],
  currentIndex: number,
) => {
  const trimmed = value.trim();

  if (!trimmed) return '반환 key를 입력하세요.';
  if (trimmed.length > RETURN_KEY_MAX_LENGTH) {
    return `반환 key는 최대 ${RETURN_KEY_MAX_LENGTH}자까지 사용할 수 있습니다.`;
  }
  if (!/^[a-z]/.test(trimmed)) {
    return '첫 글자는 영문 소문자여야 합니다.';
  }
  if (!RETURN_KEY_PATTERN.test(trimmed)) {
    return '영문 소문자, 숫자, 언더바(_)만 사용할 수 있습니다. 예: analysis_result';
  }

  const isDuplicated = outputs.some(
    (output, index) =>
      index !== currentIndex && output.variable?.trim() === trimmed,
  );
  if (isDuplicated) return '이미 사용 중인 반환 key입니다.';

  return null;
};

const getUniqueVariableName = (
  outputs: AnswerNodeOutput[],
  baseValue: string,
  currentIndex?: number,
) => {
  const baseName = toSafeVariableName(baseValue);
  const usedNames = new Set(
    outputs
      .filter((_, index) => index !== currentIndex)
      .map((output) => output.variable?.trim())
      .filter(Boolean),
  );

  if (!usedNames.has(baseName)) return baseName;

  let suffix = 2;
  while (usedNames.has(`${baseName}_${suffix}`)) {
    suffix += 1;
  }
  return `${baseName}_${suffix}`;
};

export function AnswerNodePanel({ nodeId, data }: AnswerNodePanelProps) {
  const { updateNodeData, nodes, edges } = useWorkflowStore();

  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );

  const upstreamOutputMap = useMemo(() => {
    const outputMap = new Map<string, NodeOutputVariable>();
    for (const upstreamNode of upstreamNodes) {
      for (const output of getNodeOutputVariables(upstreamNode)) {
        outputMap.set(`${output.sourceNodeId}:${output.key}`, output);
        if (output.outputId) {
          outputMap.set(`${output.sourceNodeId}:${output.outputId}`, output);
        }
      }
    }
    return outputMap;
  }, [upstreamNodes]);

  const getSelectedOutput = useCallback(
    (selector: string[] | undefined) => {
      if (!Array.isArray(selector) || selector.length < 2) return undefined;
      return upstreamOutputMap.get(`${selector[0]}:${selector[1]}`);
    },
    [upstreamOutputMap],
  );

  const handleAddOutput = useCallback(() => {
    const newOutputs = [
      ...(data.outputs || []),
      { variable: '', value_selector: [] },
    ];
    updateNodeData(nodeId, { outputs: newOutputs });
  }, [data.outputs, nodeId, updateNodeData]);

  const handleUpdateOutput = useCallback(
    (index: number, key: keyof AnswerNodeOutput, value: string | string[]) => {
      const newOutputs = [...(data.outputs || [])];
      newOutputs[index] = {
        ...newOutputs[index],
        [key]: value,
      };
      updateNodeData(nodeId, { outputs: newOutputs });
    },
    [data.outputs, nodeId, updateNodeData],
  );

  const handleRemoveOutput = useCallback(
    (index: number) => {
      const newOutputs = [...(data.outputs || [])];
      newOutputs.splice(index, 1);
      updateNodeData(nodeId, { outputs: newOutputs });
    },
    [data.outputs, nodeId, updateNodeData],
  );

  const applyOutputMapping = useCallback(
    (droppedOutput: NodeOutputVariable, index: number) => {
      const currentOutputs = data.outputs || [];
      const existingIndex = currentOutputs.findIndex((output, outputIndex) => {
        if (outputIndex === index) return false;
        return selectorEquals(output.value_selector, droppedOutput);
      });

      const targetIndex = index >= 0 ? index : existingIndex;
      const nextVariable = getUniqueVariableName(
        currentOutputs,
        droppedOutput.key,
        targetIndex,
      );
      const nextOutput = {
        variable: currentOutputs[targetIndex]?.variable || nextVariable,
        value_selector: selectorFor(droppedOutput),
      };

      const nextOutputs = currentOutputs.map((output, outputIndex) =>
        outputIndex === targetIndex ? { ...output, ...nextOutput } : output,
      );

      updateNodeData(nodeId, { outputs: nextOutputs });
    },
    [data.outputs, nodeId, updateNodeData],
  );

  const outputs = data.outputs || [];

  return (
    <div className="flex flex-col gap-4">
      <CollapsibleSection title="반환값 매핑" showDivider>
        <div className="flex flex-col gap-3">
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs leading-snug text-gray-500">
              좌측 입력 패널에서 출력 칩을 클릭해 최종 응답으로 내보낼 값을
              연결하세요.
            </p>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                handleAddOutput();
              }}
              className="shrink-0 rounded p-1 transition-colors hover:bg-gray-200"
              title="빈 반환값 추가"
              aria-label="빈 반환값 추가"
            >
              <Plus className="h-4 w-4 text-gray-600" />
            </button>
          </div>

          {outputs.length === 0 && (
            <div className="flex min-h-14 items-center justify-center rounded-lg border border-dashed border-blue-200 bg-blue-50/60 px-3 py-3 text-xs font-medium text-blue-700 transition-colors hover:border-blue-300 hover:bg-blue-50">
              빈 반환값을 추가한 뒤 좌측 입력 변수를 연결하세요.
            </div>
          )}

          {outputs.map((output, index) => {
            const selectedOutput = getSelectedOutput(output.value_selector);
            const sourceTitle = selectedOutput?.sourceTitle;
            const returnKeyError = getReturnKeyError(
              output.variable,
              outputs,
              index,
            );

            return (
              <div
                key={index}
                className={`group flex flex-col gap-2 rounded-lg border bg-white p-3 shadow-sm transition-all hover:shadow-md ${
                  returnKeyError
                    ? 'border-red-200 hover:border-red-300'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <div className="h-1.5 w-1.5 rounded-full bg-blue-500/50" />
                    <span className="text-[10px] font-bold tracking-wider text-gray-400">
                      반환값 {index + 1}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveOutput(index)}
                    className="flex h-5 w-5 items-center justify-center rounded bg-transparent text-gray-400 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                    title="반환값 삭제"
                    aria-label="반환값 삭제"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <div
                    className="flex-[4]"
                    title="좌측 입력 변수를 클릭해서 소스를 연결하거나 교체"
                  >
                    <VariableSelectorSlot
                      value={output.value_selector}
                      selectedOutput={selectedOutput}
                      sourceLabel={sourceTitle}
                      label={`반환값 ${index + 1} 소스`}
                      placeholder="입력 변수 클릭"
                      kind="mapping"
                      onChange={(_, droppedOutput) =>
                        applyOutputMapping(droppedOutput, index)
                      }
                    />
                  </div>

                  <div className="flex flex-none items-center justify-center text-gray-400">
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100">
                      <ArrowRight className="h-3 w-3 text-gray-500" />
                    </div>
                  </div>

                  <div className="flex-[3]">
                    <input
                      type="text"
                      className={`w-full rounded-md border px-2.5 py-1.5 text-xs font-semibold placeholder:font-normal placeholder:text-gray-500 focus:outline-none focus:ring-1 ${
                        returnKeyError
                          ? 'border-red-300 bg-red-50/40 text-red-700 focus:border-red-500 focus:ring-red-100'
                          : 'border-gray-200 text-blue-600 focus:border-blue-500 focus:ring-blue-500/20'
                      }`}
                      placeholder="예: analysis_result"
                      value={output.variable}
                      maxLength={RETURN_KEY_MAX_LENGTH}
                      aria-invalid={Boolean(returnKeyError)}
                      title="최종 실행 결과에서 사용할 응답 필드명입니다."
                      onChange={(event) =>
                        handleUpdateOutput(
                          index,
                          'variable',
                          event.target.value,
                        )
                      }
                    />
                    {returnKeyError ? (
                      <p className="mt-1 text-[11px] font-medium leading-snug text-red-600">
                        {returnKeyError}
                      </p>
                    ) : (
                      <p className="mt-1 text-[11px] leading-snug text-gray-400">
                        최종 응답 JSON에서 이 이름의 필드로 반환됩니다. 최대{' '}
                        {RETURN_KEY_MAX_LENGTH}자.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </CollapsibleSection>
    </div>
  );
}
