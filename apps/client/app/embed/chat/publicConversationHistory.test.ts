import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  MAX_PUBLIC_CHAT_ENVELOPE_BYTES,
  MAX_PUBLIC_CHAT_MESSAGE_CHARS,
  buildPublicConversationHistory,
  buildPublicConversationRequest,
  buildPublicConversationRunPath,
  isPublicConversationHistoryContentEligible,
} from './publicConversationHistory';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('buildPublicConversationRunPath', () => {
  it('uses the dedicated public chat transport path and encodes the slug', () => {
    expect(buildPublicConversationRunPath('team/chat bot')).toBe(
      '/api/v1/run-public/team%2Fchat%20bot/chat',
    );
  });
});

describe('buildPublicConversationRequest', () => {
  it('uses client-held history only when the Gateway advertises V1', () => {
    expect(
      buildPublicConversationRequest(
        'chat',
        { question: 'now' },
        [
          { id: 'user-1', role: 'user', content: 'old' },
          {
            id: 'assistant-1',
            role: 'assistant',
            content: 'answer',
            historyEligible: true,
          },
        ],
        'client_history_v1',
        3,
      ),
    ).toEqual({
      path: '/api/v1/run-public/chat/chat',
      body: {
        inputs: { question: 'now' },
        deployment_version: 3,
        conversation: {
          history: [
            { role: 'user', content: 'old' },
            { role: 'assistant', content: 'answer' },
          ],
        },
      },
    });
  });

  it('uses a fresh one-time isolation ID for every legacy root request', () => {
    const randomUuids = [
      '00000000-0000-4000-8000-000000000001',
      '00000000-0000-4000-8000-000000000002',
    ];
    let randomUuidIndex = 0;
    const randomUuid = () => randomUuids[randomUuidIndex++];

    const requests = [0, 1].map(() =>
      buildPublicConversationRequest(
        'chat',
        { question: 'now' },
        [],
        undefined,
        4,
        randomUuid,
      ),
    );

    expect(requests[0]).toEqual({
      path: '/api/v1/run-public/chat',
      body: {
        inputs: {
          question: 'now',
          conversation_id:
            'public-once-v1:00000000-0000-4000-8000-000000000001',
        },
        deployment_version: 4,
      },
    });
    expect(requests[1].body.inputs).toEqual({
      question: 'now',
      conversation_id: 'public-once-v1:00000000-0000-4000-8000-000000000002',
    });
  });

  it('fails closed before a legacy root request without secure UUID support', () => {
    vi.stubGlobal('crypto', {});

    expect(() =>
      buildPublicConversationRequest(
        'chat',
        { question: 'now' },
        [],
        'legacy_v0',
        4,
      ),
    ).toThrow('Secure random UUID is unavailable.');
  });
});

describe('buildPublicConversationHistory', () => {
  it('sends only completed user/assistant turns', () => {
    expect(
      buildPublicConversationHistory([
        { id: 'welcome', role: 'assistant', content: 'welcome' },
        { id: 'user-1', role: 'user', content: 'question-1' },
        {
          id: 'assistant-1',
          role: 'assistant',
          content: 'answer-1',
          historyEligible: true,
        },
        { id: 'user-2', role: 'user', content: 'failed-question' },
        { id: 'error-2', role: 'assistant', content: 'request failed' },
        { id: 'user-3', role: 'user', content: 'pending-question' },
      ]),
    ).toEqual([
      { role: 'user', content: 'question-1' },
      { role: 'assistant', content: 'answer-1' },
    ]);
  });

  it('keeps the newest twenty completed turns', () => {
    const messages = Array.from({ length: 22 }, (_, index) => [
      { id: `user-${index}`, role: 'user' as const, content: `q-${index}` },
      {
        id: `assistant-${index}`,
        role: 'assistant' as const,
        content: `a-${index}`,
        historyEligible: true,
      },
    ]).flat();

    const history = buildPublicConversationHistory(messages);

    expect(history).toHaveLength(40);
    expect(history[0]).toEqual({ role: 'user', content: 'q-2' });
    expect(history.at(-1)).toEqual({ role: 'assistant', content: 'a-21' });
  });

  it('does not include a user message until its assistant answer exists', () => {
    expect(
      buildPublicConversationHistory([
        { id: 'user-1', role: 'user', content: 'pending' },
      ]),
    ).toEqual([]);
  });

  it('excludes a displayed fallback that was not produced by a successful run', () => {
    expect(
      buildPublicConversationHistory([
        { id: 'user-1', role: 'user', content: 'first question' },
        {
          id: 'assistant-1',
          role: 'assistant',
          content: '응답을 처리할 수 없습니다.',
          historyEligible: false,
        },
        { id: 'user-2', role: 'user', content: 'second question' },
        {
          id: 'assistant-2',
          role: 'assistant',
          content: 'actual answer',
          historyEligible: true,
        },
      ]),
    ).toEqual([
      { role: 'user', content: 'second question' },
      { role: 'assistant', content: 'actual answer' },
    ]);
  });

  it('excludes an oversized completed response without poisoning later turns', () => {
    const history = buildPublicConversationHistory([
      { id: 'user-1', role: 'user', content: 'first question' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'x'.repeat(MAX_PUBLIC_CHAT_MESSAGE_CHARS + 1),
        historyEligible: true,
      },
      { id: 'user-2', role: 'user', content: 'second question' },
      {
        id: 'assistant-2',
        role: 'assistant',
        content: 'second answer',
        historyEligible: true,
      },
    ]);

    expect(history).toEqual([
      { role: 'user', content: 'second question' },
      { role: 'assistant', content: 'second answer' },
    ]);
  });

  it('drops oldest completed pairs until the UTF-8 envelope is bounded', () => {
    const largeAnswer = '가'.repeat(16_000);
    const messages = Array.from({ length: 3 }, (_, index) => [
      { id: `user-${index}`, role: 'user' as const, content: `q-${index}` },
      {
        id: `assistant-${index}`,
        role: 'assistant' as const,
        content: largeAnswer,
        historyEligible: true,
      },
    ]).flat();

    const history = buildPublicConversationHistory(messages);
    const encoded = new TextEncoder().encode(JSON.stringify(history));

    expect(encoded.byteLength).toBeLessThanOrEqual(
      MAX_PUBLIC_CHAT_ENVELOPE_BYTES,
    );
    expect(history).toHaveLength(4);
    expect(history[0]).toEqual({ role: 'user', content: 'q-1' });
    expect(history.at(-2)).toEqual({ role: 'user', content: 'q-2' });
  });

  it('matches server Unicode character admission instead of UTF-16 units', () => {
    expect(
      isPublicConversationHistoryContentEligible(
        '😀'.repeat(MAX_PUBLIC_CHAT_MESSAGE_CHARS),
      ),
    ).toBe(true);
    expect(
      isPublicConversationHistoryContentEligible(
        '😀'.repeat(MAX_PUBLIC_CHAT_MESSAGE_CHARS + 1),
      ),
    ).toBe(false);
    expect(isPublicConversationHistoryContentEligible('\ud800')).toBe(false);
  });
});
