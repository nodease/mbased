import { AppNode } from '../types/Nodes';

export type NodeOutputDataType =
  | 'string'
  | 'number'
  | 'boolean'
  | 'object'
  | 'array'
  | 'null'
  | 'unknown';

export type NodeOutputVariable = {
  key: string;
  label: string;
  description?: string;
  dataType: NodeOutputDataType;
  outputId?: string;
  sourceNodeId: string;
  sourceTitle: string;
};

export type DraggedOutputVariable = NodeOutputVariable;

type ReferencedVariableLike = {
  name?: unknown;
  value_selector?: unknown;
};

const toOutput = (
  node: AppNode,
  key: string,
  label = key,
  outputId?: string,
  dataType: NodeOutputDataType = 'unknown',
  description?: string,
  allowLabelOverride = true,
): NodeOutputVariable => {
  const outputLabels = node.data.outputLabels;
  const labelOverride =
    allowLabelOverride &&
    outputLabels &&
    typeof outputLabels === 'object' &&
    !Array.isArray(outputLabels)
      ? String((outputLabels as Record<string, unknown>)[key] || '').trim()
      : '';

  return {
    key,
    label: labelOverride || label,
    description,
    dataType,
    outputId,
    sourceNodeId: node.id,
    sourceTitle: node.data.title || node.id,
  };
};

const outputInfoByType: Partial<
  Record<
    NonNullable<AppNode['type']>,
    Record<
      string,
      { label: string; dataType: NodeOutputDataType; description: string }
    >
  >
