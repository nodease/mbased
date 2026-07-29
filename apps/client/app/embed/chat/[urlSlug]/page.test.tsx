import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import EmbedChatPage from './page';
import type { PublicConversationContract } from '../publicConversationHistory';

vi.mock('next/navigation', () => ({
  useParams: () => ({ urlSlug: 'public-chat' }),
}));

vi.mock('@/app/features/workflow/components/execution/CitationList', () => ({
  CitationList: () => null,
}));

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const deploymentInfo = (
  version: number,
  contract: PublicConversationContract,
) => ({
  url_slug: 'public-chat',
  name: `Public chatbot v${version}`,
  version,
  type: 'chatbot',
  public_conversation_contract: contract,
  input_schema: {
    variables: [{ name: 'question', type: 'text', label: 'Question' }],
  },
  output_schema: {
    outputs: [{ variable: 'answer', label: 'Answer' }],
  },
});

describe('EmbedChatPage deployment transition', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it.each([
    {
      from: 'client_history_v1' as const,
      to: 'legacy_v0' as const,
      firstPath: '/api/v1/run-public/public-chat/chat',
      retryPath: '/api/v1/run-public/public-chat',
    },
    {
      from: 'legacy_v0' as const,
      to: 'client_history_v1' as const,
      firstPath: '/api/v1/run-public/public-chat',
      retryPath: '/api/v1/run-public/public-chat/chat',
    },
  ])(
    'refreshes $from to $to, clears old history, and retries once',
    async ({ from, to, firstPath, retryPath }) => {
      const fetchMock = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(deploymentInfo(1, from)))
        .mockResolvedValueOnce(
          jsonResponse({
            status: 'success',
            results: { answer: 'First deployment answer' },
          }),
        )
        .mockResolvedValueOnce(
          jsonResponse(
            {
              detail: {
                code: 'conversation.deployment_version_changed',
                message: 'The active deployment changed.',
              },
            },
            409,
          ),
        )
        .mockResolvedValueOnce(jsonResponse(deploymentInfo(2, to)))
        .mockResolvedValueOnce(
          jsonResponse({
            status: 'success',
            results: { answer: 'New deployment answer' },
          }),
        );
      vi.stubGlobal('fetch', fetchMock);

      render(<EmbedChatPage />);

      expect(
        await screen.findByRole('heading', { name: 'Public chatbot v1' }),
      ).toBeVisible();

      fireEvent.change(screen.getByRole('textbox'), {
        target: { value: 'First question' },
      });
      fireEvent.click(screen.getByRole('button'));
      expect(await screen.findByText('First deployment answer')).toBeVisible();

      fireEvent.change(screen.getByRole('textbox'), {
        target: { value: 'Question after redeploy' },
      });
      fireEvent.click(screen.getByRole('button'));

      expect(await screen.findByText('New deployment answer')).toBeVisible();
      expect(
        screen.getByRole('heading', { name: 'Public chatbot v2' }),
      ).toBeVisible();
      expect(screen.queryByText('First question')).not.toBeInTheDocument();
      expect(
        screen.queryByText('First deployment answer'),
      ).not.toBeInTheDocument();
      expect(screen.getByText('Question after redeploy')).toBeVisible();

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
      expect(fetchMock.mock.calls[0]).toEqual([
        '/api/v1/deployments/public/public-chat/info',
        { cache: 'no-store' },
      ]);
      expect(fetchMock.mock.calls[1]?.[0]).toBe(firstPath);
      expect(fetchMock.mock.calls[2]?.[0]).toBe(firstPath);
      expect(fetchMock.mock.calls[3]).toEqual([
        '/api/v1/deployments/public/public-chat/info',
        { cache: 'no-store' },
      ]);
      expect(fetchMock.mock.calls[4]?.[0]).toBe(retryPath);

      const staleBody = JSON.parse(
        String(fetchMock.mock.calls[2]?.[1]?.body),
      ) as Record<string, unknown>;
      expect(staleBody.deployment_version).toBe(1);

      const retryBody = JSON.parse(
        String(fetchMock.mock.calls[4]?.[1]?.body),
      ) as Record<string, unknown>;
      expect(retryBody.deployment_version).toBe(2);
      if (to === 'client_history_v1') {
        expect(retryBody.conversation).toEqual({ history: [] });
      } else {
        expect(retryBody).not.toHaveProperty('conversation');
      }
    },
  );

  it('falls back once without history when a mixed Gateway does not support /chat', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(deploymentInfo(1, 'client_history_v1')),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: 'Not Found' }, 404))
      .mockResolvedValueOnce(
        jsonResponse({
          status: 'success',
          results: { answer: 'Legacy fallback answer' },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<EmbedChatPage />);

    expect(
      await screen.findByRole('heading', { name: 'Public chatbot v1' }),
    ).toBeVisible();

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Mixed gateway question' },
    });
    fireEvent.click(screen.getByRole('button'));

    expect(await screen.findByText('Legacy fallback answer')).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      '/api/v1/run-public/public-chat/chat',
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      '/api/v1/run-public/public-chat',
    );

    const historyRequestBody = JSON.parse(
      String(fetchMock.mock.calls[1]?.[1]?.body),
    ) as Record<string, unknown>;
    expect(historyRequestBody).toEqual({
      inputs: { question: 'Mixed gateway question' },
      deployment_version: 1,
      conversation: { history: [] },
    });

    const fallbackRequestBody = JSON.parse(
      String(fetchMock.mock.calls[2]?.[1]?.body),
    ) as Record<string, unknown>;
    expect(fallbackRequestBody).toEqual({
      inputs: {
        question: 'Mixed gateway question',
        conversation_id: expect.stringMatching(
          /^public-once-v1:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
      },
      deployment_version: 1,
    });
    expect(fallbackRequestBody).not.toHaveProperty('conversation');
    expect(fallbackRequestBody).not.toHaveProperty('memory_mode');
  });

  it('does not loop when the one legacy fallback also returns 404', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(deploymentInfo(1, 'client_history_v1')),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: 'Not Found' }, 404))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Not Found' }, 404));
    vi.stubGlobal('fetch', fetchMock);

    render(<EmbedChatPage />);

    expect(
      await screen.findByRole('heading', { name: 'Public chatbot v1' }),
    ).toBeVisible();

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Unavailable mixed gateway question' },
    });
    fireEvent.click(screen.getByRole('button'));

    expect(
      await screen.findByText(
        '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      ),
    ).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});
