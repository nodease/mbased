export const LLM_TASK_TYPES = [
  { value: 'classify', label: '분류' },
  { value: 'extract', label: '추출' },
  { value: 'summarize', label: '요약' },
  { value: 'generate', label: '생성' },
  { value: 'reason', label: '추론' },
] as const;

export type LLMTaskType = (typeof LLM_TASK_TYPES)[number]['value'];
