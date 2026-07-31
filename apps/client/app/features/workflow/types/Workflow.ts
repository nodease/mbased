export * from './Nodes';
import { Node } from './Nodes';

export interface Edge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  [key: string]: unknown;
}

export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

export interface EnvVariable {
  id: string;
  key: string;
  value: string;
  type: 'string' | 'number' | 'secret';
}

export interface RuntimeVariable {
  id: string;
  key: string;
  name: string;
}

export type Features = Record<string, any>;

export interface WorkflowDraftRequest {
  nodes: Node[];
  edges: Edge[];
  viewport: Viewport;
  features?: Features;
  envVariables?: EnvVariable[];
  runtimeVariables?: RuntimeVariable[];
  mutation_context?: {
    operation_id: string;
    action: 'apply' | 'revert' | 'redo';
    expected_base_graph_hash: string;
    expected_workflow_updated_at: string;
    catalog_version: 3;
  };
}

export interface WorkflowDraftSaveRequest extends WorkflowDraftRequest {
  expected_graph_hash: string;
  expected_updated_at: string;
}

export interface WorkflowDraftResponse extends WorkflowDraftRequest {
  workflow_id: string;
  graph_hash: string;
  updated_at: string;
}

export interface WorkflowDraftSaveResponse {
  status: 'success';
  workflow_id: string;
  graph_hash: string;
  updated_at: string;
  canonical_deferred_parameters?: Array<{
    node_path: string[];
    parameter_keys: string[];
  }>;
  operation_id?: string;
  parameter_group?: unknown;
}
