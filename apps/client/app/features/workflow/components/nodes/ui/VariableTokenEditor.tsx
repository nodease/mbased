import {
  KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import { DraggedOutputVariable } from '../../../utils/nodeVariablePorts';
import { cn } from '@/lib/utils';
import { useVariableInsertion } from './useVariableInsertion';

const TOKEN_PATTERN = /{{\s*([^}]+?)\s*}}/g;
const TOKEN_ATTR = 'data-variable-token';
const TOKEN_NAME_ATTR = 'data-variable-name';
const CARET_BOUNDARY = '\u200B';

type Segment =
  | { type: 'text'; value: string }
  | { type: 'variable'; name: string; label: string; isRegistered: boolean };

const parseValueToSegments = (
  value: string,
  tokenLabels: Record<string, string> = {},
): Segment[] => {
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  TOKEN_PATTERN.lastIndex = 0;

  while ((match = TOKEN_PATTERN.exec(value)) !== null) {
    if (match.index > lastIndex) {
      segments.push({
        type: 'text',
        value: value.slice(lastIndex, match.index),
      });
    }

    const name = match[1].trim();
    const isRegistered = Object.prototype.hasOwnProperty.call(
      tokenLabels,
      name,
    );
    segments.push({
      type: 'variable',
      name,
      label: tokenLabels[name] || name,
      isRegistered,
    });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < value.length) {
    segments.push({ type: 'text', value: value.slice(lastIndex) });
  }

  return segments;
};

const createTextNode = (text: string) => document.createTextNode(text);
const createCaretBoundaryNode = () => createTextNode(CARET_BOUNDARY);
const stripCaretBoundaries = (text: string) =>
  text.replaceAll(CARET_BOUNDARY, '');

const createTokenNode = (
  name: string,
  label: string,
  options: { isRegistered?: boolean } = {},
) => {
  const isRegistered = options.isRegistered ?? true;
  const span = document.createElement('span');
  span.setAttribute(TOKEN_ATTR, 'true');
  span.setAttribute(TOKEN_NAME_ATTR, name);
  span.setAttribute('contenteditable', 'false');
  span.className = isRegistered
    ? 'mx-0.5 inline-flex max-w-full select-none items-center rounded-md border border-blue-100 bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-800 shadow-sm align-baseline'
    : 'mx-0.5 inline-flex max-w-full cursor-default select-none items-center rounded-md border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700 line-through decoration-red-400 shadow-sm align-baseline';
  span.title = isRegistered
    ? label
    : '등록되지 않았거나 연결이 끊어진 변수입니다.';
  span.textContent = label;
  return span;
};

const isTokenElement = (node: Node | null): node is HTMLElement =>
  node instanceof HTMLElement && node.getAttribute(TOKEN_ATTR) === 'true';

const tokenToText = (node: HTMLElement) =>
  `{{${node.getAttribute(TOKEN_NAME_ATTR) || node.textContent || ''}}}`;

const serializeNode = (node: Node): string => {
  if (node.nodeType === Node.TEXT_NODE) {
    return stripCaretBoundaries(node.textContent || '');
  }
  if (isTokenElement(node)) return tokenToText(node);
  return Array.from(node.childNodes).map(serializeNode).join('');
};

const serializeEditor = (editor: HTMLElement) =>
  Array.from(editor.childNodes).map(serializeNode).join('');

const isEditorEmpty = (editor: HTMLElement) =>
  serializeEditor(editor).length === 0;

const renderSegments = (
  editor: HTMLElement,
  value: string,
  tokenLabels: Record<string, string> = {},
) => {
  editor.replaceChildren();

  const segments = parseValueToSegments(value, tokenLabels);
  if (segments.length === 0) {
    editor.appendChild(createTextNode(''));
    return;
  }

  for (const segment of segments) {
    if (segment.type === 'text') {
      editor.appendChild(createTextNode(segment.value));
    } else {
      editor.appendChild(createCaretBoundaryNode());
      editor.appendChild(
        createTokenNode(segment.name, segment.label, {
          isRegistered: segment.isRegistered,
        }),
      );
      editor.appendChild(createCaretBoundaryNode());
    }
  }
};

const getActiveRange = (editor: HTMLElement) => {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;

  const range = selection.getRangeAt(0);
  const container = range.commonAncestorContainer;
  if (!editor.contains(container)) return null;
  return range;
};

const placeCaretAfter = (node: Node) => {
  const range = document.createRange();
  const selection = window.getSelection();
  range.setStartAfter(node);
  range.collapse(true);
  selection?.removeAllRanges();
  selection?.addRange(range);
};

