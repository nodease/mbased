import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/csrfToken', () => ({
  csrfFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, init),
}));

vi.mock('@/lib/activeOrganization', () => ({
  activeOrganizationHeaders: vi.fn((organizationId: string | null) =>
    organizationId ? { 'X-Organization-Id': organizationId } : {},
  ),
  getStoredActiveOrganizationId: vi.fn(() => 'org-1'),
}));

vi.mock('@/lib/apiClient', () => ({
  apiBaseUrl: 'http://localhost:8000/api/v1',
  apiClient: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

import { knowledgeApi, RAGAgentStreamEvent } from './knowledgeApi';
import { apiClient } from '@/lib/apiClient';
import { getStoredActiveOrganizationId } from '@/lib/activeOrganization';

const streamResponse = (
  chunks: Array<string | Uint8Array>,
  status = 200,
): Response => {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(
          typeof chunk === 'string' ? encoder.encode(chunk) : chunk,
        );
      }
      controller.close();
    },
  });

  return new Response(body, { status });
};

const payload = {
  knowledge_base_id: 'kb-1',
  query: 'policy',
  generation_model_id: 'model-1',
  credential_id: 'credential-1',
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('knowledgeApi.getProgressUrl', () => {
  it('includes the active organization for native EventSource authorization', () => {
    vi.mocked(getStoredActiveOrganizationId).mockReturnValueOnce(
      'org/with space',
    );

    expect(knowledgeApi.getProgressUrl('document-1')).toBe(
      'http://localhost:8000/api/v1/rag/document/document-1/progress?organizationId=org%2Fwith%20space',
    );
  });

  it('fails closed when the active organization is missing', () => {
    vi.mocked(getStoredActiveOrganizationId).mockReturnValueOnce(null);

    expect(() => knowledgeApi.getProgressUrl('document-1')).toThrow(
      'Active organization is required for document progress.',
    );
  });
});

describe('knowledgeApi.getPresignedUploadUrl', () => {
  it('binds the storage preflight to the target knowledge base', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        upload_url: 'https://storage.invalid/upload',
        s3_key: 'opaque-key',
        method: 'PUT',
      },
    });

    await knowledgeApi.getPresignedUploadUrl(
      'policy.pdf',
      'application/pdf',
      'kb-1',
    );

    expect(apiClient.post).toHaveBeenCalledWith('/rag/upload/presigned-url', {
      filename: 'policy.pdf',
      content_type: 'application/pdf',
      knowledgeBaseId: 'kb-1',
    });
  });

  it('preserves generic workflow input uploads without a knowledge base', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        upload_url: 'https://storage.invalid/upload',
        s3_key: 'opaque-key',
        method: 'PUT',
      },
    });

    await knowledgeApi.getPresignedUploadUrl('input.pdf', 'application/pdf');

    expect(apiClient.post).toHaveBeenCalledWith('/rag/upload/presigned-url', {
      filename: 'input.pdf',
      content_type: 'application/pdf',
    });
  });
});

describe('knowledgeApi.getDocumentEditConfig', () => {
  it('uses the write-scoped document configuration endpoint', async () => {
    const response = {
      editable: true,
      source_type: 'FILE',
      chunk_size: 800,
      chunk_overlap: 80,
    };
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: response });

    await expect(
      knowledgeApi.getDocumentEditConfig('kb-1', 'document-1'),
    ).resolves.toEqual(response);
    expect(apiClient.get).toHaveBeenCalledWith(
      '/knowledge/kb-1/documents/document-1/edit-config',
    );
  });
});

