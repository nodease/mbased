import type { CostOptimizerBaselineRow } from '@/app/features/workflow/types/Api';
import type { CostOptimizerCandidateRequest } from '@/app/features/workflow/types/Api';
import type { AppNode, LLMNodeData } from '@/app/features/workflow/types/Nodes';

export type KnowledgeBaseSelection = { id: string; name: string };
export type JsonSchemaFieldType =
  'string' | 'number' | 'boolean' | 'object' | 'array';
export type JsonSchemaField = {
  key: string;
  type: JsonSchemaFieldType;
  required: boolean;
};
type CandidateNumberParameterKey =
  | 'max_tokens'
  | 'temperature'
  | 'top_p'
  | 'presence_penalty'
  | 'frequency_penalty';

export type CandidateDraft = {
  model_id: string;
  fallback_model_id: string;
  auto_model_routing: boolean;
  model_routing_policy?: LLMNodeData['model_routing_policy'];
  task_type: string;
  system_prompt: string;
  user_prompt: string;
  assistant_prompt: string;
  referenced_variables: LLMNodeData['referenced_variables'];
  max_tokens: number;
  temperature: number;
  top_p: number;
  presence_penalty: number;
  frequency_penalty: number;
  stop: string[];
  removed_parameter_keys?: string[];
  output_format: 'text' | 'json';
  json_schema_fields: JsonSchemaField[];
  knowledgeBases: KnowledgeBaseSelection[];
  topK: number;
  scoreThreshold: number;
  dedupeRetrievedContext: boolean;
  retrievedContextMaxChars: number | null;
  retrievedContextCompression: 'off' | 'light' | 'strong';
  answerGroundingCheck: 'off' | 'basic' | 'strict';
};

export type BaselineNodeOptions = Partial<LLMNodeData> & {
  task_type?: string;
  output_format?:
    | { type?: 'text' | 'json'; schema?: Record<string, unknown> | null }
    | 'text'
    | 'json';
};

export type SettingsTab = 'basic' | 'advanced' | 'knowledge';
export type CostOptimizerContractChip = {
  key: string;
  source?: string;
  detail?: string;
  value?: string;
};

export const nodesFromDraft = (draft: unknown): AppNode[] => {
  const candidate = draft as {
    nodes?: AppNode[];
    graph?: { nodes?: AppNode[] };
  };
  return candidate.nodes || candidate.graph?.nodes || [];
};

export const findTargetNode = (
  draft: unknown,
  nodeId: string,
): AppNode | null => {
  const nodes = nodesFromDraft(draft);
  return nodes.find((node) => node.id === nodeId) || null;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isUsableKnowledgeBaseId = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.trim().length > 0 &&
  !value.includes('[REDACTED]');

const safeKnowledgeBaseSelections = (
  value: unknown,
): KnowledgeBaseSelection[] =>
  Array.isArray(value)
    ? value
        .filter(
          (knowledgeBase): knowledgeBase is KnowledgeBaseSelection =>
            isRecord(knowledgeBase) &&
            isUsableKnowledgeBaseId(knowledgeBase.id) &&
            typeof knowledgeBase.name === 'string',
        )
        .map((knowledgeBase) => ({
          id: knowledgeBase.id.trim(),
          name: knowledgeBase.name,
        }))
    : [];

const knowledgeBaseIdsFromSelections = (
  knowledgeBases: KnowledgeBaseSelection[],
) =>
  knowledgeBases
    .map((knowledgeBase) => knowledgeBase.id)
    .filter(isUsableKnowledgeBaseId);

const parseJsonPreview = (value: string): unknown => {
  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) {
    return value;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
};

const stringifyContractValue = (value: unknown): string | undefined => {
  if (value === null || value === undefined) return undefined;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const addUniqueContractChip = (
  chips: CostOptimizerContractChip[],
  chip: CostOptimizerContractChip,
) => {
  if (!chip.key.trim()) return;
  if (chips.some((item) => item.key === chip.key)) return;
  chips.push(chip);
};

const flattenInputContract = (
  value: unknown,
  chips: CostOptimizerContractChip[],
  path: string[] = [],
) => {
  if (value === null || value === undefined) return;

  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      flattenInputContract(item, chips, [...path, `[${index}]`]);
    });
    return;
  }

  if (isRecord(value)) {
    Object.entries(value).forEach(([key, item]) => {
      flattenInputContract(item, chips, [...path, key]);
    });
    return;
  }

  const key = path[path.length - 1] || 'input';
  const source = path.length > 1 ? path.slice(0, -1).join('.') : undefined;
  addUniqueContractChip(chips, {
    key,
    source,
    detail: path.join('.'),
    value: stringifyContractValue(value),
  });
};

