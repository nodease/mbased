import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getNodeDefinition } from '../../config/nodeRegistry';
import { NodeSettingsComparisonPanel } from '../../components/costOptimizer/NodeSettingsComparisonPanel';
import { LLMReferenceSidePanel } from '../../components/nodes/llm/components/LLMReferenceSidePanel';
import { candidateFromOptions } from '../../components/costOptimizer/costOptimizerPlaygroundModel';
import { fetchEligibleKnowledgeBases } from '@/app/features/workflow/utils/llmKnowledgeBaseSelection';
import type { CandidateDraft } from '../../components/costOptimizer/costOptimizerPlaygroundModel';
import type { LLMNodeData } from '../../types/Nodes';

const updateNodeDataMock = vi.hoisted(() => vi.fn());

vi.mock('@/app/features/workflow/store/useWorkflowStore', () => ({
  useWorkflowStore: () => ({
    updateNodeData: updateNodeDataMock,
  }),
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    getKnowledgeBase: vi.fn(),
  },
}));

vi.mock('@/app/features/workflow/utils/llmKnowledgeBaseSelection', () => ({
  MAX_CONFIGURED_KNOWLEDGE_REFERENCES: 20,
  fetchEligibleKnowledgeBases: vi.fn().mockResolvedValue({
    bases: [],
    detailsById: {},
  }),
  fetchEligibleKnowledgeCollections: vi.fn().mockResolvedValue([]),
  sanitizeSelectedKnowledgeBases: (selected: unknown) => selected,
  sanitizeSelectedKnowledgeCollections: (selected: unknown) => selected,
  isSameKnowledgeSelection: () => true,
  isSameKnowledgeCollectionSelection: () => true,
}));

const baseData: LLMNodeData = {
  title: 'LLM',
  provider: 'openai',
  model_id: 'gpt-4.1',
  system_prompt: 'system',
  user_prompt: 'user',
  assistant_prompt: '',
  referenced_variables: [],
  parameters: {},
  knowledgeBases: [],
  topK: 3,
  scoreThreshold: 0.5,
};

const baseDraft: CandidateDraft = {
  model_id: 'gpt-4.1',
  fallback_model_id: '',
  auto_model_routing: false,
  model_routing_policy: undefined,
  task_type: 'generate',
  system_prompt: 'system',
  user_prompt: 'user',
  assistant_prompt: '',
  referenced_variables: [],
  max_tokens: 1024,
  temperature: 0.3,
  top_p: 1,
  presence_penalty: 0,
  frequency_penalty: 0,
  stop: [],
  output_format: 'text',
  json_schema_fields: [],
  knowledgeBases: [],
  topK: 3,
  scoreThreshold: 0.5,
  dedupeRetrievedContext: false,
  retrievedContextMaxChars: null,
  retrievedContextCompression: 'off',
  answerGroundingCheck: 'off',
};