describe('Knowledge Collection sync API', () => {
  it('sends a canonical idempotency header without adding source configuration', async () => {
    const response = {
      job: {
        job_id: 'job-1',
        collection_id: 'collection-1',
        status: 'queued',
        progress: 'none',
        retryable: true,
        requested_at: '2026-07-14T00:00:00Z',
      },
      reused: false,
      dispatch_deferred: false,
    };
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: response });

    await expect(
      knowledgeApi.requestKnowledgeCollectionSync(
        'collection-1',
        '11111111-1111-4111-8111-111111111111',
      ),
    ).resolves.toEqual(response);
    expect(apiClient.post).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/sync-jobs',
      {},
      {
        headers: {
          'Idempotency-Key': '11111111-1111-4111-8111-111111111111',
        },
      },
    );
  });

  it('loads latest and specific safe job projections', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { job: null } })
      .mockResolvedValueOnce({
        data: {
          job_id: 'job-1',
          collection_id: 'collection-1',
          status: 'succeeded',
          progress: 'complete',
          retryable: false,
          requested_at: '2026-07-14T00:00:00Z',
        },
      });

    await knowledgeApi.getLatestKnowledgeCollectionSyncJob('collection-1');
    await knowledgeApi.getKnowledgeCollectionSyncJob('collection-1', 'job-1');

    expect(apiClient.get).toHaveBeenNthCalledWith(
      1,
      '/knowledge/collections/collection-1/sync-jobs/latest',
    );
    expect(apiClient.get).toHaveBeenNthCalledWith(
      2,
      '/knowledge/collections/collection-1/sync-jobs/job-1',
    );
  });
});

describe('knowledgeApi.getDocumentContent', () => {
  it('fetches the original through the organization-scoped API client', async () => {
    const content = new Blob(['pdf'], { type: 'application/pdf' });
    const controller = new AbortController();
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: content });

    await expect(
      knowledgeApi.getDocumentContent('kb-1', 'document-1', controller.signal),
    ).resolves.toBe(content);
    expect(apiClient.get).toHaveBeenCalledWith(
      '/knowledge/kb-1/documents/document-1/content',
      {
        headers: { 'X-Organization-Id': 'org-1' },
        responseType: 'blob',
        signal: controller.signal,
      },
    );
  });

  it('fails closed before the request when active organization is missing', async () => {
    vi.mocked(getStoredActiveOrganizationId).mockReturnValueOnce(null);

    await expect(
      knowledgeApi.getDocumentContent('kb-1', 'document-1'),
    ).rejects.toThrow('Active organization is required for document content.');
    expect(apiClient.get).not.toHaveBeenCalled();
  });
});