> = {
  llmNode: {
    text: {
      label: '응답 텍스트',
      dataType: 'string',
      description: 'LLM이 생성한 최종 응답 텍스트입니다.',
    },
    usage: {
      label: '사용량',
      dataType: 'object',
      description: '프롬프트/응답 토큰 사용량 정보입니다.',
    },
    model: {
      label: '사용 모델',
      dataType: 'string',
      description: '실제로 호출된 모델 ID입니다.',
    },
    cost: {
      label: '비용',
      dataType: 'number',
      description: '모델 가격 기준으로 계산된 실행 비용입니다.',
    },
    metadata: {
      label: '메타데이터',
      dataType: 'object',
      description: '지식 검색 등 부가 실행 정보입니다.',
    },
  },
  codeNode: {
    result: {
      label: '실행 결과',
      dataType: 'unknown',
      description: '사용자 코드가 반환한 결과입니다.',
    },
  },
  templateNode: {
    text: {
      label: '렌더링 텍스트',
      dataType: 'string',
      description: '템플릿에 변수를 적용해 생성한 텍스트입니다.',
    },
  },
  httpRequestNode: {
    status: {
      label: '상태 코드',
      dataType: 'number',
      description: 'HTTP 응답 상태 코드입니다.',
    },
    data: {
      label: '응답 데이터',
      dataType: 'unknown',
      description: 'HTTP 응답 본문입니다. JSON이면 객체/배열일 수 있습니다.',
    },
    headers: {
      label: '응답 헤더',
      dataType: 'object',
      description: 'HTTP 응답 헤더 객체입니다.',
    },
  },
  slackPostNode: {
    status: {
      label: '상태 코드',
      dataType: 'number',
      description: '검증된 Slack 전달의 HTTP 상태 코드입니다.',
    },
    delivery_status: {
      label: '전달 상태',
      dataType: 'string',
      description: 'Slack 전달이 검증된 상태입니다.',
    },
    delivery_mode: {
      label: '전달 방식',
      dataType: 'string',
      description: 'API 또는 incoming webhook 전달 방식입니다.',
    },
    message_ref: {
      label: '메시지 참조',
      dataType: 'string',
      description: 'API mode에서만 제공되는 제한된 메시지 참조입니다.',
    },
  },
  githubNode: {
    pr_title: {
      label: 'PR 제목',
      dataType: 'string',
      description: '조회한 Pull Request의 제목입니다.',
    },
    pr_body: {
      label: 'PR 본문',
      dataType: 'string',
      description: '조회한 Pull Request의 본문입니다.',
    },
    pr_state: {
      label: 'PR 상태',
      dataType: 'string',
      description: 'Pull Request의 open/closed 상태입니다.',
    },
    pr_number: {
      label: 'PR 번호',
      dataType: 'number',
      description: 'Pull Request 번호입니다.',
    },
    files_count: {
      label: '파일 수',
      dataType: 'number',
      description: 'Pull Request에 포함된 변경 파일 수입니다.',
    },
    files: {
      label: '변경 파일',
      dataType: 'array',
      description: 'Pull Request 변경 파일 목록입니다.',
    },
    diff_url: {
      label: 'Diff URL',
      dataType: 'string',
      description: 'Pull Request diff URL입니다.',
    },
    comment_id: {
      label: '댓글 ID',
      dataType: 'number',
      description: '작성된 PR 댓글 ID입니다.',
    },
    comment_url: {
      label: '댓글 URL',
      dataType: 'string',
      description: '작성된 PR 댓글 URL입니다.',
    },
    comment_body: {
      label: '댓글 본문',
      dataType: 'string',
      description: '작성된 PR 댓글 내용입니다.',
    },
  },
  mailNode: {
    emails: {
      label: '메일 목록',
      dataType: 'array',
      description: '검색된 이메일 목록입니다.',
    },
    total_count: {
      label: '메일 수',
      dataType: 'number',
      description: '검색된 이메일 개수입니다.',
    },
    folder: {
      label: '폴더',
      dataType: 'string',
      description: '검색한 메일 폴더입니다.',
    },
    processing_ref: {
      label: '메일 처리 참조',
      dataType: 'string',
      description:
        '단일 메일을 durable 모드로 검색했을 때 생성되는 불투명 처리 참조입니다.',
    },
  },
  gmailDraftNode: {
    status: {
      label: '초안 생성 상태',
      dataType: 'string',
      description: 'Gmail 답장 초안 생성 상태입니다.',
    },
    draft_ref: {
      label: '초안 작업 참조',
      dataType: 'string',
      description: '중복 처리를 막기 위한 불투명 Draft effect 참조입니다.',
    },
    processing_ref: {
      label: '메일 처리 참조',
      dataType: 'string',
      description: '원본 메일의 불투명 처리 참조입니다.',
    },
  },
  mailAcknowledgeNode: {
    status: {
      label: '처리 완료 상태',
      dataType: 'string',
      description: '필수 작업 완료 및 원본 메일 확인 상태입니다.',
    },
    processing_ref: {
      label: '메일 처리 참조',
      dataType: 'string',
      description: '완료 처리된 원본 메일의 불투명 처리 참조입니다.',
    },
  },
  scheduleTrigger: {
    triggered_at: {
      label: '실행 시각',
      dataType: 'string',
      description: '스케줄이 실행된 ISO 8601 시각입니다.',
    },
    schedule_id: {
      label: '스케줄 ID',
      dataType: 'string',
      description: '실행을 트리거한 스케줄 ID입니다.',
    },
  },
  conditionNode: {
    result: {
      label: '분기 결과',
      dataType: 'boolean',
      description: '조건 매칭 여부입니다.',
    },
    matched_case_id: {
      label: '매칭 분기 ID',
      dataType: 'unknown',
      description: '매칭된 분기 ID입니다. 없으면 null일 수 있습니다.',
    },
    selected_handle: {
      label: '선택 핸들',
      dataType: 'string',
      description: '다음 실행 경로로 선택된 핸들 ID입니다.',
    },
  },
};

const getOutputInfo = (node: AppNode, key: string) => {
  const info = node.type ? outputInfoByType[node.type]?.[key] : undefined;
  return {
    label: info?.label || key,
    dataType: info?.dataType || 'unknown',
    description: info?.description,
  };
};

const toNodeOutput = (
  node: AppNode,
  key: string,
  label?: string,
  outputId?: string,
  dataType?: NodeOutputDataType,
  description?: string,
) => {
  const info = getOutputInfo(node, key);
  return toOutput(
    node,
    key,
    label || info.label,
    outputId,
    dataType || info.dataType,
    description || info.description,
  );
};