const placeCaretBefore = (node: Node) => {
  const range = document.createRange();
  const selection = window.getSelection();
  range.setStartBefore(node);
  range.collapse(true);
  selection?.removeAllRanges();
  selection?.addRange(range);
};

const selectRange = (range: Range) => {
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
};

const getSerializedLength = (node: Node) => serializeNode(node).length;

const getTextSerializedOffset = (text: string, domOffset: number) =>
  stripCaretBoundaries(text.slice(0, domOffset)).length;

const getTextDomOffsetForSerializedOffset = (
  text: string,
  serializedOffset: number,
) => {
  if (serializedOffset <= 0) return 0;

  let visibleCount = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] !== CARET_BOUNDARY) {
      visibleCount += 1;
    }
    if (visibleCount >= serializedOffset) {
      return index + 1;
    }
  }
  return text.length;
};

const getSerializedOffsetForRange = (editor: HTMLElement, range: Range) => {
  let offset = 0;
  let found = false;

  const walk = (node: Node): void => {
    if (found) return;

    if (node === range.startContainer) {
      if (node.nodeType === Node.TEXT_NODE) {
        offset += getTextSerializedOffset(
          node.textContent || '',
          range.startOffset,
        );
      } else {
        const children = Array.from(node.childNodes);
        for (let index = 0; index < range.startOffset; index += 1) {
          offset += getSerializedLength(children[index]);
        }
      }
      found = true;
      return;
    }

    if (node.nodeType === Node.TEXT_NODE || isTokenElement(node)) {
      offset += getSerializedLength(node);
      return;
    }

    for (const child of Array.from(node.childNodes)) {
      walk(child);
      if (found) return;
    }
  };

  for (const child of Array.from(editor.childNodes)) {
    walk(child);
    if (found) break;
  }

  return offset;
};

const restoreCaretFromSerializedOffset = (
  editor: HTMLElement,
  serializedOffset: number,
) => {
  const range = document.createRange();
  const selection = window.getSelection();
  let remaining = Math.max(0, serializedOffset);

  for (const child of Array.from(editor.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      const text = child.textContent || '';
      const visibleLength = stripCaretBoundaries(text).length;
      if (remaining <= visibleLength) {
        range.setStart(
          child,
          getTextDomOffsetForSerializedOffset(text, remaining),
        );
        range.collapse(true);
        selection?.removeAllRanges();
        selection?.addRange(range);
        return;
      }
      remaining -= visibleLength;
      continue;
    }

    if (isTokenElement(child)) {
      const tokenLength = tokenToText(child).length;
      if (remaining < tokenLength) {
        range.setStartBefore(child);
        range.collapse(true);
        selection?.removeAllRanges();
        selection?.addRange(range);
        return;
      }
      remaining -= tokenLength;
    }
  }

  if (editor.lastChild) {
    range.setStartAfter(editor.lastChild);
  } else {
    range.selectNodeContents(editor);
  }
  range.collapse(true);
  selection?.removeAllRanges();
  selection?.addRange(range);
};

const insertNodeAtRange = (
  editor: HTMLElement,
  node: Node,
  range: Range | null,
) => {
  editor.focus();
  const targetRange = range || getActiveRange(editor);

  if (!targetRange) {
    editor.appendChild(node);
    placeCaretAfter(node);
    return;
  }

  targetRange.deleteContents();
  targetRange.insertNode(node);
  placeCaretAfter(node);
};

const insertTokenAtRange = (
  editor: HTMLElement,
  tokenNode: HTMLElement,
  range: Range | null,
) => {
  editor.focus();
  const targetRange = range || getActiveRange(editor);

  if (!targetRange) {
    const trailingBoundary = createCaretBoundaryNode();
    editor.appendChild(createCaretBoundaryNode());
    editor.appendChild(tokenNode);
    editor.appendChild(trailingBoundary);
    const nextRange = document.createRange();
    nextRange.setStart(
      trailingBoundary,
      trailingBoundary.textContent?.length || 0,
    );
    nextRange.collapse(true);
    selectRange(nextRange);
    return;
  }

  targetRange.deleteContents();
  const fragment = document.createDocumentFragment();
  const leadingBoundary = createCaretBoundaryNode();
  const trailingBoundary = createCaretBoundaryNode();
  fragment.appendChild(leadingBoundary);
  fragment.appendChild(tokenNode);
  fragment.appendChild(trailingBoundary);
  targetRange.insertNode(fragment);

  const nextRange = document.createRange();
  nextRange.setStart(
    trailingBoundary,
    trailingBoundary.textContent?.length || 0,
  );
  nextRange.collapse(true);
  selectRange(nextRange);
};

