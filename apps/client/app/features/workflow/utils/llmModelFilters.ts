export type WorkflowModelOption = {
  model_id_for_api_call: string;
  name: string;
  type: string;
  provider_name?: string;
  is_active: boolean;
};

const allowedModelAliases = new Set([
  'gpt-5.6',
  'gpt-5.6-sol',
  'gpt-5.6-terra',
  'gpt-5.6-luna',
  'gpt-5.5',
  'gpt-5.5-pro',
  'gpt-5.4-pro',
  'gpt-5.4',
  'gpt-5.4-mini',
  'gpt-5.4-nano',
  'gpt-5.2',
  'gpt-5.1',
  'gpt-5',
  'o3-pro',
  'o3',
  'gpt-4.1',
  'gpt-4o',
  'gpt-5-nano',
  'gpt-4.1-mini',
  'gpt-4o-mini',
  'claude-fable-5',
  'claude-opus-4-8',
  'claude-opus-4-7',
  'claude-opus-4-6',
  'claude-sonnet-5',
  'claude-sonnet-4-6',
  'claude-haiku-4-5',
  'gemini-3.5-flash',
  'gemini-3.1-pro-preview',
  'gemini-3.1-flash-lite',
  'gemini-3-flash-preview',
  'gemini-2.5-pro',
  'gemini-2.5-flash',
  'gemini-2.5-flash-lite',
]);

const blockedModelKeywords = [
  'embedding',
  'image',
  'audio',
  'realtime',
  'moderation',
  'tts',
  'whisper',
  'transcribe',
  'sora',
  'search',
];

const blockedModelTypes = new Set([
  'embedding',
  'image',
  'audio',
  'realtime',
  'moderation',
]);

const versionSuffixPattern = /-(?:\d{4}-\d{2}-\d{2}|\d{8})$/;

export const normalizeWorkflowModelId = (modelId: string) =>
  modelId.toLowerCase().replace(/^models\//, '');

export const isWorkflowChatModelOption = (model: WorkflowModelOption) => {
  const id = normalizeWorkflowModelId(model.model_id_for_api_call);
  const name = model.name.toLowerCase();
  const type = model.type.toLowerCase();

  if (model.is_active === false) return false;
  if (blockedModelTypes.has(type)) return false;
  if (blockedModelKeywords.some((keyword) => id.includes(keyword))) return false;
  if (blockedModelKeywords.some((keyword) => name.includes(keyword))) return false;
  if (versionSuffixPattern.test(id)) return false;

  return allowedModelAliases.has(id);
};
