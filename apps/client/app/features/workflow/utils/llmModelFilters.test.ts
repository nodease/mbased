import { describe, expect, it } from 'vitest';

import { isWorkflowChatModelOption } from './llmModelFilters';

const model = (overrides: Partial<Parameters<typeof isWorkflowChatModelOption>[0]>) => ({
  model_id_for_api_call: 'gpt-4.1',
  name: 'gpt-4.1',
  type: 'chat',
  provider_name: 'OpenAI',
  is_active: true,
  ...overrides,
});

describe('isWorkflowChatModelOption', () => {
  it('workflow LLM alias 모델은 provider와 무관하게 허용한다', () => {
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'gpt-5.5',
          name: 'GPT-5.5',
        }),
      ),
    ).toBe(true);
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'gpt-5.6-terra',
          name: 'GPT-5.6 Terra',
        }),
      ),
    ).toBe(true);
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'claude-sonnet-5',
          name: 'Claude Sonnet 5',
          provider_name: 'Anthropic',
        }),
      ),
    ).toBe(true);
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'models/gemini-3.5-flash',
          name: 'Gemini 3.5 Flash',
          provider_name: 'Google',
        }),
      ),
    ).toBe(true);
  });

  it('날짜 suffix 모델은 alias 중심 노출 정책에 따라 숨긴다', () => {
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'gpt-5.4-mini-2026-03-17',
          name: 'gpt-5.4-mini-2026-03-17',
        }),
      ),
    ).toBe(false);
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'gpt-5.2-pro-2025-12-11',
          name: 'gpt-5.2-pro-2025-12-11',
        }),
      ),
    ).toBe(false);
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'claude-3-5-sonnet-20241022',
          name: 'claude-3-5-sonnet-20241022',
          provider_name: 'Anthropic',
        }),
      ),
    ).toBe(false);
  });

  it('workflow 실행에서 제외한 모델은 alias여도 숨긴다', () => {
    expect(
      isWorkflowChatModelOption(
        model({
          model_id_for_api_call: 'gpt-5-mini',
          name: 'gpt-5-mini',
        }),
      ),
    ).toBe(false);
  });

  it('workflow LLM 노드에 맞지 않는 모델 용도는 숨긴다', () => {
    for (const id of [
      'text-embedding-3-small',
      'gpt-image-1',
      'gpt-audio',
      'gpt-realtime',
      'whisper-1',
      'tts-1',
      'omni-moderation-latest',
      'gpt-4o-transcribe',
      'sora-2',
      'gpt-4o-search-preview',
    ]) {
      expect(
        isWorkflowChatModelOption(
          model({
            model_id_for_api_call: id,
            name: id,
          }),
        ),
      ).toBe(false);
    }
  });
});
