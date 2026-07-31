import { renderHook, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useCanvasKeyboardShortcuts } from './useCanvasKeyboardShortcuts';
import { useWorkflowStore } from '../store/useWorkflowStore';

vi.mock('../api/workflowApi', () => ({
  workflowApi: {
    getDraftWorkflow: vi.fn(),
    syncDraftWorkflow: vi.fn(),
    createWorkflow: vi.fn(),
    listWorkflowsByApp: vi.fn(),
  },
}));

const initialState = useWorkflowStore.getState();

const createOptions = () => ({
  isEnabled: true,
  closeMenus: vi.fn(() => false),
  closePanels: vi.fn(() => false),
  toggleNodeLibrary: vi.fn(),
});

const installActionSpies = (hasSelection = false) => {
  const actions = {
    copySelectedNodes: vi.fn(),
    pasteCopiedNodes: vi.fn(),
    duplicateSelectedNodes: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    deleteSelectedElements: vi.fn(),
    clearSelection: vi.fn(),
    clearInnerNodeSelection: vi.fn(),
    hasSelectedElements: vi.fn(() => hasSelection),
  };

  useWorkflowStore.setState(actions);
  return actions;
};

const dispatchKeyDown = (
  key: string,
  init: KeyboardEventInit = {},
  target: HTMLElement | Window = window,
) => {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  const preventDefault = vi.spyOn(event, 'preventDefault');
  target.dispatchEvent(event);
  return { event, preventDefault };
};

