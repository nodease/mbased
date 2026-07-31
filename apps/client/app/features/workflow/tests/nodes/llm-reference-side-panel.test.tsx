import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LLMNodeData } from '../../types/Nodes';
import { LLMReferenceSidePanel } from '../../components/nodes/llm/components/LLMReferenceSidePanel';

const knowledgeApiMock = vi.hoisted(() => ({
  getLLMSelectableKnowledgeBases: vi.fn(),
  getLLMSelectableKnowledgeCollections: vi.fn(),
  getKnowledgeBase: vi.fn(),
}));
const updateNodeDataMock = vi.hoisted(() => vi.fn());

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: knowledgeApiMock,
}));

vi.mock('@/app/features/workflow/store/useWorkflowStore', () => ({
  useWorkflowStore: () => ({ updateNodeData: updateNodeDataMock }),
}));

const baseData = (updates: Partial<LLMNodeData> = {}): LLMNodeData => ({
  title: 'LLM',
  provider: 'openai',
  model_id: 'model-1',
  referenced_variables: [],
  parameters: {},
  ...updates,
});

const renderPanel = (data: LLMNodeData, onDataChange = vi.fn()) => {
  render(
    <LLMReferenceSidePanel
      nodeId="llm-1"
      data={data}
      onClose={() => undefined}
      embedded
      onDataChange={onDataChange}
    />,
  );
  return onDataChange;
};

describe('LLMReferenceSidePanel Knowledge selection', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('keeps saved refs on successful empty responses and hides stale labels', async () => {
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockResolvedValueOnce([]);
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockResolvedValueOnce(
      { collections: [] },
    );
    const onDataChange = renderPanel(
      baseData({
        knowledgeBases: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            name: '노출하면 안 되는 KB 이름',
          },
        ],
        knowledgeCollections: [
          {
            id: '22222222-2222-2222-2222-222222222222',
            safeLabel: '노출하면 안 되는 Collection 이름',
          },
        ],
      }),
    );

    expect(await screen.findByText('사용할 수 없는 지식')).toBeInTheDocument();
    expect(
      await screen.findByText('사용할 수 없는 Collection'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('노출하면 안 되는 KB 이름'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('노출하면 안 되는 Collection 이름'),
    ).not.toBeInTheDocument();
    expect(onDataChange).not.toHaveBeenCalled();
  });

  it('stores a Collection reference without expanding members', async () => {
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockResolvedValueOnce([]);
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockResolvedValueOnce(
      {
        collections: [
          {
            id: '33333333-3333-3333-3333-333333333333',
            safe_label: '사내 문서',
          },
        ],
      },
    );
    const onDataChange = renderPanel(baseData());

    fireEvent.click(await screen.findByRole('checkbox', { name: /사내 문서/ }));

    expect(onDataChange).toHaveBeenCalledWith({
      knowledgeCollections: [
        {
          id: '33333333-3333-3333-3333-333333333333',
          safeLabel: '사내 문서',
        },
      ],
    });
    expect(JSON.stringify(onDataChange.mock.calls)).not.toContain('members');
  });

  it('routes a matching Agent Builder Knowledge edit without changing node data first', async () => {
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockResolvedValue([
      {
        id: '11111111-1111-1111-1111-111111111111',
        name: '휴가 정책',
        documents: [
          {
            id: 'document-1',
            status: 'completed',
            chunk_count: 1,
          },
        ],
      },
    ]);
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockResolvedValue(
      { collections: [] },
    );
    const onDataChange = renderPanel(baseData());
    const received: Array<Record<string, unknown>> = [];
    const listener = (event: Event) => {
      const detail = (
        event as CustomEvent<Record<string, unknown> & { handled: boolean }>
      ).detail;
      detail.handled = true;
      received.push(detail);
    };
    window.addEventListener(
      'agent-builder:knowledge-selection-from-node',
      listener,
    );

    try {
      fireEvent.click(
        await screen.findByRole('checkbox', { name: /휴가 정책/ }),
      );
    } finally {
      window.removeEventListener(
        'agent-builder:knowledge-selection-from-node',
        listener,
      );
    }

    expect(received).toEqual([
      expect.objectContaining({
        nodeId: 'llm-1',
        knowledgeBases: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            name: '휴가 정책',
          },
        ],
        knowledgeCollections: [],
        handled: true,
      }),
    ]);
    expect(onDataChange).not.toHaveBeenCalled();
  });

  it('retains selections when either picker request fails', async () => {
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockRejectedValueOnce(
      new Error('private failure'),
    );
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockRejectedValueOnce(
      new Error('private failure'),
    );
    const onDataChange = renderPanel(
      baseData({
        knowledgeBases: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            name: '기존 KB',
          },
        ],
        knowledgeCollections: [{ id: '22222222-2222-2222-2222-222222222222' }],
      }),
    );

    expect(
      await screen.findByText('지식을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    expect(
      await screen.findByText('Collection을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/기존 선택 1개는/)).toHaveLength(2);
    expect(onDataChange).not.toHaveBeenCalled();
  });

  it('disables a 21st Collection candidate at the independent 20-item limit', async () => {
    const collections = Array.from({ length: 21 }, (_, index) => ({
      id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}`,
      safe_label: `Collection ${index + 1}`,
    }));
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockResolvedValueOnce([]);
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockResolvedValueOnce(
      { collections },
    );
    renderPanel(
      baseData({
        knowledgeCollections: collections.slice(0, 20).map((item) => ({
          id: item.id,
          safeLabel: item.safe_label,
        })),
      }),
    );

    const twentyFirst = await screen.findByRole('checkbox', {
      name: /Collection 21/,
    });

    await waitFor(() => expect(twentyFirst).toBeDisabled());
  });

  it('legacy Citation 설정은 숨김으로 닫고 Grounding과 독립적으로 편집한다', async () => {
    knowledgeApiMock.getLLMSelectableKnowledgeBases.mockResolvedValueOnce([]);
    knowledgeApiMock.getLLMSelectableKnowledgeCollections.mockResolvedValueOnce({
      collections: [],
    });
    const onDataChange = renderPanel(
      baseData({ answerGroundingCheck: 'strict' }),
    );

    expect(await screen.findByLabelText('출처 표시')).toHaveValue('hidden');
    expect(
      screen.getByLabelText('답변·검색 문서 어휘 일치도'),
    ).toHaveValue('strict');
    expect(
      screen.getByText(/출처 표시나 답변 차단 기능은 아닙니다/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('출처 표시'), {
      target: { value: 'basic' },
    });
    expect(onDataChange).toHaveBeenCalledWith({ citationDisplayMode: 'basic' });
  });
});
