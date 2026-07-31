import { memo } from 'react';
import { Node, NodeProps } from '@xyflow/react';
import { Bot } from 'lucide-react';

import { BaseNode } from '../../BaseNode';
import { LLMNodeData } from '../../../../types/Nodes';
import { ValidationBadge } from '../../../ui/ValidationBadge';

// NOTE: [LLM] LLM 노드 박스 UI (BaseNode를 사용해 일관된 껍데기 유지)
export const LLMNode = memo(
  ({ data, selected, id }: NodeProps<Node<LLMNodeData>>) => {
    // 노드 실행 필수 요건 체크
    // 1. 프롬프트가 하나라도 있어야 함 (system_prompt, user_prompt, assistant_prompt 중 하나)
    // 2. 모델이 설정되어 있어야 함
    const hasNoPrompts =
      !data.system_prompt && !data.user_prompt && !data.assistant_prompt;
    const hasNoModel = !data.model_id;
    const hasValidationIssue = hasNoPrompts || hasNoModel;

    const displayModelId = data.model_id
      ? data.model_id.replace(/^models\//, '')
      : '';
    const observability = data.observability as
      | {
          status?: string;
          total_tokens?: number;
          total_cost?: number;
          latency_ms?: number;
        }
      | undefined;
    const hasObservability =
      observability &&
      (observability.status ||
        observability.total_tokens ||
        observability.total_cost ||
        observability.latency_ms);

    return (
      <BaseNode
        id={id}
        data={data}
        selected={selected}
        icon={<Bot className="text-white" />}
        iconColor="#a855f7" // purple-500
      >
        <div className="flex flex-col gap-1">
          <div className="text-sm font-semibold text-gray-800 truncate">
            {displayModelId || '모델 미지정'}
          </div>

          {hasObservability && (
            <div
              className={`mt-1 rounded-md border px-2 py-1 text-[11px] leading-5 ${
                observability?.status === 'failure'
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : observability?.total_cost && observability.total_cost > 0.01
                    ? 'border-amber-200 bg-amber-50 text-amber-700'
                    : 'border-gray-200 bg-gray-50 text-gray-600'
              }`}
            >
              <div className="flex flex-wrap gap-x-2 gap-y-0.5">
                <span>{observability?.status || 'idle'}</span>
                <span>{observability?.total_tokens || 0} tok</span>
                <span>${Number(observability?.total_cost || 0).toFixed(5)}</span>
                <span>{observability?.latency_ms || 0}ms</span>
              </div>
            </div>
          )}

          {/* 검증 실패 시 전체 너비 경고 배지 */}

          {hasValidationIssue && <ValidationBadge />}
        </div>
      </BaseNode>
    );
  },
);
LLMNode.displayName = 'LLMNode';
