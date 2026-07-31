import { useEffect } from 'react';
import { useWorkflowStore } from '../store/useWorkflowStore';

const BLOCKING_MODAL_SELECTOR = [
  '[role="dialog"]',
  '[aria-modal="true"]',
  'dialog[open]',
  '[data-canvas-shortcut-scope="blocked"]',
  '[data-modal="true"]',
].join(', ');

const isEditableTarget = (target: EventTarget | null) => {
  if (!(target instanceof HTMLElement)) return false;

  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]',
    ),
  );
};

const isModalTarget = (target: EventTarget | null) => {
  if (!(target instanceof HTMLElement)) return false;

  return Boolean(target.closest(BLOCKING_MODAL_SELECTOR));
};

const hasBlockingModal = () =>
  typeof document !== 'undefined' &&
  document.querySelector(BLOCKING_MODAL_SELECTOR) !== null;

interface CanvasKeyboardShortcutOptions {
  isEnabled: boolean;
  isShortcutScopeBlocked?: (event: KeyboardEvent) => boolean;
  closeMenus: () => boolean;
  closePanels: () => boolean;
  toggleNodeLibrary: () => void;
}

export function useCanvasKeyboardShortcuts({
  isEnabled,
  isShortcutScopeBlocked,
  closeMenus,
  closePanels,
  toggleNodeLibrary,
}: CanvasKeyboardShortcutOptions) {
  const copySelectedNodes = useWorkflowStore(
    (state) => state.copySelectedNodes,
  );
  const pasteCopiedNodes = useWorkflowStore((state) => state.pasteCopiedNodes);
  const duplicateSelectedNodes = useWorkflowStore(
    (state) => state.duplicateSelectedNodes,
  );
  const undo = useWorkflowStore((state) => state.undo);
  const redo = useWorkflowStore((state) => state.redo);
  const deleteSelectedElements = useWorkflowStore(
    (state) => state.deleteSelectedElements,
  );
  const hasSelectedElements = useWorkflowStore(
    (state) => state.hasSelectedElements,
  );
  const clearSelection = useWorkflowStore((state) => state.clearSelection);
  const clearInnerNodeSelection = useWorkflowStore(
    (state) => state.clearInnerNodeSelection,
  );

  useEffect(() => {
    if (!isEnabled) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return;
      if (isModalTarget(event.target) || hasBlockingModal()) return;

      const key = event.key.toLowerCase();
      const isModKey = event.metaKey || event.ctrlKey;
      const isEscape = event.key === 'Escape';

      if (!isEscape && isShortcutScopeBlocked?.(event)) return;

      if (isEscape) {
        event.preventDefault();
        const closedMenu = closeMenus();
        if (!closedMenu) {
          const closedPanel = closePanels();
          if (!closedPanel) {
            clearSelection();
            clearInnerNodeSelection();
          }
        }
        return;
      }

      if (event.key === 'Delete' || event.key === 'Backspace') {
        if (!hasSelectedElements()) return;
        event.preventDefault();
        deleteSelectedElements();
        closePanels();
        return;
      }

      if (!isModKey || event.altKey) return;

      if (key === 'c' && !event.shiftKey) {
        if (window.getSelection()?.toString()) return;
        event.preventDefault();
        copySelectedNodes();
        return;
      }

      if (key === 'v' && !event.shiftKey) {
        event.preventDefault();
        pasteCopiedNodes();
        return;
      }

      if (key === 'd' && !event.shiftKey) {
        event.preventDefault();
        duplicateSelectedNodes();
        return;
      }

      if (key === 'z') {
        event.preventDefault();
        if (event.shiftKey) {
          redo();
        } else {
          undo();
        }
        return;
      }

      if (key === 'y' && !event.shiftKey) {
        event.preventDefault();
        redo();
        return;
      }

      if (key === 'b' && !event.shiftKey) {
        event.preventDefault();
        toggleNodeLibrary();
        return;
      }

    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    isEnabled,
    isShortcutScopeBlocked,
    closeMenus,
    closePanels,
    toggleNodeLibrary,
    copySelectedNodes,
    pasteCopiedNodes,
    duplicateSelectedNodes,
    undo,
    redo,
    deleteSelectedElements,
    hasSelectedElements,
    clearSelection,
    clearInnerNodeSelection,
  ]);
}
