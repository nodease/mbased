import React from 'react';

import { AppNode } from '../types/Nodes';

type VisiblePropertyDefinition = {
  key: string;
  label: string;
  multiline?: boolean;
  getValue: (node: AppNode) => string;
};

const normalizePrompt = (value: string) => value.replace(/\s+/g, ' ').trim();
const asText = (value: unknown) => String(value ?? '').trim();
const asList = (value: unknown, key: string) =>
  Array.isArray(value)
    ? value
        .map((item) =>
          item && typeof item === 'object'
            ? asText((item as Record<string, unknown>)[key])
            : asText(item),
        )
        .filter(Boolean)
        .join(', ')
    : '';
const asKeyValueList = (value: unknown, keyName = 'key', valueName = 'value') =>
  Array.isArray(value)
    ? value
        .map((item) => {
          if (!item || typeof item !== 'object') return '';
          const record = item as Record<string, unknown>;
          const key = asText(record[keyName]);
          const itemValue = asText(record[valueName]);
          if (!key && !itemValue) return '';
          return itemValue ? `${key}: ${itemValue}` : key;
        })
        .filter(Boolean)
        .join(', ')
    : '';
const asTokenList = (value: unknown, key: string) =>
  Array.isArray(value)
    ? value
        .map((item) =>
          item && typeof item === 'object'
            ? asText((item as Record<string, unknown>)[key])
            : asText(item),
        )
        .filter(Boolean)
        .map((name) => `{{${name}}}`)
        .join(' ')
    : '';

export const VISIBLE_NODE_PROPERTIES: Partial<
  Record<NonNullable<AppNode['type']>, VisiblePropertyDefinition[]>
