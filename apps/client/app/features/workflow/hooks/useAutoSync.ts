import { useEffect, useRef, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { debounce, isEqual } from 'lodash';
import { useReactFlow } from '@xyflow/react';
import { useWorkflowStore } from '../store/useWorkflowStore';
import { workflowApi } from '../api/workflowApi';
import {
  agentBuilderApi,
  type AgentBuilderParameterGroup,
} from '../api/agentBuilderApi';
import { DEFAULT_NODES } from '../constants'; // 노드가 하나도 없을 때 쓸 기본값
import { AppNode } from '../types/Nodes';
import {
  cleanupInvalidEdges,
  formatGraphIssue,
} from '../utils/validateWorkflowGraph';
import { toast } from 'sonner';
import { buildWorkflowDraftPayload } from '../utils/workflowDraftPayload';
import {
  acquireWorkflowDraftSave,
  tryAcquireWorkflowDraftSave,
} from '../utils/workflowDraftSaveCoordinator';
import { assignMissingNodeDisplayNumbers } from '../utils/nodeNumbering';
import { workflowDraftSaveStateEqual } from '../utils/workflowDraftComparison';

type CanonicalDraftMetadata = {
  graphHash: string;
  updatedAt: string;
};

const canonicalDraftMetadataFrom = (
  value: unknown,
): CanonicalDraftMetadata | null => {
  if (!value || typeof value !== 'object') return null;
  const graphHash = (value as { graph_hash?: unknown }).graph_hash;
  const updatedAt = (value as { updated_at?: unknown }).updated_at;
  return typeof graphHash === 'string' && typeof updatedAt === 'string'
    ? { graphHash, updatedAt }
    : null;
};

const getHttpStatus = (error: unknown) =>
  typeof error === 'object' &&
  error !== null &&
  'response' in error &&
  typeof (error as { response?: { status?: unknown } }).response?.status ===
    'number'
    ? (error as { response: { status: number } }).response.status
    : undefined;

const getErrorCode = (error: unknown) => {
  if (typeof error !== 'object' || error === null || !('response' in error)) {
    return undefined;
  }
  const data = (error as { response?: { data?: unknown } }).response?.data;
  if (!data || typeof data !== 'object') return undefined;
  const directCode = (data as { code?: unknown }).code;
  if (typeof directCode === 'string') return directCode;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (!detail || typeof detail !== 'object') return undefined;
  const detailCode = (detail as { code?: unknown }).code;
  return typeof detailCode === 'string' ? detailCode : undefined;
};

const isStaleGraphError = (error: unknown) =>
  getHttpStatus(error) === 409 && getErrorCode(error) === 'stale_graph';

const isAmbiguousSaveFailure = (error: unknown) => {
  const status = getHttpStatus(error);
  return status === undefined || status >= 500;
};

export const useAutoSync = () => {
  const params = useParams(); // 주소창의 파라미터 읽기
  const workflowId = params.id as string;
  const { getViewport, setViewport } = useReactFlow(); // React Flow 인스턴스 접근
  const setViewportRef = useRef(setViewport);

  // Zustand Store에서 상태들을 가져오기
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const features = useWorkflowStore((state) => state.features);
  const envVariables = useWorkflowStore((state) => state.envVariables);
  const runtimeVariables = useWorkflowStore((state) => state.runtimeVariables);
  const setWorkflowData = useWorkflowStore((state) => state.setWorkflowData);
  const setHasUnsavedChanges = useWorkflowStore(
    (state) => state.setHasUnsavedChanges,
  );
  const hasUnsavedChanges = useWorkflowStore(
    (state) => state.hasUnsavedChanges,
  );
  const workflowAccess = useWorkflowStore((state) => state.workflowAccess);
  const isAgentBuilderMutationSaving = useWorkflowStore(
    (state) => state.isAgentBuilderMutationSaving,
  );

  // 로딩 완료 여부 체크
  const isLoadedRef = useRef(false);
  const pendingRedoRef = useRef<{
    operationId: string;
    sessionId: string;
    baseGraphHash: string;
    workflowUpdatedAt: string;
    finalGraph: {
      nodes: typeof nodes;
      edges: typeof edges;
    };
    baseGraph: {
      nodes: typeof nodes;
      edges: typeof edges;
    };
  } | null>(null);
  const pendingAutosyncWaitRef = useRef<string | null>(null);

  useEffect(() => {
    setViewportRef.current = setViewport;
  }, [setViewport]);

  useEffect(() => {
    pendingRedoRef.current = null;
  }, [workflowId]);

  // 1. 초기 데이터 로딩 (페이지 진입 시 1회 실행)
  useEffect(() => {
    if (!workflowId) return; //TODO: 가져올 workflow 가 없다는 뜻. 주소로 접근한거면 유저에게 접근불가 메시지 보여줘야함

    const loadWorkflow = async () => {
      try {
        const data = await workflowApi.getDraftWorkflow(workflowId);
        const canonicalMetadata = canonicalDraftMetadataFrom(data);
        if (canonicalMetadata) {
          useWorkflowStore.getState().setCanonicalDraftMetadata({
            workflowId,
            ...canonicalMetadata,
          });
        }

        if (data) {
          if (data.features?.noteNodes) {
            if (data.nodes) {
              data.nodes = [...data.nodes, ...data.features.noteNodes];
            } else {
              data.nodes = data.features.noteNodes;
            }
          }

          if (data.nodes && data.nodes.length > 0) {
            data.nodes = data.nodes.map((node: any) => {
              // TODO: 임시 마이그레이션 로직, 삭제 필요
              if (node.type === 'start') {
                return { ...node, type: 'startNode' };
              }
              return node;
            });

            //TODO: 노드가 없으면 '에러' 대신 '기본값을 주입'하고 있습니다. 백엔드 연동되면 에러페이지 리다이렉트로 수정합니다.
          } else {
            data.nodes = DEFAULT_NODES as AppNode[];
          }
          const cleanupResult = cleanupInvalidEdges(data);
          setWorkflowData(cleanupResult.graph, workflowId);

          if (cleanupResult.removedIssues.length > 0) {
            const firstIssue = cleanupResult.removedIssues[0];
            toast.warning(
              `표시할 수 없는 연결 ${cleanupResult.removedIssues.length}개를 정리했습니다.`,
              {
                description: firstIssue
                  ? formatGraphIssue(firstIssue)
                  : undefined,
              },
            );

            try {
              const cleanupSaveResponse = await workflowApi.syncDraftWorkflow(
                workflowId,
                {
                  ...buildWorkflowDraftPayload(
                    cleanupResult.graph,
                    cleanupResult.graph.viewport ||
                      data.viewport || { x: 0, y: 0, zoom: 1 },
                  ),
                  expected_graph_hash: data.graph_hash,
                  expected_updated_at: data.updated_at,
                },
              );
              const cleanupMetadata =
                canonicalDraftMetadataFrom(cleanupSaveResponse);
              if (cleanupMetadata) {
                useWorkflowStore.getState().setCanonicalDraftMetadata({
                  workflowId,
                  ...cleanupMetadata,
                });
              }
            } catch {
              toast.error('정리된 워크플로우 저장에 실패했습니다.');
            }
          }

          // 저장된 viewport를 React Flow에 적용
          if (cleanupResult.graph.viewport) {
            setViewportRef.current(cleanupResult.graph.viewport);
          }
        }

        isLoadedRef.current = true;
      } catch {
        // Failed to load workflow
      }
    };

    isLoadedRef.current = false; // 다른 워크플로우로 이동했을 때를 대비해 초기화
    loadWorkflow();
  }, [workflowId, setWorkflowData]);

  // 2. 자동 저장 (Debounce)
  /* eslint-disable react-hooks/refs -- refs are read only when the debounced callback runs after render */
  const debouncedSync = useMemo(
    () =>
      debounce(
        // eslint-disable-next-line react-hooks/refs -- debounce 콜백은 렌더 이후에만 실행되며 최신 ref를 읽어 저장 충돌을 막는다.
        async (
          currentNodes: typeof nodes,
          currentEdges: typeof edges,
          currentFeatures: typeof features,
          currentEnvVars: typeof envVariables,
          currentRuntimeVars: typeof runtimeVariables,
        ) => {
          if (!workflowId) {
            return;
          }
          if (useWorkflowStore.getState().isAgentBuilderMutationSaving) {
            return;
          }
          if (!useWorkflowStore.getState().hasUnsavedChanges) {
            return;
          }
          if (workflowAccess?.can_write === false) {
            return;
          }
          let nodesToSave = currentNodes;
          let edgesToSave = currentEdges;
          let featuresToSave = currentFeatures;
          let envVariablesToSave = currentEnvVars;
          let runtimeVariablesToSave = currentRuntimeVars;

          const pendingRevert =
            useWorkflowStore.getState().pendingAgentBuilderRevert;
          let pendingRedo = null;
          if (!pendingRevert && pendingRedoRef.current) {
            if (
              isEqual(currentNodes, pendingRedoRef.current.finalGraph.nodes) &&
              isEqual(currentEdges, pendingRedoRef.current.finalGraph.edges)
            ) {
              pendingRedo = pendingRedoRef.current;
            } else {
              pendingRedoRef.current = null;
            }
          }
          const releaseWorkflowSave =
            pendingRevert || pendingRedo
              ? await acquireWorkflowDraftSave(workflowId, 'undo_redo')
              : tryAcquireWorkflowDraftSave(workflowId, 'autosync');
          let acquiredWorkflowSave = releaseWorkflowSave;
          if (!acquiredWorkflowSave) {
            if (pendingAutosyncWaitRef.current === workflowId) return;
            pendingAutosyncWaitRef.current = workflowId;
            try {
              acquiredWorkflowSave = await acquireWorkflowDraftSave(
                workflowId,
                'autosync',
              );
            } finally {
              if (pendingAutosyncWaitRef.current === workflowId) {
                pendingAutosyncWaitRef.current = null;
              }
            }
            const latest = useWorkflowStore.getState();
            if (
              latest.activeWorkflowId !== workflowId ||
              latest.isAgentBuilderMutationSaving ||
              !latest.hasUnsavedChanges ||
              latest.pendingAgentBuilderRevert ||
              pendingRedoRef.current
            ) {
              acquiredWorkflowSave();
              return;
            }
            nodesToSave = latest.nodes;
            edgesToSave = latest.edges;
            featuresToSave = latest.features;
            envVariablesToSave = latest.envVariables;
            runtimeVariablesToSave = latest.runtimeVariables;
          }
          const currentViewport = getViewport();

          try {
            const graphToSave = pendingRevert?.revertGraph ??
              pendingRedo?.finalGraph ?? {
                nodes: nodesToSave,
                edges: edgesToSave,
              };

            if (pendingRevert || pendingRedo) {
              useWorkflowStore.getState().setAgentBuilderMutationSaving(true);
            }

            const mutationContext = pendingRevert
              ? {
                  operation_id: pendingRevert.operationId,
                  action: 'revert' as const,
                  expected_base_graph_hash: pendingRevert.resultGraphHash,
                  expected_workflow_updated_at: pendingRevert.workflowUpdatedAt,
                  catalog_version: 3 as const,
                }
              : pendingRedo
                ? {
                    operation_id: pendingRedo.operationId,
                    action: 'redo' as const,
                    expected_base_graph_hash: pendingRedo.baseGraphHash,
                    expected_workflow_updated_at: pendingRedo.workflowUpdatedAt,
                    catalog_version: 3 as const,
                  }
                : undefined;
            const canonicalMetadata = useWorkflowStore
              .getState()
              .getCanonicalDraftMetadata(workflowId);
            const expectedMetadata = mutationContext
              ? {
                  graphHash: mutationContext.expected_base_graph_hash,
                  updatedAt: mutationContext.expected_workflow_updated_at,
                }
              : canonicalMetadata;
            if (!expectedMetadata) {
              if (pendingRevert || pendingRedo) {
                useWorkflowStore
                  .getState()
                  .setAgentBuilderMutationSaving(false);
              }
              toast.warning(
                'Workflow 저장 기준을 확인하는 중입니다. 잠시 후 다시 시도해주세요.',
              );
              return;
            }
            const saveRequest = {
              ...buildWorkflowDraftPayload(
                {
                  nodes: graphToSave.nodes,
                  edges: graphToSave.edges,
                  viewport: currentViewport,
                  features: featuresToSave,
                  envVariables: envVariablesToSave,
                  runtimeVariables: runtimeVariablesToSave,
                },
                currentViewport,
              ),
              mutation_context: mutationContext,
              expected_graph_hash: expectedMetadata.graphHash,
              expected_updated_at: expectedMetadata.updatedAt,
            };

            try {
              // 서버에 저장 요청
              const save = () =>
                workflowApi.syncDraftWorkflow(workflowId, saveRequest);
              let saveResponse;
              try {
                saveResponse = await save();
              } catch (firstError) {
                if (
                  (!pendingRevert && !pendingRedo) ||
                  !isAmbiguousSaveFailure(firstError)
                ) {
                  throw firstError;
                }
                try {
                  saveResponse = await save();
                } catch (retryError) {
                  const [canonicalResult, sessionResult] =
                    await Promise.allSettled([
                      workflowApi.getDraftWorkflow(workflowId),
                      agentBuilderApi.getSession(
                        (pendingRevert ?? pendingRedo)!.sessionId,
                      ),
                    ]);
                  if (
                    canonicalResult.status !== 'fulfilled' ||
                    sessionResult.status !== 'fulfilled' ||
                    !Array.isArray(canonicalResult.value?.nodes) ||
                    !Array.isArray(canonicalResult.value?.edges)
                  ) {
                    toast.warning(
                      'Agent Builder history 저장 결과를 확인하는 중입니다. Undo/Redo 상태를 유지합니다.',
                    );
                    return;
                  }

                  const canonical = canonicalResult.value;
                  const canonicalGraph = {
                    nodes: canonical.nodes.filter(
                      (node: AppNode) => node.type !== 'note',
                    ),
                    edges: canonical.edges,
                  };
                  const desiredGraph = {
                    nodes: saveRequest.nodes,
                    edges: graphToSave.edges,
                  };
                  const redoSnapshot = pendingRevert
                    ? useWorkflowStore
                        .getState()
                        .redoStack.findLast(
                          (snapshot) =>
                            snapshot.agentBuilderOperation?.operationId ===
                            pendingRevert.operationId,
                        )
                    : null;
                  const oppositeGraph = pendingRevert
                    ? redoSnapshot
                      ? {
                          nodes: redoSnapshot.nodes.filter(
                            (node) => node.type !== 'note',
                          ),
                          edges: redoSnapshot.edges,
                        }
                      : null
                    : (pendingRedo?.baseGraph ?? null);
                  const activeMutation = sessionResult.value
                    .active_graph_mutation as Record<string, unknown> | null;
                  const activeStatus = activeMutation?.status;
                  const sameOperation =
                    !activeMutation ||
                    activeMutation.operation_id ===
                      (pendingRevert ?? pendingRedo)!.operationId;
                  const groupStatus =
                    sessionResult.value.parameter_group?.status;
                  const canceledSession =
                    sameOperation &&
                    (activeStatus === 'reverted' ||
                      groupStatus === 'canceled' ||
                      (!activeMutation &&
                        !sessionResult.value.parameter_group));
                  const completedSession =
                    sameOperation &&
                    (activeStatus === 'acknowledged' ||
                      groupStatus === 'completed' ||
                      groupStatus === 'active');

                  if (
                    isEqual(canonicalGraph, desiredGraph) &&
                    canceledSession &&
                    typeof canonical.graph_hash === 'string' &&
                    typeof canonical.updated_at === 'string'
                  ) {
                    saveResponse = {
                      ...canonical,
                      graph_hash: canonical.graph_hash,
                      updated_at: canonical.updated_at,
                      parameter_group:
                        sessionResult.value.parameter_group ?? null,
                    };
                  } else if (
                    oppositeGraph &&
                    isEqual(canonicalGraph, oppositeGraph) &&
                    (pendingRevert ? completedSession : canceledSession)
                  ) {
                    useWorkflowStore
                      .getState()
                      .ingestCanonicalDraftMetadata(canonical, workflowId);
                    const state = useWorkflowStore.getState();
                    const canonicalNodes = canonical.nodes as typeof nodes;
                    const canonicalEdges = canonical.edges as typeof edges;
                    if (pendingRevert && redoSnapshot) {
                      const restoredHistory = redoSnapshot.agentBuilderHistory
                        ? {
                            ...redoSnapshot.agentBuilderHistory,
                            presentation: 'completed' as const,
                          }
                        : undefined;
                      useWorkflowStore.setState({
                        nodes: canonicalNodes,
                        edges: canonicalEdges,
                        workflows: state.workflows.map((workflow) =>
                          workflow.id === state.activeWorkflowId
                            ? {
                                ...workflow,
                                nodes: canonicalNodes,
                                edges: canonicalEdges,
                              }
                            : workflow,
                        ),
                        undoStack: [
                          ...state.undoStack,
                          {
                            nodes: structuredClone(graphToSave.nodes),
                            edges: structuredClone(graphToSave.edges),
                            agentBuilderOperation:
                              redoSnapshot.agentBuilderOperation,
                            agentBuilderHistory: restoredHistory,
                          },
                        ],
                        redoStack: state.redoStack.filter(
                          (snapshot) => snapshot !== redoSnapshot,
                        ),
                        pendingAgentBuilderRevert: null,
                        recoveredAgentBuilderParameterGroup: {
                          sessionId: pendingRevert.sessionId,
                          parameterGroup:
                            sessionResult.value.parameter_group ?? null,
                        },
                        hasUnsavedChanges: false,
                      });
                      pendingRedoRef.current = null;
                    } else if (pendingRedo) {
                      const boundary = state.undoStack.at(-1);
                      useWorkflowStore.setState({
                        nodes: canonicalNodes,
                        edges: canonicalEdges,
                        workflows: state.workflows.map((workflow) =>
                          workflow.id === state.activeWorkflowId
                            ? {
                                ...workflow,
                                nodes: canonicalNodes,
                                edges: canonicalEdges,
                              }
                            : workflow,
                        ),
                        undoStack: state.undoStack.slice(0, -1),
                        redoStack: [
                          ...state.redoStack,
                          {
                            nodes: structuredClone(
                              pendingRedo.finalGraph.nodes,
                            ),
                            edges: structuredClone(
                              pendingRedo.finalGraph.edges,
                            ),
                            agentBuilderOperation:
                              boundary?.agentBuilderOperation,
                            agentBuilderHistory: boundary?.agentBuilderHistory,
                          },
                        ],
                        hasUnsavedChanges: false,
                      });
                    }
                    toast.error(
                      'Agent Builder history 변경이 서버에 반영되지 않아 canonical 상태로 돌아왔습니다.',
                    );
                    return;
                  } else if (
                    !isEqual(canonicalGraph, desiredGraph) &&
                    (!oppositeGraph || !isEqual(canonicalGraph, oppositeGraph))
                  ) {
                    const state = useWorkflowStore.getState();
                    const canonicalMetadata = canonicalDraftMetadataFrom(canonical);
                    const normalized = assignMissingNodeDisplayNumbers(
                      canonical.nodes as typeof nodes,
                      canonical.features ?? state.features,
                    );
                    const canonicalNodes = normalized.nodes;
                    const canonicalEdges = canonical.edges as typeof edges;
                    useWorkflowStore.setState({
                      nodes: canonicalNodes,
                      edges: canonicalEdges,
                      features: normalized.features,
                      workflows: state.workflows.map((workflow) =>
                        workflow.id === state.activeWorkflowId
                          ? {
                              ...workflow,
                              nodes: canonicalNodes,
                              edges: canonicalEdges,
                              features: normalized.features,
                            }
                          : workflow,
                      ),
                      undoStack: [],
                      redoStack: [],
                      pendingAgentBuilderRevert: null,
                      recoveredAgentBuilderParameterGroup: null,
                      agentBuilderHistoryNotice: null,
                      pendingAgentBuilderApplySnapshot: null,
                      pendingAgentBuilderApplyCreatedBoundary: false,
                      hasUnsavedChanges: false,
                      ...(canonicalMetadata
                        ? {
                            canonicalDraftMetadata: {
                              ...state.canonicalDraftMetadata,
                              [workflowId]: {
                                workflowId,
                                ...canonicalMetadata,
                              },
                            },
                          }
                        : {}),
                    });
                    pendingRedoRef.current = null;
                    toast.error(
                      'Workflow가 다른 변경으로 갱신되어 서버의 최신 상태로 재동기화했습니다. 이전 Undo/Redo 기록은 사용할 수 없습니다.',
                    );
                    return;
                  } else {
                    toast.warning(
                      'Agent Builder history 저장 결과를 확인하는 중입니다. Undo/Redo 상태를 유지합니다.',
                    );
                    return;
                  }
                  if (!saveResponse) throw retryError;
                }
              }
              const responseWorkflowId =
                typeof saveResponse?.workflow_id === 'string'
                  ? saveResponse.workflow_id
                  : workflowId;
              if (responseWorkflowId !== workflowId) {
                useWorkflowStore
                  .getState()
                  .ingestCanonicalDraftMetadata(saveResponse, workflowId);
                return;
              }
              const latestState = useWorkflowStore.getState();
              const ordinarySaveStillCurrent =
                Boolean(pendingRevert || pendingRedo) ||
                (latestState.activeWorkflowId === workflowId &&
                  workflowDraftSaveStateEqual(
                    {
                      nodes: latestState.nodes,
                      edges: latestState.edges,
                      viewport: currentViewport,
                      features: latestState.features,
                      envVariables: latestState.envVariables,
                      runtimeVariables: latestState.runtimeVariables,
                    },
                    saveRequest,
                  ));
              if (
                typeof saveResponse?.graph_hash === 'string' &&
                typeof saveResponse?.updated_at === 'string'
              ) {
                useWorkflowStore
                  .getState()
                  .ingestCanonicalDraftMetadata(saveResponse, workflowId, {
                    applyDeferredProjection: ordinarySaveStillCurrent,
                  });
                const activeWorkflowId =
                  useWorkflowStore.getState().activeWorkflowId;
                if (activeWorkflowId !== workflowId) {
                  return;
                }
                useWorkflowStore
                  .getState()
                  .refreshNextAgentBuilderRevertBoundary({
                    resultGraphHash: saveResponse.graph_hash,
                    workflowUpdatedAt: saveResponse.updated_at,
                  });
              }
              if (!ordinarySaveStillCurrent) {
                return;
              }
              if (pendingRevert) {
                const redoSnapshot = useWorkflowStore
                  .getState()
                  .redoStack.findLast(
                    (snapshot) =>
                      snapshot.agentBuilderOperation?.operationId ===
                      pendingRevert.operationId,
                  );
                if (
                  redoSnapshot &&
                  typeof saveResponse?.graph_hash === 'string' &&
                  typeof saveResponse?.updated_at === 'string'
                ) {
                  pendingRedoRef.current = {
                    operationId: pendingRevert.operationId,
                    sessionId: pendingRevert.sessionId,
                    baseGraphHash: saveResponse.graph_hash,
                    workflowUpdatedAt: saveResponse.updated_at,
                    finalGraph: {
                      nodes: structuredClone(redoSnapshot.nodes),
                      edges: structuredClone(redoSnapshot.edges),
                    },
                    baseGraph: {
                      nodes: structuredClone(graphToSave.nodes),
                      edges: structuredClone(graphToSave.edges),
                    },
                  };
                }
                const savedParameterGroup = saveResponse?.parameter_group as
                  AgentBuilderParameterGroup | null | undefined;
                if (savedParameterGroup !== undefined) {
                  useWorkflowStore
                    .getState()
                    .setRecoveredAgentBuilderParameterGroup({
                      sessionId: pendingRevert.sessionId,
                      parameterGroup: savedParameterGroup ?? null,
                    });
                } else {
                  try {
                    const session = await agentBuilderApi.getSession(
                      pendingRevert.sessionId,
                    );
                    useWorkflowStore
                      .getState()
                      .setRecoveredAgentBuilderParameterGroup({
                        sessionId: pendingRevert.sessionId,
                        parameterGroup: session.parameter_group ?? null,
                      });
                  } catch {
                    toast.warning(
                      '변경은 되돌렸지만 Agent Builder 설정 상태를 새로고침하지 못했습니다.',
                    );
                  }
                }
                useWorkflowStore.getState().clearPendingAgentBuilderRevert();
              }
              if (pendingRedo) {
                pendingRedoRef.current = null;
              }
              setHasUnsavedChanges(false);
            } catch (error) {
              if (pendingRevert || pendingRedo) {
                toast.warning(
                  'Agent Builder history 저장 결과를 확인하지 못했습니다. Undo/Redo 상태를 유지합니다.',
                );
              } else if (isStaleGraphError(error)) {
                toast.error('stale_graph: Workflow changed on the server.', {
                  description:
                    '최신 draft를 다시 불러온 뒤 저장을 재시도해야 합니다.',
                });
              } else {
                toast.error('Workflow draft save failed.', {
                  description:
                    '변경 내용은 편집기에 남아 있습니다. 잠시 후 다시 저장됩니다.',
                });
              }
            } finally {
              if (pendingRevert || pendingRedo) {
                useWorkflowStore
                  .getState()
                  .setAgentBuilderMutationSaving(false);
              }
            }
          } finally {
            acquiredWorkflowSave();
          }
        },
        1000, // 1초 동안 추가 입력이 없으면 저장
        { maxWait: 300000 }, // 5분이 지나면 강제로 한 번 저장
      ),
    [
      workflowId,
      workflowAccess?.can_write,
      setHasUnsavedChanges,
      setWorkflowData,
      getViewport,
    ],
  );
  /* eslint-enable react-hooks/refs */

  // debouncedSync가 변경되면 ref 업데이트
  const debouncedSyncRef = useRef(debouncedSync);

  useEffect(() => {
    debouncedSyncRef.current = debouncedSync;
  }, [debouncedSync]);

  useEffect(() => {
    // 빈 내용으로 덮어쓰기 방지하기 위해 로딩이 완료되지 않았으면 바로 리턴
    if (!isLoadedRef.current) return;
    if (isAgentBuilderMutationSaving) {
      debouncedSyncRef.current.cancel();
      return;
    }
    if (!hasUnsavedChanges) {
      debouncedSyncRef.current.cancel();
      return;
    }

    debouncedSyncRef.current(
      nodes,
      edges,
      features,
      envVariables,
      runtimeVariables,
    );
  }, [
    nodes,
    edges,
    features,
    envVariables,
    runtimeVariables,
    workflowAccess,
    isAgentBuilderMutationSaving,
    hasUnsavedChanges,
  ]);
};
