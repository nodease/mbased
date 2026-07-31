import { memo, useMemo } from 'react';
import { ValidationBadge } from '../../../ui/ValidationBadge';
import { hasIncompleteVariables } from '../../../../utils/validationUtils';
import { NodeProps, Node } from '@xyflow/react';
import { Mail } from 'lucide-react';
import { MailNodeData } from '../../../../types/Nodes';
import { BaseNode } from '../../BaseNode';

export const MailNode = memo(
  ({ id, data, selected }: NodeProps<Node<MailNodeData>>) => {
    const hasValidationIssue = useMemo(() => {
      return (
        !data.credential_id || hasIncompleteVariables(data.referenced_variables)
      );
    }, [data.credential_id, data.referenced_variables]);

    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle={true}
        icon={<Mail className="text-white" />}
        iconColor="#EA4335"
      >
        <div className="flex flex-col gap-1 p-1">
          <div className="flex items-center gap-2">
            <div className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-100 text-gray-700 border border-gray-200">
              Mail
            </div>
            <div className="text-xs text-gray-600 flex-1 truncate">
              {data.credential_id ? 'Credential 연결됨' : 'Credential 필요'}
            </div>
          </div>
          {hasValidationIssue && <ValidationBadge />}
        </div>
      </BaseNode>
    );
  },
);

MailNode.displayName = 'MailNode';
