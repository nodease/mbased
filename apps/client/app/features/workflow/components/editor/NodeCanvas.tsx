'use client';

import {
  BarChart3,
  Loader2,
  Plus,
  StickyNote,
  Play,
  Trash2,
  Settings,
} from 'lucide-react';
import { NodeSelector } from './NodeSelector';
import NodeLibrarySidebar from './NodeLibrarySidebar';
import { calculateAutoLayout } from '../../utils/layoutHelpers';
import { useDeployment } from '../../hooks/useDeployment';
import { useContextMenu } from '../../hooks/useContextMenu';
import { useNodeCreation } from '../../hooks/useNodeCreation';
import { MemoryModeToggle, useMemoryMode } from './memory/MemoryModeControls';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { ClockIcon } from '@/app/features/workflow/components/nodes/icons';
import { DeploymentFlowModal } from '../deployment/DeploymentFlowModal';

import { useCallback, useMemo, useEffect, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams } from 'next/navigation';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  useReactFlow,
  type Viewport,
  type NodeTypes,
} from '@xyflow/react';

import '@xyflow/react/dist/style.css';

import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { Node, WorkflowNodeData } from '../../types/Nodes';
import { nodeTypes as coreNodeTypes } from '../nodes';
import { PuzzleEdge } from '../nodes/edges/PuzzleEdge';
import { CustomConnectionLine } from '../nodes/edges/CustomConnectionLine';
import NotePost from './NotePost';
import BottomPanel from './BottomPanel';
import { AppSearchModal } from '../modals/AppSearchModal';
import { useKeyboardShortcut } from '../../hooks/useKeyboardShortcut';
import { useCanvasKeyboardShortcuts } from '../../hooks/useCanvasKeyboardShortcuts';
import { App } from '@/app/features/app/api/appApi';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { useDragConnectionPreview } from '../../hooks/useDragConnectionPreview';
import { DragConnectionOverlay } from './DragConnectionOverlay';
import { SettingsSidebar } from './SettingsSidebar';
import { VersionHistorySidebar } from './VersionHistorySidebar';
import { TestSidebar } from './TestSidebar';
import { NodeFullscreenEditor } from './NodeFullscreenEditor';
import { getSnapBackgroundGap } from '../../utils/gridSnap';
import { hasIncomingHandle } from '../../utils/validateWorkflowGraph';
import { WORKFLOW_NODE_SIZE } from '../../utils/workflowCanvasGeometry';
import { AgentBuilderPanel } from '../agentBuilder/AgentBuilderPanel';
import { copyTestExecutionLocationQueryParams } from '../../utils/testExecutionLocation';
import { collectDeploymentLlmNodes } from '../../utils/publicChatConversationConsumers';

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 1.6;

