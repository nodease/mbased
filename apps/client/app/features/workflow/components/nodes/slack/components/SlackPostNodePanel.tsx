import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { SlackPostNodeData } from '../../../../types/Nodes';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import {
  DraggedOutputVariable,
  getDroppedOutputReferenceName,
  getTokenLabelMap,
  upsertNamedSelector,
} from '@/app/features/workflow/utils/nodeVariablePorts';
import { VariableTokenEditor } from '../../ui/VariableTokenEditor';
import { UnregisteredVariablesAlert } from '../../../ui/UnregisteredVariablesAlert';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import {
  collectSlackTemplateVariables,
  isNonEmptySlackJsonArrayTemplate,
  isValidSlackJsonArrayTemplate,
  isValidCommercialSlackWebhookUrl,
} from '../../../../utils/slackDelivery';
import { isWorkflowNodeSecretReference } from '../../../../utils/workflowNodeSecret';

interface SlackPostNodePanelProps {
  nodeId: string;
  data: SlackPostNodeData;
}

export function SlackPostNodePanel({ nodeId, data }: SlackPostNodePanelProps) {
  const { activeWorkflowId, updateNodeData, nodes, edges } = useWorkflowStore();
  const [botTokenDraft, setBotTokenDraft] = useState('');
  const [webhookUrlDraft, setWebhookUrlDraft] = useState('');
  const [secretStatus, setSecretStatus] = useState<string | null>(null);
  const [secretSaving, setSecretSaving] = useState(false);
  const botTokenRevisionRef = useRef(0);
  const webhookUrlRevisionRef = useRef(0);
  const activeWorkflowIdRef = useRef(activeWorkflowId);

  useEffect(() => {
    activeWorkflowIdRef.current = activeWorkflowId;
  }, [activeWorkflowId]);

  const mode = data.slackMode || 'api';
  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );
  const tokenLabels = useMemo(
    () => getTokenLabelMap(data.referenced_variables || [], upstreamNodes),
    [data.referenced_variables, upstreamNodes],
  );

  const update = useCallback(
    (next: Partial<SlackPostNodeData>) => updateNodeData(nodeId, next),
    [nodeId, updateNodeData],
  );
  const handleDrop = useCallback(
    (output: DraggedOutputVariable) => {
      const name = getDroppedOutputReferenceName(
        data.referenced_variables || [],
        output,
        'value_selector',
      );
      update({
        referenced_variables: upsertNamedSelector(
          data.referenced_variables || [],
          output,
          'value_selector',
        ),
      });
      return name;
    },
    [data.referenced_variables, update],
  );
  const storeSecret = useCallback(
    async (
      parameterKey: 'bot_token' | 'url',
      secretValue: string,
    ) => {
      if (!activeWorkflowId || !secretValue.trim()) return;
      const submittedWorkflowId = activeWorkflowId;
      const submittedRevision =
        parameterKey === 'bot_token'
          ? botTokenRevisionRef.current
          : webhookUrlRevisionRef.current;
      setSecretSaving(true);
      setSecretStatus(null);
      try {
        const result = await workflowApi.storeNodeSecret(activeWorkflowId, {
          node_id: nodeId,
          node_type: 'slackPostNode',
          parameter_key: parameterKey,
          secret_value: secretValue.trim(),
        });
        const currentRevision =
          parameterKey === 'bot_token'
            ? botTokenRevisionRef.current
            : webhookUrlRevisionRef.current;
        if (
          activeWorkflowIdRef.current !== submittedWorkflowId ||
          currentRevision !== submittedRevision
        ) {
          setSecretStatus(
            '입력 또는 Workflow가 변경되어 저장 결과를 적용하지 않았습니다.',
          );
          return;
        }
        if (parameterKey === 'bot_token') {
          update({
            authConfig: {
              ...(data.authConfig || {}),
              token: result.secret_reference,
            },
          });
          setBotTokenDraft('');
          setSecretStatus('Bot Token이 저장되었습니다.');
        } else {
          update({ url: result.secret_reference });
          setWebhookUrlDraft('');
          setSecretStatus('Webhook URL이 저장되었습니다.');
        }
      } catch {
        setSecretStatus('보안 설정을 저장하지 못했습니다.');
      } finally {
        setSecretSaving(false);
      }
    },
    [activeWorkflowId, data.authConfig, nodeId, update],
  );
  const missingVariables = useMemo(() => {
    const configured = new Set(
      (data.referenced_variables || [])
        .map((item) => item.name)
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
    ).filter((name) => !configured.has(name));
  }, [
    data.attachments,
    data.blocks,
    data.channel,
    data.icon_emoji,
    data.message,
    data.referenced_variables,
    data.thread_ts,
    data.username,
    mode,
  ]);
  const hasLegacyAuthType =
    mode === 'api'
      ? data.authType !== undefined && data.authType !== 'bearer'
      : data.authType !== undefined && data.authType !== 'none';
  const hasLegacyHttpConfiguration =
    (data.method !== undefined && data.method !== 'POST') ||
    (data.headers?.length || 0) > 0 ||
    Boolean(data.body?.trim()) ||
    (data.timeout !== undefined && data.timeout !== 5000) ||
    hasLegacyAuthType ||
    (mode === 'api' &&
      data.url !== undefined &&
      data.url !== '' &&
      data.url !== 'https://slack.com/api/chat.postMessage');
  const blocksInvalid =
    Boolean(data.blocks?.trim()) &&
    !isValidSlackJsonArrayTemplate(data.blocks || '');
  const attachmentsInvalid =
    Boolean(data.attachments?.trim()) &&
    !isValidSlackJsonArrayTemplate(data.attachments || '');
  const hasDeliveryPayload =
    Boolean(data.message?.trim()) ||
    isNonEmptySlackJsonArrayTemplate(data.blocks || '') ||
    isNonEmptySlackJsonArrayTemplate(data.attachments || '');

  return (
    <div className="flex flex-col gap-4 p-4 text-foreground">
      <div className="flex gap-2" role="group" aria-label="Slack 전달 방식">
        {(['api', 'webhook'] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            className={`rounded border px-3 py-1.5 text-xs ${
              mode === candidate
                ? 'border-[#4A154B] bg-[#4A154B] text-white'
                : 'border-border bg-background text-foreground'
            }`}
            onClick={() =>
              update({
                slackMode: candidate,
                channel: candidate === 'api' ? data.channel || '' : '',
                url:
                  candidate === 'webhook' &&
                  (isValidCommercialSlackWebhookUrl(data.url) ||
                    isWorkflowNodeSecretReference(data.url))
                    ? data.url
                    : undefined,
                authConfig: candidate === 'api' ? data.authConfig || {} : {},
                authType: candidate === 'webhook' ? 'none' : undefined,
              })
            }
          >
            {candidate === 'api' ? 'Slack API' : 'Incoming Webhook'}
          </button>
        ))}
      </div>

      {hasLegacyHttpConfiguration ? (
        <div className="rounded border border-amber-500/50 bg-amber-500/10 p-3 text-xs text-foreground">
          <p>기존 HTTP 설정은 Slack 전송에 사용되지 않습니다.</p>
          <button
            type="button"
            className="mt-2 rounded border border-amber-500 px-2 py-1 font-medium"
            onClick={() =>
              update({
                method: undefined,
                headers: undefined,
                body: undefined,
                timeout: undefined,
                authType: mode === 'webhook' ? 'none' : undefined,
                url: mode === 'api' ? undefined : data.url,
              })
            }
          >
            기존 HTTP 설정 제거
          </button>
        </div>
      ) : null}

      {mode === 'api' ? (
        <CollapsibleSection title="Slack API 설정" defaultOpen showDivider>
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium">봇 토큰</label>
            <input
              type="password"
              className="h-9 rounded border border-border bg-background px-3 text-sm text-foreground"
              value={botTokenDraft}
              onChange={(event) => {
                botTokenRevisionRef.current += 1;
                setBotTokenDraft(event.target.value);
              }}
              autoComplete="off"
            />
            <button
              type="button"
              className="w-fit rounded border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-60"
              disabled={secretSaving || !botTokenDraft.trim()}
              onClick={() => void storeSecret('bot_token', botTokenDraft)}
            >
              Bot Token 적용
            </button>
            <label className="text-xs font-medium">채널 ID</label>
            <input
              className="h-9 rounded border border-border bg-background px-3 text-sm text-foreground"
              value={data.channel || ''}
              onChange={(event) => update({ channel: event.target.value })}
              placeholder="C0123456789"
            />
          </div>
        </CollapsibleSection>
      ) : (
        <CollapsibleSection
          title="Incoming Webhook 설정"
          defaultOpen
          showDivider
        >
          <label className="text-xs font-medium">Webhook URL</label>
          <input
            type="password"
            className="mt-1 h-9 w-full rounded border border-border bg-background px-3 text-sm text-foreground"
            value={webhookUrlDraft}
            onChange={(event) => {
              webhookUrlRevisionRef.current += 1;
              setWebhookUrlDraft(event.target.value);
            }}
            autoComplete="off"
          />
          <button
            type="button"
            className="mt-2 w-fit rounded border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-60"
            disabled={secretSaving || !webhookUrlDraft.trim()}
            onClick={() => void storeSecret('url', webhookUrlDraft)}
          >
            Webhook URL 적용
          </button>
          <p className="mt-1 text-xs text-muted-foreground">
            `https://hooks.slack.com/services/` 형식만 허용됩니다.
          </p>
        </CollapsibleSection>
      )}

      {secretStatus ? (
        <p className="text-xs text-muted-foreground" role="status">
          {secretStatus}
        </p>
      ) : null}

      <CollapsibleSection title="메시지" defaultOpen showDivider>
        <VariableTokenEditor
          className="min-h-24 text-sm"
          value={data.message || ''}
          onChange={(message) => update({ message })}
          onDropOutput={handleDrop}
          tokenLabels={tokenLabels}
          ariaLabel="Slack 메시지"
        />
        {missingVariables.length > 0 && (
          <UnregisteredVariablesAlert variables={missingVariables} />
        )}
      </CollapsibleSection>

      <CollapsibleSection title="블록 (선택)" showDivider>
        <textarea
          className="min-h-28 w-full rounded border border-border bg-background p-2 font-mono text-xs text-foreground"
          value={data.blocks || ''}
          onChange={(event) => update({ blocks: event.target.value })}
          placeholder='[ { "type": "section" } ]'
          aria-label="Slack 블록 JSON"
        />
        {blocksInvalid ? (
          <ValidationAlert message="블록은 유효한 JSON 배열이어야 합니다." />
        ) : null}
      </CollapsibleSection>

      <CollapsibleSection title="첨부 (선택)" showDivider>
        <textarea
          className="min-h-28 w-full rounded border border-border bg-background p-2 font-mono text-xs text-foreground"
          value={data.attachments || ''}
          onChange={(event) => update({ attachments: event.target.value })}
          placeholder='[ { "color": "#4A154B", "text": "알림" } ]'
          aria-label="Slack 첨부 JSON"
        />
        {attachmentsInvalid ? (
          <ValidationAlert message="첨부는 유효한 JSON 배열이어야 합니다." />
        ) : null}
      </CollapsibleSection>

      {!hasDeliveryPayload ? (
        <ValidationAlert message="메시지, 블록 또는 첨부 중 하나가 필요합니다." />
      ) : null}

      {(mode === 'api' && (!data.authConfig?.token || !data.channel)) ||
      (mode === 'webhook' &&
        !isValidCommercialSlackWebhookUrl(data.url) &&
        !isWorkflowNodeSecretReference(data.url)) ? (
        <ValidationAlert message="Slack 전달 설정을 완료해야 실행할 수 있습니다." />
      ) : null}
    </div>
  );
}
