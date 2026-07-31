import { apiClient } from '@/lib/apiClient';
import type { Edge } from '@xyflow/react';
import type { Node } from '../types/Workflow';

export type AgentBuilderStatus =
  | 'planning'
  | 'graph_mutation_ready'
  | 'parameter_configuration'
  | 'completed'
  | 'stale'
  | 'stale_protocol'
  | 'draft_ready'
  | 'clarification_required'
  | 'validation_failed'
  | 'unsupported'
  | 'configuration_required'
  | 'failed'
  | 'canceled';

export type AgentBuilderParameterTask = {
  task_id: string;
  group_id: string;
  step_id: string;
  node_id: string;
  node_type: string;
  parameter_key: string;
  task_group?: string | null;
  label: string;
  input_type:
    | 'boolean'
    | 'code'
    | 'credential_ref'
    | 'json'
    | 'number'
    | 'resource_ref'
    | 'secret'
    | 'select'
    | 'text'
    | 'textarea'
    | 'variable_selector'
    | 'variable_selector_list';
  required: boolean;
  confirmation_required?: boolean;
  defer_policy: 'forbidden' | 'allow_unresolved';
  status:
    | 'completed'
    | 'pending'
    | 'active'
    | 'skipped'
    | 'deferred'
    | 'invalid'
    | 'canceled';
  task_version: number;
  stable_order: number;
  resolution_source:
    | 'user_request'
    | 'existing_graph'
    | 'upstream_selector'
    | 'catalog_default'
    | null;
  recommendation_fingerprint?: string | null;
  reason: string;
  input_guidance: string;
  node_label?: string;
  node_purpose?: string;
  configuration_state?: 'resolved' | 'unresolved';
  validation?: Record<string, unknown>;
  sensitivity?: 'safe' | 'reference_only' | 'secret_forbidden';
  candidates?: Array<{
    candidate_id: string;
    reference_value?: string | null;
    kind: 'resource_ref' | 'credential_ref';
    label: string;
    description: string;
  }>;
  suggestions?: Array<{
    suggestion_id: string;
    kind: 'variable_selector';
    label: string;
    description: string;
    source_node_id: string;
    output_key: string;
    value_type: string;
    value_selector: string[];
    json_path: string;
  }>;
};

export type AgentBuilderGraph = {
  nodes: Node[];
  edges: Edge[];
  viewport?: { x: number; y: number; zoom: number };
};

export type AgentBuilderGraphOperation =
  | { op: 'add_node'; node: Node }
  | { op: 'remove_node'; node_id: string }
  | { op: 'add_edge'; edge: Edge }
  | { op: 'remove_edge'; edge_id: string }
  | {
      op: 'replace_node_data';
      node_id: string;
      data: Record<string, unknown>;
    }
  | {
      op: 'replace_node_position';
      node_id: string;
      position: { x: number; y: number };
    };

export type AgentBuilderGraphMutation = {
  operation_id: string;
  kind:
    | 'initial_graph'
    | 'graph_edit'
    | 'replace_workflow'
    | 'parameter_update'
    | 'knowledge_binding';
  status?:
    | 'pending_apply'
    | 'pending_save'
    | 'pending_ack'
    | 'acknowledged'
    | 'blocked'
    | 'reverted';
  generation_mode?: 'configure_and_generate' | 'structure_only';
  workflow_id?: string;
  base_graph_hash?: string;
  expected_workflow_updated_at: string;
  expected_result_graph_hash?: string;
  catalog_version?: 3;
  operations: AgentBuilderGraphOperation[];
  affected_node_ids?: string[];
};

export type AgentBuilderParameterGroup = {
  group_id: string;
  status:
    | 'pending_save'
    | 'pending_ack'
    | 'active'
    | 'completed'
    | 'blocked'
    | 'canceled';
  tasks: AgentBuilderParameterTask[];
};

export type AgentBuilderValidationResult = {
  valid: boolean;
  issues: Array<{ code: string; message: string; path?: string | null }>;
};

export type AgentBuilderMissingParameter = {
  key: string;
  label: string;
};

export type AgentBuilderNodeConfigurationIssue = {
  node_id: string;
  node_type: string;
  node_label: string;
  capability: string;
  missing_parameters: AgentBuilderMissingParameter[];
};

export type AgentBuilderEditTargetReference = {
  reference_type: 'natural_language_node' | 'selected_node' | 'selected_edge';
  query?: string | null;
  capabilities: string[];
  node_types: string[];
};

export type AgentBuilderEditOperation = {
  operation_id: string;
  operation: 'insert';
  placement: 'before' | 'after' | 'between';
  step_refs: string[];
  target: AgentBuilderEditTargetReference;
};

export type AgentBuilderStructuredRequest = {
  request_type:
    | 'new_workflow'
    | 'modify_workflow'
    | 'clarification'
    | 'unsupported'
    | 'validation_failure';
  intent_summary: string;
  steps: Array<Record<string, unknown>>;
  parameter_guidance_hints: Array<Record<string, unknown>>;
  knowledge_requirements: Array<Record<string, unknown>>;
};

