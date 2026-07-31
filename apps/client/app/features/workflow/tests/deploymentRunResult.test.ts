import { describe, expect, it } from 'vitest';

import type { DeploymentRunInfoResponse } from '../types/Deployment';
import {
  getDeploymentRunCitations,
  getDeploymentRunFinalPreview,
} from '../utils/deploymentRunResult';

const deployment = {
  deployment_id: 'deployment-1',
  app_id: 'app-1',
  workflow_id: 'workflow-1',
  name: '사내 문서 질문 응답 봇',
  version: 1,
  type: 'chatbot',
  output_schema: {
    outputs: [{ variable: 'final_answer', label: '최종 답변' }],
  },
} as DeploymentRunInfoResponse;

describe('deployment run final preview', () => {
  it('workflow output이 있으면 최종 출력으로 표시한다', () => {
    const preview = getDeploymentRunFinalPreview(deployment, {
      status: 'success',
      results: {
        output: '개발팀 신입 연봉 기준은 사내 보상 밴드 문서를 따릅니다.',
      },
    });

    expect(preview.kind).toBe('text');
    expect(preview.text).toContain('개발팀 신입 연봉 기준');
    expect(preview.sourceLabel).toBe('워크플로우 최종 출력');
  });

  it('safe output schema의 출력 변수를 우선 표시한다', () => {
    const preview = getDeploymentRunFinalPreview(deployment, {
      status: 'success',
      results: {
        raw_llm_output: { answer: 'LLM 원본 답변' },
        final_answer: '최종 사용자 답변',
      },
    });

    expect(preview.kind).toBe('text');
    expect(preview.text).toBe('최종 사용자 답변');
    expect(preview.sourceLabel).toBe('최종 답변');
  });

  it('output schema의 앞 출력이 null이면 다음 출력 후보를 표시한다', () => {
    const preview = getDeploymentRunFinalPreview(
      {
        ...deployment,
        output_schema: {
          outputs: [
            { variable: 'empty_answer', label: '빈 답변' },
            { variable: 'final_answer', label: '최종 답변' },
          ],
        },
      },
      {
        status: 'success',
        results: {
          empty_answer: null,
          final_answer: '실제 최종 사용자 답변',
        },
      },
    );

    expect(preview.kind).toBe('text');
    expect(preview.text).toBe('실제 최종 사용자 답변');
    expect(preview.sourceLabel).toBe('최종 답변');
  });

  it('output schema가 없는 기존 챗봇의 단일 custom text output을 표시한다', () => {
    const preview = getDeploymentRunFinalPreview(
      { ...deployment, output_schema: undefined },
      {
        status: 'success',
        results: {
          final_answer: '기존 챗봇 응답도 유지합니다.',
        },
      },
    );

    expect(preview.kind).toBe('text');
    expect(preview.text).toBe('기존 챗봇 응답도 유지합니다.');
  });
});

describe('deployment run citations', () => {
  it('versioned safe sidecar만 순서대로 읽는다', () => {
    const citations = getDeploymentRunCitations({
      status: 'success',
      results: {
        answer: '응답',
        __nodease_citations: {
          version: 1,
          items: [
            {
              citation_id: 'evidence-2',
              evidence_rank: 2,
              label: '개발 온보딩',
              page_number: 4,
              section: null,
              content_preview: null,
            },
            {
              citation_id: 'evidence-1',
              evidence_rank: 1,
              label: '공통 휴가 정책',
              page_number: null,
              section: '연차 신청',
              content_preview: '연차는 사전에 신청합니다.',
            },
          ],
        },
      },
    });

    expect(citations.map((item) => item.label)).toEqual([
      '공통 휴가 정책',
      '개발 온보딩',
    ]);
    expect(citations[0].contentPreview).toBe('연차는 사전에 신청합니다.');
  });

  it('stream node id가 reserved Citation key와 충돌하면 sidecar처럼 해석하지 않는다', () => {
    expect(
      getDeploymentRunCitations(
        {
          __nodease_citations: {
            version: 1,
            items: [
              {
                citation_id: 'evidence-1',
                evidence_rank: 1,
                label: '노드가 만든 값',
              },
            ],
          },
        },
        { knownNodeIds: ['input', '__nodease_citations', 'answer'] },
      ),
    ).toEqual([]);
  });

  it.each([
    { version: 99, items: [] },
    { version: 1, items: 'invalid' },
    {
      version: 1,
      items: [
        {
          citation_id: 'evidence-1',
          evidence_rank: 1,
          label: 'https://private.invalid/document',
        },
      ],
    },
    {
      version: 1,
      items: [
        {
          citation_id: 'evidence-1',
          evidence_rank: 1,
          label: '문서',
          document_id: 'hidden-id',
        },
      ],
    },
    {
      version: 1,
      items: [
        {
          citation_id: 'evidence-2',
          evidence_rank: 1,
          label: '순위가 맞지 않는 문서',
        },
      ],
    },
    {
      version: 1,
      items: [
        {
          citation_id: 'evidence-1',
          evidence_rank: 1,
          label: 'C:\\private\\document.pdf',
        },
      ],
    },
    {
      version: 1,
      items: [
        {
          citation_id: 'evidence-1',
          evidence_rank: 1,
          label: '문서',
          unrecognized_field: 'hidden-value',
        },
      ],
    },
  ])('malformed 또는 identity 포함 sidecar는 표시하지 않는다', (sidecar) => {
    expect(
      getDeploymentRunCitations({ __nodease_citations: sidecar }),
    ).toEqual([]);
  });
});
