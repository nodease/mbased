import { memo } from 'react';
import { FilePenLine } from 'lucide-react';
import { Node, NodeProps } from '@xyflow/react';

import { GmailDraftNodeData } from '../../../../types/Nodes';
import { BaseNode } from '../../BaseNode';
import { ValidationBadge } from '../../../ui/ValidationBadge';

export const GmailDraftNode = memo(
  ({ id, data, selected }: NodeProps<Node<GmailDraftNodeData>>) => {
    const incomplete =
      !data.credential_id ||
      data.processing_ref_selector.length < 2 ||
      data.reply_body_selector.length < 2;
    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle
        icon={<FilePenLine className="text-white" />}
        iconColor="#D93025"
      >
        <div className="flex items-center justify-between gap-2 p-1 text-xs text-gray-600">
          <span>발송 없이 Draft만 생성</span>
          {incomplete && <ValidationBadge />}
        </div>
      </BaseNode>
    );
  },
);

GmailDraftNode.displayName = 'GmailDraftNode';
