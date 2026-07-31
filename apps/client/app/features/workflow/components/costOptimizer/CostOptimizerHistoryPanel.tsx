import type { CostOptimizerHistoryController } from '../../hooks/useCostOptimizerHistory';
import {
  downstreamStateLabelOf,
  formatCandidateCost,
  formatContextDateTime,
  formatLatency,
  formatMetric,
  formatShortId,
  schemaStatusLabelOf,
} from './costOptimizerPresentation';

export interface CurrentCostOptimizerHistoryRow {
  baselineId: string | null | undefined;
  baselineModel: string | null | undefined;
  testName: string;
  model: string | null | undefined;
  totalCost: number | null | undefined;
  totalTokens: number | null | undefined;
  latencyMs: number | null | undefined;
  schemaLabel: string;
  downstreamLabel: string;
}

interface CostOptimizerHistoryPanelProps {
  history: CostOptimizerHistoryController;
  currentRow: CurrentCostOptimizerHistoryRow | null;
}

export function CostOptimizerHistoryPanel({
  history,
  currentRow,
}: CostOptimizerHistoryPanelProps) {
  const selectedSummary = (() => {
    if (history.isCurrentSelected && currentRow) {
      return `선택: 방금 실행 · ${currentRow.model || '-'} · ${formatCandidateCost(
        currentRow.totalCost,
      )}`;
    }
    if (history.selectedHistoryRow) {
      return `선택: ${
        history.selectedHistoryRow.candidate.name || '이름 없는 후보'
      } · ${history.selectedHistoryRow.candidate.model_id || '-'} · ${formatCandidateCost(
        history.selectedHistoryRow.candidate.total_cost,
      )}`;
    }
    return '선택: 없음';
  })();

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold">이전 실험 이력</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            결과 분석 기준으로 볼 후보 실행을 확인합니다. 방금 실행한 후보는
            자동 선택 상태로 표시합니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600">
            {selectedSummary}
          </span>
          <button
            type="button"
            onClick={history.toggleCollapsed}
            className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
          >
            {history.isCollapsed ? '펼치기' : '접기'}
          </button>
        </div>
      </div>

      {history.isCollapsed ? (
        <p className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs font-semibold text-slate-600">
          {selectedSummary}
        </p>
      ) : (
        <>
          <form
            className="mt-4"
            onSubmit={(event) => {
              event.preventDefault();
              history.applyFilters();
            }}
          >
            <div className="grid gap-2 text-xs sm:grid-cols-4 lg:grid-cols-8">
              <label className="grid gap-1 font-semibold text-slate-600">
                시작일
                <input
                  type="date"
                  value={history.filterDraft.dateFrom}
                  onChange={(event) =>
                    history.updateFilter('dateFrom', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                />
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                종료일
                <input
                  type="date"
                  value={history.filterDraft.dateTo}
                  onChange={(event) =>
                    history.updateFilter('dateTo', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                />
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                실행자
                <input
                  value={history.filterDraft.createdBy}
                  onChange={(event) =>
                    history.updateFilter('createdBy', event.target.value)
                  }
                  placeholder="user id"
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                />
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                적용 여부
                <select
                  value={history.filterDraft.isApplied}
                  onChange={(event) =>
                    history.updateFilter('isApplied', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                >
                  <option value="">전체</option>
                  <option value="true">적용됨</option>
                  <option value="false">미적용</option>
                </select>
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                후보 상태
                <select
                  value={history.filterDraft.candidateStatus}
                  onChange={(event) =>
                    history.updateFilter('candidateStatus', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                >
                  <option value="">전체</option>
                  <option value="success">성공</option>
                  <option value="failed">실패</option>
                  <option value="schema_failed">Schema 실패</option>
                  <option value="running">실행 중</option>
                </select>
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                모델 필터
                <input
                  value={history.filterDraft.model}
                  onChange={(event) =>
                    history.updateFilter('model', event.target.value)
                  }
                  placeholder="예: gpt-4.1-mini"
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                />
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                Schema 상태
                <select
                  value={history.filterDraft.schemaStatus}
                  onChange={(event) =>
                    history.updateFilter('schemaStatus', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                >
                  <option value="">전체</option>
                  <option value="not_checked">미검사</option>
                  <option value="pass">통과</option>
                  <option value="failed">실패</option>
                </select>
              </label>
              <label className="grid gap-1 font-semibold text-slate-600">
                Downstream 상태
                <select
                  value={history.filterDraft.downstreamState}
                  onChange={(event) =>
                    history.updateFilter('downstreamState', event.target.value)
                  }
                  className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900"
                >
                  <option value="">전체</option>
                  <option value="compatible">검증 가능</option>
                  <option value="warning">주의 필요</option>
                  <option value="incompatible">검증 불가</option>
                  <option value="unknown">판정 전</option>
                </select>
              </label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={history.resetFilters}
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 hover:bg-slate-50"
              >
                초기화
              </button>
              <button
                type="submit"
                className="h-9 rounded-md bg-slate-900 px-3 text-xs font-bold text-white hover:bg-slate-800"
              >
                필터 적용
              </button>
            </div>
          </form>

          {history.error ? (
            <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700">
              {history.error}
            </p>
          ) : null}

          <div className="mt-4 max-h-72 overflow-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-bold">실행 시각</th>
                  <th className="px-3 py-2 font-bold">Baseline</th>
                  <th className="px-3 py-2 font-bold">테스트명</th>
                  <th className="px-3 py-2 font-bold">모델</th>
                  <th className="px-3 py-2 font-bold">비용</th>
                  <th className="px-3 py-2 font-bold">토큰</th>
                  <th className="px-3 py-2 font-bold">시간</th>
                  <th className="px-3 py-2 font-bold">Schema</th>
                  <th className="px-3 py-2 font-bold">Downstream</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {currentRow ? (
                  <tr
                    onClick={history.selectCurrent}
                    className={`cursor-pointer border-l-4 ${
                      history.isCurrentSelected
                        ? 'border-emerald-500 bg-emerald-50/70'
                        : 'border-transparent hover:bg-slate-50'
                    }`}
                  >
                    <td className="px-3 py-2 font-semibold text-slate-700">
                      방금 실행
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      <div className="font-semibold">
                        {formatShortId(currentRow.baselineId)}
                      </div>
                      <div className="text-[11px] text-slate-400">
                        {currentRow.baselineModel || '-'}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        aria-label="방금 실행 선택"
                        onClick={(event) => {
                          event.stopPropagation();
                          history.selectCurrent();
                        }}
                        className="text-left font-semibold text-slate-900 hover:text-emerald-700"
                      >
                        <span className="rounded-full border border-emerald-200 bg-white px-2 py-1 text-[11px] font-bold text-emerald-700">
                          방금 실행
                        </span>{' '}
                        {currentRow.testName}
                      </button>
                    </td>
                    <td className="px-3 py-2">{currentRow.model || '-'}</td>
                    <td className="px-3 py-2">
                      {formatCandidateCost(currentRow.totalCost)}
                    </td>
                    <td className="px-3 py-2">
                      {currentRow.totalTokens == null
                        ? '-'
                        : formatMetric(currentRow.totalTokens)}
                    </td>
                    <td className="px-3 py-2">
                      {currentRow.latencyMs == null
                        ? '-'
                        : formatLatency(currentRow.latencyMs)}
                    </td>
                    <td className="px-3 py-2">{currentRow.schemaLabel}</td>
                    <td className="px-3 py-2">{currentRow.downstreamLabel}</td>
                  </tr>
                ) : null}

                {history.rows.map(({ experiment, candidate }) => (
                  <tr
                    key={`${experiment.experiment_id}-${candidate.candidate_id}`}
                    onClick={() =>
                      history.selectHistory(
                        experiment.experiment_id,
                        candidate.candidate_id,
                      )
                    }
                    className={`cursor-pointer border-l-4 ${
                      history.isSelectedHistoryCandidate(
                        experiment.experiment_id,
                        candidate.candidate_id,
                      )
                        ? 'border-emerald-500 bg-emerald-50/70'
                        : 'border-transparent hover:bg-slate-50'
                    }`}
                  >
                    <td className="px-3 py-2 text-slate-600">
                      {formatContextDateTime(
                        candidate.created_at || experiment.created_at,
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      <div className="font-semibold">
                        {formatShortId(
                          experiment.baseline_summary?.baseline_id ||
                            experiment.baseline_node_run_id,
                        )}
                      </div>
                      <div className="text-[11px] text-slate-400">
                        {experiment.baseline_summary?.model || '-'}
                      </div>
                    </td>
                    <td className="px-3 py-2 font-semibold text-slate-900">
                      <button
                        type="button"
                        aria-label={`${candidate.name || '이름 없는 후보'} 선택`}
                        onClick={(event) => {
                          event.stopPropagation();
                          history.selectHistory(
                            experiment.experiment_id,
                            candidate.candidate_id,
                          );
                        }}
                        className="text-left font-semibold text-slate-900 hover:text-emerald-700"
                      >
                        {candidate.name || '이름 없는 후보'}
                      </button>
                    </td>
                    <td className="px-3 py-2">{candidate.model_id || '-'}</td>
                    <td className="px-3 py-2">
                      {formatCandidateCost(candidate.total_cost)}
                    </td>
                    <td className="px-3 py-2">
                      {typeof candidate.total_tokens === 'number'
                        ? formatMetric(candidate.total_tokens)
                        : '-'}
                    </td>
                    <td className="px-3 py-2">
                      {typeof candidate.latency_ms === 'number'
                        ? formatLatency(candidate.latency_ms)
                        : '-'}
                    </td>
                    <td className="px-3 py-2">
                      {schemaStatusLabelOf(candidate.schema_status)}
                    </td>
                    <td className="px-3 py-2">
                      {downstreamStateLabelOf(candidate.downstream_state)}
                    </td>
                  </tr>
                ))}

                {!currentRow && !history.isLoading && history.rows.length === 0 ? (
                  <tr>
                    <td
                      colSpan={9}
                      className="px-3 py-6 text-center font-semibold text-slate-500"
                    >
                      조건에 맞는 이전 실험 이력이 없습니다.
                    </td>
                  </tr>
                ) : null}
                {history.isLoading ? (
                  <tr>
                    <td
                      colSpan={9}
                      className="px-3 py-6 text-center font-semibold text-slate-500"
                    >
                      이전 실험 이력을 불러오는 중입니다.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
