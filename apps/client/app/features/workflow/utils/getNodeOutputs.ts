import { Node } from '../types/Workflow';
import { AppNode } from '../types/Nodes';
import { getNodeOutputVariables } from './nodeVariablePorts';

export const getNodeOutputs = (node: Node): string[] => {
  return getNodeOutputVariables(node as AppNode).map((output) => output.key);
};