export type AgentBuilderSessionResponse = {
  session_id: string;
  workflow_id?: string | null;
  app_id?: string | null;
  protocol_version?: 'direct_edit_v1' | null;
  default_generation_mode?: 'configure_and_generate' | 'structure_only';
  status: string;
  messages: AgentBuilderSessionMessage[];
  active_request?: Record<string, unknown> | null;
  active_graph_mutation?: Record<string, unknown> | null;
  parameter_group?: AgentBuilderParameterGroup | null;
  pending_request?: Record<string, unknown> | null;
};

export type AgentBuilderSessionMessage =
  | AgentBuilderMessageResponse
  | {
      kind: 'user';
      request_id: string;
      content: string;
      redacted?: boolean;
    }
  | {
      kind: 'assistant';
      request_id: string;
      response: AgentBuilderMessageResponse;
    };

export type AgentBuilderMessageResponse = {
  request_id: string;
  status: AgentBuilderStatus;
  structured_plan?: AgentBuilderStructuredRequest | null;
  knowledge_resolution?: AgentBuilderKnowledgeResolution | null;
  graph_mutation?: AgentBuilderGraphMutation | null;
  parameter_group?: AgentBuilderParameterGroup | null;
  clarification_questions: string[];
  clarification_options: Array<Record<string, unknown>>;
  validation_result?: AgentBuilderValidationResult | null;
  warnings: string[];
};

export type AgentBuilderKnowledgeCandidateOption = {
  type?: string | null;
  candidate_id: string;
  resolution_id?: string | null;
  requirement_id?: string | null;
  collection_handle?: string | null;
  kb_handle?: string | null;
  selection_key?: string | null;
  label?: string | null;
  safe_label?: string | null;
  confidence?: 'high' | 'medium' | 'low' | null;
  score?: number | null;
  reason_category?: string | null;
  reason?: string | null;
  threshold_result?: string | null;
  runtime_availability?: string | null;
};

export type AgentBuilderKnowledgeSelectedOption = {
  selection_type?: 'collection' | 'knowledge_base' | null;
  candidate_id?: string | null;
  collection_handle?: string | null;
  kb_handle?: string | null;
  resolution_id?: string | null;
  requirement_id?: string | null;
  label?: string | null;
  safe_label?: string | null;
};

export type AgentBuilderKnowledgeResolution = {
  resolution_id?: string | null;
  requirement_id?: string | null;
  target_node_id?: string | null;
  timing: 'before_graph' | 'after_graph';
  required: boolean;
  candidates: AgentBuilderKnowledgeCandidateOption[];
  collections?: AgentBuilderKnowledgeCollection[];
  ungrouped_kbs?: AgentBuilderKnowledgeKBCandidate[];
  selected: AgentBuilderKnowledgeSelectedOption[];
  selected_collection_handles?: string[];
  selected_kb_handles?: string[];
  selection_status?: 'pending_ack' | 'completed' | 'unapplied' | null;
};

export type AgentBuilderKnowledgeCandidateSelection = {
  candidate_id: string;
  resolution_id?: string | null;
  requirement_id?: string | null;
};

export type AgentBuilderKnowledgeKBCandidate = {
  kb_handle: string;
  selection_key: string;
  safe_label?: string | null;
  score?: number | null;
  shared_collection_count?: number;
};

export type AgentBuilderKnowledgeCollection = {
  collection_handle: string;
  safe_label?: string | null;
  score?: number | null;
  children: AgentBuilderKnowledgeKBCandidate[];
};

export type AgentBuilderKnowledgeSelectionResponse = {
  resolution_id: string;
  selected_candidates: AgentBuilderKnowledgeCandidateSelection[];
  selected_collection_handles?: string[];
  selected_kb_handles?: string[];
  graph_mutation: AgentBuilderGraphMutation;
};

export type AgentBuilderIntentModelOption = {
  model: {
    id: string;
    model_id_for_api_call: string;
    name: string;
    provider_name: string;
  };
  credential: {
    id: string;
    credential_name: string;
  };
  provider_name?: string;
  relation_priority: number;
};

export type AgentBuilderIntentModelProvider = {
  provider_name: string;
  options: AgentBuilderIntentModelOption[];
  unavailable_reason?: string | null;
};

export type AgentBuilderIntentModelSelection = {
  credentialId: string;
  modelId: string;
};

