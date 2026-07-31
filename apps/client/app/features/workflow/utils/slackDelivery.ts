const SLACK_TEMPLATE_TOKEN = /{{\s*([^}]+?)\s*}}/g;
const STRICT_SLACK_TEMPLATE_TOKEN =
  /{{\s*([A-Za-z_][A-Za-z0-9_]{0,127})\s*}}/g;
const SLACK_TEMPLATE_TOKEN_AT_INDEX =
  /{{\s*([A-Za-z_][A-Za-z0-9_]{0,127})\s*}}/y;
const UNSAFE_SLACK_TEMPLATE_MARKERS = ['{{', '{%', '%}', '{#', '#}'];
const SLACK_WEBHOOK_PATH =
  /^\/services\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/;

export const isValidCommercialSlackWebhookUrl = (value: unknown): boolean => {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    new TextEncoder().encode(value).length > 2048 ||
    !value.startsWith('https://hooks.slack.com/services/')
  )
    return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === 'https:' &&
      url.hostname === 'hooks.slack.com' &&
      url.port === '' &&
      url.username === '' &&
      url.password === '' &&
      url.search === '' &&
      url.hash === '' &&
      SLACK_WEBHOOK_PATH.test(url.pathname)
    );
  } catch {
    return false;
  }
};

export const collectSlackTemplateVariables = (
  ...values: unknown[]
): string[] => {
  const names = new Set<string>();
  for (const value of values) {
    if (typeof value !== 'string') continue;
    for (const match of value.matchAll(SLACK_TEMPLATE_TOKEN)) {
      const name = match[1].trim();
      if (name) names.add(name);
    }
  }
  return Array.from(names);
};

const parseSlackJsonArrayTemplateShape = (value: string): unknown[] | null => {
  const withoutTokens = value.replace(STRICT_SLACK_TEMPLATE_TOKEN, '');
  if (
    UNSAFE_SLACK_TEMPLATE_MARKERS.some((marker) =>
      withoutTokens.includes(marker),
    )
  )
    return null;

  const output: string[] = [];
  let index = 0;
  let inString = false;
  let escaped = false;
  let stringContainsToken = false;
  while (index < value.length) {
    SLACK_TEMPLATE_TOKEN_AT_INDEX.lastIndex = index;
    const match = SLACK_TEMPLATE_TOKEN_AT_INDEX.exec(value);
    if (match) {
      if (escaped) return null;
      stringContainsToken = inString;
      output.push(inString ? 'x' : 'null');
      index = SLACK_TEMPLATE_TOKEN_AT_INDEX.lastIndex;
      continue;
    }

    const char = value[index];
    output.push(char);
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
        if (stringContainsToken) {
          let lookahead = index + 1;
          while (/\s/.test(value[lookahead] || '')) lookahead += 1;
          if (value[lookahead] === ':') return null;
        }
        stringContainsToken = false;
      }
    } else if (char === '"') {
      inString = true;
      stringContainsToken = false;
    }
    index += 1;
  }

  try {
    const parsed: unknown = JSON.parse(output.join(''));
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

export const isValidSlackJsonArrayTemplate = (value: string): boolean =>
  parseSlackJsonArrayTemplateShape(value) !== null;

export const isNonEmptySlackJsonArrayTemplate = (value: string): boolean =>
  (parseSlackJsonArrayTemplateShape(value)?.length || 0) > 0;