export const inputContractChipsFromBaseline = (
  baseline: CostOptimizerBaselineRow | null,
): CostOptimizerContractChip[] => {
  if (!baseline) return [];
  const sourceValue =
    baseline.input !== undefined
      ? baseline.input
      : parseJsonPreview(baseline.input_preview || '');
  const chips: CostOptimizerContractChip[] = [];
  flattenInputContract(sourceValue, chips);
  return chips;
};

const selectorReferencesNode = (
  value: unknown,
  nodeId: string,
): value is string[] =>
  Array.isArray(value) &&
  value.length > 0 &&
  value.every((item) => typeof item === 'string') &&
  value[0] === nodeId;

const collectDirectSelectors = (
  value: unknown,
  nodeId: string,
  chips: CostOptimizerContractChip[],
  source?: string,
) => {
  if (selectorReferencesNode(value, nodeId)) {
    addUniqueContractChip(chips, {
      key: value[1] || 'output',
      source,
      detail: value.join('.'),
    });
    return;
  }

  if (Array.isArray(value)) {
    value.forEach((item) =>
      collectDirectSelectors(item, nodeId, chips, source),
    );
    return;
  }

  if (!isRecord(value)) return;

  Object.entries(value).forEach(([key, item]) => {
    if (
      key === 'value_selector' ||
      key === 'variable_selector' ||
      key === 'source_selector'
    ) {
      collectDirectSelectors(item, nodeId, chips, source);
      return;
    }
    collectDirectSelectors(item, nodeId, chips, source);
  });
};

export const downstreamOutputContractChipsFromNodes = (
  nodes: AppNode[],
  nodeId: string,
): CostOptimizerContractChip[] => {
  const chips: CostOptimizerContractChip[] = [];

  nodes
    .filter((node) => node.id !== nodeId)
    .forEach((node) => {
      const data = (node.data || {}) as Record<string, unknown>;
      const source =
        typeof data.title === 'string' && data.title.trim()
          ? data.title
          : node.id;

      if (
        node.type === 'variableExtractionNode' &&
        selectorReferencesNode(data.source_selector, nodeId)
      ) {
        const mappings = Array.isArray(data.mappings) ? data.mappings : [];
        mappings.forEach((mapping) => {
          if (!isRecord(mapping) || typeof mapping.name !== 'string') return;
          addUniqueContractChip(chips, {
            key: mapping.name,
            source,
            detail:
              typeof mapping.json_path === 'string'
                ? `JSON path: ${mapping.json_path}`
                : undefined,
          });
        });
        return;
      }

      collectDirectSelectors(data, nodeId, chips, source);
    });

  return chips;
};

const jsonSchemaFieldTypes = new Set<JsonSchemaFieldType>([
  'string',
  'number',
  'boolean',
  'object',
  'array',
]);

const schemaFieldsFromOutputFormat = (
  outputFormat: BaselineNodeOptions['output_format'],
): JsonSchemaField[] => {
  if (!isRecord(outputFormat) || !isRecord(outputFormat.schema)) return [];

  const properties = outputFormat.schema.properties;
  if (!isRecord(properties)) return [];

  const required = Array.isArray(outputFormat.schema.required)
    ? outputFormat.schema.required.filter(
        (field): field is string => typeof field === 'string',
      )
    : [];

  return Object.entries(properties).map(([key, propertySchema]) => {
    const type = isRecord(propertySchema) ? propertySchema.type : null;
    return {
      key,
      type:
        typeof type === 'string' &&
        jsonSchemaFieldTypes.has(type as JsonSchemaFieldType)
          ? (type as JsonSchemaFieldType)
          : 'string',
      required: required.includes(key),
    };
  });
};

const outputSchemaFromFields = (
  fields: JsonSchemaField[],
): Record<string, unknown> => {
  const normalizedFields = fields
    .map((field) => ({
      key: field.key.trim(),
      type: jsonSchemaFieldTypes.has(field.type) ? field.type : 'string',
      required: field.required,
    }))
    .filter((field) => field.key.length > 0);

  if (normalizedFields.length === 0) return {};

  return {
    type: 'object',
    properties: Object.fromEntries(
      normalizedFields.map((field) => [field.key, { type: field.type }]),
    ),
    required: normalizedFields
      .filter((field) => field.required)
      .map((field) => field.key),
  };
};

