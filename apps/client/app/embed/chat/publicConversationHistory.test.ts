import { describe, expect, it } from 'vitest';

import {
  buildPublicConversationHistory,
  buildPublicConversationRunPath,
} from './publicConversationHistory';

describe('buildPublicConversationRunPath', () => {
  it('uses the dedicated public chat transport path and encodes the slug', () => {
    expect(buildPublicConversationRunPath('team/chat bot')).toBe(
      '/api/v1/run-public/team%2Fchat%20bot/chat',
    );
  });
});

describe('buildPublicConversationHistory', () => {
  it('sends only completed user/assistant turns', () => {
    expect(
      buildPublicConversationHistory([
        { id: 'welcome', role: 'assistant', content: 'welcome' },
        { id: 'user-1', role: 'user', content: 'question-1' },
        { id: 'assistant-1', role: 'assistant', content: 'answer-1' },
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
});
