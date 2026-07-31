import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';

import {
  fitPanelWidths,
  getDefaultPanelWidthsForLayout,
  getNodeEditorMaxLayoutWidth,
  type HorizontalResizeHandle,
  NODE_EDITOR_PANEL_WIDTHS,
  type NodeEditorPanelWidths,
  resizePanelWidths,
  sumPanelWidths,
} from '../utils/nodeEditorPanelLayout';

export function useResizableNodeEditorPanels() {
  const [panelWidths, setPanelWidths] = useState<NodeEditorPanelWidths>(
    NODE_EDITOR_PANEL_WIDTHS.default,
  );
  const [layoutWidth, setLayoutWidth] = useState<number>(
    sumPanelWidths(NODE_EDITOR_PANEL_WIDTHS.default) +
      NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2,
  );
  const [isResizableLayout, setIsResizableLayout] = useState(true);
  const layoutShellRef = useRef<HTMLElement>(null);
  const hasCustomPanelWidthsRef = useRef(false);
  const activeDragCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const layoutShell = layoutShellRef.current;
    if (!layoutShell) return;

    const updateLayoutWidth = () => {
      const shellWidth = layoutShell.getBoundingClientRect().width;
      const nextLayoutWidth = getNodeEditorMaxLayoutWidth(shellWidth);
      const minResizableWidth =
        sumPanelWidths(NODE_EDITOR_PANEL_WIDTHS.min) +
        NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2;
      const nextIsResizableLayout = nextLayoutWidth >= minResizableWidth;

      setIsResizableLayout(nextIsResizableLayout);
      setLayoutWidth(nextLayoutWidth);
      if (!hasCustomPanelWidthsRef.current) {
        setPanelWidths(getDefaultPanelWidthsForLayout(nextLayoutWidth));
      }
    };

    updateLayoutWidth();
    const resizeObserver = new ResizeObserver(updateLayoutWidth);
    resizeObserver.observe(layoutShell);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(
    () => () => {
      activeDragCleanupRef.current?.();
    },
    [],
  );

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

  const handleHorizontalResizeStart = useCallback(
    (handle: HorizontalResizeHandle, event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      activeDragCleanupRef.current?.();

      const startX = event.clientX;
      const startWidths = fittedPanelWidths;
      const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
        const deltaX = moveEvent.clientX - startX;
        hasCustomPanelWidthsRef.current = true;
        setPanelWidths(
          resizePanelWidths(handle, startWidths, deltaX, layoutWidth),
        );
      };
      const cleanup = () => {
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', cleanup);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        activeDragCleanupRef.current = null;
      };

      activeDragCleanupRef.current = cleanup;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', cleanup);
    },
    [fittedPanelWidths, layoutWidth],
  );

  const handleHorizontalResizeKeyDown = useCallback(
    (handle: HorizontalResizeHandle, event: KeyboardEvent<HTMLDivElement>) => {
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

  return {
    layoutShellRef,
    isResizableLayout,
    fittedPanelWidths,
    fittedLayoutWidth,
    handleHorizontalResizeStart,
    handleHorizontalResizeKeyDown,
  };
}
