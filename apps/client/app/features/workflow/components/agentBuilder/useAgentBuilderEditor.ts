import { agentBuilderApi } from '../../api/agentBuilderApi';
import { workflowApi } from '../../api/workflowApi';
import { useWorkflowStore } from '../../store/useWorkflowStore';
import type {
  Edge,
  Node,
  Viewport,
  WorkflowDraftRequest,
  WorkflowDraftSaveRequest,
} from '../../types/Workflow';
import {
  applyAgentBuilderOperations,
  type AgentBuilderGraphMutation,
} from './agentBuilderGraphMutation';
import { acquireWorkflowDraftSave } from '../../utils/workflowDraftSaveCoordinator';
import { buildWorkflowDraftPayload } from '../../utils/workflowDraftPayload';
import { workflowDraftTimestampsEqual } from '../../utils/workflowDraftCAS';

const payloadMatchesCurrentEditor = (
  expectedPayload: WorkflowDraftRequest,
  viewport: Viewport,
) => {
  const current = useWorkflowStore.getState();
  const currentPayload = buildWorkflowDraftPayload(
    {
      nodes: current.nodes.filter((node) => node.type !== 'note'),
      edges: current.edges,
      viewport,
      features: {
        ...current.features,
        noteNodes: current.nodes.filter((node) => node.type === 'note'),
      },
      envVariables: current.envVariables,
      runtimeVariables: current.runtimeVariables,
    },
    viewport,
    { noteNodesSource: 'features' },
  );
  return JSON.stringify(currentPayload) === JSON.stringify(expectedPayload);
};

const isAmbiguousSaveFailure = (error: unknown) => {
  const status =
    typeof (error as { response?: { status?: unknown } })?.response?.status ===
    'number'
      ? (error as { response: { status: number } }).response.status
      : null;
  return status === null || status >= 500;
};

