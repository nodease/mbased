export const MAX_PUBLIC_CHAT_TURNS = 20;

export const buildPublicConversationRunPath = (urlSlug: string): string =>
  `/api/v1/run-public/${encodeURIComponent(urlSlug)}/chat`;

export interface PublicConversationHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface DisplayMessage extends PublicConversationHistoryMessage {
  id: string;
}

export const buildPublicConversationHistory = (
  messages: readonly DisplayMessage[],
): PublicConversationHistoryMessage[] => {
  const turns: PublicConversationHistoryMessage[][] = [];
  let pendingUser: PublicConversationHistoryMessage | null = null;

  for (const message of messages) {
    if (
      message.id === 'welcome' ||
      message.id.startsWith('error-') ||
      !message.content.trim()
    ) {
      continue;
    }
    if (message.role === 'user') {
      pendingUser = { role: 'user', content: message.content };
      continue;
    }
    if (pendingUser) {
      turns.push([
        pendingUser,
        { role: 'assistant', content: message.content },
      ]);
      pendingUser = null;
    }
  }

  return turns.slice(-MAX_PUBLIC_CHAT_TURNS).flat();
};
