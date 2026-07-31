import { useCallback, useEffect, useMemo, useState } from 'react';

import { workflowApi } from '../api/workflowApi';
import {
  costOptimizerHistoryListParams,
  createDefaultCostOptimizerHistoryFilters,
  experimentFromCandidateDetail,
  flattenCostOptimizerHistory,
  type CostOptimizerHistoryFilters,
  type CostOptimizerHistoryRow,
  type SelectedHistoryTarget,
} from '../components/costOptimizer/costOptimizerHistoryModel';
import type { CostOptimizerExperimentSummary } from '../types/Api';

interface UseCostOptimizerHistoryOptions {
  enabled: boolean;
  workflowId: string;
  nodeId: string;
  baselineId: string | null;
  isReportMode: boolean;
  deepLinkExperimentId: string | null;
  deepLinkCandidateId: string | null;
  onDeepLinkResolved: (experiment: CostOptimizerExperimentSummary) => void;
  onDeepLinkError: (message: string) => void;
}

export function useCostOptimizerHistory({
  enabled,
  workflowId,
  nodeId,
  baselineId,
  isReportMode,
  deepLinkExperimentId,
  deepLinkCandidateId,
  onDeepLinkResolved,
  onDeepLinkError,
}: UseCostOptimizerHistoryOptions) {
  const [filterDraft, setFilterDraft] = useState<CostOptimizerHistoryFilters>(
    createDefaultCostOptimizerHistoryFilters,
  );
  const [appliedFilters, setAppliedFilters] =
    useState<CostOptimizerHistoryFilters>(
      createDefaultCostOptimizerHistoryFilters,
    );
  const [historyItems, setHistoryItems] = useState<
    CostOptimizerExperimentSummary[]
  >([]);
  const [selectedTarget, setSelectedTarget] =
    useState<SelectedHistoryTarget>(null);
  const [selectedDetail, setSelectedDetail] =
    useState<CostOptimizerHistoryRow | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(true);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');

  useEffect(() => {
    if (!enabled || !deepLinkExperimentId || !deepLinkCandidateId) return;

    let active = true;
    const loadDetail = async () => {
      setIsLoadingDetail(true);
      setDetailError('');
      try {
        const detail = await workflowApi.getCostOptimizerExperimentCandidate(
          workflowId,
          nodeId,
          deepLinkExperimentId,
          deepLinkCandidateId,
        );
        if (!active) return;
        const experiment = experimentFromCandidateDetail(detail);
        const row = { experiment, candidate: detail.candidate };
        setSelectedDetail(row);
        setSelectedTarget({
          type: 'history',
          experimentId: deepLinkExperimentId,
          candidateId: deepLinkCandidateId,
        });
        setIsCollapsed(true);
        onDeepLinkResolved(experiment);
      } catch {
        if (active) {
          const message = '선택한 비교 실험 이력을 불러오지 못했습니다.';
          setDetailError(message);
          onDeepLinkError(message);
        }
      } finally {
        if (active) setIsLoadingDetail(false);
      }
    };

    void loadDetail();
    return () => {
      active = false;
    };
  }, [
    deepLinkCandidateId,
    deepLinkExperimentId,
    enabled,
    nodeId,
    onDeepLinkError,
    onDeepLinkResolved,
    workflowId,
  ]);

  useEffect(() => {
    if (!enabled || !baselineId || !isReportMode) return;

    let active = true;
    const loadHistory = async () => {
      setIsLoadingList(true);
      setListError('');
      try {
        const response = await workflowApi.listCostOptimizerExperiments(
          workflowId,
          nodeId,
          costOptimizerHistoryListParams(baselineId, appliedFilters),
        );
        if (active) setHistoryItems(response.items || []);
      } catch {
        if (!active) return;
        setHistoryItems([]);
        setListError('이전 실험 이력을 불러오지 못했습니다.');
      } finally {
        if (active) setIsLoadingList(false);
      }
    };

    void loadHistory();
    return () => {
      active = false;
    };
  }, [appliedFilters, baselineId, enabled, isReportMode, nodeId, workflowId]);

  const rows = useMemo(
    () => flattenCostOptimizerHistory(historyItems),
    [historyItems],
  );
  const selectedHistoryRow = useMemo(() => {
    if (selectedTarget?.type !== 'history') return null;
    if (
      selectedDetail?.experiment.experiment_id === selectedTarget.experimentId &&
      selectedDetail.candidate.candidate_id === selectedTarget.candidateId
    ) {
      return selectedDetail;
    }
    return (
      rows.find(
        (row) =>
          row.experiment.experiment_id === selectedTarget.experimentId &&
          row.candidate.candidate_id === selectedTarget.candidateId,
      ) || null
    );
  }, [rows, selectedDetail, selectedTarget]);

  const updateFilter = useCallback(
    <K extends keyof CostOptimizerHistoryFilters>(
      key: K,
      value: CostOptimizerHistoryFilters[K],
    ) => {
      setFilterDraft((current) => ({ ...current, [key]: value }));
    },
    [],
  );
  const applyFilters = useCallback(() => {
    setAppliedFilters({ ...filterDraft });
  }, [filterDraft]);
  const resetFilters = useCallback(() => {
    const defaults = createDefaultCostOptimizerHistoryFilters();
    setFilterDraft(defaults);
    setAppliedFilters(defaults);
  }, []);
  const selectCurrent = useCallback(() => {
    setSelectedTarget({ type: 'current' });
  }, []);
  const selectHistory = useCallback(
    (experimentId: string, candidateId: string) => {
      setSelectedDetail(null);
      setSelectedTarget({ type: 'history', experimentId, candidateId });
    },
    [],
  );
  const clearSelection = useCallback(() => {
    setSelectedDetail(null);
    setSelectedTarget(null);
  }, []);
  const isSelectedHistoryCandidate = useCallback(
    (experimentId: string, candidateId: string) =>
      selectedTarget?.type === 'history' &&
      selectedTarget.experimentId === experimentId &&
      selectedTarget.candidateId === candidateId,
    [selectedTarget],
  );

  return {
    filterDraft,
    updateFilter,
    applyFilters,
    resetFilters,
    rows,
    selectedTarget,
    selectedHistoryRow,
    selectCurrent,
    selectHistory,
    clearSelection,
    isSelectedHistoryCandidate,
    isCurrentSelected:
      selectedTarget?.type === 'current' || selectedTarget === null,
    isCollapsed,
    toggleCollapsed: () => setIsCollapsed((current) => !current),
    isLoading: isLoadingList || isLoadingDetail,
    error: detailError || listError,
  };
}

export type CostOptimizerHistoryController = ReturnType<
  typeof useCostOptimizerHistory
>;
