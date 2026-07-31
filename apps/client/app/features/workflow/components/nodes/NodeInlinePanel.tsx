import { cn } from '@/lib/utils';
import { AppNode } from '../../types/Nodes';
import { StartNodePanel } from './start/components/StartNodePanel';
import { AnswerNodePanel } from './answer/components/AnswerNodePanel';
import { HttpRequestNodePanel } from './http/components/HttpRequestNodePanel';
import { SlackPostNodePanel } from './slack/components/SlackPostNodePanel';
import { CodeNodePanel } from './code/components/CodeNodePanel';
import { ConditionNodePanel } from './condition/components/ConditionNodePanel';
import { LLMNodePanel } from './llm/components/LLMNodePanel';
import { TemplateNodePanel } from './template/components/TemplateNodePanel';
import { WorkflowNodePanel } from './workflow/components/WorkflowNodePanel';
import { FileExtractionNodePanel } from './file_extraction/components/FileExtractionNodePanel';
import { VariableExtractionNodePanel } from './variable_extraction/components/VariableExtractionNodePanel';
import { WebhookTriggerNodePanel } from './webhook/components/WebhookTriggerNodePanel';
import { ScheduleTriggerNodePanel } from './schedule/components/ScheduleTriggerNodePanel';
import { GithubNodePanel } from './github/components/GithubNodePanel';
import { MailNodePanel } from './mail/components/MailNodePanel';
import { GmailDraftNodePanel } from './mail/components/GmailDraftNodePanel';
import { MailAcknowledgeNodePanel } from './mail/components/MailAcknowledgeNodePanel';
import { LoopNodePanel } from './loop/components/LoopNodePanel';
import { VisiblePropertiesControl } from './VisiblePropertiesControl';

const isPanelSupported = (node: AppNode) =>
  node.type !== 'note' && node.type !== undefined;

type NodeInlinePanelSidePanelId = 'knowledge';

const NodePanelBody = ({
  node,
  onOpenSidePanel,
}: {
  node: AppNode;
  onOpenSidePanel?: (panelId: NodeInlinePanelSidePanelId) => void;
}) => {
  if (node.type === 'startNode') {
    return <StartNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'answerNode') {
    return <AnswerNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'httpRequestNode') {
    return <HttpRequestNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'slackPostNode') {
    return <SlackPostNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'codeNode') {
    return <CodeNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'conditionNode') {
    return <ConditionNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'llmNode') {
    return (
      <LLMNodePanel
        nodeId={node.id}
        data={node.data}
        onOpenKnowledgeBaseSettings={
          onOpenSidePanel ? () => onOpenSidePanel('knowledge') : undefined
        }
      />
    );
  }
  if (node.type === 'templateNode') {
    return <TemplateNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'workflowNode') {
    return <WorkflowNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'fileExtractionNode') {
    return <FileExtractionNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'variableExtractionNode') {
    return <VariableExtractionNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'webhookTrigger') {
    return <WebhookTriggerNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'scheduleTrigger') {
    return <ScheduleTriggerNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'githubNode') {
    return <GithubNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'mailNode') {
    return <MailNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'gmailDraftNode') {
    return <GmailDraftNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'mailAcknowledgeNode') {
    return <MailAcknowledgeNodePanel nodeId={node.id} data={node.data} />;
  }
  if (node.type === 'loopNode') {
    return <LoopNodePanel nodeId={node.id} data={node.data} />;
  }

  return (
    <div className="rounded-md border border-dashed border-gray-200 bg-gray-50 p-4 text-xs text-gray-500">
      이 노드는 아직 내부 편집 패널을 제공하지 않습니다.
    </div>
  );
};

export const NodeInlinePanel = ({
  node,
  showFrame = true,
  onOpenSidePanel,
}: {
  node: AppNode;
  showFrame?: boolean;
  onOpenSidePanel?: (panelId: NodeInlinePanelSidePanelId) => void;
}) => {
  if (!isPanelSupported(node)) return null;

  return (
    <div
      className={cn(
        'nodrag nowheel min-w-0 max-w-full overflow-visible',
        showFrame && 'mt-4 border-t border-gray-100 pt-4',
      )}
    >
      <div className="min-w-0 max-w-full [&_*]:min-w-0 [&_input]:max-w-full [&_input]:text-gray-700 [&_input::placeholder]:text-gray-500 [&_select]:max-w-full [&_select]:text-gray-700 [&_textarea]:max-w-full [&_textarea]:text-gray-800 [&_textarea::placeholder]:text-gray-500">
        {node.type !== 'llmNode' && <VisiblePropertiesControl node={node} />}
        <NodePanelBody node={node} onOpenSidePanel={onOpenSidePanel} />
      </div>
    </div>
  );
};

export type { NodeInlinePanelSidePanelId };
export type { DraggedOutputVariable } from '../../utils/nodeVariablePorts';
