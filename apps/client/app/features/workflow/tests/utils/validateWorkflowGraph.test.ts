import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { AppNode, ConditionNode } from '../../types/Nodes';
import type { Edge, WorkflowDraftRequest } from '../../types/Workflow';
import {
  cleanupInvalidEdges,
  validateConnection,
  validateWorkflowGraph,
} from '../../utils/validateWorkflowGraph';

const node = (id: string, type: AppNode['type'], title: string): AppNode =>
  ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: { title },
  }) as AppNode;

const draft = (nodes: AppNode[], edges: Edge[]): WorkflowDraftRequest => ({
  nodes,
  edges,
  viewport: { x: 0, y: 0, zoom: 1 },
});

describe('validateWorkflowGraph', () => {
  it('accepts legacy direct-only, Collection-only, and mixed Knowledge references in nested graphs', () => {
    const directId = '11111111-1111-1111-1111-111111111111';
    const collectionId = '22222222-2222-2222-2222-222222222222';
    const nestedLlm = {
      ...node('nested-llm', 'llmNode', 'Nested LLM'),
      data: {
        title: 'Nested LLM',
        knowledgeCollections: [{ id: collectionId, safeLabel: '사내 문서' }],
      },
    } as unknown as AppNode;
    const container = {
      ...node('loop', 'loopNode', 'Loop'),
      data: {
        title: 'Loop',
        loop_key: 'items',
        inputs: [],
        outputs: [],
        parallel_mode: false,
        error_strategy: 'end',
        flatten_output: false,
        subGraph: { nodes: [nestedLlm], edges: [] },
      },
    } as unknown as AppNode;
    const mixedLlm = {
      ...node('llm', 'llmNode', 'LLM'),
      data: {
        title: 'LLM',
        knowledgeBases: [{ id: directId, name: '' }],
        knowledgeCollections: [{ id: collectionId }],
      },
    } as AppNode;
    const legacyLlm = node('legacy-llm', 'llmNode', 'Legacy LLM');

    const result = validateWorkflowGraph(
      draft([mixedLlm, legacyLlm, container], []),
    );

    expect(result.ok).toBe(true);
  });

  it('rejects a 21st Knowledge reference without silently slicing either list', () => {
    const references = Array.from({ length: 21 }, (_, index) => ({
      id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}`,
      safeLabel: `Collection ${index}`,
    }));
    const llm = {
      ...node('llm', 'llmNode', 'LLM'),
      data: { title: 'LLM', knowledgeCollections: references },
    } as AppNode;

    const result = validateWorkflowGraph(draft([llm], []));

    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: 'KNOWLEDGE_REFERENCE_LIMIT_EXCEEDED',
          nodeId: 'llm',
        }),
      ]),
    );
  });

  it.each([
    {
      knowledgeBases: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '정책',
          forged: true,
        },
      ],
    },
    {
      knowledgeCollections: [{ id: '11111111-1111-1111-1111-11111111111A' }],
    },
    {
      knowledgeCollections: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          safeLabel: '잘못된\u0000이름',
        },
      ],
    },
    { knowledgeBases: null },
  ])(
    'rejects malformed Knowledge graph data without echoing values: %o',
    (data) => {
      const llm = {
        ...node('llm', 'llmNode', 'LLM'),
        data: { title: 'LLM', ...data },
      } as AppNode;

      const result = validateWorkflowGraph(draft([llm], []));

      expect(result.errors).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ code: 'KNOWLEDGE_REFERENCE_INVALID' }),
        ]),
      );
      expect(JSON.stringify(result.errors)).not.toContain('정책');
      expect(JSON.stringify(result.errors)).not.toContain(
        '11111111-1111-1111-1111-11111111111A',
      );
    },
  );

  it('rejects selectors for removed Slack raw outputs', () => {
    const slack = {
      ...node('slack', 'slackPostNode', 'Slack'),
      data: {
        title: 'Slack',
        slackMode: 'api',
        referenced_variables: [],
      },
    } as AppNode;
    const consumer = {
      ...node('consumer', 'templateNode', '템플릿'),
      data: {
        title: '템플릿',
        referenced_variables: [
          { name: 'raw', value_selector: ['slack', 'headers'] },
        ],
      },
    } as AppNode;

    const result = validateWorkflowGraph(draft([slack, consumer], []));

    expect(result.ok).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'SLACK_REMOVED_OUTPUT_SELECTOR' }),
      ]),
    );
  });

  it('rejects removed Slack selectors inside nested graphs and arbitrary fields', () => {
    const nestedSlack = {
      ...node('nested-slack', 'slackPostNode', 'Nested Slack'),
      data: {
        title: 'Nested Slack',
        slackMode: 'api',
        referenced_variables: [],
      },
    } as AppNode;
    const nestedConsumer = {
      ...node('nested-consumer', 'answerNode', 'Nested Answer'),
      data: {
        title: 'Nested Answer',
        outputs: [
          { variable: 'raw', value_selector: ['nested-slack', 'data'] },
        ],
      },
    } as AppNode;
    const container = {
      ...node('loop', 'loopNode', 'Loop'),
      data: {
        title: 'Loop',
        loop_key: 'items',
        inputs: [],
        outputs: [],
        parallel_mode: false,
        error_strategy: 'end',
        flatten_output: false,
        subGraph: { nodes: [nestedSlack, nestedConsumer], edges: [] },
      },
    } as unknown as AppNode;

    const result = validateWorkflowGraph(draft([container], []));

    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: 'SLACK_REMOVED_OUTPUT_SELECTOR',
          nodeId: 'nested-consumer',
        }),
      ]),
    );
  });

  it('rejects removed Slack raw outputs in condition selectors', () => {
    const slack = {
      ...node('slack', 'slackPostNode', 'Slack'),
      data: { title: 'Slack', slackMode: 'api', referenced_variables: [] },
    } as AppNode;
    const condition = {
      ...node('condition', 'conditionNode', '조건'),
      data: {
        title: '조건',
        cases: [
          {
            id: 'case-1',
            variable_selector: ['slack', 'data'],
            operator: 'equals',
            value: 'ok',
          },
        ],
      },
    } as unknown as AppNode;

    const result = validateWorkflowGraph(draft([slack, condition], []));

    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: 'SLACK_REMOVED_OUTPUT_SELECTOR',
          nodeId: 'condition',
        }),
      ]),
    );
  });

  it('rejects message_ref only when the source Slack node uses webhook mode', () => {
    const consumer = {
      ...node('consumer', 'templateNode', '템플릿'),
      data: {
        title: '템플릿',
        referenced_variables: [
          { name: 'ref', value_selector: ['slack', 'message_ref'] },
        ],
      },
    } as AppNode;
    const slack = (slackMode: 'api' | 'webhook') =>
      ({
        ...node('slack', 'slackPostNode', 'Slack'),
        data: { title: 'Slack', slackMode, referenced_variables: [] },
      }) as AppNode;

    const webhookResult = validateWorkflowGraph(
      draft([slack('webhook'), consumer], []),
    );
    const apiResult = validateWorkflowGraph(
      draft([slack('api'), consumer], []),
    );

    expect(webhookResult.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'SLACK_REMOVED_OUTPUT_SELECTOR' }),
      ]),
    );
    expect(
      apiResult.errors.some(
        (issue) => issue.code === 'SLACK_REMOVED_OUTPUT_SELECTOR',
      ),
    ).toBe(false);
  });

  it('warns about custom legacy Slack HTTP configuration without blocking edit', () => {
    const slack = {
      ...node('slack', 'slackPostNode', 'Slack'),
      data: {
        title: 'Slack',
        slackMode: 'api',
        method: 'POST',
        headers: [{ key: 'X-Legacy', value: '1' }],
        referenced_variables: [],
      },
    } as AppNode;

    const result = validateWorkflowGraph(draft([slack], []));

    expect(result.ok).toBe(true);
    expect(result.warnings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'SLACK_LEGACY_CONFIGURATION' }),
      ]),
    );
  });

  it('warns when API mode keeps an alternate legacy endpoint or auth mode', () => {
    const slack = {
      ...node('slack', 'slackPostNode', 'Slack'),
      data: {
        title: 'Slack',
        slackMode: 'api',
        url: 'https://example.invalid/slack',
        authType: 'none',
        referenced_variables: [],
      },
    } as AppNode;

    const result = validateWorkflowGraph(draft([slack], []));

    expect(result.ok).toBe(true);
    expect(result.warnings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'SLACK_LEGACY_CONFIGURATION' }),
      ]),
    );
  });

  it('matches catalog entry, terminal, and condition connection policies', () => {
    const catalog = JSON.parse(
      readFileSync(
        resolve(
          process.cwd(),
          '../../apps/shared/config/workflow_node_catalog.json',
        ),
        'utf8',
      ),
    ) as {
      nodes: Array<{
        node_type: AppNode['type'];
        connection_policy: {
          incoming: string;
          outgoing: string;
          outgoing_handles: string;
        };
      }>;
    };

    for (const definition of catalog.nodes) {
      if (definition.connection_policy.incoming === 'forbidden') {
        const result = validateWorkflowGraph(
          draft(
            [
              node('source', 'templateNode', 'Source'),
              node('target', definition.node_type, 'Target'),
            ],
            [{ id: 'edge', source: 'source', target: 'target' }],
          ),
        );
        expect(result.ok).toBe(false);
      }
      if (definition.connection_policy.outgoing === 'forbidden') {
        const result = validateWorkflowGraph(
          draft(
            [
              node('source', definition.node_type, 'Source'),
              node('target', 'templateNode', 'Target'),
            ],
            [{ id: 'edge', source: 'source', target: 'target' }],
          ),
        );
        expect(result.ok).toBe(false);
      }
    }
  });
  it('blocks edges entering a start node', () => {
    const nodes = [
      node('start', 'startNode', '입력'),
      node('template', 'templateNode', '템플릿'),
    ];
    const edges = [
      {
        id: 'bad-edge',
        source: 'template',
        sourceHandle: 'source',
        target: 'start',
        targetHandle: 'target',
      },
    ];

    const result = validateWorkflowGraph(draft(nodes, edges));

    expect(result.ok).toBe(false);
    expect(result.errors[0]).toMatchObject({
      code: 'START_NODE_HAS_INCOMING_EDGE',
      edgeId: 'bad-edge',
      sourceNodeTitle: '템플릿',
      targetNodeTitle: '입력',
    });
  });

  it('blocks condition edges that use an unknown branch handle', () => {
    const condition: ConditionNode = {
      id: 'condition',
      type: 'conditionNode',
      position: { x: 0, y: 0 },
      data: {
        title: '조건',
        cases: [
          {
            id: 'case-1',
            case_name: 'Yes',
            conditions: [],
            logical_operator: 'and',
          },
        ],
      },
    };
    const result = validateWorkflowGraph(
      draft(
        [condition, node('template', 'templateNode', '템플릿')],
        [
          {
            id: 'bad-condition-edge',
            source: 'condition',
            sourceHandle: 'missing-case',
            target: 'template',
          },
        ],
      ),
    );

    expect(result.ok).toBe(false);
    expect(result.errors[0].code).toBe('INVALID_CONDITION_SOURCE_HANDLE');
  });

  it('blocks condition edges that omit sourceHandle instead of treating them as default', () => {
    const condition: ConditionNode = {
      id: 'condition',
      type: 'conditionNode',
      position: { x: 0, y: 0 },
      data: {
        title: '조건',
        cases: [
          {
            id: 'case-1',
            case_name: 'Yes',
            conditions: [],
            logical_operator: 'and',
          },
        ],
      },
    };

    const result = validateWorkflowGraph(
      draft(
        [condition, node('template', 'templateNode', '템플릿')],
        [
          {
            id: 'missing-handle-condition-edge',
            source: 'condition',
            target: 'template',
          },
        ],
      ),
    );

    expect(result.ok).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          code: 'INVALID_CONDITION_SOURCE_HANDLE',
          edgeId: 'missing-handle-condition-edge',
        }),
      ]),
    );
  });

  it('cleans edges that cannot be represented or executed', () => {
    const nodes = [
      node('start', 'startNode', '입력'),
      node('template', 'templateNode', '템플릿'),
    ];
    const graph = draft(nodes, [
      {
        id: 'bad-edge',
        source: 'template',
        sourceHandle: 'source',
        target: 'start',
        targetHandle: 'target',
      },
      {
        id: 'good-edge',
        source: 'start',
        sourceHandle: 'source',
        target: 'template',
        targetHandle: 'target',
      },
    ]);

    const result = cleanupInvalidEdges(graph);

    expect(result.removedIssues).toHaveLength(1);
    expect(result.graph.edges.map((edge) => edge.id)).toEqual(['good-edge']);
  });

  it('preserves LLM RAG selection data when cleaning invalid edges', () => {
    const llmNode = {
      ...node('llm', 'llmNode', 'LLM'),
      data: {
        title: 'LLM',
        knowledgeBases: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            name: '제품 정책',
          },
        ],
        knowledgeCollections: [
          {
            id: '22222222-2222-2222-2222-222222222222',
            safeLabel: '사내 문서',
          },
        ],
        topK: 4,
        scoreThreshold: 0.6,
        dedupeRetrievedContext: true,
        retrievedContextMaxChars: 4000,
        retrievedContextCompression: 'light',
        answerGroundingCheck: 'basic',
      },
    } as AppNode;
    const graph = draft(
      [node('start', 'startNode', '입력'), llmNode],
      [
        {
          id: 'bad-edge',
          source: 'llm',
          sourceHandle: 'source',
          target: 'start',
          targetHandle: 'target',
        },
        {
          id: 'good-edge',
          source: 'start',
          sourceHandle: 'source',
          target: 'llm',
          targetHandle: 'target',
        },
      ],
    );

    const result = cleanupInvalidEdges(graph);
    const cleanedLlmNode = result.graph.nodes.find(
      (cleanedNode) => cleanedNode.id === 'llm',
    );

    expect(result.graph.edges.map((edge) => edge.id)).toEqual(['good-edge']);
    expect(cleanedLlmNode?.data).toMatchObject({
      knowledgeBases: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '제품 정책',
        },
      ],
      knowledgeCollections: [
        {
          id: '22222222-2222-2222-2222-222222222222',
          safeLabel: '사내 문서',
        },
      ],
      topK: 4,
      scoreThreshold: 0.6,
      dedupeRetrievedContext: true,
      retrievedContextMaxChars: 4000,
      retrievedContextCompression: 'light',
      answerGroundingCheck: 'basic',
    });
  });

  it('detects cycles before execution', () => {
    const nodes = [
      node('start', 'startNode', '입력'),
      node('template', 'templateNode', '템플릿'),
      node('llm', 'llmNode', 'LLM'),
    ];
    const edges = [
      { id: 'e1', source: 'start', target: 'template' },
      { id: 'e2', source: 'template', target: 'llm' },
      { id: 'e3', source: 'llm', target: 'template' },
    ];

    const result = validateWorkflowGraph(draft(nodes, edges));

    expect(result.ok).toBe(false);
    expect(result.errors.some((issue) => issue.code === 'CYCLE_DETECTED')).toBe(
      true,
    );
  });

  it('blocks invalid number-based connections through the central validator', () => {
    const nodes = [
      node('start', 'startNode', '입력'),
      node('template', 'templateNode', '템플릿'),
    ];

    const result = validateConnection(nodes, [], {
      source: 'template',
      sourceHandle: 'source',
      target: 'start',
      targetHandle: 'target',
    });

    expect(result.ok).toBe(false);
    expect(result.errors[0].code).toBe('START_NODE_HAS_INCOMING_EDGE');
  });
});