describe('FR-003 RAG cost optimization options', () => {
  afterEach(() => {
    cleanup();
    updateNodeDataMock.mockClear();
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValue({
      bases: [],
      detailsById: {},
    });
  });

  it('새 LLM 노드는 RAG 비용 최적화 옵션 기본값을 가진다', () => {
    const llmNodeDefinition = getNodeDefinition('llm');
    const defaultData = llmNodeDefinition?.defaultData?.() as LLMNodeData;

    expect(defaultData).toEqual(
      expect.objectContaining({
        scoreThreshold: 0.3,
        topK: 5,
        dedupeRetrievedContext: false,
        retrievedContextMaxChars: undefined,
        retrievedContextCompression: 'off',
        answerGroundingCheck: 'basic',
        citationDisplayMode: 'detailed',
      }),
    );
  });

  it('값이 없는 기존 LLM 노드와 비용 최적화 후보는 새 검색 기본값을 사용한다', async () => {
    render(
      <LLMReferenceSidePanel
        nodeId="llm-legacy-rag"
        data={{ ...baseData, scoreThreshold: undefined, topK: undefined }}
        onClose={vi.fn()}
        embedded
      />,
    );

    await waitFor(() => {
      const sliders = screen.getAllByRole('slider');
      expect(sliders[0]).toHaveValue('0.3');
      expect(sliders[1]).toHaveValue('5');
    });

    const draft = candidateFromOptions({});
    expect(draft.scoreThreshold).toBe(0.3);
    expect(draft.topK).toBe(5);
  });

  it('어휘 일치도 값이 없는 기존 LLM 노드는 기본을 선택한다', async () => {
    render(
      <LLMReferenceSidePanel
        nodeId="llm-legacy"
        data={{ ...baseData, answerGroundingCheck: undefined }}
        onClose={vi.fn()}
        embedded
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByLabelText('답변·검색 문서 어휘 일치도'),
      ).toHaveValue('basic'),
    );
  });

  it('지식 베이스 탭에서 RAG 비용 최적화 옵션을 편집한다', async () => {
    const onDataChange = vi.fn();

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
        onDataChange={onDataChange}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText('비용 최적화')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('checkbox', { name: /중복 근거 제거/ }));
    expect(onDataChange).toHaveBeenCalledWith({
      dedupeRetrievedContext: true,
    });

    fireEvent.change(screen.getByLabelText('참조 문서 길이 제한'), {
      target: { value: '6000' },
    });
    expect(onDataChange).toHaveBeenCalledWith({
      retrievedContextMaxChars: 6000,
    });

    fireEvent.change(screen.getByLabelText('검색 문서 압축'), {
      target: { value: 'light' },
    });
    expect(onDataChange).toHaveBeenCalledWith({
      retrievedContextCompression: 'light',
    });

    fireEvent.change(screen.getByLabelText('답변·검색 문서 어휘 일치도'), {
      target: { value: 'basic' },
    });
    expect(onDataChange).toHaveBeenCalledWith({
      answerGroundingCheck: 'basic',
    });

    fireEvent.change(screen.getByLabelText('출처 표시'), {
      target: { value: 'detailed' },
    });
    expect(onDataChange).toHaveBeenCalledWith({
      citationDisplayMode: 'detailed',
    });
  });

  it('일반 LLM 노드 지식 베이스 탭에서 선택한 RAG 연결을 노드 데이터에 반영한다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-1',
          name: '제품 정책',
          description: '제품 정책 문서',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {},
    });

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /제품 정책/ }));

    expect(updateNodeDataMock).toHaveBeenCalledWith('llm-1', {
      knowledgeBases: [{ id: 'kb-1', name: '제품 정책' }],
    });
  });

  it('일반 LLM 노드 지식 베이스 탭에서 여러 RAG 연결을 유지한다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValue({
      bases: [
        {
          id: 'kb-1',
          name: '제품 정책',
          description: '제품 정책 문서',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
        {
          id: 'kb-2',
          name: '개발팀 규칙',
          description: '개발팀 운영 문서',
          document_count: 2,
          created_at: '2026-07-02T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {},
    });

    const { rerender } = render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /제품 정책/ }));
    expect(updateNodeDataMock).toHaveBeenLastCalledWith('llm-1', {
      knowledgeBases: [{ id: 'kb-1', name: '제품 정책' }],
    });

    rerender(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={{
          ...baseData,
          knowledgeBases: [{ id: 'kb-1', name: '제품 정책' }],
        }}
        onClose={vi.fn()}
        embedded
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /개발팀 규칙/ }));
    expect(updateNodeDataMock).toHaveBeenLastCalledWith('llm-1', {
      knowledgeBases: [
        { id: 'kb-1', name: '제품 정책' },
        { id: 'kb-2', name: '개발팀 규칙' },
      ],
    });
  });

  it('완료된 문서가 있는 KB가 없으면 RAG 선택 후보를 비워 안내한다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [],
      detailsById: {},
    });

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    expect(
      await screen.findByText('완료된 문서가 있는 지식 베이스가 없습니다.'),
    ).toBeInTheDocument();
  });

  it('읽기 전용 LLM 노드에서는 RAG 연결 선택을 변경하지 않는다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-1',
          name: '제품 정책',
          description: '제품 정책 문서',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {},
    });

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
        readOnly
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /제품 정책/ }));

    expect(updateNodeDataMock).not.toHaveBeenCalled();
  });

  it('지식 베이스 목록 조회 실패 시 기존 RAG 연결을 지우지 않는다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockRejectedValueOnce(
      new Error('network failed'),
    );

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={{
          ...baseData,
          knowledgeBases: [{ id: 'kb-existing', name: '기존 정책' }],
        }}
        onClose={vi.fn()}
        embedded
      />,
    );

    expect(
      await screen.findByText('지식을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/기존 선택/)).toBeInTheDocument();
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);
    expect(updateNodeDataMock).not.toHaveBeenCalled();
  });

  it('지식 베이스 목록 조회 실패 시 raw 오류 내용을 화면에 노출하지 않는다', async () => {
    vi.mocked(fetchEligibleKnowledgeBases).mockRejectedValueOnce(
      new Error('provider failed with api_key=secret-like-value'),
    );

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    expect(
      await screen.findByText('지식을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/secret-like-value|api_key|provider failed/i),
    ).not.toBeInTheDocument();
  });

  it('특수문자가 포함된 지식 베이스 이름을 선택 payload에 보존한다', async () => {
    const kbName = 'R&D 정책 / 승인: <Beta>';
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-special',
          name: kbName,
          description: '특수문자 이름 테스트',
          document_count: 2,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {},
    });

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /R&D 정책/ }));

    expect(updateNodeDataMock).toHaveBeenCalledWith('llm-1', {
      knowledgeBases: [{ id: 'kb-special', name: kbName }],
    });
  });

  it('긴 지식 베이스 이름과 문서명을 패널 안에서 줄임 처리한다', async () => {
    const kbName =
      '사내문서: 아주 긴 개발팀 커밋 브랜치 PR 컨벤션 문서 제목 '.repeat(
        4,
      );
    const filename =
      '12345678-1234-1234-1234-123456789abc_' +
      '매우-긴-파일명-커밋-브랜치-PR-컨벤션-세부-운영-가이드.md'.repeat(
        3,
      );

    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-long',
          name: kbName,
          description: '긴 표시명 테스트',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {
        'kb-long': {
          id: 'kb-long',
          name: kbName,
          description: '긴 표시명 테스트',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
          documents: [
            {
              id: 'doc-long',
              filename,
              status: 'completed',
              created_at: '2026-07-01T00:00:00Z',
              updated_at: '2026-07-01T00:00:00Z',
              chunk_count: 7,
              token_count: 700,
            },
          ],
        },
      },
    });

    render(
      <LLMReferenceSidePanel
        nodeId="llm-1"
        data={baseData}
        onClose={vi.fn()}
        embedded
      />,
    );

    const kbLabel = await screen.findByText((content, element) => {
      return (
        element?.tagName === 'SPAN' && content.trim() === kbName.trim()
      );
    });
    expect(kbLabel).toHaveClass('truncate');

    fireEvent.click(screen.getByText('지식 목록 보기'));

    const fileLabel = await screen.findByText(
      /매우-긴-파일명-커밋-브랜치-PR-컨벤션/,
    );
    expect(fileLabel).toHaveClass('truncate');
  });

  it('모바일 폭에서도 다국어 지식 라벨은 키보드 포커스와 줄임 처리를 유지한다', async () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 360,
    });
    const kbName =
      '社内規程 개발팀 onboarding policy длинное-название '.repeat(3);
    const filename =
      '12345678-1234-1234-1234-123456789abc_' +
      '온보딩-規程-commit-convention-branch-review-long-name.md'.repeat(2);

    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-i18n',
          name: kbName,
          description: '다국어 표시명 테스트',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {
        'kb-i18n': {
          id: 'kb-i18n',
          name: kbName,
          description: '다국어 표시명 테스트',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
          documents: [
            {
              id: 'doc-i18n',
              filename,
              status: 'completed',
              created_at: '2026-07-01T00:00:00Z',
              updated_at: '2026-07-01T00:00:00Z',
              chunk_count: 2,
              token_count: 220,
            },
          ],
        },
      },
    });

    try {
      render(
        <LLMReferenceSidePanel
          nodeId="llm-1"
          data={baseData}
          onClose={vi.fn()}
          embedded
        />,
      );

      const checkbox = await screen.findByRole('checkbox', {
        name: /社内規程 개발팀 onboarding policy/,
      });
      checkbox.focus();
      expect(checkbox).toHaveFocus();

      const kbLabel = await screen.findByText((content, element) => {
        return (
          element?.tagName === 'SPAN' &&
          content.includes('社内規程 개발팀 onboarding policy')
        );
      });
      expect(kbLabel).toHaveClass('truncate');
      expect(kbLabel.closest('.min-w-0')).not.toBeNull();

      const expandButton = screen.getByRole('button', {
        name: /지식 목록 보기/,
      });
      expandButton.focus();
      expect(expandButton).toHaveFocus();
      fireEvent.click(expandButton);

      const fileLabel = await screen.findByText(/온보딩-規程/);
      expect(fileLabel).toHaveClass('truncate');
      expect(fileLabel).toHaveClass('flex-1');
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        value: originalInnerWidth,
      });
    }
  });

  it('A/B 후보 지식 베이스 탭에서 지식 베이스를 선택한다', async () => {
    const onNodeDataChange = vi.fn();
    vi.mocked(fetchEligibleKnowledgeBases).mockResolvedValueOnce({
      bases: [
        {
          id: 'kb-1',
          name: 'HR 정책',
          description: '사내 HR 정책 문서',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
        },
      ],
      detailsById: {
        'kb-1': {
          id: 'kb-1',
          name: 'HR 정책',
          description: '사내 HR 정책 문서',
          document_count: 1,
          created_at: '2026-07-01T00:00:00Z',
          embedding_model: 'text-embedding-3-small',
          documents: [
            {
              id: 'doc-1',
              filename: 'hr-policy.md',
              status: 'completed',
              created_at: '2026-07-01T00:00:00Z',
              updated_at: '2026-07-01T00:00:00Z',
              chunk_count: 3,
              token_count: 240,
            },
          ],
        },
      },
    });

    render(
      <NodeSettingsComparisonPanel
        title="B Candidate"
        nodeId="llm-1"
        tab="knowledge"
        onTabChange={vi.fn()}
        draft={baseDraft}
        onChange={vi.fn()}
        onNodeDataChange={onNodeDataChange}
      />,
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: /HR 정책/ }));

    expect(onNodeDataChange).toHaveBeenCalledWith({
      knowledgeBases: [{ id: 'kb-1', name: 'HR 정책' }],
    });
  });
});
