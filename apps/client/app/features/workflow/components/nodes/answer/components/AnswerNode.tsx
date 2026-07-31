import React, { memo } from 'react';
import { NodeProps, Node } from '@xyflow/react';
import { MessageSquare } from 'lucide-react';
import { AnswerNodeData } from '../../../../types/Nodes';
import { BaseNode } from '../../BaseNode';

export const AnswerNode = memo(
  ({ data, selected, id }: NodeProps<Node<AnswerNodeData>>) => {
    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle={false}
        icon={<MessageSquare className="text-white" />}
        iconColor="#10b981"
      />
    );
  },
);
AnswerNode.displayName = 'AnswerNode';
