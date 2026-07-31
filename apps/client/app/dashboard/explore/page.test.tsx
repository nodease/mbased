import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ExplorePage from './page';
import { appApi } from '@/app/features/app/api/appApi';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/app/features/app/api/appApi', () => ({
  appApi: {
    getExploreApps: vi.fn(),
    cloneApp: vi.fn(),
  },
}));

vi.mock('./components/AppGraphModal', () => ({
  AppGraphModal: () => null,
}));

describe('ExplorePage title', () => {
  beforeEach(() => {
    vi.mocked(appApi.getExploreApps).mockResolvedValue([]);
  });

  it('마켓플레이스 제목 왼쪽에 검색 아이콘을 표시한다', async () => {
    render(<ExplorePage />);

    const title = await screen.findByRole('heading', {
      level: 1,
      name: '마켓플레이스',
    });
    expect(title.firstElementChild).toHaveClass('lucide-search');
  });
});
