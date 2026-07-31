export const AGENT_BUILDER_KNOWLEDGE_SELECTION_FROM_NODE =
  'agent-builder:knowledge-selection-from-node';

export type AgentBuilderNodeKnowledgeBaseRef = {
  id: string;
  name: string;
};

export type AgentBuilderNodeKnowledgeCollectionRef = {
  id: string;
  safeLabel?: string;
};

export type AgentBuilderNodeKnowledgeSelectionEventDetail = {
  nodeId: string;
  knowledgeBases: AgentBuilderNodeKnowledgeBaseRef[];
  knowledgeCollections: AgentBuilderNodeKnowledgeCollectionRef[];
  handled: boolean;
};

export const requestAgentBuilderNodeKnowledgeSelection = (
  detail: Omit<AgentBuilderNodeKnowledgeSelectionEventDetail, 'handled'>,
): boolean => {
  const eventDetail: AgentBuilderNodeKnowledgeSelectionEventDetail = {
    ...detail,
    handled: false,
  };
  window.dispatchEvent(
    new CustomEvent(AGENT_BUILDER_KNOWLEDGE_SELECTION_FROM_NODE, {
      detail: eventDetail,
    }),
  );
  return eventDetail.handled;
};
