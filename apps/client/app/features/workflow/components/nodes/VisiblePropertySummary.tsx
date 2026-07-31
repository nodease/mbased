import { AppNode } from '../../types/Nodes';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { getUpstreamNodes } from '../../utils/getUpstreamNodes';
import { getTokenLabelMap } from '../../utils/nodeVariablePorts';
import {
  getVisiblePropertyDefinitions,
  renderTokenPreview,
} from '../../utils/visibleNodeProperties';

const getNodeTokenReferences = (node: AppNode) => {
  const data = node.data as Record<string, unknown>;

  switch (node.type) {
    case 'templateNode':
      return data.variables;
    case 'workflowNode':
    case 'loopNode':
      return data.inputs;
    default:
      return data.referenced_variables;
  }
};

export const VisiblePropertySummary = ({ node }: { node: AppNode }) => {
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const upstreamNodes = getUpstreamNodes(node.id, nodes, edges) as AppNode[];
  const tokenLabels = getTokenLabelMap(
    getNodeTokenReferences(node),
    upstreamNodes,
  );
  const visibleKeys = Array.isArray(node.data.visibleProperties)
    ? node.data.visibleProperties
    : [];
  const items = getVisiblePropertyDefinitions(node)
    .filter((definition) => visibleKeys.includes(definition.key))
    .map((definition) => ({
      ...definition,
      value: definition.getValue(node),
    }))
    .filter((item) => item.value.trim().length > 0);

  if (items.length === 0) return null;

  return (
    <div className="mt-4 flex min-w-0 flex-col gap-2.5 border-t border-gray-100 pt-3">
      {items.map((item) => (
        <div key={item.key} className="min-w-0 text-[13px] leading-relaxed">
          {item.multiline ? (
            <>
              <div className="mb-0.5 font-semibold text-gray-500">
                {item.label}:
              </div>
              <div className="line-clamp-2 min-w-0 break-words font-medium text-gray-800">
                {renderTokenPreview(item.value, tokenLabels)}
              </div>
            </>
          ) : (
            <div className="line-clamp-1 min-w-0 break-words font-medium text-gray-800">
              <span className="font-semibold text-gray-500">
                {item.label}:{' '}
              </span>
              {renderTokenPreview(item.value, tokenLabels)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
