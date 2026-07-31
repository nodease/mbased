import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import StatisticsPage from './page';

vi.mock('./components/MonitoringTab', () => ({
  MonitoringTab: () => null,
}));

vi.mock('./components/LogTab', () => ({
  LogTab: () => null,
}));

describe('StatisticsPage title', () => {
  it('통계 제목 왼쪽에 통계 아이콘을 표시한다', () => {
    render(<StatisticsPage />);

    const title = screen.getByRole('heading', { level: 1, name: '통계' });
    expect(title.firstElementChild).toHaveClass('lucide-chart-column');
  });
});
