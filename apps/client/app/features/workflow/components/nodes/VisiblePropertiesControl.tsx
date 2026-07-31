import { AppNode } from '../../types/Nodes';
import { getVisiblePropertyDefinitions } from '../../utils/visibleNodeProperties';
import { PropertyVisibilityToggle } from './ui/PropertyVisibilityToggle';

export const VisiblePropertiesControl = ({ node }: { node: AppNode }) => {
  const definitions = getVisiblePropertyDefinitions(node);
  if (definitions.length === 0) return null;

  return (
    <div className="mb-3 rounded-md border border-gray-200 bg-gray-50 px-3 py-2">
      <div className="mb-2 text-[11px] font-semibold text-gray-500">
        Collapsed 표시
      </div>
      <div className="flex flex-wrap gap-1.5">
        {definitions.map((definition) => (
          <div
            key={definition.key}
            className="flex items-center gap-1 rounded border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700"
          >
            <span>{definition.label}</span>
            <PropertyVisibilityToggle
              nodeId={node.id}
              propertyKey={definition.key}
            />
          </div>
        ))}
      </div>
    </div>
  );
};
