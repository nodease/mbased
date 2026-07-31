import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { MailNodeData } from '../../../../types/Nodes';
import {
  MailCredentialOption,
  mailCredentialApi,
} from '../../../../api/mailCredentialApi';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { RoundedSelect } from '../../../ui/RoundedSelect';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import {
  DraggedOutputVariable,
  getDroppedOutputReferenceName,
  getTokenLabelMap,
  upsertNamedSelector,
} from '../../../../utils/nodeVariablePorts';
import { VariableTokenEditor } from '../../ui/VariableTokenEditor';

interface MailNodePanelProps {
  nodeId: string;
  data: MailNodeData;
}

export function MailNodePanel({ nodeId, data }: MailNodePanelProps) {
  const { updateNodeData, nodes, edges } = useWorkflowStore();
  const [credentials, setCredentials] = useState<MailCredentialOption[]>([]);
  const [credentialsLoading, setCredentialsLoading] = useState(true);
  const [credentialsError, setCredentialsError] = useState(false);
  const [oauthStarting, setOauthStarting] = useState(false);
  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );

  const handleUpdateData = useCallback(
    (key: keyof MailNodeData, value: unknown) => {
      updateNodeData(nodeId, { [key]: value });
    },
    [nodeId, updateNodeData],
  );

  useEffect(() => {
    let active = true;
    setCredentialsLoading(true);
    setCredentialsError(false);
    mailCredentialApi
      .listAvailable()
      .then((options) => {
        if (active) setCredentials(options);
      })
      .catch(() => {
        if (active) setCredentialsError(true);
      })
      .finally(() => {
        if (active) setCredentialsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const credentialMissing = !data.credential_id;
  const selectedCredentialUnavailable =
    Boolean(data.credential_id) &&
    !credentialsLoading &&
    !credentials.some((credential) => credential.id === data.credential_id);

  const handleKeywordDropOutput = useCallback(
    (output: DraggedOutputVariable) => {
      const referenceName = getDroppedOutputReferenceName(
        data.referenced_variables,
        output,
        'value_selector',
      );
      handleUpdateData(
        'referenced_variables',
        upsertNamedSelector(
          data.referenced_variables,
          output,
          'value_selector',
        ),
      );
      return referenceName;
    },
    [data.referenced_variables, handleUpdateData],
  );

  const tokenLabels = useMemo(
    () => getTokenLabelMap(data.referenced_variables, upstreamNodes),
    [data.referenced_variables, upstreamNodes],
  );

  const refreshCredentials = useCallback(async () => {
    setCredentialsLoading(true);
    setCredentialsError(false);
    try {
      setCredentials(await mailCredentialApi.listAvailable());
    } catch {
      setCredentialsError(true);
    } finally {
      setCredentialsLoading(false);
    }
  }, []);

  return (
    <div className="flex flex-col gap-2">
      <CollapsibleSection
        title="Mail Credential"
        defaultOpen={true}
        showDivider
      >
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">
              연결 계정
            </label>
            <RoundedSelect
              value={data.credential_id || ''}
              onChange={(value) =>
                updateNodeData(nodeId, {
                  credential_id: value || null,
                  configuration_state: value ? 'resolved' : 'unresolved',
                })
              }
              options={credentials.map((credential) => ({
                label: `${credential.credential_name} (${credential.email_preview})`,
                value: credential.id,
              }))}
              placeholder={
                credentialsLoading ? '불러오는 중' : 'Mail credential 선택'
              }
            />
          </div>
          {credentialMissing && !credentialsLoading && (
            <ValidationAlert message="Mail credential을 선택해주세요." />
          )}
          {selectedCredentialUnavailable && (
            <ValidationAlert message="선택한 Mail credential을 사용할 수 없습니다." />
          )}
          {credentialsError && (
            <ValidationAlert message="Mail credential 목록을 불러오지 못했습니다." />
          )}
          <button
            type="button"
            disabled={oauthStarting}
            onClick={async () => {
              const popup = window.open(
                'about:blank',
                'gmail-oauth',
                'popup,width=560,height=720',
              );
              if (!popup) {
                setCredentialsError(true);
                return;
              }
              popup.opener = null;
              setOauthStarting(true);
              try {
                const { authorization_url } =
                  await mailCredentialApi.startGoogleOAuth('Gmail OAuth');
                popup.location.replace(authorization_url);
              } catch {
                popup.close();
                setCredentialsError(true);
              } finally {
                setOauthStarting(false);
              }
            }}
            className="h-8 rounded border border-gray-300 bg-white px-3 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {oauthStarting ? '연결 준비 중' : 'Gmail OAuth 연결'}
          </button>
          <button
            type="button"
            title="Credential 목록 새로고침"
            aria-label="Credential 목록 새로고침"
            disabled={credentialsLoading}
            onClick={refreshCredentials}
            className="flex h-8 w-8 items-center justify-center rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="검색 옵션" defaultOpen={true} showDivider>
        <div className="flex flex-col gap-2 relative">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">
              검색 키워드
            </label>
            <VariableTokenEditor
              className="min-h-20 font-mono text-xs"
              placeholder="검색 키워드를 입력하세요..."
              value={data.keyword || ''}
              onChange={(value) => handleUpdateData('keyword', value)}
              onDropOutput={handleKeywordDropOutput}
              tokenLabels={tokenLabels}
              ariaLabel="메일 검색 키워드"
            />
            <p className="text-[10px] text-gray-500">
              검색 키워드에 커서를 둔 뒤 좌측 입력 패널에서 변수를 클릭해
              추가하세요.
            </p>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">
              보낸 사람
            </label>
            <input
              type="text"
              className="h-8 w-full rounded border border-gray-300 px-2 text-sm focus:outline-none focus:border-blue-500"
              placeholder="예) sender@example.com"
              value={data.sender || ''}
              onChange={(e) => handleUpdateData('sender', e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">제목</label>
            <input
              type="text"
              className="h-8 w-full rounded border border-gray-300 px-2 text-sm focus:outline-none focus:border-blue-500"
              placeholder="메일 제목..."
              value={data.subject || ''}
              onChange={(e) => handleUpdateData('subject', e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-700">
                시작 날짜
              </label>
              <input
                type="date"
                className="h-8 w-full rounded border border-gray-300 px-2 text-sm focus:outline-none focus:border-blue-500"
                value={data.start_date || ''}
                onChange={(e) => handleUpdateData('start_date', e.target.value)}
              />
              <p className="text-[10px] text-gray-500">💡 기본값: 7일 전</p>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-700">
                종료 날짜
              </label>
              <input
                type="date"
                className="h-8 w-full rounded border border-gray-300 px-2 text-sm focus:outline-none focus:border-blue-500"
                value={data.end_date || ''}
                onChange={(e) => handleUpdateData('end_date', e.target.value)}
              />
            </div>
          </div>

          {/* 폴더 */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">폴더</label>
            <RoundedSelect
              value={data.folder || 'INBOX'}
              onChange={(val) => handleUpdateData('folder', val)}
              options={[
                { label: 'INBOX', value: 'INBOX' },
                { label: 'SENT', value: 'SENT' },
                { label: 'DRAFTS', value: 'DRAFTS' },
                { label: 'SPAM', value: 'SPAM' },
                { label: 'TRASH', value: 'TRASH' },
              ]}
              placeholder="폴더 선택"
              className="h-8 py-1"
            />
          </div>

          {/* 최대 결과 수 */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">
              최대 결과 수
            </label>
            <input
              type="number"
              min="1"
              max="100"
              className="h-8 w-full rounded border border-gray-300 px-2 text-sm focus:outline-none focus:border-blue-500"
              value={data.max_results ?? ''}
              placeholder="5"
              onChange={(e) => {
                const value =
                  e.target.value === '' ? undefined : parseInt(e.target.value);
                handleUpdateData('max_results', value);
              }}
            />
            <p className="text-[10px] text-gray-500">
              💡 기본값: 5 (비어있을 때)
            </p>
          </div>

          {/* 체크박스 */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">
              처리 모드
            </label>
            <RoundedSelect
              value={data.processing_mode || 'search_only'}
              onChange={(value) =>
                updateNodeData(nodeId, {
                  processing_mode: value,
                  mark_as_read: value === 'durable' ? false : data.mark_as_read,
                })
              }
              options={[
                { label: '검색만', value: 'search_only' },
                { label: '자동화 처리 추적', value: 'durable' },
              ]}
              placeholder="처리 모드"
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="unread-only"
              className="h-4 w-4 rounded border-gray-300"
              checked={data.unread_only || false}
              onChange={(e) =>
                handleUpdateData('unread_only', e.target.checked)
              }
            />
            <label htmlFor="unread-only" className="text-xs text-gray-700">
              읽지 않은 메일만
            </label>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="mark-as-read"
              className="h-4 w-4 rounded border-gray-300"
              checked={data.mark_as_read || false}
              disabled={data.processing_mode === 'durable'}
              onChange={(e) =>
                handleUpdateData('mark_as_read', e.target.checked)
              }
            />
            <label htmlFor="mark-as-read" className="text-xs text-gray-700">
              {data.processing_mode === 'durable'
                ? '처리 완료 노드에서 읽음 처리'
                : '검색 후 읽음 처리'}
            </label>
          </div>
        </div>
      </CollapsibleSection>
    </div>
  );
}
