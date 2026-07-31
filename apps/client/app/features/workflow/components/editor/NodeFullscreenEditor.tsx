'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Pencil,
  X,
} from 'lucide-react';

import { getNodeDefinitionByType } from '../../config/nodeRegistry';
import { useKeyboardShortcut } from '../../hooks/useKeyboardShortcut';
import { useNodeIO } from '../../hooks/useNodeIO';
import {
  NodeNavigationItem,
  useNodeNavigation,
} from '../../hooks/useNodeNavigation';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import { buildOutputLabelPatch } from '../../utils/nodeOutputLabels';
import { NodeOutputVariable } from '../../utils/nodeVariablePorts';
import {
  fitPanelWidths,
  getDefaultPanelWidthsForLayout,
  getNodeEditorMaxLayoutWidth,
  HorizontalResizeHandle,
  NODE_EDITOR_PANEL_WIDTHS,
  NodeEditorPanelWidths,
  resizePanelWidths,
  sumPanelWidths,
} from '../../utils/nodeEditorPanelLayout';
import { LLMNodeData } from '../../types/Nodes';
import { LLMReferenceSidePanel } from '../nodes/llm/components/LLMReferenceSidePanel';
import {
  NodeInlinePanel,
  NodeInlinePanelSidePanelId,
} from '../nodes/NodeInlinePanel';
import { NodeOutputsSection } from '../nodes/NodeOutputsSection';
import { VariableInsertionProvider } from '../nodes/ui/VariableInsertionProvider';
import { useVariableInsertion } from '../nodes/ui/useVariableInsertion';

type RightPanelTabId = 'knowledge';

type RightPanelTab = {
  id: RightPanelTabId;
  label: string;
};

const RIGHT_PANEL_TAB_LABELS: Record<RightPanelTabId, string> = {
  knowledge: '지식 베이스',
};

// 입력 변수 칩의 소스 노드별 색상 (BaseNode의 호버 패널과 동일한 팔레트)
const INPUT_CHIP_COLORS = [
  'border-rose-200 bg-rose-50 text-rose-700',
  'border-blue-200 bg-blue-50 text-blue-700',
  'border-emerald-200 bg-emerald-50 text-emerald-700',
  'border-amber-200 bg-amber-50 text-amber-700',
  'border-violet-200 bg-violet-50 text-violet-700',
  'border-cyan-200 bg-cyan-50 text-cyan-700',
  'border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700',
  'border-slate-200 bg-slate-50 text-slate-700',
];

const getInputChipColor = (sourceNodeId: string) => {
  let hash = 0;
  for (let index = 0; index < sourceNodeId.length; index += 1) {
    hash =
      (hash * 31 + sourceNodeId.charCodeAt(index)) % INPUT_CHIP_COLORS.length;
  }
  return INPUT_CHIP_COLORS[hash];
};

const getInitialNodeEditorLayoutWidth = () => {
  if (typeof window === 'undefined') {
    return (
      sumPanelWidths(NODE_EDITOR_PANEL_WIDTHS.default) +
      NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2
    );
  }

  return getNodeEditorMaxLayoutWidth(window.innerWidth);
};

const PanelResizeHandle = ({
  label,
  onPointerDown,
  onKeyDown,
}: {
  label: string;
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
}) => (
  <div
    role="separator"
    aria-orientation="vertical"
    aria-label={label}
    tabIndex={0}
    onPointerDown={onPointerDown}
    onKeyDown={onKeyDown}
    className="group relative z-10 w-2 shrink-0 cursor-col-resize bg-white transition-colors hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-inset"
  >
    <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-slate-200 transition-colors group-hover:bg-blue-400" />
    <div className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-300 transition-colors group-hover:bg-blue-400" />
  </div>
);

const VariableInsertionStatus = () => {
  const { activeTarget, message } = useVariableInsertion();

  return (
    <div className="mb-2 text-[11px] leading-relaxed text-slate-400">
      <span>
        {activeTarget ? (
          <>
            입력 위치:{' '}
            <span className="font-semibold text-blue-600">
              {activeTarget.label}
            </span>
          </>
        ) : (
          '가운데 설정에서 입력할 필드를 선택한 뒤 변수를 클릭하세요.'
        )}
      </span>
      {message && (
        <span className="ml-1 font-medium text-slate-500">{message}</span>
      )}
    </div>
  );
};

