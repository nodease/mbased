import React, { useCallback, useMemo, useState, useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { getStoredActiveOrganizationId } from '@/lib/activeOrganization';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { TemplateNodeData } from '../../../../types/Nodes';
import { UnregisteredVariablesAlert } from '../../../ui/UnregisteredVariablesAlert';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import { TemplateWizardModal } from '../../../modals/TemplateWizardModal';
import {
  DraggedOutputVariable,
  getDroppedOutputReferenceName,
  getTokenLabelMap,
  upsertNamedSelector,
} from '@/app/features/workflow/utils/nodeVariablePorts';
import { resolveWorkflowWizardOrganizationId } from '@/app/features/workflow/utils/resolveWorkflowWizardOrganizationId';
import { VariableTokenEditor } from '../../ui/VariableTokenEditor';

interface TemplateNodePanelProps {
  nodeId: string;
  data: TemplateNodeData;
}

// 노드 실행 필수 요건 체크
// 1. 템플릿 내용이 비어있지 않아야 함
// 2. 입력 변수 매핑이 완료되어야 함

export const TemplateNodePanel: React.FC<TemplateNodePanelProps> = ({
  nodeId,
  data,
}) => {
  const { nodes, edges, updateNodeData, activeWorkflowId, workflowAccess } =
    useWorkflowStore();
  const wizardOrganizationId = resolveWorkflowWizardOrganizationId(
    workflowAccess,
    activeWorkflowId,
  );

  // 템플릿 마법사 모달 상태
  const [showWizardModal, setShowWizardModal] = useState(false);
  const [hasCredentials, setHasCredentials] = useState<boolean | null>(null);

  // Credential 확인 (마운트 시)
  useEffect(() => {
    let active = true;

    const checkCredentials = async () => {
      setHasCredentials(null);
      if (wizardOrganizationId === null) return;

      try {
        const organizationId =
          wizardOrganizationId ?? getStoredActiveOrganizationId();
        const query = organizationId
          ? `?organization_id=${encodeURIComponent(organizationId)}`
          : '';
        const res = await fetch(`/api/v1/template-wizard/check-credentials${query}`, {
          method: 'GET',
          credentials: 'include',
        });
        if (!active) return;
        if (res.ok) {
          const data = await res.json();
          setHasCredentials(data.has_credentials);
        }
      } catch {
        if (active) setHasCredentials(false);
      }
    };

    void checkCredentials();
    return () => {
      active = false;
    };
  }, [wizardOrganizationId]);

  // 등록된 변수명 목록 추출
  const registeredVariableNames = useMemo(() => {
    return (data.variables || [])
      .map((v) => v.name?.trim())
      .filter(Boolean) as string[];
  }, [data.variables]);

  // 1. 상위 노드
  const upstreamNodes = useMemo(() => {
    return getUpstreamNodes(nodeId, nodes, edges);
  }, [nodeId, nodes, edges]);

  // 템플릿 변경 핸들러
  const handleTemplateChange = (value: string) => {
    updateNodeData(nodeId, { template: value });
  };

  const handleTemplateDropOutput = useCallback(
    (output: DraggedOutputVariable) => {
      const referenceName = getDroppedOutputReferenceName(
        data.variables,
        output,
        'value_selector',
      );
      updateNodeData(nodeId, {
        variables: upsertNamedSelector(
          data.variables,
          output,
          'value_selector',
        ),
      });
      return referenceName;
    },
    [data.variables, nodeId, updateNodeData],
  );

  const validationErrors = useMemo(() => {
    const template = data.template || '';
    const registeredNames = new Set(
      (data.variables || []).map((v) => v.name?.trim()).filter(Boolean),
    );
    const errors: string[] = [];

    const regex = /{{\s*([^}]+?)\s*}}/g;
    let match;
    while ((match = regex.exec(template)) !== null) {
      const varName = match[1].trim();
      if (varName && !registeredNames.has(varName)) {
        errors.push(varName);
      }
    }
    return Array.from(new Set(errors)); // 중복 제거
  }, [data.template, data.variables]);

  const tokenLabels = useMemo(
    () => getTokenLabelMap(data.variables, upstreamNodes),
    [data.variables, upstreamNodes],
  );

  return (
    <div className="flex flex-col gap-2">
      {/* 템플릿 에디터 */}
      <CollapsibleSection title="템플릿" showDivider>
        <div className="flex flex-col gap-2 relative">
          {/* 헤더: 설명 + 마법사 버튼 */}
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-gray-500 leading-snug">
              원하는 내용을 자유롭게 템플릿으로 작성하세요.
            </p>

            <div className="group/wizard relative shrink-0">
              <button
                type="button"
                onClick={() => setShowWizardModal(true)}
                disabled={hasCredentials === false || wizardOrganizationId === null}
                title={
                  hasCredentials === false
                    ? 'Provider를 먼저 등록해주세요'
                    : 'AI로 템플릿 작성/개선하기'
                }
                className="flex items-center gap-1 px-1.5 py-0.5 text-pink-500 hover:text-pink-700 hover:bg-pink-50 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-[10px]"
              >
                <Sparkles className="w-3 h-3" />
                <span>템플릿 마법사</span>
              </button>
              <div className="absolute z-50 hidden group-hover/wizard:block w-36 p-2 text-[11px] text-gray-600 bg-white border border-gray-200 rounded-lg shadow-lg right-0 top-8">
                AI가 템플릿을 작성/개선해드려요
                <div className="absolute -top-1 right-2 w-2 h-2 bg-white border-l border-t border-gray-200 rotate-45" />
              </div>
            </div>
          </div>
          <p className="text-xs text-gray-500 leading-snug">
            변수가 필요한 위치에 커서를 둔 뒤 좌측 입력 패널의 변수를
            클릭하세요.
          </p>

          <VariableTokenEditor
            value={data.template || ''}
            onChange={handleTemplateChange}
            onDropOutput={handleTemplateDropOutput}
            tokenLabels={tokenLabels}
            placeholder="예: 안녕하세요, 고객님!"
            ariaLabel="템플릿"
            className="min-h-[150px] font-mono"
          />

          {validationErrors.length > 0 && (
            <UnregisteredVariablesAlert variables={validationErrors} />
          )}
        </div>
      </CollapsibleSection>

      {/* 템플릿 마법사 모달 */}
      <TemplateWizardModal
        isOpen={showWizardModal}
        onClose={() => setShowWizardModal(false)}
        originalTemplate={data.template || ''}
        registeredVariables={registeredVariableNames}
        organizationId={wizardOrganizationId}
        onApply={(improvedTemplate) => {
          handleTemplateChange(improvedTemplate);
        }}
      />
    </div>
  );
};