export const agentBuilderApi = {
  async getModelOptions(): Promise<AgentBuilderIntentModelProvider[]> {
    const response = await apiClient.get('/agent-builder/model-options');
    return response.data;
  },

  async createSession(input: {
    workflowId?: string | null;
    appId?: string | null;
  }): Promise<AgentBuilderSessionResponse> {
    const response = await apiClient.post('/agent-builder/sessions', {
      workflow_id: input.workflowId ?? undefined,
      app_id: input.appId ?? undefined,
    });
    return response.data;
  },

  async getSession(sessionId: string): Promise<AgentBuilderSessionResponse> {
    const response = await apiClient.get(
      `/agent-builder/sessions/${sessionId}`,
    );
    return response.data;
  },

  async sendMessage(
    sessionId: string,
    input: {
      message: string;
      workflowId?: string | null;
      appId?: string | null;
      selectedNodeId?: string | null;
      selectedEdgeId?: string | null;
      intentModelSelection?: AgentBuilderIntentModelSelection | null;
      generationMode?: 'configure_and_generate' | 'structure_only';
    },
  ): Promise<AgentBuilderMessageResponse> {
    const response = await apiClient.post(
      `/agent-builder/sessions/${sessionId}/messages`,
      {
        message: input.message,
        generation_mode: input.generationMode ?? 'configure_and_generate',
        workflow_id: input.workflowId ?? undefined,
        app_id: input.appId ?? undefined,
        selected_node_id: input.selectedNodeId ?? undefined,
        selected_edge_id: input.selectedEdgeId ?? undefined,
        intent_model_selection: input.intentModelSelection
          ? {
              credential_id: input.intentModelSelection.credentialId,
              model_id: input.intentModelSelection.modelId,
            }
          : undefined,
      },
    );
    return response.data;
  },

  async cancelRequest(requestId: string): Promise<AgentBuilderMessageResponse> {
    const response = await apiClient.post(
      `/agent-builder/requests/${requestId}/cancel`,
    );
    return response.data;
  },

  async acknowledgeMutation(
    sessionId: string,
    input: {
      operationId: string;
      workflowId: string;
      graphHash: string;
      workflowUpdatedAt: string;
    },
  ): Promise<{
    operation_id: string;
    operation_status: 'acknowledged';
    graph_hash: string;
    updated_at: string;
    parameter_group?: AgentBuilderParameterGroup | null;
    completed_task_id?: string | null;
    completed_knowledge_resolution_id?: string | null;
    next_task_id?: string | null;
  }> {
    const response = await apiClient.post(
      `/agent-builder/sessions/${sessionId}/graph-mutations/${input.operationId}/ack`,
      {
        workflow_id: input.workflowId,
        graph_hash: input.graphHash,
        updated_at: input.workflowUpdatedAt,
      },
    );
    return response.data;
  },

  async decideParameterTask(
    sessionId: string,
    taskId: string,
    input: {
      operationId: string;
      expectedTaskVersion: number;
      action: 'confirm' | 'set' | 'clear' | 'defer' | 'skip' | 'previous';
      value?: Record<string, unknown>;
    },
  ): Promise<{
    task: AgentBuilderParameterTask;
    graph_mutation?: AgentBuilderGraphMutation | null;
    next_task_id?: string | null;
    group_status: string;
    awaiting_persistence_ack: boolean;
  }> {
    const response = await apiClient.patch(
      `/agent-builder/sessions/${sessionId}/parameter-tasks/${taskId}`,
      {
        operation_id: input.operationId,
        expected_task_version: input.expectedTaskVersion,
        action: input.action,
        value: input.value,
      },
    );
    return response.data;
  },

  async selectKnowledge(
    sessionId: string,
    input: {
      resolutionId: string;
      selectedCandidates?: AgentBuilderKnowledgeCandidateSelection[];
      selectedCollectionHandles?: string[];
      selectedKbHandles?: string[];
      editorTargetNodeId?: string;
      selectedKnowledgeBaseIds?: string[];
      selectedKnowledgeCollectionIds?: string[];
    },
  ): Promise<AgentBuilderKnowledgeSelectionResponse> {
    const response = await apiClient.post(
      `/agent-builder/sessions/${sessionId}/knowledge-selection`,
      {
        resolution_id: input.resolutionId,
        selected_candidates: input.selectedCandidates ?? [],
        selected_collection_handles: input.selectedCollectionHandles ?? [],
        selected_kb_handles: input.selectedKbHandles ?? [],
        editor_target_node_id: input.editorTargetNodeId,
        selected_knowledge_base_ids: input.selectedKnowledgeBaseIds ?? [],
        selected_knowledge_collection_ids:
          input.selectedKnowledgeCollectionIds ?? [],
      },
    );
    return response.data;
  },

  async cancelParameterGroup(
    sessionId: string,
    groupId: string,
    input: {
      operationId: string;
      expectedTaskId: string;
      expectedTaskVersion: number;
    },
  ): Promise<{
    operation_id: string;
    parameter_group: AgentBuilderParameterGroup;
  }> {
    const response = await apiClient.post(
      `/agent-builder/sessions/${sessionId}/parameter-groups/${groupId}/cancel`,
      {
        operation_id: input.operationId,
        expected_task_id: input.expectedTaskId,
        expected_task_version: input.expectedTaskVersion,
      },
    );
    return response.data;
  },
};
