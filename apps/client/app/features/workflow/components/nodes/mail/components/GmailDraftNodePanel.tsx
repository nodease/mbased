import { useEffect, useMemo, useState } from 'react';

import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import {
  MailCredentialOption,
  mailCredentialApi,
} from '../../../../api/mailCredentialApi';
import { GmailDraftNodeData } from '../../../../types/Nodes';
import { getNodeOutputVariables } from '../../../../utils/nodeVariablePorts';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { RoundedSelect } from '../../../ui/RoundedSelect';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import { VariableSelectorSlot } from '../../ui/VariableSelectorSlot';

export function GmailDraftNodePanel({
  nodeId,
  data,
}: {
  nodeId: string;
  data: GmailDraftNodeData;
}) {
  const { nodes, updateNodeData } = useWorkflowStore();
  const [credentials, setCredentials] = useState<MailCredentialOption[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let active = true;
    mailCredentialApi
      .listAvailable()
      .then((options) => {
        if (active) {
          setCredentials(
            options.filter(
              (option) =>
                option.provider === 'gmail' && option.auth_type === 'oauth2',
            ),
          );
        }
      })
      .catch(() => {
        if (active) setLoadFailed(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const selectedOutput = (selector: string[]) => {
    const node = nodes.find((candidate) => candidate.id === selector?.[0]);
    return node
      ? getNodeOutputVariables(node).find(
          (output) =>
            output.key === selector?.[1] || output.outputId === selector?.[1],
        )
      : undefined;
  };
  const credentialUnavailable = useMemo(
    () =>
      Boolean(data.credential_id) &&
      !credentials.some((option) => option.id === data.credential_id),
    [credentials, data.credential_id],
  );
  const processingSource = nodes.find(
    (candidate) => candidate.id === data.processing_ref_selector?.[0],
  );
  const processingSourceInvalid =
    Boolean(data.processing_ref_selector?.length) &&
    (processingSource?.type !== 'mailNode' ||
      data.processing_ref_selector?.[1] !== 'processing_ref' ||
      processingSource.data.processing_mode !== 'durable' ||
      processingSource.data.max_results !== 1);

  return (
    <div className="flex flex-col gap-2">
      <CollapsibleSection title="Gmail 연결" defaultOpen showDivider>
        <RoundedSelect
          value={data.credential_id || ''}
          onChange={(value) =>
            updateNodeData(nodeId, {
              credential_id: value || null,
              configuration_state: value ? 'resolved' : 'unresolved',
            })
          }
          options={credentials.map((credential) => ({
            value: credential.id,
            label: `${credential.credential_name} (${credential.email_preview})`,
          }))}
          placeholder="Gmail OAuth credential 선택"
        />
        {!data.credential_id && (
          <ValidationAlert message="Gmail OAuth credential을 선택해주세요." />
        )}
        {credentialUnavailable && (
          <ValidationAlert message="선택한 Gmail credential을 사용할 수 없습니다." />
        )}
        {loadFailed && (
          <ValidationAlert message="Gmail credential 목록을 불러오지 못했습니다." />
        )}
      </CollapsibleSection>

      <CollapsibleSection title="입력 연결" defaultOpen showDivider>
        <div className="flex flex-col gap-3">
          <VariableSelectorSlot
            value={data.processing_ref_selector}
            selectedOutput={selectedOutput(data.processing_ref_selector)}
            label="Mail processing reference"
            onChange={(selector) =>
              updateNodeData(nodeId, { processing_ref_selector: selector })
            }
          />
          {processingSourceInvalid && (
            <ValidationAlert message="단일 Draft 처리는 결과 수 1인 durable Mail 노드의 처리 참조가 필요합니다." />
          )}
          <VariableSelectorSlot
            value={data.reply_body_selector}
            selectedOutput={selectedOutput(data.reply_body_selector)}
            label="답장 본문"
            onChange={(selector) =>
              updateNodeData(nodeId, { reply_body_selector: selector })
            }
          />
          <p className="text-[11px] text-gray-500">
            이 노드는 메일을 발송하지 않고 Gmail Draft만 생성합니다.
          </p>
        </div>
      </CollapsibleSection>
    </div>
  );
}
