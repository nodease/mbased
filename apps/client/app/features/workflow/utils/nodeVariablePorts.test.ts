import { describe, expect, it } from 'vitest';

import {
  applyDroppedOutputToNodeData,
  getDroppedOutputReferenceName,
  getNodeOutputVariables,
  NodeOutputVariable,
  upsertNamedSelector,
} from './nodeVariablePorts';
import { AppNode } from '../types/Nodes';

const makeNode = (
  type: NonNullable<AppNode['type']>,
  data: Record<string, unknown>,
): AppNode =>
  ({
    id: `${type}-1`,
    type,
    position: { x: 0, y: 0 },
    data: {
      title: `${type} title`,
      ...data,
    },
  }) as AppNode;

const makeOutput = (
  output: Omit<NodeOutputVariable, 'dataType'> &
    Partial<Pick<NodeOutputVariable, 'dataType'>>,
): NodeOutputVariable => ({
  dataType: 'unknown',
  ...output,
});

describe('nodeVariablePorts', () => {
  it('start node variables를 output chip으로 변환한다', () => {
    const node = makeNode('startNode', {
      variables: [
        { name: 'customer_message', label: '고객 문의' },
        { name: 'priority', label: '우선순위' },
      ],
    });

    expect(getNodeOutputVariables(node)).toEqual([
      {
        key: 'customer_message',
        label: '고객 문의',
        description: '워크플로우 시작 시 입력받은 값입니다.',
        dataType: 'unknown',
        outputId: 'customer_message',
        sourceNodeId: 'startNode-1',
        sourceTitle: 'startNode title',
      },
      {
        key: 'priority',
        label: '우선순위',
        description: '워크플로우 시작 시 입력받은 값입니다.',
        dataType: 'unknown',
        outputId: 'priority',
        sourceNodeId: 'startNode-1',
        sourceTitle: 'startNode title',
      },
    ]);
  });

  it('start node 출력 라벨은 별도 outputLabels보다 variables label을 우선한다', () => {
    const node = makeNode('startNode', {
      outputLabels: {
        customer_message: '오래된 출력 라벨',
      },
      variables: [{ name: 'customer_message', label: '고객 문의' }],
    });

    expect(getNodeOutputVariables(node)[0]).toMatchObject({
      key: 'customer_message',
      label: '고객 문의',
    });
  });

  it('code node에 output을 drop하면 inputs source를 갱신한다', () => {
    const node = makeNode('codeNode', {
      inputs: [{ name: 'result', source: 'old-node.result' }],
      code: '',
      timeout: 10,
    });

    expect(
      applyDroppedOutputToNodeData(
        node,
        makeOutput({
          key: 'result',
          label: 'result',
          sourceNodeId: 'source-node',
          sourceTitle: 'Source',
        }),
      ),
    ).toEqual({
      inputs: [{ name: 'result', source: 'source-node.result' }],
    });
  });

  it('llm node에 output을 drop하면 referenced_variables에 value selector를 추가한다', () => {
    const node = makeNode('llmNode', {
      referenced_variables: [],
      provider: '',
      model_id: '',
      parameters: {},
    });

    expect(
      applyDroppedOutputToNodeData(
        node,
        makeOutput({
          key: 'text',
          label: 'text',
          sourceNodeId: 'source-node',
          sourceTitle: 'Source',
        }),
      ),
    ).toEqual({
      referenced_variables: [
        { name: 'text', value_selector: ['source-node', 'text'] },
      ],
    });
  });

  it('같은 selector의 output을 다시 drop하면 입력변수를 중복 추가하지 않는다', () => {
    expect(
      upsertNamedSelector(
        [{ name: 'text', value_selector: ['source-node', 'text'] }],
        makeOutput({
          key: 'text',
          label: 'text',
          sourceNodeId: 'source-node',
          sourceTitle: 'Source',
        }),
        'value_selector',
      ),
    ).toEqual([{ name: 'text', value_selector: ['source-node', 'text'] }]);
  });

  it('같은 key의 다른 output을 drop하면 충돌 없는 alias를 만든다', () => {
    const existing = [{ name: 'text', value_selector: ['node-a', 'text'] }];
    const output = makeOutput({
      key: 'text',
      label: 'text',
      sourceNodeId: 'node-b',
      sourceTitle: 'Node B',
    });

    expect(
      getDroppedOutputReferenceName(existing, output, 'value_selector'),
    ).toBe('text_2');
    expect(upsertNamedSelector(existing, output, 'value_selector')).toEqual([
      { name: 'text', value_selector: ['node-a', 'text'] },
      { name: 'text_2', value_selector: ['node-b', 'text'] },
    ]);
  });

  it('이미 등록된 selector를 다시 drop하면 기존 alias를 재사용한다', () => {
    const existing = [{ name: 'llm_text', value_selector: ['node-a', 'text'] }];
    const output = makeOutput({
      key: 'text',
      label: 'text',
      sourceNodeId: 'node-a',
      sourceTitle: 'Node A',
    });

    expect(
      getDroppedOutputReferenceName(existing, output, 'value_selector'),
    ).toBe('llm_text');
    expect(upsertNamedSelector(existing, output, 'value_selector')).toEqual(
      existing,
    );
  });

  it('condition node에 start output을 drop하면 변수 id로 첫 번째 조건을 갱신한다', () => {
    const node = makeNode('conditionNode', {
      cases: [
        {
          id: 'case-1',
          case_name: 'Default',
          conditions: [
            {
              id: 'condition-1',
              variable_selector: [],
              operator: 'equals',
              value: '',
            },
          ],
          logical_operator: 'and',
        },
      ],
    });

    const patch = applyDroppedOutputToNodeData(
      node,
      makeOutput({
        key: 'customer_message',
        label: '고객 문의',
        outputId: 'start-variable-id',
        sourceNodeId: 'start-node',
        sourceTitle: 'Start',
      }),
    );

    expect(patch).toMatchObject({
      cases: [
        {
          conditions: [
            {
              variable_selector: ['start-node', 'start-variable-id'],
            },
          ],
        },
      ],
    });
  });

  it('answer node에 output을 drop하면 최종 반환값 매핑을 추가한다', () => {
    const node = makeNode('answerNode', {
      outputs: [],
    });

    expect(
      applyDroppedOutputToNodeData(
        node,
        makeOutput({
          key: 'text',
          label: '응답 텍스트',
          sourceNodeId: 'llm-node',
          sourceTitle: 'LLM',
        }),
      ),
    ).toEqual({
      outputs: [{ variable: 'text', value_selector: ['llm-node', 'text'] }],
    });
  });

  it('answer node에 같은 selector를 다시 drop하면 반환값을 중복 추가하지 않는다', () => {
    const node = makeNode('answerNode', {
      outputs: [{ variable: 'analysis', value_selector: ['llm-node', 'text'] }],
    });

    expect(
      applyDroppedOutputToNodeData(
        node,
        makeOutput({
          key: 'text',
          label: '응답 텍스트',
          sourceNodeId: 'llm-node',
          sourceTitle: 'LLM',
        }),
      ),
    ).toEqual({
      outputs: [{ variable: 'analysis', value_selector: ['llm-node', 'text'] }],
    });
  });

  it('answer node에 같은 key의 다른 output을 drop하면 반환 key 충돌을 피한다', () => {
    const node = makeNode('answerNode', {
      outputs: [{ variable: 'text', value_selector: ['llm-a', 'text'] }],
    });

    expect(
      applyDroppedOutputToNodeData(
        node,
        makeOutput({
          key: 'text',
          label: '응답 텍스트',
          sourceNodeId: 'llm-b',
          sourceTitle: 'LLM B',
        }),
      ),
    ).toEqual({
      outputs: [
        { variable: 'text', value_selector: ['llm-a', 'text'] },
        { variable: 'text_2', value_selector: ['llm-b', 'text'] },
      ],
    });
  });

  it('template/http 계열 출력 key를 workflow engine 반환값과 맞춘다', () => {
    const templateNode = makeNode('templateNode', {
      template: '',
      variables: [],
    });
    const httpNode = makeNode('httpRequestNode', {
      method: 'GET',
      url: '',
      headers: [],
      body: '',
      timeout: 30000,
      authType: 'none',
      authConfig: {},
      referenced_variables: [],
    });
    const slackNode = makeNode('slackPostNode', {
      slackMode: 'api',
      authConfig: {},
      referenced_variables: [],
    });

    expect(
      getNodeOutputVariables(templateNode).map((output) => output.key),
    ).toEqual(['text']);
    expect(
      getNodeOutputVariables(httpNode).map((output) => output.key),
    ).toEqual(['status', 'data', 'headers']);
    expect(
      getNodeOutputVariables(slackNode).map((output) => output.key),
    ).toEqual(['status', 'delivery_status', 'delivery_mode', 'message_ref']);

    const webhookSlackNode = makeNode('slackPostNode', {
      slackMode: 'webhook',
      authConfig: {},
      referenced_variables: [],
    });
    expect(
      getNodeOutputVariables(webhookSlackNode).map((output) => output.key),
    ).toEqual(['status', 'delivery_status', 'delivery_mode']);
  });

  it('webhook/file extraction의 동적 출력 key를 노드 데이터에서 만든다', () => {
    const webhookNode = makeNode('webhookTrigger', {
      provider: 'custom',
      variable_mappings: [
        { variable_name: 'issue_key', json_path: 'issue.key' },
        { variable_name: 'summary', json_path: 'issue.fields.summary' },
      ],
    });
    const fileExtractionNode = makeNode('fileExtractionNode', {
      referenced_variables: [
        { name: 'invoice_text', value_selector: ['start', 'file'] },
        { name: 'contract_text', value_selector: ['start', 'contract'] },
      ],
    });

    expect(
      getNodeOutputVariables(webhookNode).map((output) => output.key),
    ).toEqual(['issue_key', 'summary']);
    expect(
      getNodeOutputVariables(fileExtractionNode).map((output) => output.key),
    ).toEqual(['invoice_text', 'contract_text']);
  });

  it('github/mail/condition 출력 key를 workflow engine 반환값과 맞춘다', () => {
    const githubNode = makeNode('githubNode', {
      action: 'get_pr',
      api_token: '',
      repo_owner: '',
      repo_name: '',
      pr_number: '',
      referenced_variables: [],
    });
    const mailNode = makeNode('mailNode', {
      credential_id: 'mail-credential-1',
      folder: 'INBOX',
      unread_only: false,
      mark_as_read: false,
      referenced_variables: [],
    });
    const conditionNode = makeNode('conditionNode', {
      cases: [],
    });

    expect(
      getNodeOutputVariables(githubNode).map((output) => output.key),
    ).toEqual([
      'pr_title',
      'pr_body',
      'pr_state',
      'pr_number',
      'files_count',
      'files',
      'diff_url',
      'comment_id',
      'comment_url',
      'comment_body',
    ]);
    expect(
      getNodeOutputVariables(mailNode).map((output) => output.key),
    ).toEqual(['emails', 'total_count', 'folder', 'processing_ref']);
    expect(
      getNodeOutputVariables(conditionNode).map((output) => output.key),
    ).toEqual(['result', 'matched_case_id', 'selected_handle']);
  });

  it('llm 출력 변수에 라벨, 설명, 데이터 타입을 포함한다', () => {
    const node = makeNode('llmNode', {
      referenced_variables: [],
      provider: '',
      model_id: '',
      parameters: {},
    });

    expect(getNodeOutputVariables(node)).toMatchObject([
      {
        key: 'text',
        label: '응답 텍스트',
        dataType: 'string',
      },
      {
        key: 'usage',
        label: '사용량',
        dataType: 'object',
      },
      {
        key: 'model',
        label: '사용 모델',
        dataType: 'string',
      },
      {
        key: 'cost',
        label: '비용',
        dataType: 'number',
      },
      {
        key: 'metadata',
        label: '메타데이터',
        dataType: 'object',
      },
    ]);
  });
});
