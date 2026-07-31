import {
  afterEach,
  describe,
  expect,
  it,
  vi,
  type MockedFunction,
} from 'vitest';

vi.mock('@/lib/csrfToken', () => ({
  attachCsrfProtection: vi.fn(),
  csrfFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, init),
}));
import { workflowApi } from './workflowApi';
import { setActiveOrganizationId } from '@/lib/activeOrganization';

const streamResponse = (chunks: string[], status = 200): Response => {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });

  return new Response(body, { status });
};

const getFetchInit = (fetchMock: MockedFunction<typeof fetch>): RequestInit => {
  const init = fetchMock.mock.calls[0]?.[1];
  if (!init) throw new Error('Expected fetch to receive request options');
  return init;
};

afterEach(() => {
  setActiveOrganizationId(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('workflowApi.executeWorkflowStream', () => {
  it('sends the active organization header through the Next stream proxy', async () => {
    setActiveOrganizationId('org-1');
    const events: unknown[] = [];
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      streamResponse(['data: {"type":"workflow_finish"}\n\n']),
    );
    vi.stubGlobal('fetch', fetchMock);

    await workflowApi.executeWorkflowStream(
      'workflow-1',
      { question: 'hello' },
      (event) => {
        events.push(event);
      },
    );

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/stream-api\/workflows\/workflow-1$/),
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: expect.any(Headers),
        body: JSON.stringify({
          inputs: { question: 'hello' },
          graph_snapshot: undefined,
        }),
      }),
    );
    const init = getFetchInit(fetchMock);
    const headers = new Headers(init.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-Organization-Id')).toBe('org-1');
    expect(events).toEqual([{ type: 'workflow_finish' }]);
  });

  it('실제 SSE 빈 줄 구분자로 이어진 workflow_start와 node_start를 각각 파싱한다', async () => {
    const events: unknown[] = [];
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      streamResponse([
        'data: {"type":"workflow_start","data":{"run_id":"run-1"}}\n\n' +
          'data: {"type":"node_start","data":{"node_id":"llm-1"}}\n\n',
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);

    await workflowApi.executeWorkflowStream(
      'workflow-1',
      { question: 'hello' },
      (event) => {
        events.push(event);
      },
    );

    expect(events).toEqual([
      { type: 'workflow_start', data: { run_id: 'run-1' } },
      { type: 'node_start', data: { node_id: 'llm-1' } },
    ]);
  });

  it('does not set Content-Type manually for FormData stream requests', async () => {
    setActiveOrganizationId('org-1');
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      streamResponse(['data: {"type":"workflow_finish"}\n\n']),
    );
    vi.stubGlobal('fetch', fetchMock);
    const formData = new FormData();
    formData.set('file', new Blob(['demo']), 'demo.txt');

    await workflowApi.executeWorkflowStream('workflow-1', formData);

    const init = getFetchInit(fetchMock);
    const headers = new Headers(init.headers);
    expect(headers.has('Content-Type')).toBe(false);
    expect(headers.get('X-Organization-Id')).toBe('org-1');
    expect(init.body).toBe(formData);
  });

  it('requests the active deployment policy for an automatic-routing test', async () => {
    const fetchMock: MockedFunction<typeof fetch> = vi.fn(async () =>
      streamResponse(['data: {"type":"workflow_finish"}\n\n']),
    );
    vi.stubGlobal('fetch', fetchMock);

    await workflowApi.executeWorkflowStream(
      'workflow-1',
      { question: 'hello' },
      undefined,
      { useActiveDeploymentRoutingPolicy: true },
    );

    expect(getFetchInit(fetchMock).body).toBe(
      JSON.stringify({
        inputs: { question: 'hello' },
        graph_snapshot: undefined,
        use_active_deployment_routing_policy: true,
      }),
    );
  });
});