const InputVariableChip = ({
  input,
  chipColor,
}: {
  input: NodeOutputVariable;
  chipColor: string;
}) => {
  const { activeTarget, insertOutput } = useVariableInsertion();
  const title = activeTarget
    ? `${input.label} 변수를 ${activeTarget.label}에 추가`
    : `${input.label} - 먼저 가운데 설정에서 입력할 필드를 선택`;

  return (
    <div
      role="button"
      tabIndex={0}
      onMouseDown={(event) => {
        event.preventDefault();
      }}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        insertOutput(input);
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        insertOutput(input);
      }}
      className={
        'inline-flex max-w-full cursor-pointer items-center rounded-md border px-2 py-1 text-xs font-semibold shadow-sm transition-transform hover:-translate-y-px ' +
        chipColor
      }
      title={title}
      aria-label={title}
    >
      <span className="max-w-full truncate">{input.label || input.key}</span>
    </div>
  );
};

const getNodeTitle = (item: NodeNavigationItem) =>
  String(item.node.data?.title || item.node.id);

const getNodeSummaryDescription = (
  node: NonNullable<NodeNavigationItem['node']>,
) => {
  const definition = getNodeDefinitionByType(node.type || '');
  return String(node.data?.description || definition?.description || '');
};

const NodeSummaryIcon = ({
  type,
  className = 'h-10 w-10 rounded-xl',
}: {
  type?: string;
  className?: string;
}) => {
  const definition = getNodeDefinitionByType(type || '');
  const icon = definition?.icon;

  return (
    <div
      className={`flex shrink-0 items-center justify-center text-white shadow-sm ${className}`}
      style={{ backgroundColor: definition?.color || '#3b82f6' }}
    >
      {React.isValidElement(icon) &&
        React.cloneElement(icon as React.ReactElement<{ className?: string }>, {
          className: 'h-5 w-5',
        })}
    </div>
  );
};

