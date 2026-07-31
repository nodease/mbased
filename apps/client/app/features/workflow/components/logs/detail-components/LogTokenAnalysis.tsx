import { LLMTrace, WorkflowRun } from '@/app/features/workflow/types/Api';
import { BarChart, Zap } from 'lucide-react';
import type { ReactNode } from 'react';
import { getNodeDisplayInfo } from '../shared/nodeDisplayInfo';

interface LogTokenAnalysisProps {
  run: WorkflowRun;
  llmTraces?: LLMTrace[];
  loading?: boolean;
  error?: boolean;
  onNodeSelect?: (nodeId: string) => void;
}

interface UsageByNode {
  nodeId: string;
  nodeType: string;
  displayLabel: string;
  displayColor: string;
  displayIcon: ReactNode;
  model: string;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cost: number;
  latencyMs?: number | null;
  status?: string;
}

interface LegacyUsage {
  total_tokens?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
}

interface LegacyNodeOutputs {
  usage?: LegacyUsage;
  cost?: number;
  model?: string;
}

const getLegacyOutputs = (outputs: unknown): LegacyNodeOutputs => {
  if (!outputs || typeof outputs !== 'object') return {};
  return outputs as LegacyNodeOutputs;
};

export const LogTokenAnalysis = ({
  run,
  llmTraces = [],
  loading = false,
  error = false,
  onNodeSelect,
}: LogTokenAnalysisProps) => {
  const nodeRuns = run.node_runs || [];
  const nodeTypeById = new Map(
    nodeRuns.map((node) => [node.node_id, node.node_type]),
  );

  const usageByNode: UsageByNode[] = (
    llmTraces.length > 0
      ? llmTraces
          .map((trace) => {
            const nodeId = trace.node_id || 'unknown';
            const nodeType = nodeTypeById.get(nodeId) || 'llm';
            const displayInfo = getNodeDisplayInfo(nodeType);
            return {
              nodeId,
              nodeType,
              displayLabel: displayInfo.label,
              displayColor: displayInfo.color,
              displayIcon: displayInfo.icon,
              model: trace.model_name || trace.provider || 'Unknown',
              totalTokens: trace.total_tokens || 0,
              promptTokens: trace.prompt_tokens || 0,
              completionTokens: trace.completion_tokens || 0,
              cost: Number(trace.total_cost || 0),
              latencyMs: trace.latency_ms,
              status: trace.status,
            };
          })
      : nodeRuns
          .filter((node) => getLegacyOutputs(node.outputs).usage?.total_tokens)
          .map((node) => {
            const outputs = getLegacyOutputs(node.outputs);
            const usage = outputs.usage;
            const cost = outputs.cost || 0;
            const displayInfo = getNodeDisplayInfo(node.node_type);
            return {
              nodeId: node.node_id,
              nodeType: node.node_type,
              displayLabel: displayInfo.label,
              displayColor: displayInfo.color,
              displayIcon: displayInfo.icon,
              model: outputs.model || 'Unknown',
              totalTokens: usage?.total_tokens || 0,
              promptTokens: usage?.prompt_tokens || 0,
              completionTokens: usage?.completion_tokens || 0,
              cost,
            };
          })
  ).sort((a, b) => b.totalTokens - a.totalTokens);

  if (loading && usageByNode.length === 0) {
    return (
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm">
        LLM trace를 불러오는 중입니다...
      </div>
    );
  }

  if (usageByNode.length === 0) return null;

  const usageByNodeSummary = Object.values(
    usageByNode.reduce(
      (acc, node) => {
        if (!acc[node.nodeId]) {
          acc[node.nodeId] = { ...node };
          return acc;
        }
        acc[node.nodeId].totalTokens += node.totalTokens;
        acc[node.nodeId].promptTokens += node.promptTokens;
        acc[node.nodeId].completionTokens += node.completionTokens;
        acc[node.nodeId].cost += node.cost;
        if (node.status && node.status !== 'success') {
          acc[node.nodeId].status = node.status;
        }
        return acc;
      },
      {} as Record<string, UsageByNode>,
    ),
  ).sort((a, b) => b.totalTokens - a.totalTokens);

  const usageByModel = usageByNode.reduce(
    (acc, curr) => {
      const model = curr.model;
      if (!acc[model]) {
        acc[model] = {
          model,
          totalTokens: 0,
          cost: 0,
          count: 0,
        };
      }
      acc[model].totalTokens += curr.totalTokens;
      acc[model].cost += curr.cost;
      acc[model].count += 1;
      return acc;
    },
    {} as Record<string, { model: string; totalTokens: number; cost: number; count: number }>,
  );

  const modelStats = Object.values(usageByModel).sort(
    (a, b) => b.totalTokens - a.totalTokens,
  );
  const maxNodeTotalTokens = usageByNodeSummary[0]?.totalTokens || 1;

  return (
    <>
      {error && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
          LLM trace 조회에 실패해 실행 로그에 포함된 usage 정보만 표시합니다.
        </div>
      )}
      <div className="mb-6 grid grid-cols-1 gap-4 animate-in slide-in-from-top-2 duration-300 md:grid-cols-2">
        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-4 flex items-center gap-2 border-b border-gray-100 pb-2">
            <Zap className="h-4 w-4 text-amber-500" />
            <h4 className="text-sm font-semibold text-gray-800">
              LLM 노드별 토큰 사용량
            </h4>
          </div>
          <div className="space-y-3">
            {usageByNodeSummary.map((node) => (
              <button
                key={node.nodeId}
                onClick={() => onNodeSelect?.(node.nodeId)}
                className="flex w-full cursor-pointer flex-col gap-1 rounded-lg p-2 text-left transition-colors hover:bg-gray-50"
              >
                <div className="flex items-center justify-between text-xs">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-medium ${node.displayColor}`}
                  >
                    {node.displayIcon}
                    {node.displayLabel}
                  </span>
                  <span className="font-bold text-gray-900">
                    {node.totalTokens.toLocaleString()} tks
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-amber-400"
                    style={{
                      width: `${Math.min(
                        (node.totalTokens / maxNodeTotalTokens) * 100,
                        100,
                      )}%`,
                    }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-gray-500">
                  <span>{node.model}</span>
                  <span>
                    ${node.cost.toFixed(5)}
                    {node.latencyMs != null ? ` · ${node.latencyMs}ms` : ''}
                    {node.status && node.status !== 'success'
                      ? ` · ${node.status}`
                      : ''}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="mb-4 flex items-center gap-2 border-b border-gray-100 pb-2">
            <BarChart className="h-4 w-4 text-blue-500" />
            <h4 className="text-sm font-semibold text-gray-800">
              모델별 토큰 사용량
            </h4>
          </div>
          <div className="space-y-3">
            {modelStats.map((stat) => (
              <div key={stat.model} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-gray-700">{stat.model}</span>
                  <div className="text-right">
                    <div className="font-bold text-gray-900">
                      {stat.totalTokens.toLocaleString()} tks
                    </div>
                  </div>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-blue-500"
                    style={{
                      width: `${Math.min(
                        (stat.totalTokens / (modelStats[0].totalTokens || 1)) * 100,
                        100,
                      )}%`,
                    }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-gray-500">
                  <span>{stat.count}회 호출</span>
                  <span className="font-medium text-gray-700">
                    ${stat.cost.toFixed(5)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};