export const candidateFromOptions = (
  options: BaselineNodeOptions | null | undefined,
): CandidateDraft => {
  const data = options || {};
  const params = data.parameters || {};
  const outputFormat =
    typeof data.output_format === 'string'
      ? data.output_format
      : data.output_format?.type;

  return {
    model_id: data.model_id || '',
    fallback_model_id: data.fallback_model_id || '',
    auto_model_routing: Boolean(data.auto_model_routing),
    model_routing_policy: isRecord(data.model_routing_policy)
      ? data.model_routing_policy
      : undefined,
    task_type: data.task_type || 'generate',
    system_prompt: data.system_prompt || '',
    user_prompt: data.user_prompt || '',
    assistant_prompt: data.assistant_prompt || '',
    referenced_variables: data.referenced_variables || [],
    max_tokens:
      typeof params.max_tokens === 'number' ? params.max_tokens : 4096,
    temperature:
      typeof params.temperature === 'number' ? params.temperature : 0.7,
    top_p: typeof params.top_p === 'number' ? params.top_p : 1,
    presence_penalty:
      typeof params.presence_penalty === 'number' ? params.presence_penalty : 0,
    frequency_penalty:
      typeof params.frequency_penalty === 'number'
        ? params.frequency_penalty
        : 0,
    stop: Array.isArray(params.stop)
      ? params.stop.filter((item): item is string => typeof item === 'string')
      : [],
    removed_parameter_keys: [],
    output_format: outputFormat === 'json' ? 'json' : 'text',
    json_schema_fields: schemaFieldsFromOutputFormat(data.output_format),
    knowledgeBases: safeKnowledgeBaseSelections(data.knowledgeBases),
    topK: typeof data.topK === 'number' ? data.topK : 5,
    scoreThreshold:
      typeof data.scoreThreshold === 'number' ? data.scoreThreshold : 0.3,
    dedupeRetrievedContext: data.dedupeRetrievedContext ?? false,
    retrievedContextMaxChars:
      typeof data.retrievedContextMaxChars === 'number'
        ? data.retrievedContextMaxChars
        : null,
    retrievedContextCompression:
      data.retrievedContextCompression === 'light' ||
      data.retrievedContextCompression === 'strong'
        ? data.retrievedContextCompression
        : 'off',
    answerGroundingCheck:
      data.answerGroundingCheck === 'off' ||
      data.answerGroundingCheck === 'basic' ||
      data.answerGroundingCheck === 'strict'
        ? data.answerGroundingCheck
        : 'basic',
  };
};

export const candidateFromNode = (node: AppNode | null): CandidateDraft =>
  candidateFromOptions((node?.data || {}) as BaselineNodeOptions);

export const applyCandidatePatchToDraft = (
  draft: CandidateDraft,
  patch: Record<string, unknown>,
): CandidateDraft => {
  const next = { ...draft };
  const parameters = isRecord(patch.parameters) ? patch.parameters : {};
  const knowledge = isRecord(patch.knowledge) ? patch.knowledge : {};

  if (typeof patch.auto_model_routing === 'boolean') {
    next.auto_model_routing = patch.auto_model_routing;
  }
  if (isRecord(patch.model_routing_policy)) {
    next.model_routing_policy = {
      ...(next.model_routing_policy || {}),
      ...patch.model_routing_policy,
    } as LLMNodeData['model_routing_policy'];
  }

  const setNumberParameter = (key: CandidateNumberParameterKey) => {
    if (!(key in parameters)) return;
    if (parameters[key] === null) {
      next.removed_parameter_keys = [
        ...new Set([...(next.removed_parameter_keys || []), key]),
      ];
      return;
    }
    if (typeof parameters[key] === 'number') {
      next[key] = parameters[key];
      next.removed_parameter_keys = (next.removed_parameter_keys || []).filter(
        (removedKey) => removedKey !== key,
      );
    }
  };

  setNumberParameter('max_tokens');
  setNumberParameter('temperature');
  setNumberParameter('top_p');
  setNumberParameter('presence_penalty');
  setNumberParameter('frequency_penalty');
  if (Array.isArray(parameters.stop)) {
    next.stop = parameters.stop.filter(
      (item): item is string => typeof item === 'string',
    );
  }

  if (typeof knowledge.top_k === 'number') {
    next.topK = knowledge.top_k;
  }
  if (typeof knowledge.score_threshold === 'number') {
    next.scoreThreshold = knowledge.score_threshold;
  }
  if (typeof knowledge.dedupe_retrieved_context === 'boolean') {
    next.dedupeRetrievedContext = knowledge.dedupe_retrieved_context;
  }
  if (
    knowledge.retrieved_context_max_chars === null ||
    typeof knowledge.retrieved_context_max_chars === 'number'
  ) {
    next.retrievedContextMaxChars = knowledge.retrieved_context_max_chars;
  }
  if (
    knowledge.retrieved_context_compression === 'off' ||
    knowledge.retrieved_context_compression === 'light' ||
    knowledge.retrieved_context_compression === 'strong'
  ) {
    next.retrievedContextCompression =
      knowledge.retrieved_context_compression;
  }
  if (
    knowledge.answer_grounding_check === 'off' ||
    knowledge.answer_grounding_check === 'basic' ||
    knowledge.answer_grounding_check === 'strict'
  ) {
    next.answerGroundingCheck = knowledge.answer_grounding_check;
  }

  return next;
};