export default function NodeCanvas() {
  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    interactiveMode,
    workflows,
    activeWorkflowId,
    updateWorkflowViewport,
    setNodes,
    updateNodeData,
    addNode,
    isVersionHistoryOpen,
    toggleVersionHistory,
    isFullscreen,
    workflowAccess,
    setEdges,
    isSettingsOpen,
    toggleSettings,
    isTestPanelOpen,
    toggleTestPanel,
    clearInnerNodeSelection,
    selectedInnerNode,
    snapGridSize,
    setSnapTemporarilyDisabled,
    numberConnection,
    updateNumberConnectionInput,
    cancelNumberConnection,
    fullscreenNodeId,
    openNodeFullscreen,
    syncNodeFullscreenFromUrl,
    testExecutionStatus,
    testExecutionRunId,
    testSelectedNodeId,
    isTestUploading,
    hasUnsavedChanges,
  } = useWorkflowStore();

  const {
    fitView,
    setViewport,
    getViewport,
    screenToFlowPosition,
    deleteElements,
  } = useReactFlow();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [, setSelectedNodeType] = useState<string | null>(null);
  const [searchModalContext, setSearchModalContext] = useState<{
    isOpen: boolean;
    position?: { x: number; y: number };
  }>({ isOpen: false });
  const [isParamPanelOpen, setIsParamPanelOpen] = useState(false);
  const [isRefPanelOpen, setIsRefPanelOpen] = useState(false);
  const [isNodeLibraryOpen, setIsNodeLibraryOpen] = useState(true);
  const reactFlowWrapperRef = useRef<HTMLDivElement>(null);
  const hoveredNodeIdRef = useRef<string | null>(null);
  const backgroundGap = getSnapBackgroundGap(snapGridSize);
  const numberConnectionCandidates = useMemo(() => {
    if (!numberConnection) return [];
    return nodes
      .filter(
        (node) =>
          node.id !== numberConnection.sourceNodeId &&
          hasIncomingHandle(node) &&
          typeof node.data?.displayNumber === 'number',
      )
      .map((node) => ({
        node,
        displayNumber: String(node.data.displayNumber),
      }));
  }, [nodes, numberConnection]);
  const currentNumberMatches = useMemo(() => {
    if (!numberConnection?.input) return [];
    return numberConnectionCandidates.filter((candidate) =>
      candidate.displayNumber.startsWith(numberConnection.input),
    );
  }, [numberConnection?.input, numberConnectionCandidates]);

  const connectNumberTarget = useCallback(
    (targetNodeId: string) => {
      if (!numberConnection) return;
      onConnect({
        source: numberConnection.sourceNodeId,
        sourceHandle: numberConnection.sourceHandleId,
        target: targetNodeId,
        targetHandle: 'target',
      });
    },
    [numberConnection, onConnect],
  );

  // Drag connection preview
  const {
    previewState,
    onDragOver: handleDragOver,
    resetPreview,
  } = useDragConnectionPreview(nodes, edges);

  // Memory mode controls
  const router = useRouter();
  const {
    isMemoryModeEnabled,
    hasProviderKey,
    providerKeyStatus,
    memoryModeDescription,
    toggleMemoryMode,
    appendMemoryFlag,
    modals: memoryModeModals,
  } = useMemoryMode(router, toast);

  // Publish state
  const rawCanPublish = useWorkflowStore((state) => state.canPublish());
  const isReadOnly = workflowAccess?.can_write === false;
  const canExecute = workflowAccess?.can_execute !== false;
  const canPublish = rawCanPublish;

  useEffect(() => {
    if (!numberConnection) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return;
      }

      if (/^\d$/.test(event.key)) {
        event.preventDefault();
        const nextInput = `${numberConnection.input}${event.key}`;
        const matches = numberConnectionCandidates.filter((candidate) =>
          candidate.displayNumber.startsWith(nextInput),
        );
        const exactMatches = matches.filter(
          (candidate) => candidate.displayNumber === nextInput,
        );
        const prefixMatches = matches.filter(
          (candidate) => candidate.displayNumber !== nextInput,
        );

        if (exactMatches.length === 1 && prefixMatches.length === 0) {
          connectNumberTarget(exactMatches[0].node.id);
          return;
        }

        updateNumberConnectionInput(nextInput);
        return;
      }

      if (event.key === 'Backspace') {
        event.preventDefault();
        updateNumberConnectionInput(numberConnection.input.slice(0, -1));
        return;
      }

      if (event.key === 'Escape') {
        event.preventDefault();
        cancelNumberConnection();
        return;
      }

      if (event.key === 'Enter') {
        event.preventDefault();
        const exactMatches = numberConnectionCandidates.filter(
          (candidate) => candidate.displayNumber === numberConnection.input,
        );
        if (exactMatches.length === 1) {
          connectNumberTarget(exactMatches[0].node.id);
          return;
        }
        cancelNumberConnection();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    cancelNumberConnection,
    connectNumberTarget,
    numberConnection,
    numberConnectionCandidates,
    updateNumberConnectionInput,
  ]);

  // Deployment logic (extracted to hook)
  const {
    showDeployFlowModal,
    setShowDeployFlowModal,
    showDeployDropdown,
    setShowDeployDropdown,
    deploymentType,
    toggleDeployDropdown,
    handlePublishAsRestAPI,
    handlePublishAsWebApp,
    handlePublishAsWidget,
    handlePublishAsChatbot,
    handlePublishAsInternalChatbot,
    handlePublishAsWorkflowNode,
    handlePublishAsSchedule,
    handlePublishAsWebhook,
    handleDeploy,
  } = useDeployment({
    nodes,
    edges,
    isSettingsOpen,
    toggleSettings,
    isVersionHistoryOpen,
    toggleVersionHistory,
    isTestPanelOpen,
    toggleTestPanel,
    setSelectedNodeId,
    setSelectedNodeType,
  });

  const deploymentLlmNodes = useMemo(
    () => collectDeploymentLlmNodes(nodes),
    [nodes],
  );

  // Start node detection for deployment options
  const startNode = useMemo(() => {
    return nodes.find(
      (n) =>
        n.type === 'startNode' ||
        n.type === 'webhookTrigger' ||
        n.type === 'scheduleTrigger',
    );
  }, [nodes]);

  // Context menu hook
  const {
    contextMenu,
    nodeContextMenu,
    edgeContextMenu,
    isContextNodeSelectorOpen,
    contextMenuPos,
    setContextMenu,
    setNodeContextMenu,
    setEdgeContextMenu,
    setIsContextNodeSelectorOpen,
    onPaneContextMenu,
    handleCloseContextMenu,
    handleAddNodeFromContext,
    handleAddMemoFromContext,
    handleTestRunFromContext,
    handleSelectNodeFromContext,
  } = useContextMenu({
    triggerWorkflowRun: useWorkflowStore.getState().triggerWorkflowRun,
    setSearchModalContext,
  });

  // Node creation hook
  const {
    onDrop,
    handleAddNodeFromLibrary,
    handleAddNodeAfterSelected,
  } = useNodeCreation({
    edges,
    setEdges,
    previewState,
    resetPreview,
    setSearchModalContext,
  });

  useEffect(() => {
    if (isFullscreen || isReadOnly) {
      setIsNodeLibraryOpen(false);
    } else {
      setIsNodeLibraryOpen(true);
    }
  }, [isFullscreen, isReadOnly]);

  useEffect(() => {
    if (!reactFlowWrapperRef.current) {
      setSnapTemporarilyDisabled(false);
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Alt') {
        setSnapTemporarilyDisabled(true);
      }
    };
    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.key === 'Alt') {
        setSnapTemporarilyDisabled(false);
      }
    };
    const handleBlur = () => setSnapTemporarilyDisabled(false);

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    window.addEventListener('blur', handleBlur);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
      window.removeEventListener('blur', handleBlur);
      setSnapTemporarilyDisabled(false);
    };
  }, [setSnapTemporarilyDisabled]);

  useKeyboardShortcut(
    ['Meta', 'k'],
    () => {
      setSearchModalContext({ isOpen: true });
    },
    { preventDefault: true },
  );

  // 설정, 버전 기록, 테스트 패널이 열리면 노드 상세 패널과 배포 드롭다운 닫기
  useEffect(() => {
    if (isSettingsOpen || isVersionHistoryOpen || isTestPanelOpen) {
      setSelectedNodeId(null);
      setSelectedNodeType(null);
      setIsParamPanelOpen(false);
      setIsRefPanelOpen(false);
      setShowDeployDropdown(false);
    }
  }, [
    isSettingsOpen,
    isVersionHistoryOpen,
    isTestPanelOpen,
    setShowDeployDropdown,
  ]);

  const handleSelectApp = useCallback(
    async (app: App & { active_deployment_id?: string; version?: number }) => {
      if (isReadOnly) return;
      const baseNode: Node = {
        id: `workflow-${Date.now()}`,
        type: 'workflowNode',
        position:
          searchModalContext.position ||
          screenToFlowPosition({
            x: window.innerWidth / 2,
            y: window.innerHeight / 2,
          }),
        data: {
          title: app.name,
          name: app.name,
          workflowId: app.workflow_id || '',
          appId: app.id,
          icon: app.icon?.content || '⚡️',
          description: app.description || '설명 없음',
          status: 'idle',
          version: app.version || 0,
          deployment_id: app.active_deployment_id,
          expanded: false,
          outputs: [],
        } as WorkflowNodeData,
      };
      const newNode = addNode(baseNode);
      setSearchModalContext({ isOpen: false });

      if (app.active_deployment_id) {
        try {
          const deployment = await workflowApi.getDeployment(
            app.active_deployment_id,
          );
          const outputKeys =
            deployment.output_schema?.outputs?.map(
              (o: { variable: string }) => o.variable,
            ) || [];
          updateNodeData(newNode.id, { outputs: outputKeys });
        } catch {
          // Failed to load workflow outputs
        }
      }
    },
    [
      screenToFlowPosition,
      updateNodeData,
      searchModalContext.position,
      addNode,
      isReadOnly,
    ],
  );

  const handleCanvasNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      if (isReadOnly) {
        const selectionChanges = changes.filter(
          (change) => change.type === 'select',
        );
        if (selectionChanges.length > 0) {
          onNodesChange(selectionChanges);
        }
        return;
      }
      onNodesChange(changes);
    },
    [isReadOnly, onNodesChange],
  );

  const handleCanvasEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      if (isReadOnly) {
        const selectionChanges = changes.filter(
          (change) => change.type === 'select',
        );
        if (selectionChanges.length > 0) {
          onEdgesChange(selectionChanges);
        }
        return;
      }
      onEdgesChange(changes);
    },
    [isReadOnly, onEdgesChange],
  );

  const handleCanvasConnect = useCallback(
    (...args: Parameters<typeof onConnect>) => {
      if (isReadOnly) return;
      onConnect(...args);
    },
    [isReadOnly, onConnect],
  );

  const handleCanvasDrop = useCallback(
    (event: React.DragEvent) => {
      if (isReadOnly) return;
      onDrop(event);
    },
    [isReadOnly, onDrop],
  );

  const nodeTypes = useMemo(
    () =>
      ({
        ...coreNodeTypes,
        note: NotePost,
      }) as unknown as NodeTypes,
    [],
  );

  const edgeTypes = useMemo(() => ({ puzzle: PuzzleEdge }), []);
  const selectedEdgeId = useMemo(
    () => edges.find((edge) => edge.selected)?.id ?? null,
    [edges],
  );
  const defaultEdgeOptions = useMemo(
    () => ({
      type: 'puzzle',
      style: { strokeWidth: 10, stroke: '#d1d5db' },
      animated: false,
    }),
    [],
  );

  const prevActiveWorkflowId = useRef(activeWorkflowId);

  useEffect(() => {
    const activeWorkflow = workflows.find((w) => w.id === activeWorkflowId);

    if (prevActiveWorkflowId.current !== activeWorkflowId) {
      if (activeWorkflow?.viewport) {
        setViewport(activeWorkflow.viewport);
      }
      prevActiveWorkflowId.current = activeWorkflowId;
    }
  }, [activeWorkflowId, workflows, setViewport]);

  const handleMoveEnd = useCallback(
    (_event: unknown, viewport: Viewport) => {
      updateWorkflowViewport(activeWorkflowId, viewport);
    },
    [activeWorkflowId, updateWorkflowViewport],
  );

  const handleNodeMouseEnter = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      hoveredNodeIdRef.current = node.id;
    },
    [],
  );

  const handleNodeMouseLeave = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (hoveredNodeIdRef.current === node.id) {
        hoveredNodeIdRef.current = null;
      }
    },
    [],
  );

  const handleNodeWheelZoom = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      const hoveredNodeId = hoveredNodeIdRef.current;
      if (!hoveredNodeId) return;

      const target = event.target as HTMLElement | null;
      if (
        target?.closest(
          'input, textarea, select, [contenteditable="true"], .nowheel',
        )
      ) {
        return;
      }

      const hoveredNode = nodes.find((node) => node.id === hoveredNodeId);
      if (!hoveredNode) return;

      event.preventDefault();
      event.stopPropagation();

      const viewport = getViewport();
      const measuredNode = hoveredNode as Node & {
        measured?: { width?: number; height?: number };
        width?: number;
        height?: number;
      };
      const nodeWidth =
        measuredNode.measured?.width ??
        measuredNode.width ??
        WORKFLOW_NODE_SIZE.width;
      const nodeHeight =
        measuredNode.measured?.height ??
        measuredNode.height ??
        WORKFLOW_NODE_SIZE.height;
      const nodeCenter = {
        x: hoveredNode.position.x + nodeWidth / 2,
        y: hoveredNode.position.y + nodeHeight / 2,
      };
      const screenCenter = {
        x: nodeCenter.x * viewport.zoom + viewport.x,
        y: nodeCenter.y * viewport.zoom + viewport.y,
      };
      const zoomFactor = Math.exp(-event.deltaY * 0.0015);
      const nextZoom = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, viewport.zoom * zoomFactor),
      );

      if (nextZoom === viewport.zoom) return;

      const nextViewport = {
        x: screenCenter.x - nodeCenter.x * nextZoom,
        y: screenCenter.y - nodeCenter.y * nextZoom,
        zoom: nextZoom,
      };

      setViewport(nextViewport, { duration: 80 });
      updateWorkflowViewport(activeWorkflowId, nextViewport);
    },
    [
      activeWorkflowId,
      getViewport,
      nodes,
      setViewport,
      updateWorkflowViewport,
    ],
  );

  useEffect(() => {
    if (isVersionHistoryOpen || isSettingsOpen) {
      setSelectedNodeId(null);
      setSelectedNodeType(null);
      setIsParamPanelOpen(false);
    }
  }, [isVersionHistoryOpen, isSettingsOpen]);

  const handleNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      if (node.type && node.type !== 'note') {
        if (isVersionHistoryOpen) {
          toggleVersionHistory();
        }
        if (isSettingsOpen) {
          toggleSettings();
        }
        if (isTestPanelOpen) {
          toggleTestPanel();
        }

        // 메인 노드 클릭 시 내부 노드 선택 해제
        clearInnerNodeSelection();
        setSelectedNodeId(node.id);
        setSelectedNodeType(null);
        setIsParamPanelOpen(false);
        setIsRefPanelOpen(false);
      }
    },
    [
      isVersionHistoryOpen,
      toggleVersionHistory,
      isSettingsOpen,
      toggleSettings,
      isTestPanelOpen,
      toggleTestPanel,
      clearInnerNodeSelection,
    ],
  );

  useEffect(() => {
    const handleAgentBuilderOpenNodeSettings = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          nodeId?: unknown;
          section?: 'routing' | 'connection';
        }>
      ).detail;
      const nodeId = detail?.nodeId;
      if (
        typeof nodeId !== 'string' ||
        !nodes.some((node) => node.id === nodeId && node.type !== 'note')
      ) {
        return;
      }
      if (isVersionHistoryOpen) {
        toggleVersionHistory();
      }
      if (isSettingsOpen) {
        toggleSettings();
      }
      if (isTestPanelOpen) {
        toggleTestPanel();
      }
      clearInnerNodeSelection();
      openNodeFullscreen(nodeId, detail?.section);
    };

    window.addEventListener(
      'agent-builder:open-node-settings',
      handleAgentBuilderOpenNodeSettings,
    );
    return () =>
      window.removeEventListener(
        'agent-builder:open-node-settings',
        handleAgentBuilderOpenNodeSettings,
      );
  }, [
    clearInnerNodeSelection,
    isSettingsOpen,
    isTestPanelOpen,
    isVersionHistoryOpen,
    nodes,
    openNodeFullscreen,
    toggleSettings,
    toggleTestPanel,
    toggleVersionHistory,
  ]);

  const handleClosePanel = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedNodeType(null);
    setIsParamPanelOpen(false);
    setIsRefPanelOpen(false);
  }, []);

  const closeCanvasMenus = useCallback(() => {
    if (searchModalContext.isOpen) {
      setSearchModalContext({ isOpen: false });
      return true;
    }
    if (showDeployDropdown) {
      setShowDeployDropdown(false);
      return true;
    }
    if (
      contextMenu ||
      nodeContextMenu ||
      edgeContextMenu ||
      isContextNodeSelectorOpen
    ) {
      handleCloseContextMenu();
      setIsContextNodeSelectorOpen(false);
      return true;
    }
    return false;
  }, [
    searchModalContext.isOpen,
    showDeployDropdown,
    setShowDeployDropdown,
    contextMenu,
    nodeContextMenu,
    edgeContextMenu,
    isContextNodeSelectorOpen,
    handleCloseContextMenu,
    setIsContextNodeSelectorOpen,
  ]);

  const closeCanvasPanels = useCallback(() => {
    if (isParamPanelOpen || isRefPanelOpen || selectedNodeId) {
      handleClosePanel();
      return true;
    }
    if (selectedInnerNode) {
      clearInnerNodeSelection();
      return true;
    }
    if (isSettingsOpen) {
      toggleSettings();
      return true;
    }
    if (isVersionHistoryOpen) {
      toggleVersionHistory();
      return true;
    }
    if (isTestPanelOpen) {
      toggleTestPanel();
      return true;
    }
    return false;
  }, [
    isParamPanelOpen,
    isRefPanelOpen,
    selectedNodeId,
    handleClosePanel,
    selectedInnerNode,
    clearInnerNodeSelection,
    isSettingsOpen,
    toggleSettings,
    isVersionHistoryOpen,
    toggleVersionHistory,
    isTestPanelOpen,
    toggleTestPanel,
  ]);

  const isCanvasShortcutScopeBlocked = useCallback(
    () =>
      searchModalContext.isOpen ||
      showDeployFlowModal ||
      showDeployDropdown ||
      Boolean(
        contextMenu ||
        nodeContextMenu ||
        edgeContextMenu ||
        isContextNodeSelectorOpen,
      ),
    [
      searchModalContext.isOpen,
      showDeployFlowModal,
      showDeployDropdown,
      contextMenu,
      nodeContextMenu,
      edgeContextMenu,
      isContextNodeSelectorOpen,
    ],
  );

  const reactFlowConfig = useMemo(() => {
    if (interactiveMode === 'touchpad') {
      return {
        panOnDrag: [1, 2],
        panOnScroll: true,
        zoomOnScroll: false,
        zoomOnPinch: true,
        selectionOnDrag: true,
        connectionRadius: 50,
      };
    } else {
      return {
        panOnDrag: true,
        panOnScroll: false,
        zoomOnScroll: true,
        zoomOnPinch: true,
        selectionOnDrag: false,
        connectionRadius: 50,
      };
    }
  }, [interactiveMode]);

  const handleAutoLayout = useCallback(() => {
    if (isReadOnly) return;
    const layoutedNodes = calculateAutoLayout(nodes, edges);
    setNodes(layoutedNodes);

    setTimeout(() => {
      fitView({ padding: 0.2, duration: 300 });
      const viewport = getViewport();
      updateWorkflowViewport(activeWorkflowId, viewport);
    }, 100);
  }, [
    nodes,
    edges,
    setNodes,
    fitView,
    getViewport,
    updateWorkflowViewport,
    activeWorkflowId,
    isReadOnly,
  ]);

  useCanvasKeyboardShortcuts({
    isEnabled: !isReadOnly,
    isShortcutScopeBlocked: isCanvasShortcutScopeBlocked,
    closeMenus: closeCanvasMenus,
    closePanels: closeCanvasPanels,
    toggleNodeLibrary: () => setIsNodeLibraryOpen((prev) => !prev),
  });

  const currentAppId = useMemo(() => {
    const activeWorkflow = workflows.find((w) => w.id === activeWorkflowId);
    return activeWorkflow?.appId;
  }, [workflows, activeWorkflowId]);

  // 노드 우클릭 핸들러
  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      if (isReadOnly) return;
      event.preventDefault();
      event.stopPropagation();
      setNodeContextMenu({
        x: event.clientX,
        y: event.clientY,
        nodeId: node.id,
      });
      setEdgeContextMenu(null);
      setContextMenu(null);
    },
    [isReadOnly, setContextMenu, setEdgeContextMenu, setNodeContextMenu],
  );

  // Edge 우클릭 핸들러
  const onEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: { id: string }) => {
      if (isReadOnly) return;
      event.preventDefault();
      event.stopPropagation();
      setEdgeContextMenu({
        x: event.clientX,
        y: event.clientY,
        edgeId: edge.id,
      });
      setNodeContextMenu(null);
      setContextMenu(null);
    },
    [isReadOnly, setContextMenu, setEdgeContextMenu, setNodeContextMenu],
  );

  // 노드 삭제 핸들러 (React Flow 내부 로직 사용)
  const handleDeleteNode = useCallback(() => {
    if (isReadOnly) return;
    if (!nodeContextMenu) return;
    deleteElements({ nodes: [{ id: nodeContextMenu.nodeId }] });
    setNodeContextMenu(null);
  }, [isReadOnly, nodeContextMenu, deleteElements, setNodeContextMenu]);

  // Edge 삭제 핸들러 (React Flow 내부 로직 사용)
  const handleDeleteEdge = useCallback(() => {
    if (isReadOnly) return;
    if (!edgeContextMenu) return;
    deleteElements({ edges: [{ id: edgeContextMenu.edgeId }] });
    setEdgeContextMenu(null);
  }, [isReadOnly, edgeContextMenu, deleteElements, setEdgeContextMenu]);

  useEffect(() => {
    const handleClick = () => handleCloseContextMenu();
    window.addEventListener('click', handleClick);
    return () => window.removeEventListener('click', handleClick);
  }, [handleCloseContextMenu]);

  const [headerActionsRoot, setHeaderActionsRoot] =
    useState<HTMLElement | null>(null);
  const searchParams = useSearchParams();
  const ndvNodeParam = searchParams.get('node');

  // useEffect for tabParam removed

  useEffect(() => {
    const currentNodeParam =
      typeof window !== 'undefined'
        ? new URLSearchParams(window.location.search).get('node')
        : ndvNodeParam;

    if (!currentNodeParam) {
      if (fullscreenNodeId) {
        syncNodeFullscreenFromUrl(null);
      }
      return;
    }

    const hasTargetNode = nodes.some((node) => node.id === currentNodeParam);
    if (hasTargetNode && fullscreenNodeId !== currentNodeParam) {
      syncNodeFullscreenFromUrl(currentNodeParam);
    }
  }, [
    fullscreenNodeId,
    ndvNodeParam,
    nodes,
    syncNodeFullscreenFromUrl,
  ]);

  useEffect(() => {
    const handlePopState = () => {
      const nodeId = new URLSearchParams(window.location.search).get('node');
      if (!nodeId) {
        syncNodeFullscreenFromUrl(null);
        return;
      }

      if (nodes.some((node) => node.id === nodeId)) {
        syncNodeFullscreenFromUrl(nodeId);
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [nodes, syncNodeFullscreenFromUrl]);

  useEffect(() => {
    setHeaderActionsRoot(
      document.getElementById('workflow-editor-header-actions'),
    );
  }, []);

  const workflowHeaderActions = (
    <>
      <div className="flex h-9 items-center rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
        <div className="flex h-full items-center px-2">
          <MemoryModeToggle
            isEnabled={isMemoryModeEnabled}
            hasProviderKey={hasProviderKey}
            providerKeyStatus={providerKeyStatus}
            description={memoryModeDescription}
            onToggle={toggleMemoryMode}
          />
        </div>
        <div className="mx-1 h-4 w-px bg-slate-200" />
        <button
          onClick={toggleSettings}
          className="flex h-full items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950"
        >
          <Settings className="h-4 w-4" />
          <span>설정</span>
        </button>
        <div className="mx-1 h-4 w-px bg-slate-200" />
        <button
          onClick={toggleVersionHistory}
          className="flex h-full items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950"
        >
          <ClockIcon className="h-4 w-4" />
          <span>버전</span>
        </button>
        <div className="mx-1 h-4 w-px bg-slate-200" />
        <button
          onClick={() => {
            const query = new URLSearchParams({ tab: 'logs' });
            const testExecutionQuery = new URLSearchParams(
              window.location.search,
            );
            if (testExecutionRunId) {
              testExecutionQuery.set('testRun', testExecutionRunId);
            }
            if (testSelectedNodeId) {
              testExecutionQuery.set('testNode', testSelectedNodeId);
            }
            copyTestExecutionLocationQueryParams(testExecutionQuery, query);
            router.push(`/modules/${activeWorkflowId}/report?${query.toString()}`);
          }}
          className="flex h-full items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950"
        >
          <BarChart3 className="h-4 w-4" />
          <span>보고</span>
        </button>
        <div className="mx-1 h-4 w-px bg-slate-200" />
        <div className="relative h-full">
          <button
            disabled={!canPublish}
            onClick={toggleDeployDropdown}
            className={`flex h-full items-center gap-1.5 rounded-md px-3 text-[13px] font-medium transition-colors ${
              !canPublish
                ? 'cursor-not-allowed text-gray-400'
                : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950'
            }`}
          >
            <span>게시하기</span>
            <svg
              className={`h-3.5 w-3.5 transition-transform ${
                showDeployDropdown ? 'rotate-180' : ''
              }`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>

          {showDeployDropdown && canPublish && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setShowDeployDropdown(false)}
              />
              <div className="absolute right-0 z-20 mt-2 w-64 rounded-lg border border-slate-200 bg-white py-2 text-left shadow-lg">
                {startNode?.type === 'webhookTrigger' && (
                  <button
                    onClick={handlePublishAsWebhook}
                    className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                  >
                    <div className="font-medium text-gray-900">
                      웹훅으로 개시하기
                    </div>
                    <div className="mt-1 text-sm text-gray-500">
                      URL 호출로 실행
                    </div>
                  </button>
                )}

                {startNode?.type === 'scheduleTrigger' && (
                  <button
                    onClick={handlePublishAsSchedule}
                    className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                  >
                    <div className="font-medium text-gray-900">
                      알람으로 개시하기
                    </div>
                    <div className="mt-1 text-sm text-gray-500">
                      설정된 주기에 따라 실행
                    </div>
                  </button>
                )}

                {(startNode?.type === 'startNode' || !startNode) && (
                  <>
                    <button
                      onClick={handlePublishAsRestAPI}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        REST API로 배포
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        내 서비스나 백엔드 서버에서 호출
                      </div>
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handlePublishAsWebApp}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        공개 웹페이지 생성
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        설치 없이 바로 쓸 수 있는 페이지 제공
                      </div>
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handlePublishAsChatbot}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        공개 챗봇 배포
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        대화 맥락을 기억하는 공개 채팅 페이지 제공
                      </div>
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handlePublishAsInternalChatbot}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        내부 챗봇 배포
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        로그인 사용자 권한으로 사내 Knowledge 실행
                      </div>
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handlePublishAsWidget}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        사이트에 임베드
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        스크립트 코드로 내 웹사이트에 삽입
                      </div>
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handlePublishAsWorkflowNode}
                      className="w-full px-4 py-3 text-left transition-colors hover:bg-gray-50"
                    >
                      <div className="font-medium text-gray-900">
                        서브 모듈로 배포
                      </div>
                      <div className="mt-1 text-sm text-gray-500">
                        다른 모듈에서 재사용
                      </div>
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <button
        onClick={canExecute ? toggleTestPanel : undefined}
        disabled={!canExecute}
        title={canExecute ? '테스트 실행' : '현재 권한으로는 실행할 수 없습니다'}
        className={`flex h-9 items-center gap-1.5 rounded-lg px-4 text-[13px] font-semibold text-white shadow-sm transition-colors ${
          !canExecute
            ? 'cursor-not-allowed bg-gray-300 text-gray-500'
            : testExecutionStatus === 'running'
              ? 'bg-blue-600 hover:bg-blue-700'
              : testExecutionStatus === 'success'
                ? 'bg-emerald-600 hover:bg-emerald-700'
                : testExecutionStatus === 'failure'
                  ? 'bg-red-600 hover:bg-red-700'
                  : 'bg-slate-950 hover:bg-slate-800'
        }`}
      >
        {testExecutionStatus === 'running' ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Play className="h-3.5 w-3.5 fill-current" />
        )}
        {testExecutionStatus === 'running'
          ? isTestUploading
            ? '업로드 중'
            : '실행 중'
          : testExecutionStatus === 'success'
            ? '결과'
            : testExecutionStatus === 'failure'
              ? '실패'
              : '테스트 실행'}
      </button>
    </>
  );

  return (
    <div className="relative flex flex-1 flex-col overflow-hidden bg-slate-50 p-3">
      {headerActionsRoot
        ? createPortal(workflowHeaderActions, headerActionsRoot)
        : null}
      {/* Main Content Area Container */}
      <div className="flex h-full flex-1 flex-col overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
        {/* Tab Header Removed */}

        {/* Content Area */}
        <div className="flex-1 relative overflow-hidden">
          {/* 1. Editor Tab Content */}
          <div
            className="relative flex h-full w-full flex-row gap-2"
          >
            {/* Node Library Sidebar */}
            <div className="flex h-full flex-col py-3 pl-3">
              <div
                className={`z-20 flex-1 rounded-lg bg-white transition-all duration-300 ease-in-out ${
                  isNodeLibraryOpen
                    ? 'w-64 border border-slate-200 shadow-sm'
                    : 'w-0 border-none'
                }`}
              >
                <NodeLibrarySidebar
                  isOpen={isNodeLibraryOpen}
                  onToggle={() => setIsNodeLibraryOpen(!isNodeLibraryOpen)}
                  onAddNode={handleAddNodeFromLibrary}
                  onAddNodeAfterSelected={handleAddNodeAfterSelected}
                  onOpenAppSearch={() =>
                    setSearchModalContext({ isOpen: true })
                  }
                />
              </div>
            </div>

            {/* Editor Canvas Container */}
            <div className="flex-1 h-full relative flex flex-col overflow-hidden">
              {/* App Search Modal */}
              <AppSearchModal
                isOpen={searchModalContext.isOpen}
                onClose={() => setSearchModalContext({ isOpen: false })}
                onSelect={handleSelectApp}
                excludedAppId={currentAppId}
              />

              {/* ReactFlow 캔버스 */}
              <div
                ref={reactFlowWrapperRef}
                className="w-full h-full relative"
                onContextMenu={(e) => e.preventDefault()}
                onDragOver={handleDragOver}
                onDrop={handleCanvasDrop}
                onWheelCapture={handleNodeWheelZoom}
              >
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={handleCanvasNodesChange}
                  onEdgesChange={handleCanvasEdgesChange}
                  onConnect={handleCanvasConnect}
                  onMoveEnd={handleMoveEnd}
                  onNodeClick={handleNodeClick}
                  onNodeMouseEnter={handleNodeMouseEnter}
                  onNodeMouseLeave={handleNodeMouseLeave}
                  onPaneContextMenu={isReadOnly ? undefined : onPaneContextMenu}
                  onNodeContextMenu={onNodeContextMenu}
                  onEdgeContextMenu={onEdgeContextMenu}
                  nodesDraggable={!isReadOnly}
                  nodesConnectable={!isReadOnly}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  defaultEdgeOptions={defaultEdgeOptions}
                  connectionLineComponent={CustomConnectionLine}
                  defaultViewport={{ x: 0, y: 0, zoom: 0.8 }}
                  minZoom={MIN_ZOOM}
                  maxZoom={MAX_ZOOM}
                  deleteKeyCode={null}
                  attributionPosition="bottom-right"
                  className="bg-slate-50"
                  {...reactFlowConfig}
                >
                  <Background
                    variant={BackgroundVariant.Dots}
                    gap={backgroundGap}
                    size={1}
                    color="#cbd5e1"
                  />
                </ReactFlow>

                {isReadOnly && (
                  <div className="absolute left-4 top-4 z-30 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 shadow-sm">
                    {workflowAccess?.auth_state || 'viewer'} · 읽기 전용
                  </div>
                )}

                {numberConnection && (
                  <div className="pointer-events-none absolute left-1/2 top-4 z-40 flex -translate-x-1/2 flex-col items-center gap-1">
                    <div className="rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-lg">
                      <span className="text-blue-700">연결할 노드 번호</span>
                      <span className="ml-2 inline-flex min-w-8 items-center justify-center rounded-md bg-blue-50 px-2 py-0.5 font-bold tabular-nums text-blue-700">
                        {numberConnection.input || '-'}
                      </span>
                      <span className="ml-2 text-slate-400">
                        숫자 입력 · Enter 확정 · Esc 취소
                      </span>
                    </div>
                    {numberConnection.input && (
                      <div className="rounded-md border border-slate-200 bg-white/95 px-2 py-1 text-[11px] font-medium text-slate-500 shadow-sm">
                        후보 {currentNumberMatches.length}개
                      </div>
                    )}
                  </div>
                )}

                {/* Drag connection preview overlay */}
                <DragConnectionOverlay
                  nearestNode={previewState.nearestNode}
                  draggedNodePosition={previewState.draggedNodePosition}
                  isRight={previewState.isRight}
                />

                {/* 플로팅 하단 패널 */}
                <BottomPanel
                  onCenterNodes={handleAutoLayout}
                  isPanelOpen={false}
                  onOpenAppSearch={() =>
                    setSearchModalContext({ isOpen: true })
                  }
                />

                {/* Context Menu UI */}
                {contextMenu && !isReadOnly && (
                  <div
                    className="fixed z-50 bg-white rounded-lg shadow-xl border border-gray-200 py-1 min-w-[180px]"
                    style={{ top: contextMenu.y, left: contextMenu.x }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={handleAddNodeFromContext}
                      className="w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
                    >
                      <Plus className="w-4 h-4 text-gray-500" />
                      노드 추가
                    </button>
                    <button
                      onClick={handleAddMemoFromContext}
                      className="w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
                    >
                      <StickyNote className="w-4 h-4 text-gray-500" />
                      메모 추가
                    </button>
                    <div className="my-1 border-t border-gray-100" />
                    <button
                      onClick={handleTestRunFromContext}
                      className="w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
                    >
                      <Play className="w-4 h-4 text-gray-500" />
                      테스트 실행
                    </button>
                  </div>
                )}

                {/* 노드 우클릭 삭제 메뉴 */}
                {nodeContextMenu && !isReadOnly && (
                  <div
                    className="fixed z-50 bg-white rounded-lg shadow-xl border border-gray-200 py-1 min-w-[140px]"
                    style={{ top: nodeContextMenu.y, left: nodeContextMenu.x }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={handleDeleteNode}
                      className="w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
                    >
                      <Trash2 className="w-4 h-4" />
                      노드 삭제
                    </button>
                  </div>
                )}

                {/* Edge 우클릭 삭제 메뉴 */}
                {edgeContextMenu && !isReadOnly && (
                  <div
                    className="fixed z-50 bg-white rounded-lg shadow-xl border border-gray-200 py-1 min-w-[140px]"
                    style={{ top: edgeContextMenu.y, left: edgeContextMenu.x }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={handleDeleteEdge}
                      className="w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
                    >
                      <Trash2 className="w-4 h-4" />
                      연결선 삭제
                    </button>
                  </div>
                )}

                {/* Context Menu Node Selector Modal */}
                {isContextNodeSelectorOpen && !isReadOnly && (
                  <div
                    className="fixed z-50"
                    style={{
                      left: contextMenuPos.x,
                      top:
                        typeof window !== 'undefined' &&
                        window.innerHeight - contextMenuPos.y < 420
                          ? 'auto'
                          : contextMenuPos.y,
                      bottom:
                        typeof window !== 'undefined' &&
                        window.innerHeight - contextMenuPos.y < 420
                          ? window.innerHeight - contextMenuPos.y
                          : 'auto',
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <NodeSelector onSelect={handleSelectNodeFromContext} />
                  </div>
                )}

                {/* Close Node Selector when clicking outside (overlay) */}
                {isContextNodeSelectorOpen && !isReadOnly && (
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setIsContextNodeSelectorOpen(false)}
                  />
                )}
              </div>
            </div>
          </div>

        </div>
      </div>
      {/* Sidebars */}
      <SettingsSidebar />
      <VersionHistorySidebar />
      <TestSidebar appendMemoryFlag={appendMemoryFlag} />

      {/* 노드 전체화면 설정(NDV) */}
      <NodeFullscreenEditor />

      {/* Deployment Flow Modal */}
      <DeploymentFlowModal
        isOpen={showDeployFlowModal}
        onClose={() => setShowDeployFlowModal(false)}
        appId={currentAppId}
        deploymentType={deploymentType}
        llmNodes={deploymentLlmNodes}
        onDeploy={handleDeploy}
      />

      {!isReadOnly && (
        <AgentBuilderPanel
          workflowId={activeWorkflowId}
          appId={currentAppId}
          nodes={nodes}
          edges={edges}
          hasUnsavedChanges={hasUnsavedChanges}
          selectedNodeId={selectedNodeId}
          selectedEdgeId={selectedEdgeId}
        />
      )}

      {/* Memory Mode Modals */}
      {memoryModeModals}
    </div>
  );
}