const NodeNavigationPopover = ({
  items,
  align,
  onSelect,
}: {
  items: NodeNavigationItem[];
  align: 'left' | 'right';
  onSelect: (nodeId: string) => void;
}) => (
  <div
    className={`absolute top-full z-30 mt-2 w-80 rounded-lg border border-slate-200 bg-white p-2 shadow-xl ${
      align === 'left' ? 'left-0' : 'right-0'
    }`}
  >
    <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
      {items.map((item) => {
        const definition = getNodeDefinitionByType(item.node.type || '');
        const title = getNodeTitle(item);
        const description = getNodeSummaryDescription(item.node);

        return (
          <button
            key={item.node.id}
            type="button"
            onClick={() => onSelect(item.node.id)}
            className="flex w-full min-w-0 gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-slate-50"
          >
            <NodeSummaryIcon
              type={item.node.type}
              className="mt-0.5 h-8 w-8 rounded-lg"
            />
            <span className="min-w-0 flex-1">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                {definition?.name || item.node.type || 'Node'}
              </span>
              <span className="block truncate text-sm font-semibold text-slate-900">
                {title}
              </span>
              {description && (
                <span className="mt-0.5 block line-clamp-2 text-xs leading-relaxed text-slate-500">
                  {description}
                </span>
              )}
              {item.badges.length > 0 && (
                <span className="mt-1 flex flex-wrap gap-1">
                  {item.badges.map((badge) => (
                    <span
                      key={badge.id}
                      className="inline-flex max-w-full items-center rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600"
                    >
                      <span className="truncate">{badge.label}</span>
                    </span>
                  ))}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  </div>
);

const NodeNavigationButton = ({
  item,
  direction,
  onClick,
}: {
  item: NodeNavigationItem;
  direction: 'previous' | 'next';
  onClick: () => void;
}) => {
  const title = getNodeTitle(item);
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-10 max-w-full items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50"
      title={title}
    >
      {direction === 'previous' && <ChevronLeft className="h-4 w-4" />}
      <NodeSummaryIcon type={item.node.type} className="h-6 w-6 rounded-md" />
      <span className="min-w-0 truncate">{title}</span>
      {direction === 'next' && <ChevronRight className="h-4 w-4" />}
    </button>
  );
};

/**
 * NodeFullscreenEditor
 * 노드를 더블클릭하면 열리는 전체화면 노드 설정 화면(NDV).
 * n8n의 NDV를 참고해 화면을 세로 3분할합니다.
 *  - 좌측: 이 노드가 받을 수 있는 입력 변수 / 이 노드가 만드는 출력 변수
 *  - 중앙: 노드 상세 설정 (기존 NodeInlinePanel 재사용)
 *  - 우측: 추후 사용을 위해 비워둠
 *
 * 상단 EditorHeader는 가리지 않도록, 이 컴포넌트는 NodeCanvas 내부의
 * relative 컨테이너 안에서 absolute inset-0 으로만 배치됩니다.
 */
export function NodeFullscreenEditor() {
  const fullscreenNodeId = useWorkflowStore((state) => state.fullscreenNodeId);
  const closeNodeFullscreen = useWorkflowStore(
    (state) => state.closeNodeFullscreen,
  );
  const openNodeFullscreen = useWorkflowStore(
    (state) => state.openNodeFullscreen,
  );
  const updateNodeData = useWorkflowStore((state) => state.updateNodeData);
  const workflowNodes = useWorkflowStore((state) => state.nodes);
  const { node, inputVariableGroups, outputVariables } =
    useNodeIO(fullscreenNodeId);
  const { previousNodes, nextNodes, primaryPreviousNode } =
    useNodeNavigation(fullscreenNodeId);

  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState('');
  const [editingOutputKey, setEditingOutputKey] = useState<string | null>(null);
  const [editingOutputId, setEditingOutputId] = useState<string | undefined>();
  const [draftOutputLabel, setDraftOutputLabel] = useState('');
  const [outputLabelBeforeEdit, setOutputLabelBeforeEdit] = useState('');
  const [inputPanelRatio, setInputPanelRatio] = useState(0.5);
  const [expandedInputSources, setExpandedInputSources] = useState<{
    nodeId: string | null;
    sourceIds: Set<string>;
  }>({ nodeId: null, sourceIds: new Set() });
  const [rightPanelState, setRightPanelState] = useState<{
    nodeId: string | null;
    openTabs: RightPanelTab[];
    activeTabId: RightPanelTabId | null;
  }>({ nodeId: null, openTabs: [], activeTabId: null });
  const [openNavigationPopover, setOpenNavigationPopover] = useState<
    'previous' | 'next' | null
  >(null);
  const [panelWidths, setPanelWidths] = useState<NodeEditorPanelWidths>(() =>
    getDefaultPanelWidthsForLayout(getInitialNodeEditorLayoutWidth()),
  );
  const [layoutWidth, setLayoutWidth] = useState<number>(
    getInitialNodeEditorLayoutWidth,
  );
  const titleInputRef = useRef<HTMLInputElement>(null);
  const outputLabelInputRef = useRef<HTMLInputElement>(null);
  const leftPanelRef = useRef<HTMLDivElement>(null);
  const layoutShellRef = useRef<HTMLDivElement>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  const hasCustomPanelWidthsRef = useRef(false);

  // 풀스크린을 닫을 때는 항상 이 함수를 통해, 편집 상태도 함께 리셋한다.
  const handleClose = () => {
    setIsEditingTitle(false);
    setEditingOutputKey(null);
    setEditingOutputId(undefined);
    closeNodeFullscreen();
  };

  useKeyboardShortcut(
    'Escape',
    () => {
      if (fullscreenNodeId) handleClose();
    },
    { preventDefault: false },
  );

  useEffect(() => {
    if (!isEditingTitle) return;
    titleInputRef.current?.focus();
    titleInputRef.current?.select();
  }, [isEditingTitle]);

  useEffect(() => {
    if (!editingOutputKey) return;
    outputLabelInputRef.current?.focus();
    outputLabelInputRef.current?.select();
  }, [editingOutputKey]);

  useEffect(() => {
    if (!fullscreenNodeId) return;

    const layoutShell = layoutShellRef.current;
    if (!layoutShell) return;

    const updateLayoutWidth = () => {
      const measuredWidth = layoutShell.getBoundingClientRect().width;
      let shellWidth = measuredWidth;
      if (shellWidth <= 0 && typeof window !== 'undefined') {
        shellWidth = window.innerWidth;
      }
      const nextLayoutWidth = getNodeEditorMaxLayoutWidth(
        shellWidth,
      );
      setLayoutWidth(nextLayoutWidth);
      if (!hasCustomPanelWidthsRef.current) {
        setPanelWidths(getDefaultPanelWidthsForLayout(nextLayoutWidth));
      }
    };

    updateLayoutWidth();
    const animationFrame = window.requestAnimationFrame(updateLayoutWidth);
    const resizeObserver = new ResizeObserver(updateLayoutWidth);
    resizeObserver.observe(layoutShell);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
    };
  }, [fullscreenNodeId, node?.id]);

  const definition = getNodeDefinitionByType(node?.type || '');
  const nodeTypeLabel = definition?.name || 'Node';
  const nodeDescription = String(
    node?.data.description || definition?.description || '',
  );
  const titleText = String(node?.data.title || 'Untitled Node');
  const sourceDisplayNumberMap = new Map(
    workflowNodes.map((item) => [item.id, item.data?.displayNumber]),
  );
  const expandedInputSourceIds =
    expandedInputSources.nodeId === fullscreenNodeId
      ? expandedInputSources.sourceIds
      : new Set<string>();
  const isCurrentRightPanelState = rightPanelState.nodeId === fullscreenNodeId;
  const rightPanelTabs = isCurrentRightPanelState
    ? rightPanelState.openTabs
    : [];
  const activeRightTabId = isCurrentRightPanelState
    ? rightPanelState.activeTabId
    : null;
  const fittedPanelWidths = fitPanelWidths(panelWidths, layoutWidth);
  const fittedLayoutWidth =
    sumPanelWidths(fittedPanelWidths) +
    NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2;

  const updateHorizontalPanelWidths = useCallback(
    (handle: HorizontalResizeHandle, deltaX: number) => {
      hasCustomPanelWidthsRef.current = true;
      setPanelWidths((current) =>
        resizePanelWidths(handle, current, deltaX, layoutWidth),
      );
    },
    [layoutWidth],
  );

  const openRightPanelTab = useCallback(
    (tabId: RightPanelTabId) => {
      setRightPanelState((current) => {
        const currentTabs =
          current.nodeId === fullscreenNodeId ? current.openTabs : [];
        const hasTab = currentTabs.some((tab) => tab.id === tabId);
        return {
          nodeId: fullscreenNodeId,
          openTabs: hasTab
            ? currentTabs
            : [
                ...currentTabs,
                { id: tabId, label: RIGHT_PANEL_TAB_LABELS[tabId] },
              ],
          activeTabId: tabId,
        };
      });
    },
    [fullscreenNodeId],
  );

  const closeRightPanelTab = useCallback(
    (tabId: RightPanelTabId) => {
      setRightPanelState((current) => {
        if (current.nodeId !== fullscreenNodeId) return current;

        const tabIndex = current.openTabs.findIndex((tab) => tab.id === tabId);
        if (tabIndex === -1) return current;

        const nextTabs = current.openTabs.filter((tab) => tab.id !== tabId);
        const nextActiveTabId =
          current.activeTabId === tabId
            ? (nextTabs[Math.max(0, tabIndex - 1)]?.id ?? null)
            : current.activeTabId;

        return {
          nodeId: fullscreenNodeId,
          openTabs: nextTabs,
          activeTabId: nextActiveTabId,
        };
      });
    },
    [fullscreenNodeId],
  );

  const openNodeInlineSidePanel = useCallback(
    (panelId: NodeInlinePanelSidePanelId) => {
      if (panelId === 'knowledge') {
        openRightPanelTab('knowledge');
      }
    },
    [openRightPanelTab],
  );

  const navigateToNode = useCallback(
    (targetNodeId: string) => {
      setOpenNavigationPopover(null);
      setIsEditingTitle(false);
      setEditingOutputKey(null);
      setEditingOutputId(undefined);
      openNodeFullscreen(targetNodeId);
    },
    [openNodeFullscreen],
  );

  const startTitleEdit = () => {
    setDraftTitle(titleText);
    setIsEditingTitle(true);
  };

  const cancelTitleEdit = () => setIsEditingTitle(false);

  const saveTitleEdit = () => {
    if (!node) return;
    const nextTitle = draftTitle.trim();
    if (nextTitle) updateNodeData(node.id, { title: nextTitle });
    setIsEditingTitle(false);
  };

  const handlePanelResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const panel = leftPanelRef.current;
      if (!panel) return;

      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);

      const rect = panel.getBoundingClientRect();
      const updateRatio = (clientY: number) => {
        const nextRatio = (clientY - rect.top) / rect.height;
        setInputPanelRatio(Math.min(0.75, Math.max(0.25, nextRatio)));
      };

      updateRatio(event.clientY);

      const handlePointerMove = (moveEvent: PointerEvent) => {
        updateRatio(moveEvent.clientY);
      };
      const handlePointerUp = () => {
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    },
    [],
  );

  const handleHorizontalResizeStart = useCallback(
    (
      handle: HorizontalResizeHandle,
      event: React.PointerEvent<HTMLDivElement>,
    ) => {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);

      const startX = event.clientX;
      const startWidths = fittedPanelWidths;

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX;
        hasCustomPanelWidthsRef.current = true;
        setPanelWidths(
          resizePanelWidths(handle, startWidths, deltaX, layoutWidth),
        );
      };
      const handlePointerUp = () => {
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    },
    [fittedPanelWidths, layoutWidth],
  );

  const handleHorizontalResizeKeyDown = useCallback(
    (
      handle: HorizontalResizeHandle,
      event: React.KeyboardEvent<HTMLDivElement>,
    ) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const direction = event.key === 'ArrowLeft' ? -1 : 1;
      updateHorizontalPanelWidths(
        handle,
        direction * NODE_EDITOR_PANEL_WIDTHS.keyboardStep,
      );
    },
    [updateHorizontalPanelWidths],
  );

  const startOutputLabelEdit = useCallback(
    (event: React.MouseEvent<HTMLDivElement>, output: NodeOutputVariable) => {
      event.preventDefault();
      event.stopPropagation();
      setEditingOutputKey(output.key);
      setEditingOutputId(output.outputId);
      setDraftOutputLabel(output.label || output.key);
      setOutputLabelBeforeEdit(output.label || output.key);
    },
    [],
  );

  const cancelOutputLabelEdit = useCallback(() => {
    setDraftOutputLabel(outputLabelBeforeEdit);
    setEditingOutputKey(null);
    setEditingOutputId(undefined);
  }, [outputLabelBeforeEdit]);

  const saveOutputLabelEdit = useCallback(() => {
    if (!node || !editingOutputKey) return;
    const nextLabel = draftOutputLabel.trim();
    if (!nextLabel) {
      cancelOutputLabelEdit();
      return;
    }

    updateNodeData(
      node.id,
      buildOutputLabelPatch(node, editingOutputKey, editingOutputId, nextLabel),
    );
    setOutputLabelBeforeEdit(nextLabel);
    setDraftOutputLabel(nextLabel);
    setEditingOutputKey(null);
    setEditingOutputId(undefined);
  }, [
    cancelOutputLabelEdit,
    draftOutputLabel,
    editingOutputKey,
    editingOutputId,
    node,
    updateNodeData,
  ]);

  if (!fullscreenNodeId || !node) return null;

  return (
    <div
      className="nowheel absolute inset-0 isolate z-[70] flex flex-col overflow-hidden bg-white"
      data-canvas-shortcut-scope="blocked"
    >
      {/* 헤더 */}
      <div className="z-10 flex h-32 shrink-0 flex-col border-b border-slate-200 bg-white px-5">
        <div className="relative flex min-h-0 flex-1 items-center justify-center">
          <button
            type="button"
            onClick={handleClose}
            className="absolute left-0 top-4 flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950"
            title="워크플로우로 가기"
            aria-label="워크플로우로 가기"
          >
            <ChevronLeft className="h-4 w-4" />
            워크플로우로 가기
          </button>

          <div className="flex min-w-0 max-w-[560px] items-center gap-3">
            <NodeSummaryIcon type={node.type} />

            <div className="flex min-w-0 flex-col">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                {nodeTypeLabel}
              </span>

              {isEditingTitle ? (
                <div className="flex min-w-0 items-center gap-1">
                  <input
                    ref={titleInputRef}
                    value={draftTitle}
                    onChange={(event) => setDraftTitle(event.target.value)}
                    onBlur={cancelTitleEdit}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        saveTitleEdit();
                      }
                      if (event.key === 'Escape') {
                        event.preventDefault();
                        event.stopPropagation();
                        cancelTitleEdit();
                      }
                    }}
                    className="min-w-0 flex-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-lg font-bold leading-tight text-gray-900 outline-none ring-2 ring-blue-100"
                    aria-label="노드 이름 수정"
                  />
                  <button
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={saveTitleEdit}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-gray-200 bg-white text-blue-600 shadow-sm transition-colors hover:border-blue-300 hover:bg-blue-50"
                    title="이름 저장"
                    aria-label="이름 저장"
                  >
                    <Check className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={cancelTitleEdit}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 hover:text-gray-800"
                    title="이름 수정 취소"
                    aria-label="이름 수정 취소"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div className="group flex min-w-0 items-center gap-1.5">
                  <h2
                    className="min-w-0 truncate text-lg font-bold leading-tight text-gray-900"
                    title={titleText}
                  >
                    {titleText}
                  </h2>
                  <button
                    type="button"
                    onClick={startTitleEdit}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-400 opacity-0 transition-all hover:bg-gray-100 hover:text-gray-800 group-hover:opacity-100 focus:opacity-100"
                    title="노드 이름 수정"
                    aria-label="노드 이름 수정"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              {nodeDescription && (
                <p className="mt-0.5 truncate text-xs text-slate-500">
                  {nodeDescription}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="flex h-12 shrink-0 items-center justify-between">
          <div className="relative flex min-w-0 flex-1 items-center justify-start">
            {previousNodes.length === 0 ? (
              <div className="text-xs font-medium text-slate-300">
                이전 없음
              </div>
            ) : previousNodes.length === 1 && primaryPreviousNode ? (
              <NodeNavigationButton
                item={primaryPreviousNode}
                direction="previous"
                onClick={() => navigateToNode(primaryPreviousNode.node.id)}
              />
            ) : (
              <div className="flex min-w-0 items-center gap-2">
                {primaryPreviousNode && (
                  <NodeNavigationButton
                    item={primaryPreviousNode}
                    direction="previous"
                    onClick={() => navigateToNode(primaryPreviousNode.node.id)}
                  />
                )}
                <button
                  type="button"
                  onClick={() =>
                    setOpenNavigationPopover((current) =>
                      current === 'previous' ? null : 'previous',
                    )
                  }
                  className="flex h-10 shrink-0 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50"
                  aria-expanded={openNavigationPopover === 'previous'}
                >
                  {primaryPreviousNode
                    ? `+${previousNodes.length - 1}`
                    : `← 이전 ${previousNodes.length}개`}
                  <ChevronDown className="h-4 w-4" />
                </button>
              </div>
            )}

            {openNavigationPopover === 'previous' && (
              <NodeNavigationPopover
                items={previousNodes}
                align="left"
                onSelect={navigateToNode}
              />
            )}
          </div>

          <div className="relative flex min-w-0 flex-1 items-center justify-end">
            {nextNodes.length === 0 ? (
              <div className="text-xs font-medium text-slate-300">
                다음 없음
              </div>
            ) : nextNodes.length === 1 ? (
              <NodeNavigationButton
                item={nextNodes[0]}
                direction="next"
                onClick={() => navigateToNode(nextNodes[0].node.id)}
              />
            ) : (
              <button
                type="button"
                onClick={() =>
                  setOpenNavigationPopover((current) =>
                    current === 'next' ? null : 'next',
                  )
                }
                className="flex h-10 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50"
                aria-expanded={openNavigationPopover === 'next'}
              >
                다음 {nextNodes.length}개
                <ChevronRight className="h-4 w-4" />
                <ChevronDown className="h-4 w-4" />
              </button>
            )}

            {openNavigationPopover === 'next' && (
              <NodeNavigationPopover
                items={nextNodes}
                align="right"
                onSelect={navigateToNode}
              />
            )}
          </div>
        </div>
      </div>

      {/* 본문: 가로 3분할 */}
      <VariableInsertionProvider>
        <div className="flex h-full min-h-0 flex-1 justify-center overflow-hidden bg-slate-100">
          <div
            ref={layoutShellRef}
            className="flex h-full min-h-0 w-full justify-center overflow-hidden"
          >
            <div
              ref={layoutRef}
              className="grid h-full min-h-0 max-w-[90vw] overflow-hidden bg-white"
              style={{
                width: `${fittedLayoutWidth}px`,
                gridTemplateColumns: `${fittedPanelWidths.left}px 8px ${fittedPanelWidths.center}px 8px ${fittedPanelWidths.right}px`,
              }}
            >
              {/* 좌측: 입력 / 출력 */}
              <div
                ref={leftPanelRef}
                className="flex min-w-0 flex-col overflow-hidden bg-slate-50"
              >
                <div
                  className="flex min-h-0 flex-col p-4"
                  style={{ flexBasis: `${inputPanelRatio * 100}%` }}
                >
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                    입력
                  </div>
                  <VariableInsertionStatus />
                  {inputVariableGroups.length === 0 ? (
                    <div className="rounded-md border border-dashed border-gray-200 bg-white p-3 text-xs text-gray-400">
                      연결된 입력 노드가 없습니다.
                    </div>
                  ) : (
                    <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                      <div className="flex flex-col gap-3">
                        {inputVariableGroups.map((group) => {
                          const chipColor = getInputChipColor(
                            group.sourceNodeId,
                          );
                          const isCollapsed =
                            inputVariableGroups.length > 1 &&
                            !expandedInputSourceIds.has(group.sourceNodeId);
                          const sourceDisplayNumber =
                            sourceDisplayNumberMap.get(group.sourceNodeId);
                          return (
                            <div key={group.sourceNodeId} className="min-w-0">
                              <button
                                type="button"
                                onClick={() =>
                                  setExpandedInputSources((current) => {
                                    const currentSourceIds =
                                      current.nodeId === fullscreenNodeId
                                        ? current.sourceIds
                                        : new Set<string>();
                                    const next = new Set(currentSourceIds);
                                    if (next.has(group.sourceNodeId)) {
                                      next.delete(group.sourceNodeId);
                                    } else {
                                      next.add(group.sourceNodeId);
                                    }
                                    return {
                                      nodeId: fullscreenNodeId,
                                      sourceIds: next,
                                    };
                                  })
                                }
                                className="mb-1 flex w-full items-center justify-between gap-2 rounded-md px-0.5 py-1 text-left text-xs font-semibold text-gray-700 hover:bg-white"
                                aria-expanded={!isCollapsed}
                              >
                                <span className="flex min-w-0 items-center gap-1.5">
                                  <span className="min-w-0 truncate">
                                    {group.sourceTitle}
                                  </span>
                                  {typeof sourceDisplayNumber === 'number' && (
                                    <span
                                      className="inline-flex h-5 shrink-0 items-center rounded-full border border-slate-200 bg-white px-1.5 text-[10px] font-bold text-slate-500"
                                      title={`노드 번호 ${sourceDisplayNumber}`}
                                    >
                                      #{sourceDisplayNumber}
                                    </span>
                                  )}
                                </span>
                                <span className="flex shrink-0 items-center gap-1 text-[10px] text-gray-400">
                                  {group.outputs.length}
                                  {isCollapsed ? (
                                    <ChevronRight className="h-3.5 w-3.5" />
                                  ) : (
                                    <ChevronDown className="h-3.5 w-3.5" />
                                  )}
                                </span>
                              </button>
                              {!isCollapsed && (
                                <div className="flex flex-wrap content-start gap-1">
                                  {group.outputs.map((input) => (
                                    <InputVariableChip
                                      key={`${input.sourceNodeId}-${input.key}`}
                                      input={input}
                                      chipColor={chipColor}
                                    />
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

              <div
                role="separator"
                aria-orientation="horizontal"
                aria-label="입출력 패널 크기 조절"
                tabIndex={0}
                onPointerDown={handlePanelResizeStart}
                onKeyDown={(event) => {
                  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')
                    return;
                  event.preventDefault();
                  setInputPanelRatio((current) => {
                    const delta = event.key === 'ArrowUp' ? -0.05 : 0.05;
                    return Math.min(0.75, Math.max(0.25, current + delta));
                  });
                }}
                className="group relative h-2 shrink-0 cursor-row-resize border-y border-slate-200 bg-slate-100 transition-colors hover:bg-blue-50"
              >
                <div className="absolute left-1/2 top-1/2 h-0.5 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-300 transition-colors group-hover:bg-blue-400" />
              </div>

              <div
                className="flex min-h-0 flex-col bg-white p-4"
                style={{ flexBasis: `${(1 - inputPanelRatio) * 100}%` }}
              >
                {outputVariables.length === 0 ? (
                  <>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                      출력
                    </div>
                    <div className="rounded-md border border-dashed border-gray-200 bg-white p-3 text-xs text-gray-400">
                      이 노드가 만드는 출력 변수가 없습니다.
                    </div>
                  </>
                ) : (
                  <>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                      출력
                    </div>
                    <NodeOutputsSection
                      hideHeader
                      className="mt-0 min-h-0 flex-1 border-t-0 pt-0"
                      listClassName="max-h-none flex-1"
                      outputs={outputVariables}
                      editingOutputKey={editingOutputKey}
                      draftOutputLabel={draftOutputLabel}
                      onDraftOutputLabelChange={setDraftOutputLabel}
                      onStartOutputLabelEdit={startOutputLabelEdit}
                      onSaveOutputLabelEdit={saveOutputLabelEdit}
                      onCancelOutputLabelEdit={cancelOutputLabelEdit}
                      outputLabelInputRef={outputLabelInputRef}
                    />
                  </>
                )}
              </div>
            </div>

            <PanelResizeHandle
              label="입출력 패널과 노드 설정 패널 사이 폭 조절"
              onPointerDown={(event) =>
                handleHorizontalResizeStart('left-center', event)
              }
              onKeyDown={(event) =>
                handleHorizontalResizeKeyDown('left-center', event)
              }
            />

            {/* 중앙: 노드 상세 설정 */}
            <div className="min-h-0 min-w-0 overflow-y-auto border-x border-slate-200 p-6">
              <div className="mx-auto w-full max-w-[720px]">
                <NodeInlinePanel
                  node={node}
                  showFrame={false}
                  onOpenSidePanel={openNodeInlineSidePanel}
                />
              </div>
            </div>

            <PanelResizeHandle
              label="노드 설정 패널과 보조 패널 사이 폭 조절"
              onPointerDown={(event) =>
                handleHorizontalResizeStart('center-right', event)
              }
              onKeyDown={(event) =>
                handleHorizontalResizeKeyDown('center-right', event)
              }
            />

            {/* 우측: 추후 사용을 위해 비워둠 */}
            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-slate-50">
              {rightPanelTabs.length > 0 && (
                <div className="flex min-h-11 items-end gap-0 overflow-x-auto border-b border-slate-200 bg-slate-100 px-2 pt-2">
                  {rightPanelTabs.map((tab) => (
                    <div
                      key={tab.id}
                      className={`group flex h-9 min-w-0 max-w-36 items-center gap-1 border border-b-0 px-3 text-xs font-semibold ${
                        activeRightTabId === tab.id
                          ? 'rounded-t-md border-slate-200 bg-white text-slate-950'
                          : 'rounded-t-md border-transparent text-slate-500 hover:bg-slate-50 hover:text-slate-800'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setRightPanelState((current) => ({
                            nodeId: fullscreenNodeId,
                            openTabs:
                              current.nodeId === fullscreenNodeId
                                ? current.openTabs
                                : rightPanelTabs,
                            activeTabId: tab.id,
                          }))
                        }
                        className="min-w-0 flex-1 truncate text-left"
                        title={tab.label}
                      >
                        {tab.label}
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          closeRightPanelTab(tab.id);
                        }}
                        className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-slate-400 opacity-0 transition-opacity hover:bg-slate-200 hover:text-slate-800 group-hover:opacity-100 focus:opacity-100"
                        aria-label={`${tab.label} 탭 닫기`}
                        title="닫기"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-hidden bg-white">
                {node.type === 'llmNode' && activeRightTabId === 'knowledge' ? (
                  <LLMReferenceSidePanel
                    embedded
                    nodeId={node.id}
                    data={node.data as LLMNodeData}
                    onClose={() => closeRightPanelTab('knowledge')}
                  />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center p-6 text-center text-xs leading-relaxed text-slate-500">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                      열린 보조 패널 없음
                    </div>
                    <p>
                      중앙 설정에서 지식 베이스 같은 보조 기능을 열면 이곳에
                      탭으로 추가됩니다.
                    </p>
                  </div>
                )}
              </div>
              </div>
            </div>
          </div>
        </div>
      </VariableInsertionProvider>
    </div>
  );
}