export const applyCandidatePatchesToDraft = (
  draft: CandidateDraft,
  patches: Array<Record<string, unknown>>,
): CandidateDraft =>
  patches.reduce<CandidateDraft>(
    (currentDraft, patch) => applyCandidatePatchToDraft(currentDraft, patch),
    draft,
  );

export const baselineOptionsOf = (
  baseline: CostOptimizerBaselineRow | null,
): BaselineNodeOptions | null =>
  isRecord(baseline?.node_options)
    ? (baseline.node_options as BaselineNodeOptions)
    : null;

export const llmDataFromCandidate = (
  candidate: CandidateDraft,
  title = 'B candidate',
) => {
  const removedParameters = new Set(candidate.removed_parameter_keys || []);
  const parameters: Record<string, unknown> = {
    max_tokens: candidate.max_tokens,
    temperature: candidate.temperature,
    top_p: candidate.top_p,
    presence_penalty: candidate.presence_penalty,
    frequency_penalty: candidate.frequency_penalty,
    stop: candidate.stop,
  };
  removedParameters.forEach((key) => {
    delete parameters[key];
  });

  return {
    title,
    provider: '',
    model_id: candidate.model_id,
    fallback_model_id: candidate.fallback_model_id,
    auto_model_routing: candidate.auto_model_routing,
    model_routing_policy: candidate.model_routing_policy,
    task_type: candidate.task_type,
    system_prompt: candidate.system_prompt,
    user_prompt: candidate.user_prompt,
    assistant_prompt: candidate.assistant_prompt,
    referenced_variables: candidate.referenced_variables,
    parameters,
    output_format: {
      type: candidate.output_format,
      schema:
        candidate.output_format === 'json'
          ? outputSchemaFromFields(candidate.json_schema_fields)
          : undefined,
    },
    knowledgeBases: candidate.knowledgeBases,
    topK: candidate.topK,
    scoreThreshold: candidate.scoreThreshold,
    dedupeRetrievedContext: candidate.dedupeRetrievedContext,
    retrievedContextMaxChars: candidate.retrievedContextMaxChars ?? undefined,
    retrievedContextCompression: candidate.retrievedContextCompression,
    answerGroundingCheck: candidate.answerGroundingCheck,
  };
};

export const compareRequestCandidateFromDraft = (
  candidate: CandidateDraft,
  label = 'B',
): CostOptimizerCandidateRequest => {
  const removedParameters = new Set(candidate.removed_parameter_keys || []);
  const parameters: Record<string, unknown> = {
    max_tokens: candidate.max_tokens,
    temperature: candidate.temperature,
    top_p: candidate.top_p,
    presence_penalty: candidate.presence_penalty,
    frequency_penalty: candidate.frequency_penalty,
    stop: candidate.stop,
  };
  removedParameters.forEach((key) => {
    delete parameters[key];
  });

  return {
    label,
    model_id: candidate.model_id,
    fallback_model_id: candidate.fallback_model_id || null,
    auto_model_routing: candidate.auto_model_routing,
    model_routing_policy: candidate.model_routing_policy,
    task_type: candidate.task_type,
    system_prompt: candidate.system_prompt,
    user_prompt: candidate.user_prompt,
    assistant_prompt: candidate.assistant_prompt,
    referenced_variables: candidate.referenced_variables,
    parameters,
    output_format: {
      type: candidate.output_format,
      schema:
        candidate.output_format === 'json'
          ? outputSchemaFromFields(candidate.json_schema_fields)
          : undefined,
    },
    knowledge: {
      knowledge_base_ids: knowledgeBaseIdsFromSelections(
        candidate.knowledgeBases,
      ),
      top_k: candidate.topK,
      score_threshold: candidate.scoreThreshold,
      dedupe_retrieved_context: candidate.dedupeRetrievedContext,
      retrieved_context_max_chars: candidate.retrievedContextMaxChars,
      retrieved_context_compression: candidate.retrievedContextCompression,
      answer_grounding_check: candidate.answerGroundingCheck,
    },
  };
};
