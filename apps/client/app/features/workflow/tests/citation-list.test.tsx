import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CitationList } from '../components/execution/CitationList';

describe('CitationList', () => {
  it('빈 Citation은 영역을 표시하지 않는다', () => {
    const { container } = render(<CitationList items={[]} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('접근 가능한 summary와 상세 preview를 표시한다', () => {
    render(
      <CitationList
        items={[
          {
            citationId: 'evidence-1',
            evidenceRank: 1,
            label: '공통 인사·휴가 정책 문서',
            pageNumber: 3,
            section: '연차 신청',
            contentPreview: '연차는 사전에 신청합니다.',
          },
        ]}
      />,
    );

    const summary = screen.getByText('참조 문서 (1)').closest('summary');
    expect(summary).not.toBeNull();
    expect(summary).toHaveClass('dark:text-gray-200');
    fireEvent.click(summary!);
    expect(screen.getByRole('list', { name: '답변 참조 문서' })).toBeVisible();
    expect(screen.getByText('공통 인사·휴가 정책 문서')).toHaveClass(
      'dark:text-gray-100',
    );
    expect(screen.getByText('3쪽')).toBeVisible();
    expect(screen.getByText('연차는 사전에 신청합니다.')).toBeVisible();
  });

  it('내부 챗봇 appearance는 라이트 전용 스타일만 렌더링한다', () => {
    const { container } = render(
      <CitationList
        appearance="chat"
        items={[
          {
            citationId: 'evidence-1',
            evidenceRank: 1,
            label: '개발팀 온보딩 문서',
            pageNumber: 3,
            section: '보상 기준',
            contentPreview: '신입 보상 기준은 직무 등급에 따라 결정합니다.',
          },
        ]}
      />,
    );
    const darkClassNames = Array.from(
      container.querySelectorAll<HTMLElement>('[class]'),
    ).flatMap((element) =>
      Array.from(element.classList).filter((className) =>
        className.startsWith('dark:'),
      ),
    );

    expect(darkClassNames).toEqual([]);
  });
});
