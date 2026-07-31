import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VariableInsertionProvider } from './VariableInsertionProvider';
import { useVariableInsertion } from './useVariableInsertion';

const target = {
  id: 'prompt-target',
  kind: 'text' as const,
  label: '사용자 프롬프트',
};

const output = {
  key: 'customer_message',
  label: '고객 문의',
  sourceNodeId: 'start-customer',
  sourceTitle: '고객 문의 입력',
  dataType: 'string' as const,
};

function TestTarget({ version }: { version: number }) {
  const {
    activeTarget,
    message,
    registerTarget,
    setActiveTarget,
    insertOutput,
  } = useVariableInsertion();

  React.useEffect(
    () => registerTarget(target, () => true),
    [registerTarget, version],
  );

  return (
    <div>
      <div data-testid="active-target">{activeTarget?.label || 'none'}</div>
      <div data-testid="message">{message || 'none'}</div>
      <button type="button" onClick={() => setActiveTarget(target)}>
        activate
      </button>
      <button type="button" onClick={() => insertOutput(output)}>
        insert
      </button>
    </div>
  );
}

describe('VariableInsertionProvider', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('keeps active target when the same target is re-registered', () => {
    vi.useFakeTimers();

    const { rerender } = render(
      <VariableInsertionProvider>
        <TestTarget version={1} />
      </VariableInsertionProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'activate' }));
    expect(screen.getByTestId('active-target')).toHaveTextContent(
      '사용자 프롬프트',
    );

    rerender(
      <VariableInsertionProvider>
        <TestTarget version={2} />
      </VariableInsertionProvider>,
    );

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('active-target')).toHaveTextContent(
      '사용자 프롬프트',
    );

    fireEvent.click(screen.getByRole('button', { name: 'insert' }));
    expect(screen.getByTestId('message')).toHaveTextContent(
      '사용자 프롬프트에 변수를 추가했습니다.',
    );
  });
});
