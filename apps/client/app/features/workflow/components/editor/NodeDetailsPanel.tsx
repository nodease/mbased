import { X, Pencil, Check, Pin } from 'lucide-react';
import { useEffect, useRef, useState, useMemo } from 'react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { getNodeDefinitionByType } from '../../config/nodeRegistry';

interface NodeDetailsPanelProps {
  nodeId: string | null;
  onClose: () => void;
  children: React.ReactNode;
  header?: {
    icon?: React.ReactNode | string;
    title?: string;
    description?: string;
  };
  headerActions?: React.ReactNode;
}

/**
 * NodeDetailsPanel
 * 노드 선택 시 우측에 나타나는 세부 설정 패널
 */
export default function NodeDetailsPanel({
  nodeId,
  onClose,
  children,
  headerActions,
}: NodeDetailsPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const {
    nodes,
    updateNodeData,
    selectedInnerNode,
    updateInnerNodeData,
    clearInnerNodeSelection,
  } = useWorkflowStore();

  // 제목 편집 상태
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');

  // 선택된 노드 찾기 (메인 노드 또는 내부 노드) - useMemo로 변환
  const { selectedNode, isInnerNode } = useMemo(() => {
    if (selectedInnerNode && selectedInnerNode.parentNodeId) {
      // 내부 노드인 경우
      const parentNode = nodes.find(
        (n) => n.id === selectedInnerNode.parentNodeId,
      );
      if (parentNode) {
        const subGraph = (parentNode.data as any).subGraph;
        if (subGraph && subGraph.nodes) {
          const innerNode = subGraph.nodes.find(
            (n: any) => n.id === selectedInnerNode.nodeId,
          );
          return { selectedNode: innerNode, isInnerNode: true };
        }
      }
      return { selectedNode: undefined, isInnerNode: false };
    } else {
      // 메인 노드인 경우
      const mainNode = nodes.find((n) => n.id === nodeId);
      return { selectedNode: mainNode, isInnerNode: false };
    }
  }, [nodeId, selectedInnerNode, nodes]);

  // 노드 정의 찾기 (아이콘, 설명 등)
  const nodeDef = selectedNode
    ? getNodeDefinitionByType(selectedNode.type || '')
    : null;

  // 설명 편집 상태
  // [NEW] 설명 편집 모드(isDescEditing) 및 입력값(editDesc) 상태 관리
  const [isDescEditing, setIsDescEditing] = useState(false);
  const [editDesc, setEditDesc] = useState('');

  // [NEW] 패널 고정 상태
  const [isPinned, setIsPinned] = useState(false);

  // 편집 시작 시 입력창 포커스

  useEffect(() => {
    if (isEditing && titleInputRef.current) {
      titleInputRef.current.focus();
    }
  }, [isEditing]);

  // [NEW] 설명 편집 시 입력창 자동 포커스 처리
  const descInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (isDescEditing && descInputRef.current) {
      descInputRef.current.focus();
    }
  }, [isDescEditing]);

  // 제목 저장 핸들러
  const handleSaveTitle = () => {
    if (editTitle.trim()) {
      if (isInnerNode && selectedInnerNode) {
        updateInnerNodeData(
          selectedInnerNode.parentNodeId,
          selectedInnerNode.nodeId,
          {
            title: editTitle.trim(),
          },
        );
      } else if (nodeId) {
        updateNodeData(nodeId, { title: editTitle.trim() });
      }
      setIsEditing(false);
    } else {
      // Revert if empty
      setEditTitle(
        (selectedNode?.data.title as string) || nodeDef?.name || 'Node',
      );
      setIsEditing(false);
    }
  };

  const startTitleEdit = () => {
    setEditTitle(
      (selectedNode?.data.title as string) || nodeDef?.name || 'Node',
    );
    setIsEditing(true);
  };

  // 설명 저장 핸들러
  // [NEW] 설명 수정 사항을 노드 데이터에 반영 (updateNodeData 호출)
  const handleSaveDesc = () => {
    if (isInnerNode && selectedInnerNode) {
      updateInnerNodeData(
        selectedInnerNode.parentNodeId,
        selectedInnerNode.nodeId,
        {
          description: editDesc.trim(),
        },
      );
    } else if (nodeId) {
      updateNodeData(nodeId, { description: editDesc.trim() });
    }
    setIsDescEditing(false);
  };

  const startDescEdit = () => {
    setEditDesc(
      (selectedNode?.data.description as string) ||
        nodeDef?.description ||
        '설명 없음',
    );
    setIsDescEditing(true);
  };

  // 키 입력 핸들러 (Enter: 저장, Escape: 취소)
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveTitle();
    } else if (e.key === 'Escape') {
      setEditTitle(
        (selectedNode?.data.title as string) || nodeDef?.name || 'Node',
      );
      setIsEditing(false);
    }
  };

  // [NEW] 설명 입력창 키보드 이벤트 핸들러 (Enter: 저장, Escape: 취소)
  const handleDescKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveDesc();
    } else if (e.key === 'Escape') {
      // 취소 시 원래 값으로 복원
      setEditDesc(
        (selectedNode?.data.description as string) ||
          nodeDef?.description ||
          '설명 없음',
      );
      setIsDescEditing(false);
    }
  };

  // 외부(캔버스) 클릭 시 패널 닫기
  useEffect(() => {
    if (!nodeId) return;

    const handleClickOutside = (event: MouseEvent) => {
      // 고정되면 닫지 않음
      if (isPinned) return;

      const target = event.target as HTMLElement;

      // 패널 내부 클릭 시 닫지 않음
      if (panelRef.current?.contains(target)) {
        return;
      }

      // 노드 클릭 시 닫지 않음 (노드 클릭 핸들러가 관리하도록 함)
      if (target.closest('.react-flow__node')) {
        return;
      }

      // 하단 패널이나 다른 UI 요소를 클릭하면 닫지 않음
      if (target.closest('.pointer-events-auto')) {
        return;
      }

      // 캔버스 클릭 시 패널 닫기
      if (target.closest('.react-flow__pane')) {
        if (isInnerNode) {
          clearInnerNodeSelection();
        }
        onClose();
      }
    };

    // 열 때 즉시 닫히는 것을 방지하기 위해 약간의 지연 추가
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 100);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [nodeId, onClose, isPinned]);

  if (!nodeId || !selectedNode) return null;

  return (
    <div
      ref={panelRef}
      className="absolute right-0 top-16 bottom-0 w-[400px] bg-white shadow-2xl z-50 flex flex-col border-l border-gray-200"
    >
      {/* 패널 헤더 */}
      {/* NOTE: [LLM] header prop으로 노드별 아이콘/텍스트를 표시하도록 확장 */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <div className="flex items-center gap-3 flex-1">
          <div
            className="flex items-center justify-center w-8 h-8 min-w-[32px] rounded text-white font-bold text-sm"
            style={{ backgroundColor: nodeDef?.color || '#6b7280' }}
          >
            {nodeDef?.icon || '📦'}
          </div>
          <div className="flex-1 min-w-0">
            {/* 제목 편집 영역 */}
            {isEditing ? (
              <div className="flex items-center gap-2 mb-1">
                <input
                  ref={titleInputRef}
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onBlur={handleSaveTitle}
                  className="w-full text-lg font-semibold text-gray-900 border-b-2 border-primary-500 focus:outline-none px-1 py-0.5 bg-transparent"
                />
                <button
                  onClick={handleSaveTitle}
                  className="p-1 text-green-600 hover:bg-green-50 rounded"
                >
                  <Check className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div
                className="group flex items-center gap-2 cursor-pointer mb-1"
                onClick={startTitleEdit}
              >
                <h2
                  className="text-lg font-semibold text-gray-900 truncate max-w-[200px]"
                  title={
                    (selectedNode.data.title as string) ||
                    nodeDef?.name ||
                    'Node'
                  }
                >
                  {(selectedNode.data.title as string) ||
                    nodeDef?.name ||
                    'Node'}
                </h2>
                <Pencil className="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            )}

            {/* 설명 편집 영역 */}
            {/* [NEW] 설명 부분을 클릭하여 편집 모드로 전환하거나, 편집 중에는 입력창 표시 */}
            {isDescEditing ? (
              <div className="flex items-center gap-2">
                <input
                  ref={descInputRef}
                  type="text"
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  onKeyDown={handleDescKeyDown}
                  onBlur={handleSaveDesc}
                  className="w-full text-xs text-gray-500 border-b border-primary-500 focus:outline-none px-1 py-0.5 bg-transparent"
                  placeholder="설명을 입력하세요"
                />
                <button
                  onClick={handleSaveDesc}
                  className="p-1 text-green-600 hover:bg-green-50 rounded"
                >
                  <Check className="w-3 h-3" />
                </button>
              </div>
            ) : (
              <div
                className="group flex items-center gap-2 cursor-pointer"
                onClick={startDescEdit}
              >
                <p className="text-xs text-gray-500 truncate max-w-[250px]">
                  {(selectedNode.data.description as string) ||
                    nodeDef?.description ||
                    '설명 없음'}
                </p>
                <Pencil className="w-3 h-3 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {headerActions}
          <button
            onClick={() => setIsPinned(!isPinned)}
            className={`p-1.5 rounded transition-colors ${
              isPinned
                ? 'bg-blue-50 text-blue-600'
                : 'hover:bg-gray-100 text-gray-400'
            }`}
            title={isPinned ? '패널 고정 해제' : '패널 고정'}
          >
            <Pin
              className={`w-4 h-4 ${isPinned ? 'fill-current' : ''} transition-transform`}
            />
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 rounded transition-colors"
            aria-label="Close panel"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>
      </div>

      {/* 패널 콘텐츠 - 현재는 children을 표시하며, 현재 사용에서는 보통 비어 있음 */}
      {/* 향후: 노드 유형에 따라 여기에 특정 속성 편집기 추가 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {children}

        {/* children이 없을 경우 대체 콘텐츠 */}
        {!children && (
          <div className="text-sm text-gray-500">
            Configure this node in the canvas or add property controls here.
          </div>
        )}
      </div>
    </div>
  );
}
