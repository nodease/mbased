import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import { GithubNodeData } from '../../../../types/Nodes';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { RoundedSelect } from '../../../ui/RoundedSelect';
import { ExternalLink } from 'lucide-react';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import {
  DraggedOutputVariable,
  getDroppedOutputReferenceName,
  getTokenLabelMap,
  upsertNamedSelector,
} from '@/app/features/workflow/utils/nodeVariablePorts';
import { VariableTokenEditor } from '../../ui/VariableTokenEditor';

interface GithubNodePanelProps {
  nodeId: string;
  data: GithubNodeData;
}

// 노드 실행 필수 요건 체크
// 1. API 토큰이 입력되어야 함
// 2. 소유자(Owner)가 입력되어야 함
// 3. 저장소(Repo) 이름이 입력되어야 함
// 4. PR 번호가 유효해야 함 (양수)

export function GithubNodePanel({ nodeId, data }: GithubNodePanelProps) {
  const { activeWorkflowId, updateNodeData, nodes, edges } = useWorkflowStore();
  const [tokenDraft, setTokenDraft] = useState('');
  const [tokenStatus, setTokenStatus] = useState<string | null>(null);
  const [tokenSaving, setTokenSaving] = useState(false);
  const tokenRevisionRef = useRef(0);
  const activeWorkflowIdRef = useRef(activeWorkflowId);

  useEffect(() => {
    activeWorkflowIdRef.current = activeWorkflowId;
  }, [activeWorkflowId]);

  // 상위 노드 가져오기
  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );

  const handleUpdateData = useCallback(
    (key: keyof GithubNodeData, value: unknown) => {
      updateNodeData(nodeId, { [key]: value });
    },
    [nodeId, updateNodeData],
  );

  const handleTextDropOutput = useCallback(
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

  const tokenMissing = useMemo(() => {
    return !data.api_token?.trim();
  }, [data.api_token]);

  const storeToken = useCallback(async () => {
    if (!activeWorkflowId || !tokenDraft.trim()) return;
    const submittedWorkflowId = activeWorkflowId;
    const submittedRevision = tokenRevisionRef.current;
    setTokenSaving(true);
    setTokenStatus(null);
    try {
      const result = await workflowApi.storeNodeSecret(activeWorkflowId, {
        node_id: nodeId,
        node_type: 'githubNode',
        parameter_key: 'api_token',
        secret_value: tokenDraft.trim(),
      });
      if (
        activeWorkflowIdRef.current !== submittedWorkflowId ||
        tokenRevisionRef.current !== submittedRevision
      ) {
        setTokenStatus(
          '입력 또는 Workflow가 변경되어 저장 결과를 적용하지 않았습니다.',
        );
        return;
      }
      handleUpdateData('api_token', result.secret_reference);
      setTokenDraft('');
      setTokenStatus('GitHub Token이 저장되었습니다.');
    } catch {
      setTokenStatus('GitHub Token을 저장하지 못했습니다.');
    } finally {
      setTokenSaving(false);
    }
  }, [activeWorkflowId, handleUpdateData, nodeId, tokenDraft]);

  const ownerMissing = useMemo(() => {
    return !data.repo_owner?.trim();
  }, [data.repo_owner]);

  const repoMissing = useMemo(() => {
    return !data.repo_name?.trim();
  }, [data.repo_name]);

  const prMissing = useMemo(() => {
    return !data.pr_number;
  }, [data.pr_number]);

  return (
    <div className="flex flex-col gap-2">
      {/* 1. 액션 선택 */}
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-gray-700">작업</label>
        <RoundedSelect
          value={data.action || 'get_pr'}
          onChange={(val) => {
            const newAction = val;
            handleUpdateData('action', newAction);

            // Action에 따라 title 자동 변경
            const titleMap: Record<string, string> = {
              get_pr: 'Get PR Diff',
              comment_pr: 'Comment on PR',
            };
            handleUpdateData('title', titleMap[newAction] || 'GitHub');
          }}
          options={[
            { label: 'Get PR Diff', value: 'get_pr' },
            { label: 'Comment on PR', value: 'comment_pr' },
          ]}
        />
      </div>
      <div className="border-b border-gray-200" />

      {/* 2. 인증 */}
      <CollapsibleSection title="인증" defaultOpen={true} showDivider>
        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium text-gray-700">
            GitHub 개인 액세스 토큰
          </label>
          <input
            type="password"
            className="h-8 w-full rounded border border-gray-300 px-2 text-sm font-mono focus:outline-none focus:border-blue-500"
            placeholder="ghp_xxxxxxxxxxxx"
            value={tokenDraft}
            onChange={(e) => {
              tokenRevisionRef.current += 1;
              setTokenDraft(e.target.value);
            }}
          />
          <button
            type="button"
            className="w-fit rounded border border-gray-300 px-3 py-1.5 text-xs font-medium disabled:opacity-60"
            disabled={tokenSaving || !tokenDraft.trim()}
            onClick={() => void storeToken()}
          >
            GitHub Token 적용
          </button>
          {tokenStatus ? (
            <p className="text-xs text-gray-500" role="status">
              {tokenStatus}
            </p>
          ) : null}
          <a
            href="https://github.com/settings/tokens/new?description=Moduly&scopes=repo"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-gray-50 border border-gray-200 text-xs font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors w-fit"
          >
            <ExternalLink className="w-3 h-3" />
            GitHub 토큰 발급받기 (repo 권한 포함)
          </a>
          {tokenMissing && (
            <ValidationAlert message="⚠️ API 토큰을 입력해주세요." />
          )}
        </div>
      </CollapsibleSection>

      {/* 3. 저장소 정보 */}
      <CollapsibleSection title="저장소 정보" defaultOpen={true} showDivider>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">소유자</label>
            <input
              className="h-8 w-full rounded border border-gray-300 px-2 text-sm font-mono focus:outline-none focus:border-blue-500"
              placeholder="예) facebook"
              value={data.repo_owner || ''}
              onChange={(e) => handleUpdateData('repo_owner', e.target.value)}
            />
            {ownerMissing && (
              <ValidationAlert message="⚠️ 소유자를 입력해주세요." />
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">저장소</label>
            <input
              className="h-8 w-full rounded border border-gray-300 px-2 text-sm font-mono focus:outline-none focus:border-blue-500"
              placeholder="예) react"
              value={data.repo_name || ''}
              onChange={(e) => handleUpdateData('repo_name', e.target.value)}
            />
            {repoMissing && (
              <ValidationAlert message="⚠️ 저장소 이름을 입력해주세요." />
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-700">PR 번호</label>
            <VariableTokenEditor
              className="min-h-9 font-mono text-xs"
              placeholder="예) 123"
              value={data.pr_number || ''}
              onChange={(value) => handleUpdateData('pr_number', value)}
              onDropOutput={handleTextDropOutput}
              tokenLabels={tokenLabels}
              ariaLabel="GitHub PR 번호"
            />
            <p className="text-[10px] text-gray-400">
              PR 번호에 커서를 둔 뒤 좌측 입력 패널에서 변수를 클릭해
              추가하세요.
            </p>
            {prMissing && (
              <ValidationAlert message="⚠️ PR 번호를 입력해주세요." />
            )}
          </div>
        </div>
      </CollapsibleSection>

      {/* 4. 코멘트 내용 (PR 코멘트 액션 전용) */}
      {data.action === 'comment_pr' && (
        <CollapsibleSection title="코멘트 내용" defaultOpen={true} showDivider>
          <div className="flex flex-col gap-2 relative">
            <VariableTokenEditor
              className="min-h-32 font-mono text-xs"
              placeholder="코멘트 내용을 입력하세요..."
              value={data.comment_body || ''}
              onChange={(value) => handleUpdateData('comment_body', value)}
              onDropOutput={handleTextDropOutput}
              tokenLabels={tokenLabels}
              ariaLabel="GitHub 코멘트"
            />
            <div className="text-[10px] text-gray-500">
              코멘트에 커서를 둔 뒤 좌측 입력 패널에서 변수를 클릭해 추가하세요.
            </div>
          </div>
        </CollapsibleSection>
      )}
    </div>
  );
}