describe('useCanvasKeyboardShortcuts', () => {
  beforeEach(() => {
    useWorkflowStore.setState(initialState, true);
    document.body.innerHTML = '';
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('Ctrl/Cmd+D가 duplicateSelectedNodes를 호출한다', () => {
    const actions = installActionSpies();
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const ctrlEvent = dispatchKeyDown('d', { ctrlKey: true });
    const metaEvent = dispatchKeyDown('d', { metaKey: true });

    expect(actions.duplicateSelectedNodes).toHaveBeenCalledTimes(2);
    expect(ctrlEvent.preventDefault).toHaveBeenCalled();
    expect(metaEvent.preventDefault).toHaveBeenCalled();
  });

  it('role="dialog" 내부 버튼에서는 canvas command 단축키를 무시한다', () => {
    const actions = installActionSpies(true);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    const button = document.createElement('button');
    dialog.appendChild(button);
    document.body.appendChild(dialog);
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const duplicateEvent = dispatchKeyDown('d', { ctrlKey: true }, button);
    const metaDuplicateEvent = dispatchKeyDown('d', { metaKey: true }, button);
    const pasteEvent = dispatchKeyDown('v', { ctrlKey: true }, button);
    const deleteEvent = dispatchKeyDown('Delete', {}, button);

    expect(actions.duplicateSelectedNodes).not.toHaveBeenCalled();
    expect(actions.pasteCopiedNodes).not.toHaveBeenCalled();
    expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
    expect(actions.hasSelectedElements).not.toHaveBeenCalled();
    expect(duplicateEvent.preventDefault).not.toHaveBeenCalled();
    expect(metaDuplicateEvent.preventDefault).not.toHaveBeenCalled();
    expect(pasteEvent.preventDefault).not.toHaveBeenCalled();
    expect(deleteEvent.preventDefault).not.toHaveBeenCalled();
  });

  it('data-canvas-shortcut-scope="blocked" 내부 버튼에서는 canvas command 단축키를 무시한다', () => {
    const actions = installActionSpies(true);
    const overlay = document.createElement('div');
    overlay.setAttribute('data-canvas-shortcut-scope', 'blocked');
    const button = document.createElement('button');
    overlay.appendChild(button);
    document.body.appendChild(overlay);
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const duplicateEvent = dispatchKeyDown('d', { ctrlKey: true }, button);

    expect(actions.duplicateSelectedNodes).not.toHaveBeenCalled();
    expect(duplicateEvent.preventDefault).not.toHaveBeenCalled();
  });

  it('document에 blocking modal이 열려 있으면 body target의 canvas command 단축키를 무시하고, 제거 후 다시 동작한다', () => {
    const actions = installActionSpies(true);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    document.body.appendChild(dialog);
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const duplicateEvent = dispatchKeyDown(
      'd',
      { ctrlKey: true },
      document.body,
    );
    const deleteEvent = dispatchKeyDown('Delete', {}, document.body);
    const pasteEvent = dispatchKeyDown('v', { ctrlKey: true }, document.body);

    expect(actions.duplicateSelectedNodes).not.toHaveBeenCalled();
    expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
    expect(actions.pasteCopiedNodes).not.toHaveBeenCalled();
    expect(actions.hasSelectedElements).not.toHaveBeenCalled();
    expect(duplicateEvent.preventDefault).not.toHaveBeenCalled();
    expect(deleteEvent.preventDefault).not.toHaveBeenCalled();
    expect(pasteEvent.preventDefault).not.toHaveBeenCalled();

    dialog.remove();

    const nextDuplicateEvent = dispatchKeyDown(
      'd',
      { ctrlKey: true },
      document.body,
    );

    expect(actions.duplicateSelectedNodes).toHaveBeenCalledTimes(1);
    expect(nextDuplicateEvent.preventDefault).toHaveBeenCalled();
  });

  it('shortcut scope가 blocked이면 canvas command 단축키를 무시한다', () => {
    const actions = installActionSpies(true);
    const options = {
      ...createOptions(),
      isShortcutScopeBlocked: vi.fn(() => true),
    };
    renderHook(() => useCanvasKeyboardShortcuts(options));

    const duplicateEvent = dispatchKeyDown('d', { ctrlKey: true });
    const deleteEvent = dispatchKeyDown('Delete');

    expect(options.isShortcutScopeBlocked).toHaveBeenCalledTimes(2);
    expect(actions.duplicateSelectedNodes).not.toHaveBeenCalled();
    expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
    expect(duplicateEvent.preventDefault).not.toHaveBeenCalled();
    expect(deleteEvent.preventDefault).not.toHaveBeenCalled();
  });

  it('shortcut scope가 blocked여도 Escape는 overlay 정리를 위해 closeMenus를 호출한다', () => {
    const actions = installActionSpies();
    const options = {
      ...createOptions(),
      isShortcutScopeBlocked: vi.fn(() => true),
    };
    renderHook(() => useCanvasKeyboardShortcuts(options));

    const { preventDefault } = dispatchKeyDown('Escape');

    expect(preventDefault).toHaveBeenCalled();
    expect(options.closeMenus).toHaveBeenCalledTimes(1);
    expect(actions.clearSelection).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['input', () => document.createElement('input')],
    ['textarea', () => document.createElement('textarea')],
    ['select', () => document.createElement('select')],
    [
      'contenteditable',
      () => {
        const element = document.createElement('div');
        element.setAttribute('contenteditable', 'true');
        return element;
      },
    ],
    [
      'role="textbox"',
      () => {
        const element = document.createElement('div');
        element.setAttribute('role', 'textbox');
        return element;
      },
    ],
  ])('%s 포커스 영역에서는 Ctrl/Cmd+D를 무시한다', (_, createElement) => {
    const actions = installActionSpies();
    const target = createElement();
    document.body.appendChild(target);
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const { preventDefault } = dispatchKeyDown('d', { ctrlKey: true }, target);

    expect(actions.duplicateSelectedNodes).not.toHaveBeenCalled();
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it('Delete는 선택 요소가 없으면 preventDefault/delete/closePanels를 하지 않는다', () => {
    const actions = installActionSpies(false);
    const options = createOptions();
    renderHook(() => useCanvasKeyboardShortcuts(options));

    const { preventDefault } = dispatchKeyDown('Delete');

    expect(actions.hasSelectedElements).toHaveBeenCalled();
    expect(preventDefault).not.toHaveBeenCalled();
    expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
    expect(options.closePanels).not.toHaveBeenCalled();
  });

  it.each(['Delete', 'Backspace'])(
    '%s는 선택 요소가 있을 때만 삭제하고 패널을 닫는다',
    (key) => {
      const actions = installActionSpies(true);
      const options = createOptions();
      renderHook(() => useCanvasKeyboardShortcuts(options));

      const { preventDefault } = dispatchKeyDown(key);

      expect(preventDefault).toHaveBeenCalled();
      expect(actions.deleteSelectedElements).toHaveBeenCalled();
      expect(options.closePanels).toHaveBeenCalled();
    },
  );

  it.each(['Delete', 'Backspace'])(
    '%s는 캔버스 단축키 차단 스코프가 있으면 선택 요소를 삭제하지 않는다',
    (key) => {
      const actions = installActionSpies(true);
      const options = createOptions();
      const blockedScope = document.createElement('div');
      blockedScope.setAttribute('data-canvas-shortcut-scope', 'blocked');
      document.body.appendChild(blockedScope);
      renderHook(() => useCanvasKeyboardShortcuts(options));

      const { preventDefault } = dispatchKeyDown(key);

      expect(preventDefault).not.toHaveBeenCalled();
      expect(actions.deleteSelectedElements).not.toHaveBeenCalled();
      expect(options.closePanels).not.toHaveBeenCalled();
    },
  );

  it('일반 텍스트가 선택된 상태에서는 Ctrl+C를 브라우저 복사에 맡긴다', () => {
    const actions = installActionSpies();
    const target = document.createElement('p');
    target.textContent = '선택할 텍스트';
    document.body.appendChild(target);
    vi.spyOn(window, 'getSelection').mockReturnValue({
      isCollapsed: false,
      toString: () => '선택할 텍스트',
    } as unknown as Selection);
    renderHook(() => useCanvasKeyboardShortcuts(createOptions()));

    const { preventDefault } = dispatchKeyDown(
      'c',
      { ctrlKey: true },
      target,
    );

    expect(actions.copySelectedNodes).not.toHaveBeenCalled();
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it('C/V/Z/Shift+Z/Y/B/Esc 핵심 단축키를 유지한다', () => {
    const actions = installActionSpies();
    const options = createOptions();
    renderHook(() => useCanvasKeyboardShortcuts(options));

    dispatchKeyDown('c', { ctrlKey: true });
    dispatchKeyDown('v', { metaKey: true });
    dispatchKeyDown('z', { ctrlKey: true });
    dispatchKeyDown('z', { ctrlKey: true, shiftKey: true });
    dispatchKeyDown('y', { ctrlKey: true });
    dispatchKeyDown('b', { ctrlKey: true });
    dispatchKeyDown('Escape');

    expect(actions.copySelectedNodes).toHaveBeenCalledTimes(1);
    expect(actions.pasteCopiedNodes).toHaveBeenCalledTimes(1);
    expect(actions.undo).toHaveBeenCalledTimes(1);
    expect(actions.redo).toHaveBeenCalledTimes(2);
    expect(options.toggleNodeLibrary).toHaveBeenCalledTimes(1);
    expect(options.closeMenus).toHaveBeenCalledTimes(1);
    expect(options.closePanels).toHaveBeenCalledTimes(1);
    expect(actions.clearSelection).toHaveBeenCalledTimes(1);
    expect(actions.clearInnerNodeSelection).toHaveBeenCalledTimes(1);
  });
});
