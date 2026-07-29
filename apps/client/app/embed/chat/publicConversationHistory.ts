export const MAX_PUBLIC_CHAT_TURNS = 20;
export const MAX_PUBLIC_CHAT_MESSAGE_CHARS = 32_768;
export const MAX_PUBLIC_CHAT_ENVELOPE_BYTES = 131_072;

export type PublicConversationContract = 'client_history_v1' | 'legacy_v0';

export const buildPublicConversationRunPath = (urlSlug: string): string =>
  `/api/v1/run-public/${encodeURIComponent(urlSlug)}/chat`;

const buildLegacyPublicRunPath = (urlSlug: string): string =>
  `/api/v1/run-public/${encodeURIComponent(urlSlug)}`;

const secureRandomUuid = (): string => {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi || typeof cryptoApi.randomUUID !== 'function') {
    throw new Error('Secure random UUID is unavailable.');
  }
  return cryptoApi.randomUUID();
};

const buildLegacyPublicConversationIsolationId = (
  randomUuid: () => string,
): string => `public-once-v1:${randomUuid()}`;

export interface PublicConversationHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface DisplayMessage extends PublicConversationHistoryMessage {
  id: string;
  historyEligible?: boolean;
}

export interface PublicConversationRequest {
  path: string;
  body: Record<string, unknown>;
}

export const buildPublicConversationRequest = (
  urlSlug: string,
  inputs: Record<string, unknown>,
  messages: readonly DisplayMessage[],
  contract: PublicConversationContract | undefined,
  deploymentVersion: number,
  randomUuid: () => string = secureRandomUuid,
): PublicConversationRequest => {
  if (contract === 'client_history_v1') {
    return {
      path: buildPublicConversationRunPath(urlSlug),
      body: {
        inputs,
        deployment_version: deploymentVersion,
        conversation: {
          history: buildPublicConversationHistory(messages),
        },
      },
    };
  }
  return {
    path: buildLegacyPublicRunPath(urlSlug),
    body: {
      inputs: {
        ...inputs,
        conversation_id:
          buildLegacyPublicConversationIsolationId(randomUuid),
      },
      deployment_version: deploymentVersion,
    },
  };
};

export const isPublicConversationHistoryContentEligible = (
  content: string,
): boolean => {
  if (!content.trim()) {
    return false;
  }

  let characterCount = 0;
  for (let index = 0; index < content.length; index += 1) {
    const codeUnit = content.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      if (index + 1 >= content.length) {
        return false;
      }
      const trailing = content.charCodeAt(index + 1);
      if (trailing < 0xdc00 || trailing > 0xdfff) {
        return false;
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
    characterCount += 1;
    if (characterCount > MAX_PUBLIC_CHAT_MESSAGE_CHARS) {
      return false;
    }
  }
  return true;
};

const encodedHistoryBytes = (
  history: readonly PublicConversationHistoryMessage[],
): number => new TextEncoder().encode(JSON.stringify(history)).byteLength;

export const buildPublicConversationHistory = (
  messages: readonly DisplayMessage[],
): PublicConversationHistoryMessage[] => {
  const turns: PublicConversationHistoryMessage[][] = [];
  let pendingUser: PublicConversationHistoryMessage | null = null;

  for (const message of messages) {
    if (message.id === 'welcome') {
      continue;
    }
    if (message.role === 'user') {
      pendingUser = isPublicConversationHistoryContentEligible(message.content)
        ? { role: 'user', content: message.content }
        : null;
      continue;
    }
    if (
      message.id.startsWith('error-') ||
      message.historyEligible !== true ||
      !isPublicConversationHistoryContentEligible(message.content)
    ) {
      pendingUser = null;
      continue;
    }
    if (!pendingUser) {
      continue;
    }
    turns.push([pendingUser, { role: 'assistant', content: message.content }]);
    pendingUser = null;
  }

  const boundedTurns = turns.slice(-MAX_PUBLIC_CHAT_TURNS);
  while (
    boundedTurns.length > 0 &&
    encodedHistoryBytes(boundedTurns.flat()) > MAX_PUBLIC_CHAT_ENVELOPE_BYTES
  ) {
    boundedTurns.shift();
  }

  return boundedTurns.flat();
};
