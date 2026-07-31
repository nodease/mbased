'use client';

import { useMemo, useState } from 'react';

import type { AgentBuilderParameterTask } from '../../api/agentBuilderApi';
import type { ParameterControlHydration } from './parameterControlHydration';

const isReferenceInput = (task: AgentBuilderParameterTask) =>
  task.input_type === 'resource_ref' || task.input_type === 'credential_ref';

export const ParameterInputRenderer = ({
  task,
  hydration = { state: 'empty' },
  onSubmit,
  onSkip,
  onClear,
  onSecretSubmit,
  disabled = false,
}: {
  task: AgentBuilderParameterTask;
  hydration?: ParameterControlHydration;
  onSubmit: (value: unknown) => void;
  onSkip?: () => void;
  onClear?: () => void;
  onSecretSubmit?: (
    task: AgentBuilderParameterTask,
    value: string,
  ) => boolean | Promise<boolean>;
  disabled?: boolean;
}) => {
  const hydratedValue =
    hydration.state === 'available' ? hydration.value : undefined;
  const [value, setValue] = useState(() => {
    if (task.input_type === 'json' && hydratedValue !== undefined) {
      return JSON.stringify(hydratedValue, null, 2);
    }
    if (typeof hydratedValue === 'number') return String(hydratedValue);
    return typeof hydratedValue === 'string' ? hydratedValue : '';
  });
  const [secretSubmitting, setSecretSubmitting] = useState(false);
  const [checked, setChecked] = useState(
    task.input_type === 'boolean' && hydratedValue === true,
  );
  const [suggestionId, setSuggestionId] = useState(
    task.input_type === 'variable_selector' && typeof hydratedValue === 'string'
      ? hydratedValue
      : '',
  );
  const [suggestionIds, setSuggestionIds] = useState<string[]>(
    task.input_type === 'variable_selector_list' && Array.isArray(hydratedValue)
      ? hydratedValue.filter((item): item is string => typeof item === 'string')
      : [],
  );
  const [candidateId, setCandidateId] = useState(
    isReferenceInput(task) && typeof hydratedValue === 'string'
      ? hydratedValue
      : '',
  );
  const [candidateQuery, setCandidateQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const isReference = isReferenceInput(task);
  const filteredCandidates = useMemo(() => {
    const query = candidateQuery.trim().toLocaleLowerCase();
    if (!query) return task.candidates ?? [];
    return (task.candidates ?? []).filter((candidate) =>
      `${candidate.label} ${candidate.description}`
        .toLocaleLowerCase()
        .includes(query),
    );
  }, [candidateQuery, task.candidates]);
  const selectOptions = useMemo(
    () =>
      Array.isArray(task.validation?.options)
        ? task.validation.options.filter(
            (option): option is string => typeof option === 'string',
          )
        : [],
    [task.validation],
  );
  const selectOptionLabels = useMemo(() => {
    const labels = task.validation?.option_labels;
    if (!labels || typeof labels !== 'object' || Array.isArray(labels)) {
      return {} as Record<string, string>;
    }
    return Object.fromEntries(
      Object.entries(labels).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string',
      ),
    );
  }, [task.validation]);
  const numberMinimum =
    typeof task.validation?.min === 'number' ? task.validation.min : undefined;
  const numberMaximum =
    typeof task.validation?.max === 'number' ? task.validation.max : undefined;
  const numberRequiresInteger = task.validation?.integer === true;

  const submit = () => {
    setError(null);
    if (
      !task.required &&
      ['code', 'json', 'secret', 'select', 'text', 'textarea'].includes(
        task.input_type,
      ) &&
      value.trim() === ''
    ) {
      if (hydration.state === 'available' && onClear) {
        onClear();
      } else if (onSkip) {
        onSkip();
      }
      return;
    }
    if (isReference) {
      if (!candidateId) {
        setError('사용 권한이 있는 항목을 선택하세요.');
        return;
      }
      onSubmit(candidateId);
      return;
    }
    if (task.input_type === 'boolean') {
      onSubmit(checked);
      return;
    }
    if (task.input_type === 'variable_selector') {
      const suggestion = task.suggestions?.find(
        (item) => item.suggestion_id === suggestionId,
      );
      if (!suggestion) {
        setError('연결할 이전 노드 출력을 선택하세요.');
        return;
      }
      onSubmit(suggestion);
      return;
    }
    if (task.input_type === 'variable_selector_list') {
      const selections = suggestionIds.flatMap((id) => {
        const suggestion = task.suggestions?.find(
          (item) => item.suggestion_id === id,
        );
        return suggestion ? [suggestion] : [];
      });
      if (selections.length === 0) {
        if (!task.required && hydration.state === 'empty' && onSkip) {
          onSkip();
          return;
        }
        if (!task.required && hydration.state !== 'empty' && onClear) {
          onClear();
          return;
        }
        setError('연결할 이전 노드 출력을 하나 이상 선택하세요.');
        return;
      }
      if (selections.length !== suggestionIds.length) {
        setError('연결할 이전 노드 출력을 하나 이상 선택하세요.');
        return;
      }
      onSubmit(selections);
      return;
    }
    if (task.input_type === 'number') {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        setError('숫자 값을 입력하세요.');
        return;
      }
      if (numberRequiresInteger && !Number.isInteger(parsed)) {
        setError('\uC815\uC218\uB97C \uC785\uB825\uD558\uC138\uC694.');
        return;
      }
      if (
        (numberMinimum !== undefined && parsed < numberMinimum) ||
        (numberMaximum !== undefined && parsed > numberMaximum)
      ) {
        if (numberMinimum !== undefined && numberMaximum !== undefined) {
          setError(
            `${numberMinimum} \uC774\uC0C1 ${numberMaximum} \uC774\uD558\uC758 \uAC12\uC744 \uC785\uB825\uD558\uC138\uC694.`,
          );
        } else if (numberMinimum !== undefined) {
          setError(
            `${numberMinimum} \uC774\uC0C1\uC758 \uAC12\uC744 \uC785\uB825\uD558\uC138\uC694.`,
          );
        } else {
          setError(
            `${numberMaximum} \uC774\uD558\uC758 \uAC12\uC744 \uC785\uB825\uD558\uC138\uC694.`,
          );
        }
        return;
      }
      onSubmit(parsed);
      return;
    }
    if (task.input_type === 'json') {
      try {
        const parsed = JSON.parse(value);
        if (
          task.node_type === 'llmNode' &&
          task.parameter_key === 'output_json_schema' &&
          (parsed === null ||
            Array.isArray(parsed) ||
            typeof parsed !== 'object')
        ) {
          setError('JSON 객체를 입력하세요.');
          return;
        }
        onSubmit(parsed);
      } catch {
        setError('유효한 JSON을 입력하세요.');
      }
      return;
    }
    onSubmit(value);
  };

  const multiline = ['json', 'textarea', 'code'].includes(task.input_type);
  if (task.input_type === 'secret') {
    return (
      <div
        className="space-y-2"
        onKeyDown={(event) => event.stopPropagation()}
      >
        <p className="text-xs text-amber-700 dark:text-amber-300">
          기존 값은 표시하지 않습니다. 새 값을 입력하면 기존 설정을 교체합니다.
        </p>
        <input
          aria-label={task.label}
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={disabled || secretSubmitting}
          autoComplete="off"
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:opacity-60 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100"
        />
        {error ? (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}
        <button
          type="button"
          onClick={async () => {
            setError(null);
            const trimmed = value.trim();
            if (!trimmed) {
              if (!task.required && hydration.state !== 'empty' && onClear) {
                onClear();
                return;
              }
              if (!task.required && onSkip) {
                onSkip();
                return;
              }
              setError('값을 입력하세요.');
              return;
            }
            if (!onSecretSubmit) {
              setError('보안 저장 경로를 사용할 수 없습니다.');
              return;
            }
            setSecretSubmitting(true);
            try {
              const stored = await onSecretSubmit(task, trimmed);
              if (stored) setValue('');
            } finally {
              setSecretSubmitting(false);
            }
          }}
          disabled={disabled || secretSubmitting}
          className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          적용
        </button>
      </div>
    );
  }
  if (
    hydration.state === 'unavailable' &&
    task.sensitivity === 'secret_forbidden'
  ) {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-300">
        사용할 수 없는 기존 설정입니다.
      </p>
    );
  }
  return (
    <div className="space-y-2" onKeyDown={(event) => event.stopPropagation()}>
      {hydration.state === 'unavailable' ? (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          사용할 수 없는 기존 설정입니다.
        </p>
      ) : null}
      {isReference ? (
        <>
          <input
            aria-label={`${task.label} 검색`}
            type="search"
            value={candidateQuery}
            disabled={disabled}
            onChange={(event) => setCandidateQuery(event.target.value)}
            placeholder="이름으로 검색"
            className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
          />
          <select
            aria-label={task.label}
            value={candidateId}
            disabled={disabled || filteredCandidates.length === 0}
            onChange={(event) => setCandidateId(event.target.value)}
            className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
          >
            <option value="">권한이 있는 항목 선택</option>
            {filteredCandidates.map((candidate) => (
              <option
                key={candidate.candidate_id}
                value={candidate.candidate_id}
              >
                {candidate.label}
              </option>
            ))}
          </select>
          {(task.candidates ?? []).length === 0 ? (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              선택 가능한 항목이 없습니다. 나중에 설정할 수 있습니다.
            </p>
          ) : null}
        </>
      ) : task.input_type === 'variable_selector' ? (
        <select
          aria-label={task.label}
          value={suggestionId}
          disabled={disabled}
          onChange={(event) => setSuggestionId(event.target.value)}
          className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
        >
          <option value="">이전 노드 출력 선택</option>
          {(task.suggestions ?? []).map((suggestion) => (
            <option
              key={suggestion.suggestion_id}
              value={suggestion.suggestion_id}
            >
              {suggestion.label} ({suggestion.json_path})
            </option>
          ))}
        </select>
      ) : task.input_type === 'variable_selector_list' ? (
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-neutral-700 dark:text-neutral-200">
            이전 노드 출력 선택
          </legend>
          {(task.suggestions ?? []).map((suggestion) => (
            <label
              key={suggestion.suggestion_id}
              className="flex items-start gap-2 text-sm text-neutral-700 dark:text-neutral-200"
            >
              <input
                type="checkbox"
                checked={suggestionIds.includes(suggestion.suggestion_id)}
                disabled={disabled}
                onChange={(event) =>
                  setSuggestionIds((current) =>
                    event.target.checked
                      ? [...current, suggestion.suggestion_id]
                      : current.filter((id) => id !== suggestion.suggestion_id),
                  )
                }
              />
              <span>
                {suggestion.label} ({suggestion.json_path})
              </span>
            </label>
          ))}
        </fieldset>
      ) : task.input_type === 'select' ? (
        <select
          aria-label={task.label}
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
          className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
        >
          <option value="">선택</option>
          {selectOptions.map((option) => (
            <option key={option} value={option}>
              {selectOptionLabels[option] ?? option}
            </option>
          ))}
        </select>
      ) : task.input_type === 'boolean' ? (
        <label className="flex items-center gap-2 text-sm text-neutral-700 dark:text-neutral-200">
          <input
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={(event) => setChecked(event.target.checked)}
          />
          {task.label}
        </label>
      ) : multiline ? (
        <textarea
          aria-label={task.label}
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
          rows={task.input_type === 'code' ? 6 : 3}
          className="w-full resize-y rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
        />
      ) : (
        <input
          aria-label={task.label}
          type={
            task.input_type === 'number' ? 'number' : 'text'
          }
          value={value}
          step={
            task.input_type === 'number' && numberRequiresInteger
              ? 1
              : undefined
          }
          min={task.input_type === 'number' ? numberMinimum : undefined}
          max={task.input_type === 'number' ? numberMaximum : undefined}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
          className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-neutral-700"
        />
      )}
      {error ? (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        onClick={submit}
        disabled={disabled}
        className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
      >
        적용
      </button>
    </div>
  );
};
