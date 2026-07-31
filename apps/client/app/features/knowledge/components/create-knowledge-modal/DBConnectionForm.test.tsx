import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import DBConnectionForm from './DBConnectionForm';

afterEach(() => {
  vi.useRealTimers();
});

describe('DBConnectionForm connector test boundary', () => {
  it('starts without example credentials or SSH enabled', () => {
    const { container } = render(
      <DBConnectionForm onChange={vi.fn()} onTestConnection={vi.fn()} />,
    );

    const credentialInputs = Array.from(
      container.querySelectorAll<HTMLInputElement>(
        'input:not([type="number"]):not([type="checkbox"])',
      ),
    );
    expect(credentialInputs.length).toBeGreaterThan(0);
    expect(credentialInputs.every((input) => input.value === '')).toBe(true);
    expect(
      screen.getByRole('checkbox', { name: 'SSH 사용' }),
    ).not.toBeChecked();
    expect(screen.getByPlaceholderText('db.example.com')).toHaveValue('');
  });

  it('blocks duplicate clicks while pending', async () => {
    let resolveTest: ((value: { success: boolean }) => void) | undefined;
    const onTestConnection = vi.fn(
      () =>
        new Promise<{ success: boolean }>((resolve) => {
          resolveTest = resolve;
        }),
    );
    render(
      <DBConnectionForm
        onChange={vi.fn()}
        onTestConnection={onTestConnection}
      />,
    );

    const button = screen.getByRole('button', { name: '연결 테스트' });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onTestConnection).toHaveBeenCalledTimes(1);

    await act(async () => resolveTest?.({ success: true }));
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('enforces bounded Retry-After cooldown', async () => {
    vi.useFakeTimers();
    const onTestConnection = vi
      .fn()
      .mockResolvedValue({ success: false, retryAfter: 2 });
    render(
      <DBConnectionForm
        onChange={vi.fn()}
        onTestConnection={onTestConnection}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '연결 테스트' }));
    await act(async () => Promise.resolve());
    expect(
      screen.getByRole('button', { name: '2초 후 재시도' }),
    ).toBeDisabled();

    act(() => vi.advanceTimersByTime(1000));
    expect(
      screen.getByRole('button', { name: '1초 후 재시도' }),
    ).toBeDisabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(
      screen.getByRole('button', { name: '연결 테스트' }),
    ).not.toBeDisabled();
  });
});
