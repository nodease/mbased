'use client';

import { useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { nodeRegistry, NodeDefinition } from '../../config/nodeRegistry';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { canAddNodeDefinitionAfterTarget } from '../../hooks/useNodeCreation';
import type { AppNode } from '../../types/Nodes';

interface NodeLibraryContentProps {
  onDragStart?: (
    event: React.DragEvent,
    nodeType: string,
    nodeDef: NodeDefinition,
  ) => void;
  onSelect?: (nodeType: string, nodeDef: NodeDefinition) => void;
  onAddAfterSelected?: (nodeType: string, nodeDef: NodeDefinition) => void;
  hoveredNode?: string | null;
  onHoverNode?: (
    nodeId: string | null,
    node: any,
    event: React.MouseEvent,
  ) => void;
  disabledNodeTypes?: string[];
}

// 탭 정의 (이미지와 유사하게 구성)
const TABS = [
  { id: 'nodes', label: '노드' },
  { id: 'tools', label: '도구' },
  { id: 'start', label: '시작' },
] as const;

export const NodeLibraryContent = ({
  onDragStart,
  onSelect,
  onAddAfterSelected,
  hoveredNode,
  onHoverNode,
  disabledNodeTypes = [],
}: NodeLibraryContentProps) => {
  // 노드 개수 확인하여 초기 탭 결정
  // 처음 생성 시: 시작 노드 1개만 존재 -> 'start' 탭
  // 이후: 노드가 2개 이상이거나 시작 노드가 아닌 경우 -> 'nodes' 탭
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const isInitialState =
    nodes.length === 1 &&
    (nodes[0].type === 'startNode' ||
      nodes[0].type === 'webhookTrigger' ||
      nodes[0].type === 'scheduleTrigger');
  const initialTab = isInitialState ? 'start' : 'nodes';

  const [activeTab, setActiveTab] =
    useState<(typeof TABS)[number]['id']>(initialTab);
  const [searchQuery, setSearchQuery] = useState('');

  // 탭에 따른 카테고리 필터링
  const getFilteredCategories = () => {
    switch (activeTab) {
      case 'start':
        return ['trigger'];
      case 'tools':
        return ['plugin', 'workflow'];
      case 'nodes':
      default:
        return ['llm', 'logic', 'data', 'database'];
    }
  };

  // 노드 필터링 로직
  const filteredNodes = nodeRegistry.filter((node) => {
    const matchesSearch =
      node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      node.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      false;

    const matchesTab = getFilteredCategories().includes(node.category);

    return matchesSearch && matchesTab && node.implemented;
  });

  // 노드 비활성화 체크
  const isNodeDisabled = (nodeType: string) => {
    return disabledNodeTypes.includes(nodeType);
  };

  return (
    <div className="flex h-full w-full select-none flex-col bg-white">
      {/* 1. Tabs */}
      <div className="flex items-center border-b border-slate-100 px-4 pb-2 pt-4">
        <div className="w-full grid grid-cols-3 gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`relative flex w-full justify-center pb-2 text-sm font-semibold transition-colors ${
                activeTab === tab.id
                  ? 'text-slate-950'
                  : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {tab.label}
              {activeTab === tab.id && (
                <div className="absolute bottom-0 left-0 h-0.5 w-full rounded-t-full bg-slate-950" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* 2. Search */}
      <div className="px-4 py-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="검색 노드"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 w-full rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm font-semibold text-slate-900 transition-all placeholder:text-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </div>
      </div>

      {/* 3. Node List */}
      <div className="flex-1 overflow-y-auto px-2 pb-4 scrollbar-hide">
        {filteredNodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-slate-400">
            <p className="text-sm font-semibold">검색 결과가 없습니다</p>
          </div>
        ) : (
          <div className="space-y-4">
            {filteredNodes.map((node) => {
              const disabled = isNodeDisabled(node.type);
              const canAddAfter =
                !disabled &&
                !!onAddAfterSelected &&
                canAddNodeDefinitionAfterTarget(
                  node,
                  nodes as AppNode[],
                  edges,
                );

              return (
                <div
                  key={node.id}
                  draggable={
                    !!onDragStart && !disabled && node.category !== 'workflow'
                  }
                  onDragStart={(e) => {
                    if (!disabled && node.category !== 'workflow') {
                      onDragStart?.(e, node.type, node);
                    }
                  }}
                  onClick={() => {
                    if (!disabled) {
                      onSelect?.(node.type, node);
                    }
                  }}
                  onMouseEnter={(e) => onHoverNode?.(node.id, node, e)}
                  onMouseLeave={(e) => onHoverNode?.(null, null, e)}
                  className={`group flex items-center gap-3 rounded-lg p-2 transition-all ${
                    disabled
                      ? 'opacity-50 cursor-not-allowed'
                      : 'cursor-pointer hover:bg-slate-100 active:scale-[0.98]'
                  } ${
                    hoveredNode === node.id && !disabled ? 'bg-slate-100' : ''
                  }`}
                >
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center shadow-sm text-white transition-transform group-hover:scale-105"
                    style={{ backgroundColor: node.color }}
                  >
                    {node.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-950">
                      {node.name}
                    </div>
                    {/* Description is hidden in list, shown in hover card usually */}
                  </div>
                  {onAddAfterSelected && (
                    <button
                      type="button"
                      draggable={false}
                      disabled={!canAddAfter}
                      title={
                        canAddAfter
                          ? '기준 노드 뒤에 추가'
                          : '노드 하나를 선택하거나 단일 말단 노드가 있을 때 추가할 수 있습니다'
                      }
                      aria-label={`${node.name} 기준 노드 뒤에 추가`}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (canAddAfter) {
                          onAddAfterSelected?.(node.type, node);
                        }
                      }}
                      className={`ml-auto flex h-7 min-w-0 shrink-0 items-center gap-1 rounded-md border px-2 text-[11px] font-bold transition-colors ${
                        canAddAfter
                          ? 'border-blue-200 bg-blue-50 text-blue-700 hover:border-blue-300 hover:bg-blue-100'
                          : 'cursor-not-allowed border-slate-100 bg-slate-50 text-slate-300'
                      }`}
                    >
                      <Plus className="h-3.5 w-3.5" />
                      <span>뒤에 추가</span>
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
