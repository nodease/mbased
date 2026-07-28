import type { DeploymentOptimizationNode } from '../components/deployment/types';
import type { Node } from '../types/Nodes';
import type { PublicChatHistoryContainerPathSegment } from '../types/Deployment';

type WorkflowNodeData = {
  title?: unknown;
  subGraph?: unknown;
};

const nodeTitle = (node: Node): string => {
  const data = (node.data || {}) as WorkflowNodeData;
  return typeof data.title === 'string' && data.title.trim()
    ? data.title
    : node.type === 'loopNode'
      ? 'Loop 노드'
      : 'LLM 노드';
};

export const publicChatConsumerSelectionKey = (
  nodeId: string,
  containerPath: readonly PublicChatHistoryContainerPathSegment[],
): string =>
  JSON.stringify({
    container_path: containerPath,
    node_id: nodeId,
  });

export const collectDeploymentLlmNodes = (
  nodes: readonly Node[],
): DeploymentOptimizationNode[] => {
  const consumers: DeploymentOptimizationNode[] = [];

  const visit = (
    candidates: readonly Node[],
    containerPath: readonly PublicChatHistoryContainerPathSegment[],
    containerTitles: readonly string[],
  ) => {
    for (const node of candidates) {
      const title = nodeTitle(node);
      if (node.type === 'llmNode') {
        consumers.push({
          id: node.id,
          title:
            containerTitles.length > 0
              ? `${containerTitles.join(' / ')} / ${title}`
              : title,
          containerPath: [...containerPath],
          selectionKey: publicChatConsumerSelectionKey(node.id, containerPath),
        });
      }

      if (node.type !== 'loopNode') {
        continue;
      }
      const data = (node.data || {}) as WorkflowNodeData;
      if (
        !data.subGraph ||
        typeof data.subGraph !== 'object' ||
        Array.isArray(data.subGraph)
      ) {
        continue;
      }
      const nestedNodes = (data.subGraph as { nodes?: unknown }).nodes;
      if (!Array.isArray(nestedNodes)) {
        continue;
      }
      visit(
        nestedNodes as Node[],
        [...containerPath, { kind: 'loop', node_id: node.id }],
        [...containerTitles, title],
      );
    }
  };

  visit(nodes, [], []);
  return consumers;
};
