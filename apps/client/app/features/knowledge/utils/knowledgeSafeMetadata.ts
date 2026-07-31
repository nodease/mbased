const SAFE_TEXT_MAX_LENGTH = 512;
const SAFE_LABEL_MAX_LENGTH = 255;
const SAFE_TOPIC_MAX_LENGTH = 64;
const SAFE_TOPICS_MAX_COUNT = 10;

const TOKEN_RE = /[가-힣]+[0-9]*|[A-Za-z0-9]+/g;
const URL_RE = /https?:\/\/[^\s,;]+/gi;
const EMAIL_RE = /\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g;
const PATH_RE = /((?:[A-Za-z]:\\|\\\\)[^\s,;]+|\/(?:[\w.-]+\/)+[\w.-]+)/gi;
const SECRET_KEY_VALUE_RE =
  /\b(?:api[_-]?key|token|password|secret|authorization|credential)\s*[:=]\s*['"]?[^'"\s,;]+/gi;
const SECRET_VALUE_RE =
  /(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|bearer\s+[A-Za-z0-9._-]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/gi;
const STOP_WORDS = new Set([
  'a',
  'an',
  'and',
  'for',
  'from',
  'of',
  'or',
  'the',
  'to',
  'with',
]);

type SafeMetadataDraft = {
  safeLabel?: string;
  safeTopics?: string[];
};

export function sanitizeKnowledgeSafeText(
  value: unknown,
  maxLength = SAFE_TEXT_MAX_LENGTH,
): string | null {
  if (typeof value !== 'string') return null;
  const sanitized = value
    .split('')
    .map((character) => (isControlCharacter(character) ? ' ' : character))
    .join('')
    .replace(SECRET_KEY_VALUE_RE, ' ')
    .replace(SECRET_VALUE_RE, ' ')
    .replace(URL_RE, ' ')
    .replace(EMAIL_RE, ' ')
    .replace(PATH_RE, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ');
  return sanitized ? sanitized.slice(0, maxLength) : null;
}

function isControlCharacter(character: string): boolean {
  const code = character.charCodeAt(0);
  return (
    (code >= 0x00 && code <= 0x08) ||
    code === 0x0b ||
    code === 0x0c ||
    (code >= 0x0e && code <= 0x1f) ||
    code === 0x7f
  );
}

export function generateKnowledgeSafeLabel(name: unknown): string {
  return sanitizeKnowledgeSafeText(name, SAFE_LABEL_MAX_LENGTH) ?? '';
}

export function generateKnowledgeSafeTopics(input: {
  name?: unknown;
  description?: unknown;
}): string[] {
  const topics: string[] = [];
  const seen = new Set<string>();
  for (const value of [input.name, input.description]) {
    const safeText = sanitizeKnowledgeSafeText(value);
    if (!safeText) continue;
    for (const match of safeText.matchAll(TOKEN_RE)) {
      const topic = match[0].slice(0, SAFE_TOPIC_MAX_LENGTH);
      const topicKey = topic.toLowerCase();
      if (topic.length < 2 || STOP_WORDS.has(topicKey) || seen.has(topicKey)) {
        continue;
      }
      topics.push(topic);
      seen.add(topicKey);
      if (topics.length >= SAFE_TOPICS_MAX_COUNT) return topics;
    }
  }
  return topics;
}

export function mergeKnowledgeSafeMetadata(
  current: Record<string, unknown> | undefined,
  draft: SafeMetadataDraft,
): Record<string, unknown> {
  const next: Record<string, unknown> = {};
  const currentLabel = sanitizeKnowledgeSafeText(
    current?.safe_label,
    SAFE_LABEL_MAX_LENGTH,
  );
  if (currentLabel) {
    next.safe_label = currentLabel;
  }
  const currentDescription = sanitizeKnowledgeSafeText(
    current?.kb_safe_description,
    SAFE_TEXT_MAX_LENGTH,
  );
  if (currentDescription) {
    next.kb_safe_description = currentDescription;
  }
  const currentTopics = sanitizeTopicValues(current?.kb_safe_topics);
  if (currentTopics.length > 0) {
    next.kb_safe_topics = currentTopics;
  }
  const safeLabel = sanitizeKnowledgeSafeText(
    draft.safeLabel,
    SAFE_LABEL_MAX_LENGTH,
  );
  if (safeLabel) {
    next.safe_label = safeLabel;
  }
  const safeTopics = sanitizeTopicValues(draft.safeTopics);
  if (safeTopics.length > 0) {
    next.kb_safe_topics = safeTopics;
  }
  return next;
}

export function readKnowledgeSafeLabel(
  safeMetadata: Record<string, unknown> | undefined,
): string {
  return sanitizeKnowledgeSafeText(safeMetadata?.safe_label, SAFE_LABEL_MAX_LENGTH) ?? '';
}

export function readKnowledgeSafeTopics(
  safeMetadata: Record<string, unknown> | undefined,
): string[] {
  return sanitizeTopicValues(safeMetadata?.kb_safe_topics);
}

function sanitizeTopicValues(value: unknown): string[] {
  const rawValues =
    typeof value === 'string'
      ? value.split(/[,;\n\r]+/)
      : Array.isArray(value)
        ? value
        : [];
  const topics: string[] = [];
  for (const rawValue of rawValues) {
    const safeTopic = sanitizeKnowledgeSafeText(rawValue, SAFE_TOPIC_MAX_LENGTH);
    if (!safeTopic || topics.includes(safeTopic)) continue;
    topics.push(safeTopic);
    if (topics.length >= SAFE_TOPICS_MAX_COUNT) break;
  }
  return topics;
}
