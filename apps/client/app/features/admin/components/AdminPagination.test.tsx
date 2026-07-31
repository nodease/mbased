import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AdminPagination } from './AdminPagination';

describe('AdminPagination', () => {
  it('hasNext가 있으면 totalPages 끝에서도 다음 cursor 페이지로 이동한다', () => {
    const onPageChange = vi.fn();

    render(
      <AdminPagination
        page={2}
        totalPages={2}
        total={40}
        hasNext
        onPageChange={onPageChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '다음' }));

    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});