describe('knowledgeApi.streamAgentAnswer', () => {
  it('sends the active organization header and emits streamed events', async () => {
    const events: RAGAgentStreamEvent[] = [];
    const fetchMock = vi.fn(async () =>
      streamResponse([
        'event: retrieval.started\n',
        'data: {"answer_run_id":"run-1","correlation_id":"corr-1"}\n\n',
        'event: answer.completed\n',
        'data: {"answer_run_id":"run-1","status":"completed"}\n\n',
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);

    await knowledgeApi.streamAgentAnswer(payload, (event) =>
      events.push(event),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/rag/agent/answer/stream',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'X-Organization-Id': 'org-1',
        }),
        body: JSON.stringify(payload),
      }),
    );
    expect(events.map((event) => event.event)).toEqual([
      'retrieval.started',
      'answer.completed',
    ]);
  });

  it('raises terminal SSE error events after notifying the consumer', async () => {
    const events: RAGAgentStreamEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: retrieval.started\n',
          'data: {"answer_run_id":"run-1","correlation_id":"corr-1"}\n\n',
          'event: error\n',
          'data: {"reason_code":"pii_policy_blocked","retryable":false}\n\n',
        ]),
      ),
    );

    await expect(
      knowledgeApi.streamAgentAnswer(payload, (event) => events.push(event)),
    ).rejects.toThrow('RAG answer stream failed: pii_policy_blocked');
    expect(events.map((event) => event.event)).toEqual([
      'retrieval.started',
      'error',
    ]);
  });

  it('parses the final buffered SSE event when the stream closes', async () => {
    const events: RAGAgentStreamEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: summary\n',
          'data: {"answer_run_id":"run-1","status":"completed"}',
        ]),
      ),
    );

    await knowledgeApi.streamAgentAnswer(payload, (event) =>
      events.push(event),
    );

    expect(events).toEqual([
      {
        event: 'summary',
        data: { answer_run_id: 'run-1', status: 'completed' },
      },
    ]);
  });

  it('raises a final buffered SSE error after notifying the consumer', async () => {
    const events: RAGAgentStreamEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: error\n',
          'data: {"reason_code":"stream.timeout","retryable":true}',
        ]),
      ),
    );

    await expect(
      knowledgeApi.streamAgentAnswer(payload, (event) => events.push(event)),
    ).rejects.toThrow('RAG answer stream failed: stream.timeout');
    expect(events).toEqual([
      {
        event: 'error',
        data: { reason_code: 'stream.timeout', retryable: true },
      },
    ]);
  });

  it('flushes the decoder before parsing the final buffered event', async () => {
    const events: RAGAgentStreamEvent[] = [];
    const encoded = new TextEncoder().encode(
      'event: summary\n' + 'data: {"answer_run_id":"run-1","message":"완료"}',
    );
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          encoded.slice(0, encoded.length - 2),
          encoded.slice(encoded.length - 2),
        ]),
      ),
    );

    await knowledgeApi.streamAgentAnswer(payload, (event) =>
      events.push(event),
    );

    expect(events).toEqual([
      {
        event: 'summary',
        data: { answer_run_id: 'run-1', message: '완료' },
      },
    ]);
  });

  it('parses CRLF separated SSE events', async () => {
    const events: RAGAgentStreamEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: retrieval.started\r\n',
          'data: {"answer_run_id":"run-1","correlation_id":"corr-1"}\r\n\r\n',
          'event: answer.completed\r\n',
          'data: {"answer_run_id":"run-1","status":"completed"}\r\n\r\n',
        ]),
      ),
    );

    await knowledgeApi.streamAgentAnswer(payload, (event) =>
      events.push(event),
    );

    expect(events).toEqual([
      {
        event: 'retrieval.started',
        data: { answer_run_id: 'run-1', correlation_id: 'corr-1' },
      },
      {
        event: 'answer.completed',
        data: { answer_run_id: 'run-1', status: 'completed' },
      },
    ]);
  });

  it('parses multi-line data SSE events', async () => {
    const events: RAGAgentStreamEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: summary\n',
          'data: {"answer_run_id":"run-1",\n',
          'data: "status":"completed"}\n\n',
        ]),
      ),
    );

    await knowledgeApi.streamAgentAnswer(payload, (event) =>
      events.push(event),
    );

    expect(events).toEqual([
      {
        event: 'summary',
        data: { answer_run_id: 'run-1', status: 'completed' },
      },
    ]);
  });

  it('uses sanitized HTTP error messages from the API envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: {
                code: 'permission.denied',
                message: 'Permission denied.',
              },
            }),
            {
              status: 403,
              headers: { 'Content-Type': 'application/json' },
            },
          ),
      ),
    );

    await expect(
      knowledgeApi.streamAgentAnswer(payload, () => undefined),
    ).rejects.toThrow('Permission denied.');
  });
});

describe('knowledgeApi safe failure logging', () => {
  it('does not log raw upload errors or response payloads', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 500,
        data: { detail: 'raw-response-payload-should-not-be-logged' },
      },
      config: {
        headers: { 'X-Test-Debug': 'request-config-should-not-be-logged' },
      },
    };
    vi.mocked(apiClient.post).mockRejectedValueOnce(error);

    await expect(
      knowledgeApi.uploadKnowledgeBase({
        name: '사내 문서',
        description: '테스트',
        embeddingModel: 'text-embedding-3-small',
        topK: 5,
        similarity: 0.7,
        chunkSize: 1000,
        chunkOverlap: 100,
        apiHeaders: '{"X-Test-Debug":"request-config-should-not-be-logged"}',
        apiBody: '{"payload":"request-body-should-not-be-logged"}',
      }),
    ).rejects.toBe(error);

    expect(warnSpy).toHaveBeenCalledWith('[knowledgeApi] request failed', {
      operation: 'uploadKnowledgeBase',
      status: 500,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });

  it('logs only safe list failure metadata', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = {
      response: {
        status: 403,
        data: { detail: 'hidden-resource-name' },
      },
    };
    vi.mocked(apiClient.get).mockRejectedValueOnce(error);

    await expect(knowledgeApi.getKnowledgeBases()).rejects.toBe(error);

    expect(warnSpy).toHaveBeenCalledWith('[knowledgeApi] request failed', {
      operation: 'getKnowledgeBases',
      status: 403,
    });
    expect(warnSpy).not.toHaveBeenCalledWith(expect.anything(), error);
  });
});

