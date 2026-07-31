import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { KnowledgeSelectionControl } from './KnowledgeSelectionControl';

const candidates = Array.from({ length: 25 }, (_, index) => ({
  candidate_id: `candidate-${index + 1}`,
  label: `Knowledge ${index + 1}`,
  confidence: 1 - index / 100,
}));

describe('KnowledgeSelectionControl', () => {
  it('blocks flat-only direct responses instead of rendering a second KB UI', () => {
    const onSubmit = vi.fn();
    const onSubmitHierarchy = vi.fn();
    render(
      <KnowledgeSelectionControl
        candidates={candidates.slice(0, 3)}
        collections={[]}
        ungroupedKbs={[]}
        onSubmit={onSubmit}
        onSubmitHierarchy={onSubmitHierarchy}
      />,
    );

    expect(screen.queryByLabelText('Knowledge 1')).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Knowledge 계층 정보를 불러오지 못했습니다.',
    );
    expect(
      screen.getByRole('button', { name: 'Knowledge Base 없이 생성' }),
    ).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onSubmitHierarchy).not.toHaveBeenCalled();
  });

  it('prefers hierarchy data when hierarchy and flat candidates are both present', () => {
    render(
      <KnowledgeSelectionControl
        candidates={candidates.slice(0, 1)}
        collections={[
          {
            collection_handle: 'collection-a',
            safe_label: '사내 문서 Collection',
            children: [],
          },
        ]}
        onSubmit={vi.fn()}
        onSubmitHierarchy={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('사내 문서 Collection')).toBeInTheDocument();
    expect(screen.queryByLabelText('Knowledge 1')).toBeNull();
  });

  it('selects every child with a Collection and becomes partial when one child is cleared', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={[
          {
            collection_handle: 'collection-a',
            safe_label: '사내 문서 Collection',
            score: 0.84,
            children: [
              {
                kb_handle: 'kb-1',
                selection_key: 'shared-kb-1',
                safe_label: '사내 인사 KB',
                score: 0.9,
                shared_collection_count: 2,
              },
              {
                kb_handle: 'kb-2',
                selection_key: 'kb-2',
                safe_label: '사내 복지 KB',
                score: 0.8,
              },
            ],
          },
          {
            collection_handle: 'collection-b',
            safe_label: '경영 문서 Collection',
            score: 0.72,
            children: [
              {
                kb_handle: 'kb-1',
                selection_key: 'shared-kb-1',
                safe_label: '사내 인사 KB',
                score: 0.9,
                shared_collection_count: 2,
              },
            ],
          },
        ]}
        onSubmitHierarchy={onSubmit}
        onSubmit={vi.fn()}
      />,
    );

    const collectionCheckbox = screen.getByLabelText('사내 문서 Collection');
    fireEvent.click(collectionCheckbox);
    expect(collectionCheckbox).toBeChecked();
    expect(screen.getAllByLabelText('사내 인사 KB')[0]).toBeChecked();
    expect(screen.getByLabelText('사내 복지 KB')).toBeChecked();
    expect(screen.getByLabelText('경영 문서 Collection')).not.toBeChecked();

    fireEvent.click(screen.getByLabelText('사내 복지 KB'));
    expect(collectionCheckbox).not.toBeChecked();
    expect((collectionCheckbox as HTMLInputElement).indeterminate).toBe(true);
    expect(screen.getByText('1/2 선택')).toBeInTheDocument();
    expect(screen.getAllByLabelText('사내 인사 KB')[0]).toBeChecked();
    expect(screen.getAllByLabelText('사내 인사 KB')[1]).toBeChecked();
    expect(screen.getByLabelText('사내 복지 KB')).not.toBeChecked();

    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );
    expect(onSubmit).toHaveBeenCalledWith({
      collectionHandles: [],
      kbHandles: ['kb-1'],
    });
  });

  it('toggles a partially selected Collection between all children and none', () => {
    render(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={[
          {
            collection_handle: 'collection-a',
            safe_label: 'Internal documents',
            children: [
              {
                kb_handle: 'kb-1',
                selection_key: 'kb-1',
                safe_label: 'HR KB',
              },
              {
                kb_handle: 'kb-2',
                selection_key: 'kb-2',
                safe_label: 'Benefits KB',
              },
            ],
          },
        ]}
        onSubmitHierarchy={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const collection = screen.getByLabelText('Internal documents');
    const hrKb = screen.getByLabelText('HR KB');
    const benefitsKb = screen.getByLabelText('Benefits KB');

    fireEvent.click(hrKb);
    expect(collection).not.toBeChecked();
    expect((collection as HTMLInputElement).indeterminate).toBe(true);

    fireEvent.click(collection);
    expect(collection).toBeChecked();
    expect(hrKb).toBeChecked();
    expect(benefitsKb).toBeChecked();

    fireEvent.click(collection);
    expect(collection).not.toBeChecked();
    expect((collection as HTMLInputElement).indeterminate).toBe(false);
    expect(hrKb).not.toBeChecked();
    expect(benefitsKb).not.toBeChecked();
  });

  it('submits the Collection and every permission-visible child when fully selected', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={[
          {
            collection_handle: 'collection-a',
            safe_label: '사내 문서 Collection',
            children: [
              {
                kb_handle: 'kb-1',
                selection_key: 'kb-1',
                safe_label: '인사 KB',
              },
              {
                kb_handle: 'kb-2',
                selection_key: 'kb-2',
                safe_label: '복지 KB',
              },
            ],
          },
        ]}
        onSubmitHierarchy={onSubmit}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('사내 문서 Collection'));
    fireEvent.click(
      screen.getByRole('button', { name: '선택한 Knowledge로 생성' }),
    );

    expect(onSubmit).toHaveBeenCalledWith({
      collectionHandles: ['collection-a'],
      kbHandles: ['kb-1', 'kb-2'],
    });
  });

  it('submits an explicit empty hierarchy selection independently from checked values', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl
        timing="after_graph"
        candidates={[]}
        collections={[
          {
            collection_handle: 'collection-a',
            safe_label: '사내 문서 Collection',
            score: 0.8,
            children: [],
          },
        ]}
        onSubmitHierarchy={onSubmit}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('사내 문서 Collection'));
    fireEvent.click(
      screen.getByRole('button', { name: 'Knowledge Base 없이 계속' }),
    );

    expect(onSubmit).toHaveBeenCalledWith({
      collectionHandles: [],
      kbHandles: [],
    });
  });
  it('explains that multiple candidates are displayed in recommendation order', () => {
    render(
      <KnowledgeSelectionControl
        candidates={candidates.slice(0, 3)}
        onSubmit={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        '추천 점수 내림차순으로 표시됩니다. Collection은 실행 시 자동 라우팅하고, 하위 Knowledge Base는 Workflow에 직접 고정합니다.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('1순위')).toBeInTheDocument();
    expect(screen.getByText('3순위')).toBeInTheDocument();
  });

  it('limits candidates to 20 and submits multiple selections', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl candidates={candidates} onSubmit={onSubmit} />,
    );

    expect(screen.getAllByRole('checkbox')).toHaveLength(20);
    fireEvent.click(screen.getByLabelText('Knowledge 1'));
    fireEvent.click(screen.getByLabelText('Knowledge 2'));
    expect(
      screen.getByRole('button', {
        name: '\uC120\uD0DD\uD55C Knowledge Base\uB85C \uC0DD\uC131',
      }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: '\uC120\uD0DD\uD55C Knowledge Base\uB85C \uC0DD\uC131',
      }),
    );

    expect(onSubmit).toHaveBeenCalledWith(['candidate-1', 'candidate-2']);
  });

  it('limits hierarchical Collections and unique KB candidates to 20', () => {
    const collections = Array.from({ length: 25 }, (_, index) => ({
      collection_handle: `collection-${index + 1}`,
      safe_label: `Collection ${index + 1}`,
      score: 1 - index / 100,
      children: [
        {
          kb_handle: `kb-${index + 1}`,
          selection_key: `kb-key-${index + 1}`,
          safe_label: `KB ${index + 1}`,
          score: 1 - index / 100,
        },
      ],
    }));

    const { container } = render(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={collections}
        ungroupedKbs={[
          {
            kb_handle: 'kb-ungrouped',
            selection_key: 'kb-key-ungrouped',
            safe_label: 'Ungrouped KB',
            score: 0.1,
          },
        ]}
        onSubmit={vi.fn()}
        onSubmitHierarchy={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Collection 20')).toBeInTheDocument();
    expect(screen.queryByLabelText('Collection 21')).toBeNull();
    expect(screen.getByLabelText('KB 20')).toBeInTheDocument();
    expect(screen.queryByLabelText('KB 21')).toBeNull();
    expect(screen.queryByLabelText('Ungrouped KB')).toBeNull();
    expect(
      Array.from(container.querySelectorAll('input[type="checkbox"]')),
    ).toHaveLength(40);
    expect(
      Array.from(container.querySelectorAll('div')).some((element) =>
        element.className.includes('max-h-[156px]'),
      ),
    ).toBe(true);
  });

  it('submits an empty array when no candidate is selected', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl candidates={candidates} onSubmit={onSubmit} />,
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Knowledge Base \uC5C6\uC774 \uC0DD\uC131',
      }),
    );

    expect(onSubmit).toHaveBeenCalledWith([]);
  });

  it('hydrates a persisted candidate id into the current selection id', () => {
    const onSubmit = vi.fn();
    render(
      <KnowledgeSelectionControl
        timing="after_graph"
        candidates={[
          {
            candidate_id: 'candidate-1',
            selection_id: 'candidate-1:resolution-1:requirement-1',
            label: 'Knowledge 1',
          },
        ]}
        initialSelectedIds={['candidate-1']}
        disabled
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByLabelText('Knowledge 1')).toBeChecked();
    expect(screen.getByLabelText('Knowledge 1')).toBeDisabled();
    expect(screen.getByRole('button', { name: '선택 적용' })).toBeDisabled();
  });

  it('keeps the selected candidates visible when an apply attempt fails', () => {
    const { rerender } = render(
      <KnowledgeSelectionControl
        candidates={candidates.slice(0, 2)}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('Knowledge 1'));
    rerender(
      <KnowledgeSelectionControl
        candidates={candidates.slice(0, 2)}
        errorMessage="Knowledge Base 선택을 적용하지 못했습니다. 현재 선택은 유지됩니다. 다시 시도해주세요."
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Knowledge 1')).toBeChecked();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Knowledge Base 선택을 적용하지 못했습니다. 현재 선택은 유지됩니다. 다시 시도해주세요.',
    );
    expect(
      screen.getByRole('button', { name: '선택한 Knowledge Base로 생성' }),
    ).toBeEnabled();
  });

  it('clears local hierarchy checks after an explicit stale refresh even when handles are unchanged', () => {
    const hierarchy = [
      {
        collection_handle: 'collection-a',
        safe_label: '사내 문서',
        children: [
          {
            kb_handle: 'kb-a',
            selection_key: 'kb-a',
            safe_label: '인사 KB',
          },
        ],
      },
    ];
    const { rerender } = render(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={hierarchy}
        resetVersion={0}
        onSubmit={vi.fn()}
        onSubmitHierarchy={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('사내 문서'));
    expect(screen.getByLabelText('사내 문서')).toBeChecked();
    expect(screen.getByLabelText('인사 KB')).toBeChecked();

    rerender(
      <KnowledgeSelectionControl
        candidates={[]}
        collections={[
          {
            ...hierarchy[0],
            safe_label: '사내 문서 최신',
            children: [
              { ...hierarchy[0].children[0], safe_label: '인사 KB 최신' },
            ],
          },
        ]}
        resetVersion={1}
        onSubmit={vi.fn()}
        onSubmitHierarchy={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('사내 문서 최신')).not.toBeChecked();
    expect(screen.getByLabelText('인사 KB 최신')).not.toBeChecked();
  });

  it('shows the four timing-specific CTA labels', () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <KnowledgeSelectionControl
        timing="before_graph"
        candidates={candidates}
        onSubmit={onSubmit}
      />,
    );

    expect(
      screen.getByRole('button', {
        name: 'Knowledge Base \uC5C6\uC774 \uC0DD\uC131',
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Knowledge 1'));
    expect(
      screen.getByRole('button', {
        name: '\uC120\uD0DD\uD55C Knowledge Base\uB85C \uC0DD\uC131',
      }),
    ).toBeInTheDocument();

    rerender(
      <KnowledgeSelectionControl
        timing="after_graph"
        candidates={candidates}
        onSubmit={onSubmit}
      />,
    );
    expect(
      screen.getByRole('button', {
        name: 'Knowledge Base \uC5C6\uC774 \uACC4\uC18D',
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Knowledge 1'));
    expect(
      screen.getByRole('button', { name: '\uC120\uD0DD \uC801\uC6A9' }),
    ).toBeInTheDocument();
  });
});
