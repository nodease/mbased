import React from 'react';
import { BaseEdge, EdgeProps, getBezierPath, useStore } from '@xyflow/react';

export const PuzzleEdge = ({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  selected,
}: EdgeProps) => {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  // 노드 선택 상태 확인
  // useStore를 사용하여 노드의 선택 상태를 가져옵니다.
  const isSourceSelected = useStore((s: any) => {
    // nodeLookup이 존재하면 사용 (React Flow 12)
    if (s.nodeLookup) {
      return s.nodeLookup.get(source)?.selected;
    }
    // 하위 호환성 (구버전)
    return s.nodeInternals?.get(source)?.selected;
  });

  const isTargetSelected = useStore((s: any) => {
    if (s.nodeLookup) {
      return s.nodeLookup.get(target)?.selected;
    }
    return s.nodeInternals?.get(target)?.selected;
  });

  // 엣지 자체가 선택되었거나, 연결된 노드 중 하나가 선택된 경우 파란색 활성화
  const isActive = selected || isSourceSelected || isTargetSelected;

  // 색상 및 두께 설정
  // 기본: 회색(#9ca3af), 활성: 파란색(#3b82f6)
  const edgeColor = isActive ? '#3b82f6' : '#9ca3af';
  const edgeWidth = 2; // 간선 두께 (노드 테두리와 동일)

  return (
    <>
      {/* Markers at connection points */}
      <defs>
        {/* End marker - arrow */}
        <marker
          id={`arrow-end-${id}`}
          markerWidth="16"
          markerHeight="16"
          refX="13"
          refY="8"
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <polyline
            points="4,2 13,8 4,14"
            fill="none"
            stroke={edgeColor}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </marker>
      </defs>

      {/* 
        엣지 경로 (실선)
        얇은 회색 실선으로 노드를 연결하고 시작점에 점, 끝점에 화살표를 표시합니다.
      */}
      <BaseEdge
        path={edgePath}
        markerEnd={`url(#arrow-end-${id})`}
        style={{
          ...style,
          strokeWidth: edgeWidth,
          stroke: edgeColor,
          strokeLinecap: 'round',
          // transition 스타일 추가
          transition: 'stroke 0.3s ease, stroke-width 0.3s ease',
        }}
        className="react-flow__edge-path"
      />
    </>
  );
};
