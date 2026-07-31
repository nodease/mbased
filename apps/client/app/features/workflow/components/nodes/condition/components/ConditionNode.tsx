import { memo, useCallback } from 'react';
import type { MouseEvent } from 'react';
import { Position, Node, NodeProps } from '@xyflow/react';
import { GitFork } from 'lucide-react';

import { BaseNode, SmartHandle } from '../../BaseNode';
import { ConditionNodeData } from '../../../../types/Nodes';
import { useWorkflowStore } from '../../../../store/useWorkflowStore';
import {
  getConditionNodeHandleTop,
  getConditionNodeMinimumHeight,
} from '../../../../utils/nodeHandleLayout';

export const ConditionNode = memo(
  ({
    data,
    selected,
    id,
  }: NodeProps<Node<ConditionNodeData>> & { highlightedHandle?: string }) => {
    const cases = data.cases || [];
    const outputs = [
      { id: 'default', label: 'Default', isDefault: true },
      ...cases.map((caseItem, index) => ({
        id: caseItem.id,
        label: caseItem.case_name || `Case ${index + 1}`,
        isDefault: false,
      })),
    ];
    const numberConnection = useWorkflowStore((state) => state.numberConnection);
    const startNumberConnection = useWorkflowStore(
      (state) => state.startNumberConnection,
    );

    const highlightedHandle =
      typeof window !== 'undefined'
        ? (window as Window & { __dragHighlightedHandle__?: string | null })
            .__dragHighlightedHandle__ || null
        : null;
    const isNumberConnectionSource =
      Boolean(numberConnection) && numberConnection?.sourceNodeId === id;

    const handleSourceNumberClick = useCallback(
      (sourceHandleId: string) => (event: MouseEvent<HTMLDivElement>) => {
        event.preventDefault();
        event.stopPropagation();
        startNumberConnection(id, sourceHandleId);
      },
      [id, startNumberConnection],
    );

    const getSourceNumberClassName = (sourceHandleId: string) =>
      isNumberConnectionSource &&
      numberConnection?.sourceHandleId === sourceHandleId
        ? 'border-blue-200 bg-blue-600 text-white ring-4 ring-blue-100'
        : undefined;

    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle={false}
        icon={<GitFork className="text-white" />}
        iconColor="#f97316"
        className="pr-28"
        showBodyContent={false}
        sizeMode="auto"
        minimumHeight={getConditionNodeMinimumHeight(cases.length)}
        additionalHandles={
          <>
            {outputs.map((output, index) => {
              const isHighlighted = highlightedHandle === output.id;
              return (
                <div
                  key={output.id}
                  className={`absolute right-0 z-40 flex h-8 items-center rounded pl-2 pr-10 transition-all duration-200 ${
                    isHighlighted
                      ? 'border-2 border-blue-500 bg-blue-100 shadow-lg'
                      : 'border-2 border-transparent bg-transparent'
                  }`}
                  style={{
                    top: getConditionNodeHandleTop(index),
                    transform: 'translateY(-50%)',
                  }}
                >
                  <span
                    className={`whitespace-nowrap text-base font-semibold ${
                      output.isDefault ? 'text-gray-500' : 'text-blue-600'
                    }`}
                  >
                    {output.label}
                  </span>
                  <SmartHandle
                    type="source"
                    position={Position.Right}
                    id={output.id}
                    className="!absolute !right-0 !top-1/2"
                    displayNumber={data.displayNumber}
                    numberClassName={getSourceNumberClassName(output.id)}
                    onNumberClick={handleSourceNumberClick(output.id)}
                  />
                </div>
              );
            })}
          </>
        }
      />
    );
  },
);

ConditionNode.displayName = 'ConditionNode';
