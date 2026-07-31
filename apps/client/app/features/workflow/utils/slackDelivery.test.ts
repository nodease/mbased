import { describe, expect, it } from 'vitest';

import {
  collectSlackTemplateVariables,
  isNonEmptySlackJsonArrayTemplate,
  isValidSlackJsonArrayTemplate,
  isValidCommercialSlackWebhookUrl,
} from './slackDelivery';

describe('Slack delivery client policy', () => {
  it('accepts only the canonical commercial incoming webhook shape', () => {
    expect(
      isValidCommercialSlackWebhookUrl(
        'https://hooks.slack.com/services/T_1/B-2/secret_3',
      ),
    ).toBe(true);
    expect(
      isValidCommercialSlackWebhookUrl(
        'https://hooks.slack.com/services/T/B/secret?query=1',
      ),
    ).toBe(false);
    expect(
      isValidCommercialSlackWebhookUrl(
        'https://slack.com/api/chat.postMessage',
      ),
    ).toBe(false);
    expect(
      isValidCommercialSlackWebhookUrl(
        'https://HOOKS.SLACK.COM/services/T/B/secret',
      ),
    ).toBe(false);
  });

  it('collects template variables from every rendered Slack field', () => {
    expect(
      collectSlackTemplateVariables(
        'message {{message}}',
        '[{"text":"{{block_value}}"}]',
        'channel {{channel}}',
      ),
    ).toEqual(['message', 'block_value', 'channel']);
  });

  it('validates JSON array templates without allowing dynamic keys', () => {
    expect(
      isValidSlackJsonArrayTemplate(
        '[{"type":"section","expand":{{expanded}}}]',
      ),
    ).toBe(true);
    expect(isValidSlackJsonArrayTemplate('{"type":"section"}')).toBe(false);
    expect(isValidSlackJsonArrayTemplate('[{"{{key}}":"value"}]')).toBe(
      false,
    );
    expect(isNonEmptySlackJsonArrayTemplate('[]')).toBe(false);
    expect(isNonEmptySlackJsonArrayTemplate('[{"type":"section"}]')).toBe(
      true,
    );
  });
});
