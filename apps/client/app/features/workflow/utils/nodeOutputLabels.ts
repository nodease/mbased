import { AppNode, BaseNodeData } from '../types/Nodes';

export const getOutputLabels = (data: BaseNodeData) =>
  data.outputLabels &&
  typeof data.outputLabels === 'object' &&
  !Array.isArray(data.outputLabels)
    ? (data.outputLabels as Record<string, string>)
    : {};

const withoutOutputLabel = (
  labels: Record<string, string>,
  outputKey: string,
) => {
  const nextLabels = { ...labels };
  delete nextLabels[outputKey];
  return nextLabels;
};

export const buildOutputLabelPatch = (
  node: AppNode,
  outputKey: string,
  outputId: string | undefined,
  nextLabel: string,
) => {
  const data = node.data as BaseNodeData;
  const currentLabels = getOutputLabels(data);

  if (node.type === 'startNode' && Array.isArray(data.variables)) {
    let changed = false;
    const nextVariables = data.variables.map((variable) => {
      if (!variable || typeof variable !== 'object') return variable;

      const variableRecord = variable as Record<string, unknown>;
      const variableId = String(variableRecord.id || '').trim();
      const variableName = String(variableRecord.name || '').trim();
      const variableLabel = String(variableRecord.label || '').trim();
      const matches =
        variableName === outputKey ||
        variableId === outputId ||
        (!variableName && variableLabel === outputKey);

      if (!matches) return variable;
      changed = true;
      return { ...variableRecord, label: nextLabel };
    });

    if (changed) {
      return {
        variables: nextVariables,
        outputLabels: withoutOutputLabel(currentLabels, outputKey),
      };
    }
  }

  return {
    outputLabels: {
      ...currentLabels,
      [outputKey]: nextLabel,
    },
  };
};