> = {
  startNode: [
    {
      key: 'triggerType',
      label: '트리거',
      getValue: (node) => asText(node.data.triggerType),
    },
    {
      key: 'variables',
      label: '입력변수',
      multiline: true,
      getValue: (node) => asList(node.data.variables, 'label'),
    },
  ],
  answerNode: [
    {
      key: 'outputs',
      label: '출력변수',
      multiline: true,
      getValue: (node) => asList(node.data.outputs, 'variable'),
    },
  ],
  httpRequestNode: [
    {
      key: 'method',
      label: 'Method',
      getValue: (node) => asText(node.data.method),
    },
    {
      key: 'url',
      label: 'URL',
      multiline: true,
      getValue: (node) => asText(node.data.url),
    },
    {
      key: 'body',
      label: 'Body',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.body)),
    },
    {
      key: 'timeout',
      label: 'Timeout',
      getValue: (node) => asText(node.data.timeout),
    },
  ],
  slackPostNode: [
    {
      key: 'slackMode',
      label: '전송 방식',
      getValue: (node) => asText(node.data.slackMode),
    },
    {
      key: 'channel',
      label: '채널',
      getValue: (node) => asText(node.data.channel),
    },
    {
      key: 'message',
      label: '메시지',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.message)),
    },
    {
      key: 'blocks',
      label: 'Blocks',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.blocks)),
    },
  ],
  conditionNode: [
    {
      key: 'cases',
      label: '분기',
      multiline: true,
      getValue: (node) => asList(node.data.cases, 'case_name'),
    },
  ],
  llmNode: [
    {
      key: 'model_id',
      label: '모델',
      getValue: (node) => String(node.data.model_id || '').replace(/^models\//, ''),
    },
    {
      key: 'system_prompt',
      label: '시스템 프롬프트',
      multiline: true,
      getValue: (node) => normalizePrompt(String(node.data.system_prompt || '')),
    },
    {
      key: 'user_prompt',
      label: '사용자 프롬프트',
      multiline: true,
      getValue: (node) => normalizePrompt(String(node.data.user_prompt || '')),
    },
    {
      key: 'assistant_prompt',
      label: '어시스턴트 프롬프트',
      multiline: true,
      getValue: (node) => normalizePrompt(String(node.data.assistant_prompt || '')),
    },
  ],
  templateNode: [
    {
      key: 'template',
      label: '템플릿',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.template)),
    },
    {
      key: 'variables',
      label: '입력변수',
      multiline: true,
      getValue: (node) => asTokenList(node.data.variables, 'name'),
    },
  ],
  codeNode: [
    {
      key: 'inputs',
      label: '입력변수',
      multiline: true,
      getValue: (node) => asList(node.data.inputs, 'name'),
    },
    {
      key: 'timeout',
      label: 'Timeout',
      getValue: (node) => asText(node.data.timeout),
    },
    {
      key: 'code',
      label: 'Python 코드',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.code)),
    },
  ],
  workflowNode: [
    {
      key: 'name',
      label: '워크플로우',
      getValue: (node) => asText(node.data.name),
    },
    {
      key: 'version',
      label: '버전',
      getValue: (node) => asText(node.data.version),
    },
    {
      key: 'inputs',
      label: '입력 매핑',
      multiline: true,
      getValue: (node) => asList(node.data.inputs, 'name'),
    },
    {
      key: 'outputs',
      label: '출력',
      multiline: true,
      getValue: (node) => asList(node.data.outputs, 'name'),
    },
  ],
  webhookTrigger: [
    {
      key: 'provider',
      label: 'Provider',
      getValue: (node) => asText(node.data.provider),
    },
    {
      key: 'variable_mappings',
      label: '추출 변수',
      multiline: true,
      getValue: (node) => asList(node.data.variable_mappings, 'variable_name'),
    },
  ],
  scheduleTrigger: [
    {
      key: 'cron_expression',
      label: 'Cron',
      getValue: (node) => asText(node.data.cron_expression),
    },
    {
      key: 'timezone',
      label: 'Timezone',
      getValue: (node) => asText(node.data.timezone),
    },
  ],
  fileExtractionNode: [
    {
      key: 'referenced_variables',
      label: '입력변수',
      multiline: true,
      getValue: (node) => asList(node.data.referenced_variables, 'name'),
    },
  ],
  variableExtractionNode: [
    {
      key: 'source_selector',
      label: '입력 소스',
      getValue: (node) =>
        Array.isArray(node.data.source_selector)
          ? node.data.source_selector.join('.')
          : '',
    },
    {
      key: 'mappings',
      label: '추출 변수',
      multiline: true,
      getValue: (node) => asList(node.data.mappings, 'name'),
    },
  ],
  githubNode: [
    {
      key: 'action',
      label: '작업',
      getValue: (node) => asText(node.data.action),
    },
    {
      key: 'repo',
      label: '저장소',
      getValue: (node) => {
        const owner = asText(node.data.repo_owner);
        const name = asText(node.data.repo_name);
        return owner && name ? `${owner}/${name}` : owner || name;
      },
    },
    {
      key: 'pr_number',
      label: 'PR',
      getValue: (node) => asText(node.data.pr_number),
    },
    {
      key: 'comment_body',
      label: '코멘트',
      multiline: true,
      getValue: (node) => normalizePrompt(asText(node.data.comment_body)),
    },
  ],
  mailNode: [
    {
      key: 'credential_id',
      label: 'Mail Credential',
      getValue: (node) => (node.data.credential_id ? '연결됨' : '연결 필요'),
    },
    {
      key: 'folder',
      label: '폴더',
      getValue: (node) => asText(node.data.folder),
    },
    {
      key: 'filters',
      label: '검색 조건',
      multiline: true,
      getValue: (node) =>
        asKeyValueList([
          { key: 'keyword', value: node.data.keyword },
          { key: 'sender', value: node.data.sender },
          { key: 'subject', value: node.data.subject },
        ]),
    },
  ],
  loopNode: [
    {
      key: 'loop_key',
      label: '반복 키',
      getValue: (node) => asText(node.data.loop_key),
    },
    {
      key: 'max_iterations',
      label: '최대 반복',
      getValue: (node) => asText(node.data.max_iterations),
    },
    {
      key: 'parallel_mode',
      label: '병렬',
      getValue: (node) => (node.data.parallel_mode ? 'on' : 'off'),
    },
    {
      key: 'error_strategy',
      label: '오류 처리',
      getValue: (node) => asText(node.data.error_strategy),
    },
  ],
};

export const getVisiblePropertyDefinitions = (node?: AppNode | null) => {
  if (!node?.type) return [];
  return VISIBLE_NODE_PROPERTIES[node.type] || [];
};

export const isPropertyVisible = (
  node: AppNode | undefined,
  propertyKey: string,
) =>
  Array.isArray(node?.data.visibleProperties) &&
  node.data.visibleProperties.includes(propertyKey);

export const getNextVisibleProperties = (
  visibleProperties: unknown,
  propertyKey: string,
) => {
  const current = Array.isArray(visibleProperties)
    ? visibleProperties.filter((item): item is string => typeof item === 'string')
    : [];

  if (current.includes(propertyKey)) {
    return current.filter((item) => item !== propertyKey);
  }

  return [...current, propertyKey];
};

export const renderTokenPreview = (
  value: string,
  tokenLabels: Record<string, string> = {},
) => {
  const parts: React.ReactNode[] = [];
  const tokenPattern = /{{\s*([^}]+?)\s*}}/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(value)) !== null) {
    if (match.index > lastIndex) {
      parts.push(value.slice(lastIndex, match.index));
    }
    const tokenName = match[1].trim();
    parts.push(
      <span
        key={`${tokenName}-${match.index}`}
        className="mx-0.5 inline-flex max-w-full items-center rounded-md border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-800 shadow-sm align-baseline"
      >
        {tokenLabels[tokenName] || tokenName}
      </span>,
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < value.length) {
    parts.push(value.slice(lastIndex));
  }

  return parts.length > 0 ? parts : value;
};
