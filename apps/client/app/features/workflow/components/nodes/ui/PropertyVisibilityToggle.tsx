import { Eye, EyeOff } from 'lucide-react';

import { useWorkflowStore } from '../../../store/useWorkflowStore';
import {
  getNextVisibleProperties,
  isPropertyVisible,
} from '../../../utils/visibleNodeProperties';

type PropertyVisibilityToggleProps = {
  nodeId: string;
  propertyKey: string;
};

export const PropertyVisibilityToggle = ({
  nodeId,
  propertyKey,
}: PropertyVisibilityToggleProps) => {
  const node = useWorkflowStore((state) =>
    state.nodes.find((item) => item.id === nodeId),
  );
  const updateNodeData = useWorkflowStore((state) => state.updateNodeData);
  const visible = isPropertyVisible(node, propertyKey);

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        const latestNode = useWorkflowStore
          .getState()
          .nodes.find((item) => item.id === nodeId);
        updateNodeData(nodeId, {
          visibleProperties: getNextVisibleProperties(
            latestNode?.data.visibleProperties,
            propertyKey,
          ),
        });
      }}
      className="nodrag ml-1 flex h-4 w-4 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
      title={visible ? 'Collapsed에서 숨기기' : 'Collapsed에서 보이기'}
      aria-label={visible ? 'Collapsed에서 숨기기' : 'Collapsed에서 보이기'}
      aria-pressed={visible}
    >
      {visible ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
    </button>
  );
};
