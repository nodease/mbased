import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';

export type ParameterControlHydration =
  | { state: 'empty' }
  | { state: 'available'; value: unknown }
  | { state: 'unavailable' };

const SENSITIVE_PARAMETER_KEY =
  /(^|[_-])(api[_-]?key|secret|token|password|private[_-]?key)($|[_-])/i;
const CONDITION_BRANCH_PREFIX = 'condition_branch:';
const CONDITION_BRANCH_TARGETS_KEY =
  '_agent_builder_condition_branch_targets';
const CONDITION_BRANCH_NO_CONNECTION = '__agent_builder_no_connection__';
const LLM_PARAMETER_PATHS: Record<string, string[]> = {
  output_format_type: ['output_format', 'type'],
  output_json_schema: ['output_format', 'schema'],
  model_routing_refresh_every_runs: [
    'model_routing_policy',
    'refresh',
    'refresh_every_runs',
  ],
  model_routing_validation_budget_usd: [
    'model_routing_policy',
    'validation_budget_usd',
  ],
  model_routing_max_cohorts: ['model_routing_policy', 'max_cohorts'],
};
const NODE_PARAMETER_PATHS: Record<string, Record<string, string[]>> = {
  slackPostNode: {
    bot_token: ['authConfig', 'token'],
  },
};

const hasValue = (value: unknown) =>
  value !== undefined && value !== null && value !== '';

const sameSelector = (left: unknown, right: unknown) =>
  Array.isArray(left) &&
  Array.isArray(right) &&
  left.length === right.length &&
  left.every((item, index) => item === right[index]);

const nestedParameterValue = (
  task: AgentBuilderParameterTask,
  nodeData: Record<string, unknown>,
): { found: boolean; value?: unknown } => {
  const path =
    task.node_type === 'llmNode'
      ? LLM_PARAMETER_PATHS[task.parameter_key]
      : NODE_PARAMETER_PATHS[task.node_type]?.[task.parameter_key];
  if (!path) return { found: false };
  let value: unknown = nodeData;
  for (const key of path) {
    if (
      value === null ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      !Object.hasOwn(value, key)
    ) {
      return { found: false };
    }
    value = (value as Record<string, unknown>)[key];
  }
  return { found: true, value };
};

