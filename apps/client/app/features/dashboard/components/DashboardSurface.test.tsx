import { render, screen } from '@testing-library/react';
import { Workflow } from 'lucide-react';
import { describe, expect, it } from 'vitest';

import { DashboardPageHeader } from './DashboardSurface';

describe('DashboardPageHeader', () => {
  it('페이지 제목 왼쪽에 지정한 사이드바 아이콘을 표시한다', () => {
    render(
      <DashboardPageHeader
        icon={Workflow}
        title="워크플로우"
        description="워크플로우 운영 상태를 확인합니다."
      />,
    );

    const heading = screen.getByRole('heading', {
      level: 1,
      name: '워크플로우',
    });

    expect(heading).toHaveClass('text-2xl');
    expect(heading.firstElementChild).toHaveClass('lucide-workflow');
  });
});
