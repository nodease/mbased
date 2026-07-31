import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import Breadcrumb from './Breadcrumb';

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard/mymodule',
}));

describe('Breadcrumb', () => {
  it('워크플로우 목록 경로 이름을 표시한다', async () => {
    render(<Breadcrumb />);

    expect(await screen.findByText('워크플로우 목록')).toBeInTheDocument();
  });
});