describe('knowledgeApi collection management', () => {
  it('loads the route-safe Workflow Collection picker projection', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        collections: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            safe_label: '사내 문서',
          },
        ],
      },
    });

    const response = await knowledgeApi.getLLMSelectableKnowledgeCollections();

    expect(apiClient.get).toHaveBeenCalledWith(
      '/knowledge/llm-selectable-collections',
    );
    expect(response.collections).toEqual([
      {
        id: '11111111-1111-1111-1111-111111111111',
        safe_label: '사내 문서',
      },
    ]);
  });

  it('updates Knowledge Base safe metadata', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({
      data: {
        safe_metadata: {
          safe_label: 'People Ops',
          kb_safe_topics: ['onboarding'],
        },
        can_manage_safe_metadata: true,
      },
    });

    await knowledgeApi.updateKnowledgeSafeMetadata('kb-1', {
      safe_label: 'People Ops',
      kb_safe_topics: ['onboarding'],
    });

    expect(apiClient.patch).toHaveBeenCalledWith(
      '/knowledge/kb-1/safe-metadata',
      {
        safe_label: 'People Ops',
        kb_safe_topics: ['onboarding'],
      },
    );
  });

  it('loads Knowledge Collections from the management endpoint', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        collections: [
          {
            id: 'collection-1',
            organization_id: 'org-1',
            name: 'HR',
            is_system_managed: false,
            sync_state: 'manual',
            lifecycle_state: 'active',
            visibility: 'private',
            linked_kb_count_bucket: '1',
            active_kb_count_bucket: '1',
            can_read: true,
            can_route: true,
            can_manage: true,
            can_sync: false,
            sync_supported: true,
            safe_metadata: {},
            created_at: '2026-07-07T00:00:00Z',
            updated_at: '2026-07-07T00:00:00Z',
          },
        ],
        can_create_collection: true,
        can_change_public_visibility: true,
      },
    });

    const collections = await knowledgeApi.getKnowledgeCollections();

    expect(apiClient.get).toHaveBeenCalledWith('/knowledge/collections', {
      params: undefined,
    });
    expect(collections).toHaveLength(1);
    expect(JSON.stringify(collections)).not.toContain('raw_source_url');
  });

  it('loads Knowledge Collection management capabilities', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        collections: [],
        can_create_collection: false,
        can_change_public_visibility: false,
      },
    });

    const response = await knowledgeApi.getKnowledgeCollectionsResponse();

    expect(response.collections).toEqual([]);
    expect(response.can_create_collection).toBe(false);
    expect(response.can_change_public_visibility).toBe(false);
  });

  it('updates public visibility with explicit acknowledgement', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        collection: { id: 'collection-1', visibility: 'public' },
        public_runtime_effect: 'anonymous_public_only_candidate',
        linked_kb_count_bucket: '1',
        active_kb_count_bucket: '1',
        sensitive_content_warning: 'unknown_or_present',
      },
    });

    await knowledgeApi.updateKnowledgeCollectionVisibility('collection-1', {
      visibility: 'public',
      acknowledged_public_runtime_exposure: true,
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/visibility',
      {
        visibility: 'public',
        acknowledged_public_runtime_exposure: true,
      },
    );
  });

  it('links KBs through the Collection item endpoint', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { items: [] },
    });

    await knowledgeApi.linkKnowledgeCollectionItem('collection-1', {
      knowledge_base_id: 'kb-1',
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/items',
      { knowledge_base_id: 'kb-1' },
    );
  });

  it('restores an archived Collection through the lifecycle endpoint', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: undefined });

    await knowledgeApi.restoreKnowledgeCollection('collection-1');

    expect(apiClient.post).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/restore',
    );
  });

  it('sends the exact item set with the current order revision', async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({
      data: {
        items: [],
        order_revision: `ord_v1_${'a'.repeat(64)}`,
        reorder_supported: true,
      },
    });

    await knowledgeApi.reorderKnowledgeCollectionItems(
      'collection-1',
      [{ item_id: 'item-1', rank: 0 }],
      `ord_v1_${'0'.repeat(64)}`,
      true,
    );

    expect(apiClient.patch).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/items/reorder',
      {
        items: [{ item_id: 'item-1', rank: 0 }],
        expected_order_revision: `ord_v1_${'0'.repeat(64)}`,
        acknowledged_public_runtime_exposure: true,
      },
    );
  });

  it('loads a bounded Collection delegation subject page', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { subjects: [], next_cursor: 'next-page' },
    });

    await knowledgeApi.getKnowledgeCollectionDelegationSubjects(
      'collection-1',
      {
        subject_type: 'user',
        query: 'Alpha',
        cursor: 'current-page',
        limit: 25,
      },
    );

    expect(apiClient.get).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/delegation-subjects',
      {
        params: {
          subject_type: 'user',
          query: 'Alpha',
          cursor: 'current-page',
          limit: 25,
        },
      },
    );
  });

  it('applies Collection role bundles through the transactional endpoint', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { permissions: [] },
    });

    await knowledgeApi.grantKnowledgeCollectionPermissionBundle(
      'collection-1',
      {
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'workflow_router',
      },
    );

    expect(apiClient.post).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/permissions/bundles',
      {
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'workflow_router',
      },
    );
  });

  it('revokes one bundle and applies a multi-Collection bundle atomically', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({ data: undefined })
      .mockResolvedValueOnce({
        data: {
          operation: 'grant',
          subject_type: 'team',
          role_bundle: 'viewer',
          target_count_bucket: '2-10',
          changed_count_bucket: '2-10',
          unchanged_count_bucket: '0',
        },
      });

    await knowledgeApi.revokeKnowledgeCollectionPermissionBundle(
      'collection-1',
      {
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'viewer',
      },
    );
    await knowledgeApi.mutateKnowledgeCollectionPermissionBundles({
      collection_ids: ['collection-1', 'collection-2'],
      operation: 'grant',
      subject_type: 'team',
      subject_id: 'team-1',
      role_bundle: 'viewer',
    });

    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/knowledge/collections/collection-1/permissions/bundles/revoke',
      {
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'viewer',
      },
    );
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/knowledge/collection-permissions/bulk-bundles',
      {
        collection_ids: ['collection-1', 'collection-2'],
        operation: 'grant',
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'viewer',
      },
    );
  });

  it('sends public membership acknowledgement when unlinking', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: undefined });

    await knowledgeApi.unlinkKnowledgeCollectionItem(
      'collection-1',
      'item-1',
      true,
    );

    expect(apiClient.delete).toHaveBeenCalledWith(
      '/knowledge/collections/collection-1/items/item-1',
      {
        params: { acknowledged_public_runtime_exposure: true },
      },
    );
  });

  it('grants Knowledge domain actions to a Team', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: undefined });

    await knowledgeApi.grantKnowledgeDomainPermission({
      subject_type: 'team',
      subject_id: 'team-1',
      permission_action: 'catalog_manage',
    });

    expect(apiClient.put).toHaveBeenCalledWith(
      '/knowledge/domain-permissions/teams/team-1/catalog_manage',
      { expires_at: null },
    );
  });
});
