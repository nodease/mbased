import { describe, expect, it } from 'vitest';

import {
  generateKnowledgeSafeLabel,
  generateKnowledgeSafeTopics,
  mergeKnowledgeSafeMetadata,
} from './knowledgeSafeMetadata';

describe('knowledge safe metadata helpers', () => {
  it('generates a safe label from the KB name without unsafe fragments', () => {
    expect(
      generateKnowledgeSafeLabel('People Ops KB https://internal.example/private'),
    ).toBe('People Ops KB');
  });

  it('generates de-duplicated topics from the KB name and description', () => {
    expect(
      generateKnowledgeSafeTopics({
        name: 'People Ops KB',
        description: 'Onboarding guide for benefits and onboarding',
      }),
    ).toEqual(['People', 'Ops', 'KB', 'Onboarding', 'guide', 'benefits']);
  });

  it('merges generated values into existing safe metadata without raw keys', () => {
    expect(
      mergeKnowledgeSafeMetadata(
        {
          kb_safe_description: 'People documents',
          raw_source_url: 'https://internal.example',
        },
        { safeLabel: 'People Ops', safeTopics: ['onboarding'] },
      ),
    ).toEqual({
      kb_safe_description: 'People documents',
      safe_label: 'People Ops',
      kb_safe_topics: ['onboarding'],
    });
  });
});