const insertNodeAtSelection = (editor: HTMLElement, node: Node) => {
  insertNodeAtRange(editor, node, getActiveRange(editor));
};

const isCaretBoundaryTextNode = (node: Node | null): node is Text =>
  node?.nodeType === Node.TEXT_NODE &&
  stripCaretBoundaries(node.textContent || '').length === 0;

const getSiblingSkippingCaretBoundaries = (
  node: Node | null,
  direction: 'backward' | 'forward',
) => {
  let sibling: Node | null =
    (direction === 'backward' ? node?.previousSibling : node?.nextSibling) ||
    null;

  while (isCaretBoundaryTextNode(sibling)) {
    const currentSibling = sibling;
    sibling =
      direction === 'backward'
        ? currentSibling.previousSibling
        : currentSibling.nextSibling;
  }

  return sibling || null;
};

const getAdjacentToken = (
  editor: HTMLElement,
  direction: 'backward' | 'forward',
) => {
  const range = getActiveRange(editor);
  if (!range || !range.collapsed) return null;

  const { startContainer, startOffset } = range;

  if (startContainer.nodeType === Node.TEXT_NODE) {
    const textBeforeCaret = startContainer.textContent?.slice(0, startOffset);
    const textAfterCaret = startContainer.textContent?.slice(startOffset);

    if (
      direction === 'backward' &&
      stripCaretBoundaries(textBeforeCaret || '').length > 0
    ) {
      return null;
    }
    if (
      direction === 'forward' &&
      stripCaretBoundaries(textAfterCaret || '').length > 0
    ) {
      return null;
    }

    const sibling =
      direction === 'backward'
        ? getSiblingSkippingCaretBoundaries(startContainer, 'backward')
        : getSiblingSkippingCaretBoundaries(startContainer, 'forward');
    return isTokenElement(sibling) ? sibling : null;
  }

  if (startContainer === editor) {
    const index = direction === 'backward' ? startOffset - 1 : startOffset;
    let child: Node | null = editor.childNodes[index] || null;
    while (isCaretBoundaryTextNode(child)) {
      const currentChild: Text = child;
      child =
        direction === 'backward'
          ? currentChild.previousSibling
          : currentChild.nextSibling;
    }
    return isTokenElement(child) ? child : null;
  }

  const element =
    startContainer.nodeType === Node.ELEMENT_NODE
      ? (startContainer as HTMLElement)
      : startContainer.parentElement;
  if (!element || !editor.contains(element)) return null;

  const sibling =
    direction === 'backward'
      ? getSiblingSkippingCaretBoundaries(element, 'backward')
      : getSiblingSkippingCaretBoundaries(element, 'forward');
  return isTokenElement(sibling) ? sibling : null;
};

const getCharacterBeforeRange = (editor: HTMLElement, range: Range) => {
  const { startContainer, startOffset } = range;

  if (startContainer.nodeType === Node.TEXT_NODE) {
    return startContainer.textContent?.[startOffset - 1] || '';
  }

  if (startContainer === editor) {
    const previousChild = editor.childNodes[startOffset - 1];
    if (previousChild?.nodeType === Node.TEXT_NODE) {
      return previousChild.textContent?.slice(-1) || '';
    }
  }

  return '';
};

const moveCaretAwayFromToken = (editor: HTMLElement) => {
  const range = getActiveRange(editor);
  if (!range) return;

  const container =
    range.startContainer.nodeType === Node.ELEMENT_NODE
      ? (range.startContainer as HTMLElement)
      : range.startContainer.parentElement;

  const token = container?.closest(`[${TOKEN_ATTR}="true"]`);
  if (!token || !editor.contains(token)) return;
  placeCaretAfter(token);
};

type VariableTokenEditorProps = {
  value: string;
  onChange: (value: string) => void;
  onDropOutput?: (output: DraggedOutputVariable) => string | void;
  insertOutputRequest?: {
    id: string;
    output: DraggedOutputVariable;
  } | null;
  placeholder?: string;
  className?: string;
  ariaLabel?: string;
  tokenLabels?: Record<string, string>;
};

