import { memo, useMemo } from 'react';
import { NodeProps, Node } from '@xyflow/react';
import { Slack } from 'lucide-react';

import { SlackPostNodeData } from '../../../../types/Nodes';
import { BaseNode } from '../../BaseNode';
import { ValidationBadge } from '../../../ui/ValidationBadge';
import { hasIncompleteVariables } from '../../../../utils/validationUtils';
import {
  collectSlackTemplateVariables,
  isNonEmptySlackJsonArrayTemplate,
  isValidSlackJsonArrayTemplate,
  isValidCommercialSlackWebhookUrl,
} from '../../../../utils/slackDelivery';

export const SlackPostNode = memo(
  ({ id, data, selected }: NodeProps<Node<SlackPostNodeData>>) => {
    const mode = data.slackMode || 'api';
    const urlPreview =
      mode === 'api'
        ? 'slack.com / chat.postMessage'
        : data.url || 'https://hooks.slack.com/services/...';
    const modeClass =
      mode === 'api'
        ? 'bg-blue-100 text-blue-700 border-blue-200'
        : 'bg-purple-100 text-purple-700 border-purple-200';
    const modeLabel = mode === 'api' ? 'API' : 'Web Hook';
    const trimmedUrl = (data.url || '').trim();
    const blocksText = (data.blocks || '').trim();
    const attachmentsText = (data.attachments || '').trim();

    const missingVariables = useMemo(() => {
      const registered = new Set(
        (data.referenced_variables || [])
          .map((v) => v.name?.trim())
          .filter(Boolean),
      );
      return collectSlackTemplateVariables(
        data.message,
        data.blocks,
        data.attachments,
        mode === 'api' ? data.channel : undefined,
        data.thread_ts,
        data.username,
        data.icon_emoji,
      ).filter((name) => !registered.has(name));
    }, [
      data.attachments,
      data.blocks,
      data.channel,
      data.icon_emoji,
      data.message,
      mode,
      data.referenced_variables,
      data.thread_ts,
      data.username,
    ]);

    const blocksJsonError = useMemo(() => {
      if (!blocksText) return false;
      return !isValidSlackJsonArrayTemplate(blocksText);
    }, [blocksText]);

    const attachmentsJsonError = useMemo(() => {
      if (!attachmentsText) return false;
      return !isValidSlackJsonArrayTemplate(attachmentsText);
    }, [attachmentsText]);

    const isWebhookUrlValid =
      mode !== 'webhook' || isValidCommercialSlackWebhookUrl(data.url);

    const hasMessage = !!data.message?.trim();
    const hasValidBlocks = isNonEmptySlackJsonArrayTemplate(blocksText);
    const hasValidAttachments =
      isNonEmptySlackJsonArrayTemplate(attachmentsText);

    const hasValidationIssue =
      mode === 'webhook'
        ? !trimmedUrl ||
          !isWebhookUrlValid ||
          (!hasMessage && !hasValidBlocks && !hasValidAttachments) ||
          blocksJsonError ||
          attachmentsJsonError ||
          missingVariables.length > 0 ||
          hasIncompleteVariables(data.referenced_variables)
        : !data.authConfig?.token?.trim() ||
          !data.channel?.trim() ||
          (!hasMessage && !hasValidBlocks && !hasValidAttachments) ||
          blocksJsonError ||
          attachmentsJsonError ||
          missingVariables.length > 0 ||
          hasIncompleteVariables(data.referenced_variables);

    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        showSourceHandle={true}
        icon={<Slack className="text-white" />}
        iconColor="#4A154B"
      >
        <div className="flex flex-col gap-2 p-1">
          <div className="flex items-center gap-2 text-[11px] text-gray-600">
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${modeClass}`}
            >
              {modeLabel}
            </span>
            <span className="flex-1 truncate font-mono text-[10px] text-gray-500 max-w-[200px]">
              {urlPreview}
            </span>
          </div>

          {hasValidationIssue && <ValidationBadge />}
        </div>
      </BaseNode>
    );
  },
);

SlackPostNode.displayName = 'SlackPostNode';
