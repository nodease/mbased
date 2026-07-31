import { describe, expect, it } from 'vitest';

import {
  buildFinalResponsePreview,
  getFinalResponsePreview,
  shouldShowFinalResponseCard,
} from '../utils/testExecutionFinalResponse';
import type { Node } from '../types/Workflow';

const node = (id: string, type: string, title: string): Node =>
  ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: { title },
  }) as Node;

describe('workflow test final response preview', () => {
  const nodes = [
    node('start-1', 'startNode', '입력'),
    node('llm-1', 'llmNode', '정책 답변 LLM'),
    node('answer-1', 'answerNode', '최종 응답'),
  ];

  it('workflow_finish.output을 최우선 최종 응답으로 사용한다', () => {
    const preview = getFinalResponsePreview({
      workflowResult: {
        output: '신입 온보딩 절차는 계정 발급 후 보안 교육 순서입니다.',
      },
      nodeResults: [
        {
          nodeId: 'llm-1',
          nodeType: 'llmNode',
          output: { text: 'LLM fallback should not be used' },
        },
      ],
      nodes,
    });

    expect(preview).toMatchObject({
      kind: 'text',
      text: '신입 온보딩 절차는 계정 발급 후 보안 교육 순서입니다.',
      sourceLabel: '워크플로우 최종 출력',
      isEmpty: false,
    });
  });

  it('workflow output이 없으면 answer/response node output을 LLM보다 우선한다', () => {
    const preview = getFinalResponsePreview({
      workflowResult: {},
      nodeResults: [
        {
          nodeId: 'llm-1',
          nodeType: 'llmNode',
          output: { text: 'LLM raw answer' },
        },
        {
          nodeId: 'answer-1',
          nodeType: 'answerNode',
          output: { message: '최종 사용자에게 전달되는 응답' },
        },
      ],
      nodes,
    });

    expect(preview).toMatchObject({
      kind: 'text',
      text: '최종 사용자에게 전달되는 응답',
      sourceLabel: '최종 응답',
    });
  });

  it('전체 노드 실행 결과를 감싼 workflow output보다 answer node 출력을 우선한다', () => {
    const preview = getFinalResponsePreview({
      workflowResult: {
        output: {
          'start-1': { message: '휴가 신청 절차를 알려주세요.' },
          'llm-1': { text: '휴가 신청은 관리자 승인 후 처리됩니다.' },
          'answer-1': { reply: '휴가 신청은 관리자 승인 후 처리됩니다.' },
        },
      },
      nodeResults: [
        {
          nodeId: 'llm-1',
          nodeType: 'llmNode',
          output: { text: 'LLM 원본 응답' },
        },
        {
          nodeId: 'answer-1',
          nodeType: 'answerNode',
          output: { reply: '최종 사용자에게 보여줄 답변' },
        },
      ],
      nodes,
    });

    expect(preview).toMatchObject({
      kind: 'text',
      text: '최종 사용자에게 보여줄 답변',
      sourceLabel: '최종 응답',
    });
  });

  it('answer node의 reply output은 최종 사용자 응답 텍스트로 표시한다', () => {
    expect(
      getFinalResponsePreview({
        workflowResult: {},
        nodeResults: [
          {
            nodeId: 'answer-1',
            nodeType: 'answerNode',
            output: { reply: '신입 온보딩은 첫날 오전 10시에 시작됩니다.' },
          },
        ],
        nodes,
      }),
    ).toMatchObject({
      kind: 'text',
      text: '신입 온보딩은 첫날 오전 10시에 시작됩니다.',
      sourceLabel: '최종 응답',
    });
  });

  it('answer node가 없으면 LLM text/message/answer/content 계열 output을 fallback으로 사용한다', () => {
    expect(
      getFinalResponsePreview({
        workflowResult: {},
        nodeResults: [
          {
            nodeId: 'llm-1',
            nodeType: 'llmNode',
            output: { answer: '개발팀 커밋 컨벤션은 type: subject 형식입니다.' },
          },
        ],
        nodes,
      }),
    ).toMatchObject({
      kind: 'text',
      text: '개발팀 커밋 컨벤션은 type: subject 형식입니다.',
      sourceLabel: '정책 답변 LLM',
    });
  });

  it('JSON 응답은 raw dump 대신 필드 preview로 요약하고 상세 JSON은 별도 영역에 맡긴다', () => {
    const preview = buildFinalResponsePreview(
      {
        summary: '입사 첫날에는 보안 교육을 완료합니다.',
        next_steps: ['계정 활성화', '개발 환경 설정'],
        confidence: 0.91,
      },
      '워크플로우 최종 출력',
    );

    expect(preview.kind).toBe('json');
    if (preview.kind === 'json') {
      expect(preview.items).toEqual([
        { label: 'summary', value: '입사 첫날에는 보안 교육을 완료합니다.' },
        { label: 'next_steps', value: '2개 항목' },
        { label: 'confidence', value: '0.91' },
      ]);
      expect(preview.text).toContain('summary: 입사 첫날에는 보안 교육을 완료합니다.');
    }
  });

  it('text 계열 key 안에 JSON object가 들어오면 raw dump 대신 중첩 object preview를 사용한다', () => {
    const preview = buildFinalResponsePreview(
      {
        content: {
          summary: 'JSON schema 기반 응답입니다.',
          action_items: ['계정 발급', '보안 교육'],
        },
      },
      'LLM JSON 응답',
    );

    expect(preview.kind).toBe('json');
    if (preview.kind === 'json') {
      expect(preview.items).toEqual([
        { label: 'summary', value: 'JSON schema 기반 응답입니다.' },
        { label: 'action_items', value: '2개 항목' },
      ]);
      expect(preview.text).not.toContain('"summary"');
    }
  });

  it('배열 JSON 응답도 raw dump 대신 항목 preview로 요약한다', () => {
    const preview = buildFinalResponsePreview(
      ['계정 발급', '보안 교육'],
      '워크플로우 최종 출력',
    );

    expect(preview.kind).toBe('json');
    if (preview.kind === 'json') {
      expect(preview.items).toEqual([
        { label: '항목 1', value: '계정 발급' },
        { label: '항목 2', value: '보안 교육' },
      ]);
    }
  });

  it('권한/정책 차단성 응답은 사용자 메시지를 표시하되 내부 source와 credential 필드는 숨긴다', () => {
    const preview = buildFinalResponsePreview(
      {
        policy_message: '해당 연봉 문서는 접근 권한이 없어 답변할 수 없습니다.',
        hidden_kb_id: 'kb-private-salary',
        source_url: 's3://internal/private/salary.pdf',
        credential_id: 'cred-secret',
      },
      '정책 응답',
    );

    expect(preview).toMatchObject({
      kind: 'text',
      text: '해당 연봉 문서는 접근 권한이 없어 답변할 수 없습니다.',
      isEmpty: false,
    });
    expect(preview.text).not.toContain('kb-private-salary');
    expect(preview.text).not.toContain('salary.pdf');
    expect(preview.text).not.toContain('cred-secret');
  });

  it('JSON preview에서는 민감 key를 제외한다', () => {
    const preview = buildFinalResponsePreview(
      {
        answer: undefined,
        public_summary: '조회 가능한 문서 기준 답변입니다.',
        source_path: '/private/hr/salary.pdf',
        raw_trace_payload: { prompt: 'raw prompt' },
        api_key: 'secret',
      },
      'JSON 정책 응답',
    );

    expect(preview.kind).toBe('json');
    if (preview.kind === 'json') {
      expect(preview.items).toEqual([
        { label: 'public_summary', value: '조회 가능한 문서 기준 답변입니다.' },
      ]);
    }
  });

  it('빈 응답은 empty state로 표시할 수 있게 isEmpty를 반환한다', () => {
    expect(buildFinalResponsePreview('', '실행 결과')).toMatchObject({
      kind: 'text',
      text: '',
      isEmpty: true,
    });
  });

  it('긴 응답은 잘라내지 않고 카드 렌더러가 스크롤로 처리할 수 있게 유지한다', () => {
    const longText = '온보딩 안내 '.repeat(120);

    expect(buildFinalResponsePreview(longText, 'LLM').text).toBe(longText.trim());
  });

  it('실패 상태에서는 최종 응답 카드 대신 실패 UI를 유지한다', () => {
    expect(
      shouldShowFinalResponseCard({
        hasExecutionResult: true,
        error: '권한 정책에 의해 실행이 차단되었습니다.',
      }),
    ).toBe(false);
    expect(
      shouldShowFinalResponseCard({
        hasExecutionResult: true,
        error: null,
      }),
    ).toBe(true);
  });
});