export const VariableTokenEditor = ({
  value,
  onChange,
  onDropOutput,
  insertOutputRequest,
  placeholder,
  className,
  ariaLabel,
  tokenLabels = {},
}: VariableTokenEditorProps) => {
  const insertionTargetId = useId();
  const editorRef = useRef<HTMLDivElement>(null);
  const lastSelectionRangeRef = useRef<Range | null>(null);
  const lastSelectionOffsetRef = useRef<number | null>(null);
  const pendingCaretOffsetRef = useRef<number | null>(null);
  const lastRenderedValueRef = useRef<string | null>(null);
  const lastTokenLabelsRef = useRef('');
  const composingRef = useRef(false);
  const [isEmpty, setIsEmpty] = useState(!value);
  const { activeTarget, registerTarget, setActiveTarget, clearMessage } =
    useVariableInsertion();
  const targetLabel = ariaLabel || '텍스트 필드';
  const isActiveInsertionTarget = activeTarget?.id === insertionTargetId;

  const syncPlaceholder = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    setIsEmpty(isEditorEmpty(editor));
  }, []);

  const emitChange = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;

    const nextValue = serializeEditor(editor);
    lastRenderedValueRef.current = nextValue;
    syncPlaceholder();
    onChange(nextValue);
  }, [onChange, syncPlaceholder]);

  const rememberSelectionRange = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;

    const range = getActiveRange(editor);
    if (range) {
      lastSelectionRangeRef.current = range.cloneRange();
      lastSelectionOffsetRef.current = getSerializedOffsetForRange(
        editor,
        range,
      );
    }
  }, []);

  const ensureInsertionTarget = useCallback(() => {
    setActiveTarget({
      id: insertionTargetId,
      kind: 'text',
      label: targetLabel,
    });
    clearMessage();
  }, [clearMessage, insertionTargetId, setActiveTarget, targetLabel]);

  const activateInsertionTarget = useCallback(() => {
    ensureInsertionTarget();
    rememberSelectionRange();
  }, [ensureInsertionTarget, rememberSelectionRange]);

  const getRememberedRange = useCallback(() => {
    const editor = editorRef.current;
    const range = lastSelectionRangeRef.current;
    if (!editor) return null;

    try {
      if (range && editor.contains(range.commonAncestorContainer)) {
        return range.cloneRange();
      }
    } catch {
      // Fall through to serialized offset restore.
    }

    if (lastSelectionOffsetRef.current === null) return null;

    restoreCaretFromSerializedOffset(editor, lastSelectionOffsetRef.current);
    const restoredRange = getActiveRange(editor);
    return restoredRange ? restoredRange.cloneRange() : null;
  }, []);

  const restoreCaretAfterRender = useCallback((offset: number | null) => {
    if (offset === null) return;

    requestAnimationFrame(() => {
      const editor = editorRef.current;
      if (!editor) return;
      editor.focus();
      restoreCaretFromSerializedOffset(editor, offset);
      const range = getActiveRange(editor);
      if (range) {
        lastSelectionRangeRef.current = range.cloneRange();
        lastSelectionOffsetRef.current = offset;
      }
    });
  }, []);

  const insertOutputToken = useCallback(
    (output: DraggedOutputVariable, range?: Range | null) => {
      const editor = editorRef.current;
      if (!editor) return false;

      const droppedName = onDropOutput?.(output) || output.key;
      const label = tokenLabels[droppedName] || output.label || droppedName;
      const tokenNode = createTokenNode(droppedName, label);
      const targetRange = range ?? getRememberedRange();
      const insertionOffset =
        targetRange && editor.contains(targetRange.commonAncestorContainer)
          ? getSerializedOffsetForRange(editor, targetRange)
          : serializeEditor(editor).length;
      const nextCaretOffset = insertionOffset + tokenToText(tokenNode).length;

      insertTokenAtRange(editor, tokenNode, targetRange);
      pendingCaretOffsetRef.current = nextCaretOffset;
      emitChange();
      rememberSelectionRange();
      restoreCaretAfterRender(nextCaretOffset);
      return true;
    },
    [
      emitChange,
      getRememberedRange,
      onDropOutput,
      rememberSelectionRange,
      restoreCaretAfterRender,
      tokenLabels,
    ],
  );

  const insertOutputTokenRef = useRef(insertOutputToken);

  useEffect(() => {
    insertOutputTokenRef.current = insertOutputToken;
  }, [insertOutputToken]);

  useEffect(() => {
    if (!insertOutputRequest) return;
    insertOutputTokenRef.current(insertOutputRequest.output);
  }, [insertOutputRequest]);

  useEffect(
    () =>
      registerTarget(
        {
          id: insertionTargetId,
          kind: 'text',
          label: targetLabel,
        },
        (output) => insertOutputTokenRef.current(output),
      ),
    [insertionTargetId, registerTarget, targetLabel],
  );

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    const currentValue = serializeEditor(editor);
    const tokenLabelsSignature = JSON.stringify(tokenLabels);
    const shouldRender =
      lastRenderedValueRef.current === null ||
      lastTokenLabelsRef.current !== tokenLabelsSignature ||
      (value !== lastRenderedValueRef.current && value !== currentValue);

    if (shouldRender) {
      const activeRange = getActiveRange(editor);
      const caretOffsetBeforeRender =
        pendingCaretOffsetRef.current ??
        (activeRange ? getSerializedOffsetForRange(editor, activeRange) : null);

      renderSegments(editor, value, tokenLabels);
      syncPlaceholder();
      restoreCaretAfterRender(caretOffsetBeforeRender);
      pendingCaretOffsetRef.current = null;
    }

    lastRenderedValueRef.current = value;
    lastTokenLabelsRef.current = tokenLabelsSignature;
  }, [restoreCaretAfterRender, syncPlaceholder, value, tokenLabels]);

  useEffect(() => {
    syncPlaceholder();
  }, [syncPlaceholder]);

  const handleInput = () => {
    if (composingRef.current) return;
    ensureInsertionTarget();
    emitChange();
    rememberSelectionRange();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const editor = editorRef.current;
    if (!editor) return;

    ensureInsertionTarget();

    if (event.key === 'Backspace') {
      const token = getAdjacentToken(editor, 'backward');
      if (!token) return;
      event.preventDefault();
      const previous = token.previousSibling;
      token.remove();
      if (previous) placeCaretAfter(previous);
      emitChange();
      return;
    }

    if (event.key === 'Delete') {
      const token = getAdjacentToken(editor, 'forward');
      if (!token) return;
      event.preventDefault();
      const next = token.nextSibling;
      token.remove();
      if (next) placeCaretBefore(next);
      emitChange();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      insertNodeAtSelection(editor, createTextNode('\n'));
      emitChange();
    }
  };

  const handleBeforeInput = (event: React.FormEvent<HTMLDivElement>) => {
    const nativeEvent = event.nativeEvent as InputEvent;
    if (nativeEvent.data !== '{') return;

    const editor = editorRef.current;
    if (!editor) return;

    const range = getActiveRange(editor);
    if (!range) return;

    if (getCharacterBeforeRange(editor, range) === '{') {
      event.preventDefault();
    }
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
    const editor = editorRef.current;
    if (!editor) return;

    ensureInsertionTarget();
    event.preventDefault();
    const text = event.clipboardData
      .getData('text/plain')
      .replaceAll('{{', '{ {')
      .replaceAll('}}', '} }');
    insertNodeAtSelection(editor, createTextNode(text));
    emitChange();
    rememberSelectionRange();
  };

  const handleRememberActiveSelection = () => {
    ensureInsertionTarget();
    rememberSelectionRange();
  };

  return (
    <div
      className={cn(
        'relative rounded border border-gray-300 bg-white text-sm text-gray-800 focus-within:border-blue-500 focus-within:outline-none',
        isActiveInsertionTarget && 'ring-2 ring-blue-100',
        className,
      )}
    >
      {placeholder && (
        <div
          className={cn(
            'pointer-events-none absolute left-2 top-2 whitespace-pre-wrap text-sm leading-6 text-gray-400',
            !isEmpty && 'hidden',
          )}
        >
          {placeholder}
        </div>
      )}
      <div
        ref={editorRef}
        role="textbox"
        aria-label={ariaLabel}
        aria-multiline="true"
        contentEditable
        suppressContentEditableWarning
        className="min-h-[inherit] whitespace-pre-wrap break-words px-2 py-2 leading-6 outline-none"
        onBlur={() => {
          const editor = editorRef.current;
          if (editor) moveCaretAwayFromToken(editor);
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
          ensureInsertionTarget();
          emitChange();
          rememberSelectionRange();
        }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onBeforeInput={handleBeforeInput}
        onInput={handleInput}
        onClick={activateInsertionTarget}
        onFocus={activateInsertionTarget}
        onKeyDown={handleKeyDown}
        onKeyUp={handleRememberActiveSelection}
        onMouseUp={handleRememberActiveSelection}
        onPaste={handlePaste}
      />
    </div>
  );
};
