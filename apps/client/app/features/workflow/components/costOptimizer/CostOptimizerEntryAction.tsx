import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { BarChart3 } from 'lucide-react';
import { workflowApi } from '../../api/workflowApi';
import type { WorkflowPermissionResponse } from '../../types/Api';

interface CostOptimizerEntryActionProps {
  workflowId: string;
  nodeId: string;
  workflowAccess?: WorkflowPermissionResponse | null;
  hasUnsavedChanges?: boolean;
  onOpen?: () => void;
  label?: string;
  title?: string;
}

const canUseCostOptimizer = (workflowAccess?: WorkflowPermissionResponse | null) =>
  workflowAccess?.can_write === true;

export const CostOptimizerEntryAction = ({
  workflowId,
  nodeId,
  workflowAccess,
  hasUnsavedChanges = false,
  onOpen,
  label = '비교 분석 테스트',
  title,
}: CostOptimizerEntryActionProps) => {
  const router = useRouter();
  const hasBuilderPermission = canUseCostOptimizer(workflowAccess);
  const [isAvailable, setIsAvailable] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    if (!workflowId || !nodeId || !hasBuilderPermission) {
      setIsAvailable(false);
      return () => {
        active = false;
      };
    }

    setIsAvailable(true);
    workflowApi
      .getCostOptimizerAvailability(workflowId, nodeId)
      .then((availability) => {
        if (!active) return;
        setIsAvailable(
          availability.available && availability.permission.can_compare,
        );
      })
      .catch(() => {
        if (!active) return;
        setIsAvailable(false);
      });

    return () => {
      active = false;
    };
  }, [hasBuilderPermission, nodeId, workflowId]);

  const canUse = hasBuilderPermission && isAvailable;

  return (
    <div className="flex w-full flex-col items-stretch gap-1">
      <button
        type="button"
        disabled={!canUse}
        onClick={(event) => {
          event.stopPropagation();
          if (!canUse) return;
          if (hasUnsavedChanges) {
            setMessage('현재 노드 설정을 저장한 뒤 비교를 시작할 수 있습니다.');
            return;
          }
          setMessage(null);
          if (onOpen) {
            onOpen();
            return;
          }
          router.push(`/modules/${workflowId}/cost-optimizer/${nodeId}`);
        }}
        className="nodrag inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-emerald-600 bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:border-emerald-700 hover:bg-emerald-700 disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-gray-50 disabled:text-gray-400 disabled:shadow-none"
        title={
          canUse
            ? title || '운영 로그와 후보 실험 이력으로 최적화 화면을 엽니다.'
            : '워크플로우 수정 권한이 필요합니다.'
        }
      >
        <BarChart3 className="h-3.5 w-3.5" />
        {label}
      </button>
      {message ? (
        <p className="text-[11px] font-medium leading-relaxed text-amber-700">
          {message}
        </p>
      ) : null}
    </div>
  );
};
