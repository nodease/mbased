import { memo } from 'react';
import { CheckCheck } from 'lucide-react';
import { Node, NodeProps } from '@xyflow/react';

import { MailAcknowledgeNodeData } from '../../../../types/Nodes';
import { BaseNode } from '../../BaseNode';
import { ValidationBadge } from '../../../ui/ValidationBadge';

export const MailAcknowledgeNode = memo(
  ({ id, data, selected }: NodeProps<Node<MailAcknowledgeNodeData>>) => {
    const incomplete =
      data.processing_ref_selector.length < 2 ||
      data.required_effect_ref_selectors.length === 0;
    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle={false}
        icon={<CheckCheck className="text-white" />}
        iconColor="#188038"
      >
        <div className="flex items-center justify-between gap-2 p-1 text-xs text-gray-600">
          <span>필수 작업 성공 후 읽음 처리</span>
          {incomplete && <ValidationBadge />}
        </div>
      </BaseNode>
    );
  },
);

MailAcknowledgeNode.displayName = 'MailAcknowledgeNode';
