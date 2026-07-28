export const MAX_PUBLIC_CHAT_TURNS = 20;

export type PublicConversationContract = 'client_history_v1' | 'legacy_v0';

export const buildPublicConversationRunPath = (urlSlug: string): string =>
  `/api/v1/run-public/${encodeURIComponent(urlSlug)}/chat`;

const buildLegacyPublicRunPath = (urlSlug: string): string =>
  `/api/v1/run-public/${encodeURIComponent(urlSlug)}`;

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
): PublicConversationRequest => {
  if (contract === 'client_history_v1') {
    return {
      path: buildPublicConversationRunPath(urlSlug),
      body: {
        inputs,
        conversation: {
          history: buildPublicConversationHistory(messages),
        },
      },
    };
  }
  return {
    path: buildLegacyPublicRunPath(urlSlug),
    body: {
      inputs,
    },
  };
};

export const buildPublicConversationHistory = (
  messages: readonly DisplayMessage[],
): PublicConversationHistoryMessage[] => {
  const turns: PublicConversationHistoryMessage[][] = [];
  let pendingUser: PublicConversationHistoryMessage | null = null;

  for (const message of messages) {
    if (message.id === 'welcome' || !message.content.trim()) {
      continue;
    }
    if (message.role === 'user') {
      pendingUser = { role: 'user', content: message.content };
      continue;
    }
    if (
      message.id.startsWith('error-') ||
      message.historyEligible !== true
    ) {
      pendingUser = null;
      continue;
    }
    if (!pendingUser) {
      continue;
    }
    turns.push([
      pendingUser,
      { role: 'assistant', content: message.content },
    ]);
    pendingUser = null;
  }

  return turns.slice(-MAX_PUBLIC_CHAT_TURNS).flat();
};