export const deriveParameterControlHydration = (
  task: AgentBuilderParameterTask,
  nodeData: Record<string, unknown> | null | undefined,
): ParameterControlHydration => {
  if (!nodeData) {
    return { state: 'empty' };
  }
  const branchHandle = task.parameter_key.startsWith(CONDITION_BRANCH_PREFIX)
    ? task.parameter_key.slice(CONDITION_BRANCH_PREFIX.length)
    : null;
  const branchTargets = nodeData[CONDITION_BRANCH_TARGETS_KEY];
  const isBranchTargetMap =
    branchTargets !== null &&
    typeof branchTargets === 'object' &&
    !Array.isArray(branchTargets);
  const nestedParameter = nestedParameterValue(task, nodeData);
  if (
    branchHandle &&
    (!isBranchTargetMap ||
      !Object.hasOwn(branchTargets as Record<string, unknown>, branchHandle))
  ) {
    return { state: 'empty' };
  }
  if (
    !branchHandle &&
    !Object.hasOwn(nodeData, task.parameter_key) &&
    !nestedParameter.found
  ) {
    return { state: 'empty' };
  }
  const branchTarget = branchHandle
    ? (branchTargets as Record<string, unknown>)[branchHandle]
    : undefined;
  const current = branchHandle
    ? branchTarget === null
      ? CONDITION_BRANCH_NO_CONNECTION
      : branchTarget
    : nestedParameter.found
      ? nestedParameter.value
      : nodeData[task.parameter_key];
  if (!hasValue(current)) return { state: 'empty' };
  if (
    task.sensitivity === 'secret_forbidden' ||
    SENSITIVE_PARAMETER_KEY.test(task.parameter_key)
  ) {
    return { state: 'unavailable' };
  }

  if (
    task.input_type === 'resource_ref' ||
    task.input_type === 'credential_ref'
  ) {
    const referenceId =
      typeof current === 'string'
        ? current
        : current && typeof current === 'object'
          ? Object.entries(current).find(
              ([key, value]) =>
                ['id', 'resource_id', 'credential_id'].includes(key) &&
                typeof value === 'string',
            )?.[1]
          : null;
    const candidate = task.candidates?.find(
      (item) =>
        item.candidate_id === referenceId ||
        (typeof referenceId === 'string' &&
          item.reference_value === referenceId),
    );
    return candidate
      ? { state: 'available', value: candidate.candidate_id }
      : { state: 'unavailable' };
  }

  if (task.input_type === 'variable_selector') {
    const storedSelector =
      task.node_type === 'fileExtractionNode' &&
      task.parameter_key === 'referenced_variables' &&
      Array.isArray(current) &&
      current.length === 1
        ? current[0]
        : current;
    const selector = Array.isArray(storedSelector)
      ? storedSelector
      : storedSelector && typeof storedSelector === 'object'
        ? (storedSelector as Record<string, unknown>).value_selector
        : null;
    const suggestion = task.suggestions?.find((item) =>
      sameSelector(item.value_selector, selector),
    );
    return suggestion
      ? { state: 'available', value: suggestion.suggestion_id }
      : { state: 'unavailable' };
  }

  if (task.input_type === 'variable_selector_list') {
    if (!Array.isArray(current) || current.length === 0) {
      return { state: 'empty' };
    }
    const selectors =
      task.node_type === 'llmNode' &&
      task.parameter_key === 'referenced_variables'
        ? current.map((item) =>
            item && typeof item === 'object' && !Array.isArray(item)
              ? (item as Record<string, unknown>).value_selector
              : item,
          )
        : current;
    const suggestionIds = selectors.map((selector) =>
      task.suggestions?.find((item) => sameSelector(item.value_selector, selector))
        ?.suggestion_id,
    );
    return suggestionIds.every(
      (suggestionId): suggestionId is string => typeof suggestionId === 'string',
    ) && new Set(suggestionIds).size === suggestionIds.length
      ? { state: 'available', value: suggestionIds }
      : { state: 'unavailable' };
  }

  if (task.input_type === 'boolean') {
    return typeof current === 'boolean'
      ? { state: 'available', value: current }
      : { state: 'unavailable' };
  }
  if (task.input_type === 'number') {
    if (
      task.node_type === 'githubNode' &&
      task.parameter_key === 'pr_number' &&
      typeof current === 'string' &&
      /^\d+$/.test(current)
    ) {
      return { state: 'available', value: Number(current) };
    }
    return typeof current === 'number' && Number.isFinite(current)
      ? { state: 'available', value: current }
      : { state: 'unavailable' };
  }
  if (task.input_type === 'json') {
    if (
      task.node_type === 'slackPostNode' &&
      ['blocks', 'attachments'].includes(task.parameter_key)
    ) {
      try {
        const parsed = typeof current === 'string' ? JSON.parse(current) : current;
        return Array.isArray(parsed)
          ? { state: 'available', value: parsed }
          : { state: 'unavailable' };
      } catch {
        return { state: 'unavailable' };
      }
    }
    try {
      JSON.stringify(current);
      return { state: 'available', value: current };
    } catch {
      return { state: 'unavailable' };
    }
  }
  if (task.input_type === 'select') {
    const options = Array.isArray(task.validation?.options)
      ? task.validation.options
      : [];
    return typeof current === 'string' && options.includes(current)
      ? { state: 'available', value: current }
      : { state: 'unavailable' };
  }
  return typeof current === 'string'
    ? { state: 'available', value: current }
    : { state: 'unavailable' };
};

export const parameterControlDisplayValue = (
  task: AgentBuilderParameterTask,
  hydration: ParameterControlHydration,
): string | null => {
  if (hydration.state !== 'available') return null;
  if (
    task.input_type === 'resource_ref' ||
    task.input_type === 'credential_ref'
  ) {
    return (
      task.candidates?.find(
        (candidate) => candidate.candidate_id === hydration.value,
      )?.label ?? null
    );
  }
  if (task.input_type === 'variable_selector') {
    const suggestion = task.suggestions?.find(
      (item) => item.suggestion_id === hydration.value,
    );
    return suggestion
      ? `${suggestion.label} (${suggestion.json_path})`
      : null;
  }
  if (
    task.input_type === 'variable_selector_list' &&
    Array.isArray(hydration.value)
  ) {
    const labels = hydration.value.flatMap((suggestionId) => {
      const suggestion = task.suggestions?.find(
        (item) => item.suggestion_id === suggestionId,
      );
      return suggestion
        ? [`${suggestion.label} (${suggestion.json_path})`]
        : [];
    });
    return labels.length === hydration.value.length ? labels.join(', ') : null;
  }
  if (task.input_type === 'boolean') {
    return hydration.value ? '사용' : '사용 안 함';
  }
  if (task.input_type === 'json') {
    try {
      return JSON.stringify(hydration.value, null, 2);
    } catch {
      return null;
    }
  }
  if (task.input_type === 'select') {
    const labels = task.validation?.option_labels;
    if (
      labels &&
      typeof labels === 'object' &&
      !Array.isArray(labels) &&
      typeof hydration.value === 'string'
    ) {
      const label = (labels as Record<string, unknown>)[hydration.value];
      if (typeof label === 'string') return label;
    }
  }
  return typeof hydration.value === 'string' ||
    typeof hydration.value === 'number'
    ? String(hydration.value)
    : null;
};