const uniqueOutputs = (outputs: NodeOutputVariable[]) => {
  const seen = new Set<string>();
  return outputs.filter((output) => {
    const id = `${output.sourceNodeId}:${output.key}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
};

export const getNodeOutputVariables = (node?: AppNode | null) => {
  if (!node) return [];

  const data = node.data as Record<string, unknown>;
  const outputs: NodeOutputVariable[] = [];

  if (node.type === 'startNode' && Array.isArray(data.variables)) {
    for (const variable of data.variables as Array<Record<string, unknown>>) {
      const key = String(variable.name || variable.label || '').trim();
      const outputId = String(variable.id || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            String(variable.label || key),
            outputId || key,
            String(variable.type || 'unknown') as NodeOutputDataType,
            '워크플로우 시작 시 입력받은 값입니다.',
            false,
          ),
        );
      }
    }
  }

  if (node.type === 'answerNode' && Array.isArray(data.outputs)) {
    for (const output of data.outputs as Array<Record<string, unknown>>) {
      const key = String(output.variable || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            String(output.label || key),
            undefined,
            'unknown',
            '최종 응답으로 반환되는 값입니다.',
          ),
        );
      }
    }
  }

  if (node.type === 'workflowNode' && Array.isArray(data.outputs)) {
    for (const output of data.outputs as string[]) {
      const key = String(output).trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            key,
            undefined,
            'unknown',
            '하위 워크플로우가 반환한 출력값입니다.',
          ),
        );
      }
    }
  }

  if (node.type === 'variableExtractionNode' && Array.isArray(data.mappings)) {
    for (const mapping of data.mappings as Array<Record<string, unknown>>) {
      const key = String(mapping.name || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            key,
            undefined,
            'unknown',
            '원본 JSON에서 지정한 경로로 추출한 값입니다.',
          ),
        );
      }
    }
  }

  if (node.type === 'loopNode' && Array.isArray(data.outputs)) {
    for (const output of data.outputs as Array<Record<string, unknown>>) {
      const key = String(output.name || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            key,
            undefined,
            'unknown',
            '반복 실행 결과에서 수집한 출력값입니다.',
          ),
        );
      }
    }
  }

  if (node.type === 'webhookTrigger' && Array.isArray(data.variable_mappings)) {
    for (const mapping of data.variable_mappings as Array<
      Record<string, unknown>
    >) {
      const key = String(mapping.variable_name || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            key,
            undefined,
            'unknown',
            'Webhook payload에서 JSON path로 추출한 값입니다.',
          ),
        );
      }
    }
  }

  if (
    node.type === 'fileExtractionNode' &&
    Array.isArray(data.referenced_variables)
  ) {
    for (const variable of data.referenced_variables as Array<
      Record<string, unknown>
    >) {
      const key = String(variable.name || '').trim();
      if (key) {
        outputs.push(
          toOutput(
            node,
            key,
            key,
            undefined,
            'string',
            '파일에서 추출한 텍스트입니다.',
          ),
        );
      }
    }
  }

  if (node.type === 'slackPostNode') {
    const slackKeys = ['status', 'delivery_status', 'delivery_mode'];
    if (data.slackMode !== 'webhook') slackKeys.push('message_ref');
    for (const key of slackKeys) outputs.push(toNodeOutput(node, key));
  }

  const fallbackByType: Partial<
    Record<NonNullable<AppNode['type']>, string[]>
  > = {
    llmNode: ['text', 'usage', 'model', 'cost', 'metadata'],
    codeNode: ['result'],
    templateNode: ['text'],
    httpRequestNode: ['status', 'data', 'headers'],
    githubNode: [
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
    ],
    mailNode: ['emails', 'total_count', 'folder', 'processing_ref'],
    gmailDraftNode: ['status', 'draft_ref', 'processing_ref'],
    mailAcknowledgeNode: ['status', 'processing_ref'],
    scheduleTrigger: ['triggered_at', 'schedule_id'],
    conditionNode: ['result', 'matched_case_id', 'selected_handle'],
  };

  for (const key of fallbackByType[node.type || 'note'] || []) {
    outputs.push(toNodeOutput(node, key));
  }

  return uniqueOutputs(outputs);
};

const selectorFor = (output: DraggedOutputVariable) => [
  output.sourceNodeId,
  output.key,
];

const sourceFor = (output: DraggedOutputVariable) =>
  `${output.sourceNodeId}.${output.key}`;

const selectorsEqual = (selector: unknown, output: DraggedOutputVariable) =>
  Array.isArray(selector) &&
  selector[0] === output.sourceNodeId &&
  (selector[1] === output.key || selector[1] === output.outputId);

const toSafeReferenceName = (value: string) => {
  const normalized = value.trim().replace(/[^\w]/g, '_');
  return normalized || 'variable';
};

export const getDroppedOutputReferenceName = (
  list: unknown,
  output: DraggedOutputVariable,
  selectorKey: string,
) => {
  const current = Array.isArray(list) ? list : [];
  const matchedBySelector = current.find((item) => {
    if (!item || typeof item !== 'object') return false;
    return selectorsEqual(
      (item as Record<string, unknown>)[selectorKey],
      output,
    );
  });
  const matchedName = String(
    (matchedBySelector as Record<string, unknown> | undefined)?.name || '',
  ).trim();
  if (matchedName) return matchedName;

  const baseName = toSafeReferenceName(output.key);
  const usedNames = new Set(
    current
      .map((item) =>
        item && typeof item === 'object'
          ? String((item as Record<string, unknown>).name || '').trim()
          : '',
      )
      .filter(Boolean),
  );

  if (!usedNames.has(baseName)) return baseName;

  let suffix = 2;
  while (usedNames.has(`${baseName}_${suffix}`)) {
    suffix += 1;
  }
  return `${baseName}_${suffix}`;
};

export const upsertNamedSelector = (
  list: unknown,
  output: DraggedOutputVariable,
  selectorKey: string,
) => {
  const current = Array.isArray(list) ? list : [];
  const name = getDroppedOutputReferenceName(current, output, selectorKey);
  const nextItem = { name, [selectorKey]: selectorFor(output) };
  const index = current.findIndex(
    (item) =>
      item &&
      typeof item === 'object' &&
      ((item as Record<string, unknown>).name === name ||
        selectorsEqual((item as Record<string, unknown>)[selectorKey], output)),
  );

  if (index >= 0) {
    return current.map((item, itemIndex) =>
      itemIndex === index ? { ...(item as object), ...nextItem } : item,
    );
  }

  return [...current, nextItem];
};

const getDroppedOutputInputName = (
  list: unknown,
  output: DraggedOutputVariable,
) => getDroppedOutputReferenceName(list, output, 'value_selector');

const getDroppedAnswerOutputName = (
  list: unknown,
  output: DraggedOutputVariable,
) => {
  const current = Array.isArray(list) ? list : [];
  const matchedBySelector = current.find((item) => {
    if (!item || typeof item !== 'object') return false;
    return selectorsEqual(
      (item as Record<string, unknown>).value_selector,
      output,
    );
  });
  const matchedName = String(
    (matchedBySelector as Record<string, unknown> | undefined)?.variable || '',
  ).trim();
  if (matchedName) return matchedName;

  const baseName = toSafeReferenceName(output.key);
  const usedNames = new Set(
    current
      .map((item) =>
        item && typeof item === 'object'
          ? String((item as Record<string, unknown>).variable || '').trim()
          : '',
      )
      .filter(Boolean),
  );

  if (!usedNames.has(baseName)) return baseName;

  let suffix = 2;
  while (usedNames.has(`${baseName}_${suffix}`)) {
    suffix += 1;
  }
  return `${baseName}_${suffix}`;
};

export const getDroppedOutputTokenNameForNode = (
  node: AppNode,
  output: DraggedOutputVariable,
) => {
  const data = node.data as Record<string, unknown>;

  switch (node.type) {
    case 'llmNode':
    case 'httpRequestNode':
    case 'slackPostNode':
    case 'githubNode':
    case 'mailNode':
    case 'fileExtractionNode':
      return getDroppedOutputReferenceName(
        data.referenced_variables,
        output,
        'value_selector',
      );

    case 'templateNode':
      return getDroppedOutputInputName(data.variables, output);

    case 'workflowNode':
    case 'loopNode':
      return getDroppedOutputInputName(data.inputs, output);

    case 'codeNode':
      return toSafeReferenceName(output.key);

    default:
      return toSafeReferenceName(output.key);
  }
};

export const getTokenLabelMap = (
  references: unknown,
  upstreamNodes: AppNode[],
) => {
  const labelMap: Record<string, string> = {};
  const current = Array.isArray(references) ? references : [];

  for (const reference of current as ReferencedVariableLike[]) {
    const name = String(reference.name || '').trim();
    const selector = reference.value_selector;
    if (!name || !Array.isArray(selector)) continue;

    const [sourceNodeId, outputKey] = selector;
    const sourceNode = upstreamNodes.find((node) => node.id === sourceNodeId);
    const sourceOutput = getNodeOutputVariables(sourceNode).find(
      (output) => output.key === outputKey || output.outputId === outputKey,
    );
    const label = String(sourceOutput?.label || '').trim();
    if (label) labelMap[name] = label;
  }

  return labelMap;
};

const ACCEPT_DROPPED_OUTPUT_NODE_TYPES = new Set<NonNullable<AppNode['type']>>([
  'llmNode',
  'httpRequestNode',
  'slackPostNode',
  'githubNode',
  'mailNode',
  'fileExtractionNode',
  'templateNode',
  'workflowNode',
  'loopNode',
  'codeNode',
  'answerNode',
  'variableExtractionNode',
  'conditionNode',
]);

export const canAcceptDroppedOutput = (node: AppNode) =>
  Boolean(node.type && ACCEPT_DROPPED_OUTPUT_NODE_TYPES.has(node.type));

const createConditionId = () =>
  globalThis.crypto?.randomUUID?.() || `condition-${Date.now()}`;

const conditionSelectorFor = (output: DraggedOutputVariable) => [
  output.sourceNodeId,
  output.outputId || output.key,
];

const appendDroppedOutputToCondition = (
  cases: unknown,
  output: DraggedOutputVariable,
) => {
  const currentCases = Array.isArray(cases) ? cases : [];
  const selector = conditionSelectorFor(output);
  const nextCondition = {
    id: createConditionId(),
    variable_selector: selector,
    operator: 'equals',
    value: '',
  };

  if (currentCases.length === 0) {
    return [
      {
        id: createConditionId(),
        case_name: 'Default',
        conditions: [nextCondition],
        logical_operator: 'and',
      },
    ];
  }

  const firstCase = currentCases[0] as Record<string, unknown>;
  const conditions = Array.isArray(firstCase.conditions)
    ? firstCase.conditions
    : [];
  const existingIndex = conditions.findIndex((condition) => {
    if (!condition || typeof condition !== 'object') return false;
    const variableSelector = (condition as Record<string, unknown>)
      .variable_selector;
    return (
      Array.isArray(variableSelector) &&
      variableSelector[0] === selector[0] &&
      variableSelector[1] === selector[1]
    );
  });
  const emptyIndex = conditions.findIndex((condition) => {
    if (!condition || typeof condition !== 'object') return false;
    const variableSelector = (condition as Record<string, unknown>)
      .variable_selector;
    return !Array.isArray(variableSelector) || variableSelector.length < 2;
  });
  const updateIndex = existingIndex >= 0 ? existingIndex : emptyIndex;
  const nextConditions =
    updateIndex >= 0
      ? conditions.map((condition, index) =>
          index === updateIndex
            ? { ...(condition as object), variable_selector: selector }
            : condition,
        )
      : [...conditions, nextCondition];

  return currentCases.map((item, index) =>
    index === 0
      ? {
          ...(item as object),
          conditions: nextConditions,
        }
      : item,
  );
};

export const applyDroppedOutputToNodeData = (
  node: AppNode,
  output: DraggedOutputVariable,
) => {
  const data = node.data as Record<string, unknown>;

  switch (node.type) {
    case 'llmNode':
    case 'httpRequestNode':
    case 'slackPostNode':
    case 'githubNode':
    case 'mailNode':
    case 'fileExtractionNode':
      return {
        referenced_variables: upsertNamedSelector(
          data.referenced_variables,
          output,
          'value_selector',
        ),
      };

    case 'templateNode':
      return {
        variables: upsertNamedSelector(
          data.variables,
          output,
          'value_selector',
        ),
      };

    case 'workflowNode':
    case 'loopNode':
      return {
        inputs: upsertNamedSelector(data.inputs, output, 'value_selector'),
      };

    case 'codeNode': {
      const current = Array.isArray(data.inputs) ? data.inputs : [];
      const name = output.key;
      const nextItem = { name, source: sourceFor(output) };
      const index = current.findIndex(
        (item) =>
          item &&
          typeof item === 'object' &&
          (item as Record<string, unknown>).name === name,
      );
      return {
        inputs:
          index >= 0
            ? current.map((item, itemIndex) =>
                itemIndex === index
                  ? { ...(item as object), ...nextItem }
                  : item,
              )
            : [...current, nextItem],
      };
    }

    case 'answerNode': {
      const current = Array.isArray(data.outputs) ? data.outputs : [];
      const variable = getDroppedAnswerOutputName(current, output);
      const nextItem = { variable, value_selector: selectorFor(output) };
      const index = current.findIndex(
        (item) =>
          item &&
          typeof item === 'object' &&
          ((item as Record<string, unknown>).variable === variable ||
            selectorsEqual(
              (item as Record<string, unknown>).value_selector,
              output,
            )),
      );

      return {
        outputs:
          index >= 0
            ? current.map((item, itemIndex) =>
                itemIndex === index
                  ? { ...(item as object), ...nextItem }
                  : item,
              )
            : [...current, nextItem],
      };
    }

    case 'variableExtractionNode':
      return {
        source_selector: selectorFor(output),
      };

    case 'conditionNode':
      return {
        cases: appendDroppedOutputToCondition(data.cases, output),
      };

    default:
      return null;
  }
};