export const applyAndSaveAgentBuilderMutation = async (input: {
  sessionId: string;
  workflowId: string;
  viewport: Viewport;
  mutation: AgentBuilderGraphMutation & {
    base_graph_hash: string;
    expected_workflow_updated_at: string;
  };
}) => {
  const store = useWorkflowStore.getState();
  const assertActiveWorkflow = () => {
    const activeWorkflowId = useWorkflowStore.getState().activeWorkflowId;
    if (activeWorkflowId !== input.workflowId) {
      throw Object.assign(new Error('workflow_context_changed'), {
        code: 'workflow_context_changed',
      });
    }
  };
  store.setAgentBuilderMutationSaving(true);
  let releaseWorkflowSave: (() => void) | null = null;
  let applied = false;
  let persisted = false;
  let ambiguousSave = false;
  try {
    releaseWorkflowSave = await acquireWorkflowDraftSave(
      input.workflowId,
      'agent_builder',
    );
    assertActiveWorkflow();
    const canonical = await workflowApi.getDraftWorkflow(input.workflowId);
    assertActiveWorkflow();
    if (!Array.isArray(canonical?.nodes) || !Array.isArray(canonical?.edges)) {
      throw new Error('Agent Builder canonical base graph is unavailable');
    }
    if (
      canonical.graph_hash !== input.mutation.base_graph_hash ||
      !workflowDraftTimestampsEqual(
        canonical.updated_at,
        input.mutation.expected_workflow_updated_at,
      )
    ) {
      throw Object.assign(new Error('stale_graph'), { code: 'stale_graph' });
    }
    store.ingestCanonicalDraftMetadata(canonical, input.workflowId);
    const canonicalNodes = canonical.nodes as Node[];
    const canonicalEdges = canonical.edges as Edge[];
    const revertGraph = {
      nodes: structuredClone(
        canonicalNodes.filter((node) => node.type !== 'note'),
      ),
      edges: structuredClone(canonicalEdges),
    };
    const mutationResult = applyAgentBuilderOperations(
      revertGraph.nodes,
      revertGraph.edges,
      input.mutation.operations,
    );
    store.applyAgentBuilderGraphMutation(input.mutation, input.sessionId, {
      nodes: revertGraph.nodes,
      edges: revertGraph.edges,
    });
    applied = true;
    const current = useWorkflowStore.getState();
    const canonicalPayload = buildWorkflowDraftPayload(
      {
        nodes: mutationResult.nodes.filter((node) => node.type !== 'note'),
        edges: mutationResult.edges,
        viewport: input.viewport,
        features: {
          ...current.features,
          noteNodes: current.nodes.filter((node) => node.type === 'note'),
        },
        envVariables: current.envVariables,
        runtimeVariables: current.runtimeVariables,
      },
      input.viewport,
      { noteNodesSource: 'features' },
    );
    const saveRequest: WorkflowDraftSaveRequest = {
      ...canonicalPayload,
      expected_graph_hash: canonical.graph_hash,
      expected_updated_at: canonical.updated_at,
      mutation_context: {
        operation_id: input.mutation.operation_id,
        action: 'apply',
        expected_base_graph_hash: input.mutation.base_graph_hash,
        expected_workflow_updated_at:
          input.mutation.expected_workflow_updated_at,
        catalog_version: 3,
      },
    };
    const save = () =>
      workflowApi.syncDraftWorkflow(input.workflowId, saveRequest);
    let saveResult: { graph_hash: string; updated_at: string } | null = null;
    try {
      saveResult = await save();
    } catch (saveError) {
      if (!isAmbiguousSaveFailure(saveError)) throw saveError;
      try {
        saveResult = await save();
      } catch (retryError) {
        let confirmedUnsaved = false;
        let recoveredCanonical = false;
        let recoverySession: Awaited<
          ReturnType<typeof agentBuilderApi.getSession>
        > | null = null;
        let recoveryDraft: Awaited<
          ReturnType<typeof workflowApi.getDraftWorkflow>
        > | null = null;
        try {
          recoverySession = await agentBuilderApi.getSession(input.sessionId);
        } catch {
          recoverySession = null;
        }
        try {
          recoveryDraft = await workflowApi.getDraftWorkflow(input.workflowId);
        } catch {
          recoveryDraft = null;
        }

        const active = recoverySession?.active_graph_mutation as
          | Record<string, unknown>
          | null
          | undefined;
        const expectedResultGraphHash =
          input.mutation.expected_result_graph_hash;
        const savedWorkflowUpdatedAt = active?.saved_workflow_updated_at;
        const envelopeMatchesExpectedResult =
          active?.operation_id === input.mutation.operation_id &&
          (active?.status === 'pending_ack' ||
            active?.status === 'acknowledged') &&
          typeof expectedResultGraphHash === 'string' &&
          active?.result_graph_hash === expectedResultGraphHash &&
          typeof savedWorkflowUpdatedAt === 'string';
        const canonicalMatchesExpectedResult =
          envelopeMatchesExpectedResult &&
          recoveryDraft?.graph_hash === expectedResultGraphHash &&
          workflowDraftTimestampsEqual(
            recoveryDraft.updated_at,
            savedWorkflowUpdatedAt,
          );
        const canonicalMatchesBase =
          recoveryDraft?.graph_hash === input.mutation.base_graph_hash &&
          workflowDraftTimestampsEqual(
            recoveryDraft.updated_at,
            input.mutation.expected_workflow_updated_at,
          );

        if (canonicalMatchesExpectedResult && recoveryDraft) {
          useWorkflowStore
            .getState()
            .ingestCanonicalDraftMetadata(recoveryDraft, input.workflowId);
          saveResult = {
            graph_hash: expectedResultGraphHash,
            updated_at: savedWorkflowUpdatedAt,
          };
          recoveredCanonical = true;
        } else if (canonicalMatchesBase && recoveryDraft) {
          useWorkflowStore
            .getState()
            .ingestCanonicalDraftMetadata(recoveryDraft, input.workflowId);
          confirmedUnsaved = true;
        } else {
          ambiguousSave = true;
        }
        if (confirmedUnsaved) {
          useWorkflowStore.getState().rollbackLatestAgentBuilderGraphMutation();
          applied = false;
        }
        if (!recoveredCanonical) throw retryError;
      }
    }
    if (!saveResult) {
      throw new Error('Agent Builder canonical save result is unavailable');
    }
    useWorkflowStore
      .getState()
      .ingestCanonicalDraftMetadata(saveResult, input.workflowId);
    persisted = true;
    useWorkflowStore.getState().markLatestAgentBuilderMutationPersisted({
      operationId: input.mutation.operation_id,
      resultGraphHash: saveResult.graph_hash,
      workflowUpdatedAt: saveResult.updated_at,
      sessionId: input.sessionId,
      revertGraph,
    });
    if (payloadMatchesCurrentEditor(canonicalPayload, input.viewport)) {
      useWorkflowStore.getState().setHasUnsavedChanges(false);
    }
    const acknowledgementRequest = {
      operationId: input.mutation.operation_id,
      workflowId: input.workflowId,
      graphHash: saveResult.graph_hash,
      workflowUpdatedAt: saveResult.updated_at,
    };
    let acknowledgement;
    let recoveredSession: Awaited<
      ReturnType<typeof agentBuilderApi.getSession>
    > | null = null;
    try {
      acknowledgement = await agentBuilderApi.acknowledgeMutation(
        input.sessionId,
        acknowledgementRequest,
      );
    } catch (acknowledgementError) {
      if (!isAmbiguousSaveFailure(acknowledgementError)) {
        throw acknowledgementError;
      }
      try {
        acknowledgement = await agentBuilderApi.acknowledgeMutation(
          input.sessionId,
          acknowledgementRequest,
        );
      } catch (retryError) {
        if (!isAmbiguousSaveFailure(retryError)) throw retryError;
        const [sessionResult, canonicalResult] = await Promise.allSettled([
          agentBuilderApi.getSession(input.sessionId),
          workflowApi.getDraftWorkflow(input.workflowId),
        ]);
        if (
          sessionResult.status !== 'fulfilled' ||
          canonicalResult.status !== 'fulfilled' ||
          !sessionResult.value ||
          !canonicalResult.value
        ) {
          throw retryError;
        }
        const active = sessionResult.value.active_graph_mutation as
          Record<string, unknown> | null | undefined;
        const canonical = canonicalResult.value;
        const acknowledged =
          active?.operation_id === input.mutation.operation_id &&
          active?.status === 'acknowledged' &&
          active?.result_graph_hash === saveResult.graph_hash &&
          workflowDraftTimestampsEqual(
            active?.saved_workflow_updated_at,
            saveResult.updated_at,
          ) &&
          canonical.graph_hash === saveResult.graph_hash &&
          workflowDraftTimestampsEqual(
            canonical.updated_at,
            saveResult.updated_at,
          );
        if (!acknowledged) throw retryError;
        useWorkflowStore
          .getState()
          .ingestCanonicalDraftMetadata(canonical, input.workflowId);
        recoveredSession = sessionResult.value;
        const completionContext = active?.completion_context as
          Record<string, unknown> | undefined;
        acknowledgement = {
          operation_id: input.mutation.operation_id,
          operation_status: 'acknowledged' as const,
          graph_hash: saveResult.graph_hash,
          updated_at: saveResult.updated_at,
          parameter_group: sessionResult.value.parameter_group ?? null,
          completed_task_id:
            typeof completionContext?.parameter_task_id === 'string'
              ? completionContext.parameter_task_id
              : null,
          completed_knowledge_resolution_id:
            typeof completionContext?.knowledge_resolution_id === 'string'
              ? completionContext.knowledge_resolution_id
              : null,
          next_task_id: null,
        };
      }
    }
    if (!recoveredSession) {
      try {
        recoveredSession = await agentBuilderApi.getSession(input.sessionId);
      } catch {
        recoveredSession = null;
      }
    }
    useWorkflowStore
      .getState()
      .markLatestAgentBuilderMutationAcknowledged(
        input.mutation.operation_id,
        recoveredSession?.status === 'completed',
      );
    return { ...saveResult, acknowledgement, session: recoveredSession };
  } catch (error) {
    if (
      applied &&
      !persisted &&
      !ambiguousSave &&
      useWorkflowStore.getState().activeWorkflowId === input.workflowId
    ) {
      useWorkflowStore.getState().rollbackLatestAgentBuilderGraphMutation();
    }
    throw error;
  } finally {
    releaseWorkflowSave?.();
    useWorkflowStore.getState().setAgentBuilderMutationSaving(false);
  }
};
