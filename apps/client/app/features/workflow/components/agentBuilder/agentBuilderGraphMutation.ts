import type { Edge } from '@xyflow/react';
import type { Node } from '../../types/Workflow';
import type {
  AgentBuilderGraphMutation,
  AgentBuilderGraphOperation,
} from '../../api/agentBuilderApi';

export type { AgentBuilderGraphMutation, AgentBuilderGraphOperation };

export const applyAgentBuilderOperations = (
  currentNodes: Node[],
  currentEdges: Edge[],
  operations: AgentBuilderGraphOperation[],
) => {
  const nodes = new Map(
    structuredClone(currentNodes).map((node) => [node.id, node]),
  );
  const edges = new Map(
    structuredClone(currentEdges).map((edge) => [edge.id, edge]),
  );

  for (const operation of operations) {
    switch (operation.op) {
      case 'remove_edge': {
        if (!edges.delete(operation.edge_id)) {
          throw new Error(`edge ${operation.edge_id} is missing`);
        }
        break;
      }
      case 'remove_node': {
        if (!nodes.has(operation.node_id)) {
          throw new Error(`node ${operation.node_id} is missing`);
        }
        const connected = [...edges.values()].some(
          (edge) =>
            edge.source === operation.node_id ||
            edge.target === operation.node_id,
        );
        if (connected) {
          throw new Error(`node ${operation.node_id} still has edges`);
        }
        nodes.delete(operation.node_id);
        break;
      }
      case 'add_node': {
        if (nodes.has(operation.node.id)) {
          throw new Error(`node ${operation.node.id} already exists`);
        }
        nodes.set(operation.node.id, structuredClone(operation.node));
        break;
      }
      case 'replace_node_data': {
        const node = nodes.get(operation.node_id);
        if (!node) {
          throw new Error(`node ${operation.node_id} is missing`);
        }
        nodes.set(operation.node_id, {
          ...node,
          data: structuredClone(operation.data) as Node['data'],
        } as Node);
        break;
      }
      case 'replace_node_position': {
        const node = nodes.get(operation.node_id);
        if (!node) {
          throw new Error(`node ${operation.node_id} is missing`);
        }
        nodes.set(operation.node_id, {
          ...node,
          position: structuredClone(operation.position),
        });
        break;
      }
      case 'add_edge': {
        if (edges.has(operation.edge.id)) {
          throw new Error(`edge ${operation.edge.id} already exists`);
        }
        if (
          !nodes.has(operation.edge.source) ||
          !nodes.has(operation.edge.target)
        ) {
          throw new Error(`edge ${operation.edge.id} has a missing endpoint`);
        }
        edges.set(operation.edge.id, structuredClone(operation.edge));
        break;
      }
    }
  }

  return { nodes: [...nodes.values()], edges: [...edges.values()] };
};
