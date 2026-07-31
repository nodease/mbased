import { useMemo } from 'react';

import { AppNode } from '../types/Nodes';
import { useWorkflowStore } from '../store/useWorkflowStore';
import { getUpstreamNodes } from '../utils/getUpstreamNodes';
import {
  NodeOutputVariable,
  getNodeOutputVariables,
} from '../utils/nodeVariablePorts';

export type NodeInputVariableGroup = {
  sourceNodeId: string;
  sourceTitle: string;
  outputs: NodeOutputVariable[];
};

export type NodeIO = {
  node: AppNode | undefined;
  /** 업스트림 노드들의 출력 변수를 모두 합친 목록 (이 노드가 "입력"으로 받을 수 있는 변수들) */
  inputVariables: NodeOutputVariable[];
  /** 입력 변수를 업스트림 소스 노드 기준으로 그룹화한 목록 */
  inputVariableGroups: NodeInputVariableGroup[];
  /** 이 노드 자신이 생성하는 출력 변수 목록 */
  outputVariables: NodeOutputVariable[];
};

/**
 * useNodeIO
 * 특정 노드의 입력 변수(업스트림 노드들의 출력을 합친 것)와
 * 출력 변수(이 노드 자신의 출력)를 계산합니다.
 *
 * BaseNode의 호버 패널, NodeFullscreenEditor의 좌측 입력/출력 컬럼 등에서
 * 동일한 로직을 재사용하기 위해 분리되었습니다.
 */
export function useNodeIO(nodeId: string | null | undefined): NodeIO {
  const node = useWorkflowStore((state) =>
    nodeId
      ? (state.nodes.find((item) => item.id === nodeId) as AppNode | undefined)
      : undefined,
  );
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);

  const outputVariables = useMemo(
    () => getNodeOutputVariables(node),
    [node],
  );

  const inputVariables = useMemo(() => {
    if (!node) return [];
    return getUpstreamNodes(node.id, nodes, edges).flatMap((upstreamNode) =>
      getNodeOutputVariables(upstreamNode as AppNode),
    );
  }, [edges, node, nodes]);

  const inputVariableGroups = useMemo(() => {
    const groupMap = new Map<string, NodeInputVariableGroup>();

    for (const input of inputVariables) {
      const group = groupMap.get(input.sourceNodeId);
      if (group) {
        group.outputs.push(input);
      } else {
        groupMap.set(input.sourceNodeId, {
          sourceNodeId: input.sourceNodeId,
          sourceTitle: input.sourceTitle,
          outputs: [input],
        });
      }
    }

    return Array.from(groupMap.values());
  }, [inputVariables]);

  return { node, inputVariables, inputVariableGroups, outputVariables };
}
