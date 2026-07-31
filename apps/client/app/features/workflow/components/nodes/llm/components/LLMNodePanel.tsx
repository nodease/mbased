import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { LLMNodeData } from '../../../../types/Nodes';
import { getUpstreamNodes } from '../../../../utils/getUpstreamNodes';
import { UnregisteredVariablesAlert } from '../../../ui/UnregisteredVariablesAlert';
import { ValidationAlert } from '../../../ui/ValidationAlert';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import {
  ingestWorkflowDraftCASResult,
  resolveWorkflowDraftCASExpectation,
} from '@/app/features/workflow/utils/workflowDraftCAS';
import { CollapsibleSection } from '../../ui/CollapsibleSection';
import {
  HelpCircle,
  BookOpen,
  MousePointerClick,
  Wand2,
  FileJson,
} from 'lucide-react';
import { PromptWizardModal } from '../../../modals/PromptWizardModal';
import { ModelSelectDropdown } from './ModelSelectDropdown';
import { LLMParameterSidePanel } from './LLMParameterSidePanel';
import { resolveWorkflowWizardOrganizationId } from '@/app/features/workflow/utils/resolveWorkflowWizardOrganizationId';
import {
  DraggedOutputVariable,
  getDroppedOutputReferenceName,
  getTokenLabelMap,
  upsertNamedSelector,
} from '@/app/features/workflow/utils/nodeVariablePorts';
import { isWorkflowChatModelOption } from '@/app/features/workflow/utils/llmModelFilters';
import { VariableTokenEditor } from '../../ui/VariableTokenEditor';
import { PropertyVisibilityToggle } from '../../ui/PropertyVisibilityToggle';
import { CostOptimizerEntryAction } from '../../../costOptimizer/CostOptimizerEntryAction';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';
import type { ModelRoutingPolicyResponse } from '@/app/features/workflow/types/Api';

// LLMModelResponse와 일치하는 백엔드 응답 타입
type ModelOption = {
  id: string; // UUID
  model_id_for_api_call: string; // "gpt-4o"
  name: string;
  type: string;
  provider_name?: string;
  is_active: boolean;
};

const TOKEN_PATTERN = /{{\s*([^}]+?)\s*}}/g;

const extractTokenNames = (value: string) => {
  const names = new Set<string>();
  TOKEN_PATTERN.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = TOKEN_PATTERN.exec(value)) !== null) {
    const name = match[1].trim();
    if (name) names.add(name);
  }

  return names;
};

const selectorForOutput = (output: DraggedOutputVariable) => [
  output.sourceNodeId,
  output.outputId || output.key,
];

interface LLMNodePanelProps {
  nodeId: string;
  data: LLMNodeData;
  onOpenKnowledgeBaseSettings?: () => void;
}

type PromptHelpId = 'fallback' | 'system' | 'user' | 'assistant';
type OutputFormatType = 'text' | 'json';
type JsonSchemaFieldType = 'string' | 'number' | 'boolean' | 'object' | 'array';
type JsonSchemaField = {
  key: string;
  type: JsonSchemaFieldType;
  required: boolean;
};

const schemaFieldTypes: Array<{ value: JsonSchemaFieldType; label: string }> = [
  { value: 'string', label: 'string' },
  { value: 'number', label: 'number' },
  { value: 'boolean', label: 'boolean' },
  { value: 'object', label: 'object' },
  { value: 'array', label: 'array' },
];

const jsonSchemaFieldTypes = new Set<JsonSchemaFieldType>(
  schemaFieldTypes.map((fieldType) => fieldType.value),
);

const outputFormatTypeOf = (
  outputFormat: LLMNodeData['output_format'],
): OutputFormatType => (outputFormat?.type === 'json' ? 'json' : 'text');

const schemaFieldsFromOutputFormat = (
  outputFormat: LLMNodeData['output_format'],
): JsonSchemaField[] => {
  const schema = outputFormat?.schema;
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];

  const properties = schema.properties;
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) {
    return [];
  }

  const required = Array.isArray(schema.required)
    ? schema.required.filter((field): field is string => typeof field === 'string')
    : [];

  return Object.entries(properties).map(([key, propertySchema]) => {
    const type =
      propertySchema &&
      typeof propertySchema === 'object' &&
      !Array.isArray(propertySchema)
        ? propertySchema.type
        : null;
    return {
      key,
      type:
        typeof type === 'string' &&
        jsonSchemaFieldTypes.has(type as JsonSchemaFieldType)
          ? (type as JsonSchemaFieldType)
          : 'string',
      required: required.includes(key),
    };
  });
};

const outputSchemaFromFields = (
  fields: JsonSchemaField[],
): Record<string, unknown> => {
  const normalizedFields = fields
    .map((field) => ({
      key: field.key.trim(),
      type: jsonSchemaFieldTypes.has(field.type) ? field.type : 'string',
      required: field.required,
    }))
    .filter((field) => field.key.length > 0);

  return {
    type: 'object',
    properties: Object.fromEntries(
      normalizedFields.map((field) => [field.key, { type: field.type }]),
    ),
    required: normalizedFields
      .filter((field) => field.required)
      .map((field) => field.key),
  };
};

const outputFormatSignatureOf = (outputFormat: LLMNodeData['output_format']) =>
  JSON.stringify(outputFormat ?? null);

const HelpPopover = ({
  id,
  activeHelp,
  onToggle,
  children,
  widthClassName = 'w-48',
}: {
  id: PromptHelpId;
  activeHelp: PromptHelpId | null;
  onToggle: (id: PromptHelpId) => void;
  children: React.ReactNode;
  widthClassName?: string;
}) => {
  const isOpen = activeHelp === id;

  return (
    <div className="relative inline-block ml-1">
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onToggle(id);
        }}
        className="nodrag flex h-4 w-4 items-center justify-center rounded-full text-gray-400 transition-colors hover:text-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500/40"
        aria-label="도움말 보기"
        aria-expanded={isOpen}
      >
        <HelpCircle className="w-3 h-3" />
      </button>
      {isOpen && (
        <div
          className={`absolute left-0 top-5 z-50 rounded-lg border border-gray-200 bg-white p-2 text-[11px] text-gray-600 shadow-lg ${widthClassName}`}
          onClick={(event) => event.stopPropagation()}
        >
          {children}
          <div className="absolute -top-1 left-2 h-2 w-2 rotate-45 border-l border-t border-gray-200 bg-white" />
        </div>
      )}
    </div>
  );
};

// 노드 실행 필수 요건 체크
// 1. 시스템 프롬프트 또는 사용자 프롬프트 중 하나 이상 입력되어야 함
// 2. 모델이 선택되어야 함

const groupModelsByProvider = (models: ModelOption[]) => {
  const sorted = [...models].sort((a, b) => a.name.localeCompare(b.name));
  const grouped = sorted.reduce(
    (acc, model) => {
      const provider = model.provider_name || 'Unknown';
      if (!acc[provider]) acc[provider] = [];
      acc[provider].push(model);
      return acc;
    },
    {} as Record<string, ModelOption[]>,
  );

  return Object.entries(grouped)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([provider, providerModels]) => ({
      provider,
      models: providerModels,
    }));
};

export function LLMNodePanel({
  nodeId,
  data,
  onOpenKnowledgeBaseSettings,
}: LLMNodePanelProps) {
  const fullscreenNodeSettingsSection = useWorkflowStore(
    (state) => state.fullscreenNodeSettingsSection,
  );
  const openSettingsTab = useCallback(() => {
    window.open('/dashboard/settings', '_blank', 'noopener,noreferrer');
  }, []);
  const {
    updateNodeData,
    nodes,
    edges,
    activeWorkflowId,
    workflowAccess,
    hasUnsavedChanges,
  } = useWorkflowStore();
  const wizardOrganizationId = resolveWorkflowWizardOrganizationId(
    workflowAccess,
    activeWorkflowId,
  );
  const pendingPromptReferencesRef = useRef<
    LLMNodeData['referenced_variables']
  >([]);
  const lastSyncedOutputFormatRef = useRef(
    outputFormatSignatureOf(data.output_format),
  );
  const lastSyncedOutputFormatNodeRef = useRef(nodeId);

  const [activeHelp, setActiveHelp] = useState<PromptHelpId | null>(null);
  const [activeSettingsTab, setActiveSettingsTab] = useState<
    'basic' | 'advanced'
  >('basic');
  useEffect(() => {
    if (fullscreenNodeSettingsSection !== 'routing') return;
    setActiveSettingsTab('basic');
    const animationFrame = window.requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(
          `[data-agent-builder-node-settings-section="routing"][data-node-id="${nodeId}"]`,
        )
        ?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [fullscreenNodeSettingsSection, nodeId]);
  const [draftJsonSchemaFields, setDraftJsonSchemaFields] = useState<
    JsonSchemaField[]
  >(() => schemaFieldsFromOutputFormat(data.output_format));
  const [persistedRoutingPolicy, setPersistedRoutingPolicy] =
    useState<ModelRoutingPolicyResponse | null>(null);
  const [routingPolicyError, setRoutingPolicyError] = useState<string | null>(null);

  // 모델 상태 로드
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);

  // 프롬프트 마법사 상태
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardField, setWizardField] = useState<
    'system' | 'user' | 'assistant'
  >('system');

  // 마법사 열기 핸들러
  const openWizard = (field: 'system' | 'user' | 'assistant') => {
    setWizardField(field);
    setWizardOpen(true);
  };

  const toggleHelp = useCallback((id: PromptHelpId) => {
    setActiveHelp((current) => (current === id ? null : id));
  }, []);

  // 마법사에서 적용된 프롬프트 처리
  const handleApplyImproved = (improvedPrompt: string) => {
    const fieldMap = {
      system: 'system_prompt',
      user: 'user_prompt',
      assistant: 'assistant_prompt',
    } as const;
    handleFieldChange(fieldMap[wizardField], improvedPrompt);
  };

  const chatModelOptions = useMemo(
    () => modelOptions.filter(isWorkflowChatModelOption),
    [modelOptions],
  );
  const groupedModelOptions = useMemo(
    () => groupModelsByProvider(chatModelOptions),
    [chatModelOptions],
  );
  const selectedModel = useMemo(
    () =>
      modelOptions.find(
        (model) => model.model_id_for_api_call === data.model_id,
      ),
    [modelOptions, data.model_id],
  );
  const fallbackCandidates = useMemo(
    () =>
      chatModelOptions.filter(
        (model) => model.model_id_for_api_call !== data.model_id,
      ),
    [chatModelOptions, data.model_id],
  );
  const groupedFallbackOptions = useMemo(() => {
    const groups = groupModelsByProvider(fallbackCandidates);
    if (!data.model_id) return groups;
    const selectedProvider = (
      selectedModel?.provider_name || 'Unknown'
    ).toLowerCase();
    return [...groups].sort((a, b) => {
      const aIsSelected = a.provider.toLowerCase() === selectedProvider;
      const bIsSelected = b.provider.toLowerCase() === selectedProvider;
      if (aIsSelected !== bIsSelected) {
        return aIsSelected ? 1 : -1;
      }
      return a.provider.localeCompare(b.provider);
    });
  }, [fallbackCandidates, data.model_id, selectedModel]);
  const fallbackDisabled = !data.model_id?.trim();
  const routingPanelState = useMemo(() => {
    const policy = persistedRoutingPolicy;
    const legacyPolicy = data.model_routing_policy;
    const activePolicy = policy?.active_policy ?? legacyPolicy?.active_policy;
    const refreshEveryRuns =
      legacyPolicy?.refresh?.refresh_every_runs ??
      policy?.refresh?.refresh_every_runs ??
      20;
    const status =
      policy?.status ||
      legacyPolicy?.status ||
      (activePolicy ? 'active' : data.auto_model_routing ? 'collecting' : 'off');
    const statusLabel =
      status === 'failed'
        ? '정책 오류'
        : status === 'refreshing'
          ? '운영 성적 재평가 중'
          : policy?.learner?.mode === 'local_first'
            ? '로컬 선택 우선'
              : activePolicy
                ? '자동 라우팅 학습 중'
                : data.auto_model_routing
                  ? '첫 요청부터 Judge 선택'
                  : '사용 안 함';

    return {
      statusLabel,
      refreshEveryRuns,
      lastDecision: policy?.last_decision ?? null,
      learner: policy?.learner ?? null,
    };
  }, [
    data.auto_model_routing,
    data.model_routing_policy,
    persistedRoutingPolicy,
  ]);
  const upstreamNodes = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges],
  );
  const tokenLabels = useMemo(
    () => getTokenLabelMap(data.referenced_variables, upstreamNodes),
    [data.referenced_variables, upstreamNodes],
  );
  const outputFormatType = outputFormatTypeOf(data.output_format);
  const outputFormatSignature = useMemo(
    () => outputFormatSignatureOf(data.output_format),
    [data.output_format],
  );

  useEffect(() => {
    const nodeChanged = lastSyncedOutputFormatNodeRef.current !== nodeId;
    const outputFormatChanged =
      lastSyncedOutputFormatRef.current !== outputFormatSignature;

    if (nodeChanged || outputFormatChanged) {
      setDraftJsonSchemaFields(schemaFieldsFromOutputFormat(data.output_format));
      lastSyncedOutputFormatRef.current = outputFormatSignature;
      lastSyncedOutputFormatNodeRef.current = nodeId;
    }
  }, [data.output_format, nodeId, outputFormatSignature]);

  const validationErrors = useMemo(() => {
    const allPrompts =
      (data.system_prompt || '') +
      (data.user_prompt || '') +
      (data.assistant_prompt || '');
    const registeredNames = new Set(Object.keys(tokenLabels));
    const errors: string[] = [];

    // 정규식: 닫는 중괄호 } 를 제외한 모든 문자 1개 이상 (공백, 한글 포함)
    const regex = /{{\s*([^}]+?)\s*}}/g;
    let match;
    while ((match = regex.exec(allPrompts)) !== null) {
      const varName = match[1].trim();
      // varName이 비어있지 않고, 등록된 이름에 없으면 에러
      if (varName && !registeredNames.has(varName)) {
        errors.push(varName);
      }
    }
    return Array.from(new Set(errors));
  }, [
    data.system_prompt,
    data.user_prompt,
    data.assistant_prompt,
    tokenLabels,
  ]);

  const allPromptsEmpty = useMemo(() => {
    return (
      !data.system_prompt?.trim() &&
      !data.user_prompt?.trim() &&
      !data.assistant_prompt?.trim()
    );
  }, [data.system_prompt, data.user_prompt, data.assistant_prompt]);

  // 핸들러
  const handleUpdateData = useCallback(
    (key: keyof LLMNodeData, value: unknown) => {
      updateNodeData(nodeId, { [key]: value });
    },
    [nodeId, updateNodeData],
  );
  const updateOutputFormat = useCallback(
    (format: OutputFormatType) => {
      const nextOutputFormat =
        format === 'json'
          ? {
              type: 'json' as const,
              schema: outputSchemaFromFields(draftJsonSchemaFields),
            }
          : { type: 'text' as const };
      lastSyncedOutputFormatRef.current = outputFormatSignatureOf(nextOutputFormat);
      lastSyncedOutputFormatNodeRef.current = nodeId;
      updateNodeData(nodeId, {
        output_format: nextOutputFormat,
      });
    },
    [draftJsonSchemaFields, nodeId, updateNodeData],
  );
  const updateJsonSchemaFields = useCallback(
    (fields: JsonSchemaField[]) => {
      setDraftJsonSchemaFields(fields);
      const nextOutputFormat = {
        type: 'json' as const,
        schema: outputSchemaFromFields(fields),
      };
      lastSyncedOutputFormatRef.current =
        outputFormatSignatureOf(nextOutputFormat);
      lastSyncedOutputFormatNodeRef.current = nodeId;
      updateNodeData(nodeId, {
        output_format: nextOutputFormat,
      });
    },
    [nodeId, updateNodeData],
  );
  const addJsonSchemaField = useCallback(() => {
    updateJsonSchemaFields([
      ...draftJsonSchemaFields,
      { key: '', type: 'string', required: false },
    ]);
  }, [draftJsonSchemaFields, updateJsonSchemaFields]);
  const updateJsonSchemaField = useCallback(
    (index: number, updates: Partial<JsonSchemaField>) => {
      updateJsonSchemaFields(
        draftJsonSchemaFields.map((field, fieldIndex) =>
          fieldIndex === index ? { ...field, ...updates } : field,
        ),
      );
    },
    [draftJsonSchemaFields, updateJsonSchemaFields],
  );
  const removeJsonSchemaField = useCallback(
    (index: number) => {
      updateJsonSchemaFields(
        draftJsonSchemaFields.filter((_, fieldIndex) => fieldIndex !== index),
      );
    },
    [draftJsonSchemaFields, updateJsonSchemaFields],
  );
  const loadRoutingPolicy = useCallback(async () => {
    if (!activeWorkflowId) return;
    try {
      const policy = await workflowApi.getModelRoutingPolicy(
        activeWorkflowId,
        nodeId,
      );
      setPersistedRoutingPolicy(policy);
      setRoutingPolicyError(null);
    } catch {
      setPersistedRoutingPolicy(null);
      setRoutingPolicyError('정책 상태를 불러오지 못했습니다.');
    }
  }, [activeWorkflowId, nodeId]);

  const syncRoutingPolicy = useCallback(
    async (
      enabled: boolean,
      refreshEveryRuns: number,
      defaultModelId: string = data.model_id || '',
      fallbackModelId: string | null = data.fallback_model_id || null,
    ) => {
      if (!activeWorkflowId) return;
      try {
        const expectation = await resolveWorkflowDraftCASExpectation(
          activeWorkflowId,
        );
        const policy = await workflowApi.patchModelRoutingPolicy(
          activeWorkflowId,
          nodeId,
          {
            enabled,
            refresh_every_runs: refreshEveryRuns,
            default_model_id: defaultModelId,
            fallback_model_id: fallbackModelId,
            ...expectation,
          },
        );
        ingestWorkflowDraftCASResult(activeWorkflowId, policy);
        setPersistedRoutingPolicy(policy);
        setRoutingPolicyError(null);
      } catch {
        setRoutingPolicyError('정책 설정을 저장하지 못했습니다.');
      }
    },
    [activeWorkflowId, data.fallback_model_id, data.model_id, nodeId],
  );

  const handleAutoModelRoutingChange = useCallback(
    (enabled: boolean) => {
      handleUpdateData('auto_model_routing', enabled);
      // 켤 때는 배포 또는 첫 실행에서 Judge-first policy가 자동으로 준비된다.
      // 끌 때만 즉시 runtime policy를 off로 전환한다.
      if (!enabled) {
        void syncRoutingPolicy(false, routingPanelState.refreshEveryRuns);
      }
    },
    [
      handleUpdateData,
      routingPanelState.refreshEveryRuns,
      syncRoutingPolicy,
    ],
  );

  // Claude 계열 여부 판별 (모델 옵션 우선, 실패 시 이름 프리픽스 판단)
  const isAnthropicModelId = useCallback(
    (modelId: string) => {
      const candidate = modelOptions.find(
        (model) => model.model_id_for_api_call === modelId,
      );
      const provider = (candidate?.provider_name || '').toLowerCase();
      if (provider) return provider.includes('anthropic');
      return modelId.toLowerCase().startsWith('claude');
    },
    [modelOptions],
  );

  // Claude 모델에서 top_p를 제거해 파라미터 충돌을 방지
  const stripTopP = (parameters?: Record<string, unknown>) => {
    if (
      !parameters ||
      !Object.prototype.hasOwnProperty.call(parameters, 'top_p')
    ) {
      return parameters;
    }
    const rest = { ...parameters };
    delete rest.top_p;
    return rest;
  };

  // 모델 변경 시 Claude면 top_p 제거, 폴백 모델 중복 선택도 정리
  const handleModelChange = useCallback(
    (nextModelId: string) => {
      const updates: Partial<LLMNodeData> = { model_id: nextModelId };
      if (data.fallback_model_id && data.fallback_model_id === nextModelId) {
        updates.fallback_model_id = '';
      }
      if (isAnthropicModelId(nextModelId)) {
        const nextParams = stripTopP(data.parameters);
        if (nextParams !== data.parameters) {
          updates.parameters = nextParams || {};
        }
      }
      updateNodeData(nodeId, updates);
    },
    [
      data.fallback_model_id,
      data.parameters,
      isAnthropicModelId,
      nodeId,
      updateNodeData,
    ],
  );

  const handleRoutingDefaultModelChange = useCallback(
    (nextModelId: string) => {
      const nextFallbackModelId =
        data.fallback_model_id === nextModelId
          ? null
          : data.fallback_model_id || null;
      handleModelChange(nextModelId);
      void syncRoutingPolicy(
        true,
        routingPanelState.refreshEveryRuns,
        nextModelId,
        nextFallbackModelId,
      );
    },
    [
      data.fallback_model_id,
      handleModelChange,
      routingPanelState.refreshEveryRuns,
      syncRoutingPolicy,
    ],
  );

  const handleRoutingFallbackModelChange = useCallback(
    (nextFallbackModelId: string) => {
      handleUpdateData('fallback_model_id', nextFallbackModelId);
      void syncRoutingPolicy(
        true,
        routingPanelState.refreshEveryRuns,
        data.model_id || '',
        nextFallbackModelId || null,
      );
    },
    [
      data.model_id,
      handleUpdateData,
      routingPanelState.refreshEveryRuns,
      syncRoutingPolicy,
    ],
  );

  // 외부 갱신/새로고침 등으로 top_p가 다시 들어오는 상황을 정리
  useEffect(() => {
    if (!data.model_id) return;
    if (!isAnthropicModelId(data.model_id)) return;
    const nextParams = stripTopP(data.parameters);
    if (nextParams !== data.parameters) {
      updateNodeData(nodeId, { parameters: nextParams || {} });
    }
  }, [
    data.model_id,
    data.parameters,
    isAnthropicModelId,
    nodeId,
    updateNodeData,
  ]);

  const handleFieldChange = useCallback(
    (field: keyof LLMNodeData, value: unknown) => {
      if (
        field === 'system_prompt' ||
        field === 'user_prompt' ||
        field === 'assistant_prompt'
      ) {
        const nextSystemPrompt =
          field === 'system_prompt'
            ? String(value || '')
            : data.system_prompt || '';
        const nextUserPrompt =
          field === 'user_prompt'
            ? String(value || '')
            : data.user_prompt || '';
        const nextAssistantPrompt =
          field === 'assistant_prompt'
            ? String(value || '')
            : data.assistant_prompt || '';
        const usedNames = new Set([
          ...extractTokenNames(nextSystemPrompt),
          ...extractTokenNames(nextUserPrompt),
          ...extractTokenNames(nextAssistantPrompt),
        ]);
        const mergedReferences = [
          ...(data.referenced_variables || []),
          ...pendingPromptReferencesRef.current,
        ];
        const pendingReferenceKeys = new Set(
          pendingPromptReferencesRef.current.map(
            (reference) =>
              `${reference.name}:${reference.value_selector?.[0] || ''}:${reference.value_selector?.[1] || ''}`,
          ),
        );
        const nextReferences = mergedReferences.filter((reference, index) => {
          if (!reference.name || !usedNames.has(reference.name)) return false;
          const referenceKey = `${reference.name}:${reference.value_selector?.[0] || ''}:${reference.value_selector?.[1] || ''}`;
          if (
            !Object.prototype.hasOwnProperty.call(
              tokenLabels,
              reference.name,
            ) &&
            !pendingReferenceKeys.has(referenceKey)
          ) {
            return false;
          }
          return (
            mergedReferences.findIndex((candidate) => {
              if (candidate.name !== reference.name) return false;
              return (
                candidate.value_selector?.[0] ===
                  reference.value_selector?.[0] &&
                candidate.value_selector?.[1] === reference.value_selector?.[1]
              );
            }) === index
          );
        });

        pendingPromptReferencesRef.current =
          pendingPromptReferencesRef.current.filter((reference) =>
            usedNames.has(reference.name),
          );
        updateNodeData(nodeId, {
          [field]: value,
          referenced_variables: nextReferences,
        });
        return;
      }

      updateNodeData(nodeId, { [field]: value });
    },
    [
      data.assistant_prompt,
      data.referenced_variables,
      data.system_prompt,
      data.user_prompt,
      nodeId,
      tokenLabels,
      updateNodeData,
    ],
  );

  const handlePromptDropOutput = useCallback(
    (output: DraggedOutputVariable) => {
      const referenceName = getDroppedOutputReferenceName(
        data.referenced_variables,
        output,
        'value_selector',
      );
      const nextReferences = upsertNamedSelector(
        data.referenced_variables,
        output,
        'value_selector',
      ) as LLMNodeData['referenced_variables'];
      pendingPromptReferencesRef.current = [
        ...pendingPromptReferencesRef.current.filter(
          (reference) => reference.name !== referenceName,
        ),
        {
          name: referenceName,
          value_selector: selectorForOutput(output),
        },
      ];
      updateNodeData(nodeId, { referenced_variables: nextReferences });
      return referenceName;
    },
    [data.referenced_variables, nodeId, updateNodeData],
  );

  // 사용자가 사용 가능한 모델 가져오기
  useEffect(() => {
    const fetchMyModels = async () => {
      try {
        setLoadingModels(true);
        const res = await fetch(`/api/v1/llm/my-models`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
          },
          credentials: 'include',
        });
        if (res.ok) {
          const json = await res.json();
          setModelOptions(json);
        }
      } catch {
        setModelOptions([]);
      } finally {
        setLoadingModels(false);
      }
    };

    fetchMyModels();
  }, []);

  useEffect(() => {
    if (!data.auto_model_routing) {
      setPersistedRoutingPolicy(null);
      return;
    }
    void loadRoutingPolicy();
  }, [data.auto_model_routing, loadRoutingPolicy]);

  useEffect(() => {
    if (!activeHelp) return;

    const closeHelp = () => setActiveHelp(null);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeHelp();
    };

    window.addEventListener('click', closeHelp);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('click', closeHelp);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeHelp]);

  return (
    <div className="relative flex flex-col gap-2">
      <div className="sticky top-0 z-10 rounded-lg border border-slate-200 bg-white p-2 shadow-sm">
        <div className="flex flex-col gap-2">
          <div className="grid grid-cols-2 gap-1">
            {[
              { id: 'basic' as const, label: '기본 설정' },
              { id: 'advanced' as const, label: '고급 설정' },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveSettingsTab(tab.id)}
                className={`nodrag rounded-md px-3 py-2 text-xs font-bold transition-colors ${
                  activeSettingsTab === tab.id
                    ? 'bg-slate-900 text-white shadow-sm'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`}
                aria-pressed={activeSettingsTab === tab.id}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div>
            <CostOptimizerEntryAction
              workflowId={activeWorkflowId}
              nodeId={nodeId}
              workflowAccess={workflowAccess}
              hasUnsavedChanges={hasUnsavedChanges}
              label="비교 분석 테스트"
              title="실행 로그를 기준으로 A/B 비교 분석 테스트 화면을 엽니다."
            />
          </div>
        </div>
      </div>

      {activeSettingsTab === 'advanced' ? (
        <div className="min-h-[560px] overflow-hidden rounded-xl border border-slate-200 bg-white">
          <LLMParameterSidePanel
            embedded
            nodeId={nodeId}
            data={data}
            onClose={() => setActiveSettingsTab('basic')}
          />
        </div>
      ) : (
        <>
      <div
        data-agent-builder-node-settings-section="routing"
        data-node-id={nodeId}
        className="rounded-lg border border-slate-200 bg-slate-50 p-3"
      >

      {/* 1. 모델 선택 */}
      <CollapsibleSection title="모델" showDivider>
        <div className="flex flex-col gap-2">
          {loadingModels ? (
            <div className="text-xs text-gray-400">모델 로딩 중...</div>
          ) : modelOptions.length > 0 ? (
            <>
              <label className="flex items-start gap-2 rounded-md border border-emerald-100 bg-emerald-50 px-3 py-2">
                <input
                  type="checkbox"
                  className="nodrag mt-0.5 h-4 w-4 rounded border-emerald-300 text-emerald-600 focus:ring-emerald-500"
                  checked={Boolean(data.auto_model_routing)}
                  onChange={(event) => handleAutoModelRoutingChange(event.target.checked)}
                  aria-label="자동 모델 라우팅"
                />
                <span className="min-w-0">
                  <span className="block text-xs font-semibold text-emerald-900">
                    자동 모델 라우팅
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-emerald-700">
                    저장된 라우팅 정책이 요청에 맞는 모델을 선택합니다.
                  </span>
                </span>
              </label>

              {data.auto_model_routing ? (
                <div className="flex flex-col rounded-md border border-slate-200 bg-white p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold text-slate-800">
                        자동 라우팅 사용 중
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                        규칙이 맞지 않거나 판단이 불확실하면 기본 모델을 사용합니다.
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">
                      {routingPanelState.statusLabel}
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="rounded border border-slate-100 bg-slate-50 p-2">
                      <label className="block text-[11px] font-semibold text-slate-600">
                        기본 모델 (규칙 미일치 시)
                      </label>
                      <ModelSelectDropdown
                        value={data.model_id || ''}
                        onChange={handleRoutingDefaultModelChange}
                        models={chatModelOptions}
                        groupedModels={groupedModelOptions}
                        placeholder="기본 모델을 선택하세요"
                      />
                    </div>
                    <div className="rounded border border-slate-100 bg-slate-50 p-2">
                      <label className="block text-[11px] font-semibold text-slate-600">
                        실행 실패 대체 모델
                      </label>
                      <ModelSelectDropdown
                        value={data.fallback_model_id || ''}
                        onChange={handleRoutingFallbackModelChange}
                        models={fallbackCandidates}
                        groupedModels={groupedFallbackOptions}
                        disabled={fallbackDisabled}
                        placeholder={
                          fallbackDisabled
                            ? '먼저 기본 모델을 선택하세요'
                            : '실행 실패 대체 모델을 선택하세요'
                        }
                      />
                    </div>
                  </div>
                  {routingPanelState.lastDecision ? (
                    <div className="mt-3 rounded-md border border-sky-200 bg-sky-50 p-3 text-[11px] text-sky-900">
                      <div className="font-semibold">최근 실행 선택</div>
                      <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
                        <div>
                          <dt className="text-sky-700">선택 모델</dt>
                          <dd className="mt-0.5 font-semibold text-slate-900">
                            {routingPanelState.lastDecision.selected_model_id}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-sky-700">판단 사유</dt>
                          <dd className="mt-0.5 font-semibold text-slate-900">
                            {routingPanelState.lastDecision.reason_label}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-sky-700">실행 결과</dt>
                          <dd className="mt-0.5 font-semibold text-slate-900">
                            {routingPanelState.lastDecision.fallback_used
                              ? `대체 모델 ${routingPanelState.lastDecision.fallback_model_id || '사용'}`
                              : '선택 모델로 완료'}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                      아직 배포 실행 이력이 없습니다. 첫 실행 뒤 선택 모델과 판단 사유가 여기에 표시됩니다.
                    </p>
                  )}
                  {routingPanelState.learner ? (
                    <div className="mt-3 border-t border-slate-200 pt-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[11px] font-semibold text-slate-700">
                          자동 라우팅 학습
                        </span>
                        <span className="text-[11px] text-slate-500">
                          {routingPanelState.learner.mode === 'local_first'
                            ? '검증된 로컬 선택 사용 중'
                            : 'Judge 정답 수집 중'}
                        </span>
                      </div>
                      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-[11px] sm:grid-cols-4">
                        <div>
                          <dt className="text-slate-500">Judge 정답</dt>
                          <dd className="font-semibold text-slate-900">
                            {routingPanelState.learner.judged_request_count}건
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">처리 대기</dt>
                          <dd className="font-semibold text-slate-900">
                            {routingPanelState.learner.pending_count}건
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">최근 Judge 일치율</dt>
                          <dd className="font-semibold text-slate-900">
                            {routingPanelState.learner.recent_evaluation
                              .judge_match_rate == null
                              ? '-'
                              : `${Math.round(
                                  routingPanelState.learner.recent_evaluation
                                    .judge_match_rate * 100,
                                )}%`}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">검증 학습 버전</dt>
                          <dd className="font-semibold text-slate-900">
                            {routingPanelState.learner.active_version == null
                              ? '대기 중'
                              : `v${routingPanelState.learner.active_version}`}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : null}
                  {routingPolicyError ? (
                    <p className="mt-2 text-[11px] text-rose-600">
                      {routingPolicyError}
                    </p>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="flex items-center">
                    <label className="text-xs font-semibold text-gray-700">
                      기본 모델
                    </label>
                    <PropertyVisibilityToggle
                      nodeId={nodeId}
                      propertyKey="model_id"
                    />
                  </div>
                  <ModelSelectDropdown
                    value={data.model_id || ''}
                    onChange={handleModelChange}
                    models={chatModelOptions}
                    groupedModels={groupedModelOptions}
                    placeholder="모델을 선택하세요"
                  />
                  <div className="mt-3 flex flex-col gap-2">
                    <div className="flex items-center gap-1">
                      <label className="text-xs font-semibold text-gray-700">
                        대체 모델
                      </label>
                      <HelpPopover
                        id="fallback"
                        activeHelp={activeHelp}
                        onToggle={toggleHelp}
                        widthClassName="w-60"
                      >
                        기본 모델 호출이 실패하거나 타임아웃될 때 대신 사용할
                        모델입니다.
                      </HelpPopover>
                    </div>
                    <div className="relative group">
                      <ModelSelectDropdown
                        value={data.fallback_model_id || ''}
                        onChange={(val) =>
                          handleUpdateData('fallback_model_id', val)
                        }
                        models={fallbackCandidates}
                        groupedModels={groupedFallbackOptions}
                        disabled={fallbackDisabled}
                        placeholder={
                          fallbackDisabled
                            ? '먼저 모델을 선택하세요'
                            : '대체 모델을 선택하세요'
                        }
                      />
                      {fallbackDisabled && (
                        <div className="pointer-events-none absolute left-0 top-full z-10 mt-1 w-56 rounded border border-gray-200 bg-white p-2 text-[11px] text-gray-600 shadow-lg opacity-0 transition-opacity group-hover:opacity-100">
                          먼저 기본 모델을 설정해주세요.
                        </div>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">
                      대체 모델은 다른 Provider 사용을 권장합니다.
                      <br />
                      다른 Provider를 추가하려면 아래에서 API Key를
                      등록하세요.
                    </p>
                  </div>
                </>
              )}

              {!data.auto_model_routing && (
                <button
                  onClick={openSettingsTab}
                  className="mt-2 text-left text-xs text-blue-600 hover:text-blue-800 hover:underline"
                >
                  Provider API Key 등록하기
                </button>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-2 p-3 bg-gray-50 rounded border border-gray-200 items-center justify-center text-center">
              <span className="text-xs text-gray-500">
                사용 가능한 모델이 없습니다.
              </span>
              <button
                onClick={openSettingsTab}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium rounded transition-colors"
              >
                Provider 설정하러 가기 →
              </button>
            </div>
          )}
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="출력 형식">
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-bold text-gray-900">
            <FileJson className="h-4 w-4 text-gray-600" />
            출력 형식
          </div>
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-1">
            {(['text', 'json'] as const).map((format) => (
              <button
                key={format}
                type="button"
                onClick={() => updateOutputFormat(format)}
                className={`rounded px-3 py-1.5 text-xs font-bold transition-colors ${
                  outputFormatType === format
                    ? 'bg-emerald-600 text-white'
                    : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {format === 'json' ? 'JSON' : 'TEXT'}
              </button>
            ))}
          </div>

          {outputFormatType === 'json' ? (
            <div className="mt-3 grid gap-3 rounded-md border border-dashed border-gray-300 bg-white p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-bold text-gray-700">
                    JSON schema
                  </div>
                  <div className="text-[11px] text-gray-500">
                    flat key-type 행으로 출력 계약을 정의합니다.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={addJsonSchemaField}
                  className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-bold text-gray-700 hover:bg-gray-50"
                >
                  스키마 필드 추가
                </button>
              </div>

              {draftJsonSchemaFields.length === 0 ? (
                <div className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
                  정의된 필드가 없습니다.
                </div>
              ) : (
                <div className="grid gap-2">
                  {draftJsonSchemaFields.map((field, index) => (
                    <div
                      key={`json-schema-field-${index}`}
                      className="grid grid-cols-[minmax(120px,1fr)_minmax(110px,140px)_auto_auto] items-center gap-2 rounded-md border border-gray-200 bg-white p-2"
                    >
                      <label className="grid gap-1 text-[11px] font-semibold text-gray-500">
                        <span>필드명</span>
                        <input
                          value={field.key}
                          onChange={(event) =>
                            updateJsonSchemaField(index, {
                              key: event.target.value,
                            })
                          }
                          className="min-w-0 rounded-md border border-gray-200 px-2 py-1.5 text-xs text-gray-800 outline-none focus:border-emerald-400"
                          placeholder="예: summary"
                        />
                      </label>
                      <label className="grid gap-1 text-[11px] font-semibold text-gray-500">
                        <span>타입</span>
                        <select
                          value={field.type}
                          onChange={(event) =>
                            updateJsonSchemaField(index, {
                              type: event.target.value as JsonSchemaFieldType,
                            })
                          }
                          className="min-w-0 rounded-md border border-gray-200 px-2 py-1.5 text-xs text-gray-800 outline-none focus:border-emerald-400"
                        >
                          {schemaFieldTypes.map((fieldType) => (
                            <option key={fieldType.value} value={fieldType.value}>
                              {fieldType.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex items-center gap-1 pt-5 text-xs font-semibold text-gray-600">
                        <input
                          type="checkbox"
                          checked={field.required}
                          onChange={(event) =>
                            updateJsonSchemaField(index, {
                              required: event.target.checked,
                            })
                          }
                        />
                        필수
                      </label>
                      <button
                        type="button"
                        onClick={() => removeJsonSchemaField(index)}
                        className="mt-5 rounded-md px-2 py-1 text-xs font-bold text-red-500 hover:bg-red-50"
                      >
                        삭제
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </CollapsibleSection>

      {/* 2.5 Knowledge 선택 버튼 */}
      <div className="my-2 group">
        <button
          type="button"
          onClick={() => {
            if (onOpenKnowledgeBaseSettings) {
              onOpenKnowledgeBaseSettings();
              return;
            }
            // 부모 컴포넌트에서 사이드 패널 열기
            const event = new CustomEvent('openLLMReferencePanel', {
              detail: { nodeId },
            });
            window.dispatchEvent(event);
          }}
          className={`relative w-full py-4 px-5 rounded-xl border-2 border-dashed transition-all duration-300 flex items-center gap-4 active:scale-[0.98] ${
            (data.knowledgeBases?.length ?? 0) > 0 ||
            (data.knowledgeCollections?.length ?? 0) > 0
              ? 'border-indigo-400 bg-indigo-50/80 text-indigo-800 shadow-sm hover:shadow-md hover:bg-indigo-50 hover:border-indigo-500'
              : 'border-gray-300 bg-gray-50/50 text-gray-600 hover:border-indigo-400 hover:bg-indigo-50/30 hover:text-indigo-700 hover:shadow-sm'
          }`}
        >
          {/* 호버 시 배경 일러스트 효과 */}
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/40 to-transparent translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-1000 pointer-events-none" />

          {/* 왼쪽 아이콘 (책) */}
          <div
            className={`p-2 rounded-lg transition-colors duration-300 ${
              (data.knowledgeBases?.length ?? 0) > 0 ||
              (data.knowledgeCollections?.length ?? 0) > 0
                ? 'bg-indigo-200 text-indigo-700'
                : 'bg-gray-200 text-gray-500 group-hover:bg-indigo-100 group-hover:text-indigo-600'
            }`}
          >
            <BookOpen className="w-5 h-5" />
          </div>

          {/* 텍스트 내용 */}
          <div className="flex flex-col items-start flex-1 gap-0.5">
            <span className="font-bold text-sm tracking-tight">
              Knowledge 설정
            </span>
            <span
              className={`text-xs transition-colors duration-300 ${
                (data.knowledgeBases?.length ?? 0) > 0 ||
                (data.knowledgeCollections?.length ?? 0) > 0
                  ? 'text-indigo-600 font-medium'
                  : 'text-gray-400 group-hover:text-indigo-500'
              }`}
            >
              {(data.knowledgeBases?.length ?? 0) > 0 ||
              (data.knowledgeCollections?.length ?? 0) > 0
                ? `고정 KB ${data.knowledgeBases?.length ?? 0}개 · Collection ${
                    data.knowledgeCollections?.length ?? 0
                  }개`
                : 'LLM에 지식을 연결하세요'}
            </span>
          </div>

          {/* 오른쪽 아이콘 (클릭 동작) */}
          <div className="transform transition-all duration-300 group-hover:scale-110 group-hover:rotate-[-6deg] text-gray-300 group-hover:text-indigo-500">
            <MousePointerClick className="w-6 h-6" />
          </div>
        </button>
      </div>

      {/* 3. 프롬프트 */}
      <CollapsibleSection title="프롬프트">
        <div className="flex flex-col gap-3 relative">
          {/* 프롬프트 설명 */}
          <p className="text-xs text-gray-500 mb-1">
            LLM에 전달할 메시지를 작성하세요. 최소 1개 이상 입력이 필요합니다.
          </p>

          {allPromptsEmpty && (
            <ValidationAlert message="⚠️ 최소 1개의 프롬프트를 입력해야 실행할 수 있습니다." />
          )}

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center">
                <label className="text-xs font-semibold text-gray-700">
                  시스템 프롬프트
                </label>
                <PropertyVisibilityToggle
                  nodeId={nodeId}
                  propertyKey="system_prompt"
                />
                <HelpPopover
                  id="system"
                  activeHelp={activeHelp}
                  onToggle={toggleHelp}
                >
                  AI의 역할, 성격, 행동 규칙을 정의합니다. 모든 대화에 일관되게
                  적용됩니다.
                </HelpPopover>
              </div>
              <div className="group/wizard relative">
                <button
                  type="button"
                  onClick={() => openWizard('system')}
                  disabled={modelOptions.length === 0}
                  className="flex items-center gap-1 px-1.5 py-0.5 text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-[10px]"
                >
                  <Wand2 className="w-3 h-3" />
                  <span>프롬프트 마법사</span>
                </button>
                <div className="absolute z-50 hidden group-hover/wizard:block w-32 p-2 text-[11px] text-gray-600 bg-white border border-gray-200 rounded-lg shadow-lg right-0 top-7">
                  AI가 프롬프트를 개선해드려요
                  <div className="absolute -top-1 right-2 w-2 h-2 bg-white border-l border-t border-gray-200 rotate-45" />
                </div>
              </div>
            </div>
            <VariableTokenEditor
              className="min-h-24"
              ariaLabel="시스템 프롬프트"
              placeholder="예: 너는 친절하고 전문적인 고객 상담 AI입니다. 항상 존댓말을 사용하고, 정확하고 간결하게 답변해주세요."
              value={data.system_prompt || ''}
              onChange={(value) => handleFieldChange('system_prompt', value)}
              onDropOutput={handlePromptDropOutput}
              tokenLabels={tokenLabels}
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center">
                <label className="text-xs font-semibold text-gray-700">
                  사용자 프롬프트
                </label>
                <PropertyVisibilityToggle
                  nodeId={nodeId}
                  propertyKey="user_prompt"
                />
                <HelpPopover
                  id="user"
                  activeHelp={activeHelp}
                  onToggle={toggleHelp}
                >
                  사용자가 AI에게 보내는 질문이나 요청입니다. 좌측 입력 패널에서
                  변수를 클릭해 동적 값을 삽입할 수 있습니다.
                </HelpPopover>
              </div>
              <div className="group/wizard relative">
                <button
                  type="button"
                  onClick={() => openWizard('user')}
                  disabled={modelOptions.length === 0}
                  className="flex items-center gap-1 px-1.5 py-0.5 text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-[10px]"
                >
                  <Wand2 className="w-3 h-3" />
                  <span>프롬프트 마법사</span>
                </button>
                <div className="absolute z-50 hidden group-hover/wizard:block w-32 p-2 text-[11px] text-gray-600 bg-white border border-gray-200 rounded-lg shadow-lg right-0 top-7">
                  AI가 프롬프트를 개선해드려요
                  <div className="absolute -top-1 right-2 w-2 h-2 bg-white border-l border-t border-gray-200 rotate-45" />
                </div>
              </div>
            </div>
            <VariableTokenEditor
              className="min-h-32"
              ariaLabel="사용자 프롬프트"
              placeholder={`예: 다음 내용을 한국어로 3줄 요약해줘:\n\n여기에 입력 변수를 넣으려면 좌측 입력 패널의 변수를 클릭하세요.`}
              value={data.user_prompt || ''}
              onChange={(value) => handleFieldChange('user_prompt', value)}
              onDropOutput={handlePromptDropOutput}
              tokenLabels={tokenLabels}
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center">
                <label className="text-xs font-semibold text-gray-700">
                  어시스턴트 프롬프트
                </label>
                <PropertyVisibilityToggle
                  nodeId={nodeId}
                  propertyKey="assistant_prompt"
                />
                <HelpPopover
                  id="assistant"
                  activeHelp={activeHelp}
                  onToggle={toggleHelp}
                >
                  AI 응답의 시작 부분을 미리 지정합니다. 특정 형식이나 톤으로
                  응답을 유도할 때 유용합니다.
                </HelpPopover>
              </div>
              <div className="group/wizard relative">
                <button
                  type="button"
                  onClick={() => openWizard('assistant')}
                  disabled={modelOptions.length === 0}
                  className="flex items-center gap-1 px-1.5 py-0.5 text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-[10px]"
                >
                  <Wand2 className="w-3 h-3" />
                  <span>프롬프트 마법사</span>
                </button>
                <div className="absolute z-50 hidden group-hover/wizard:block w-32 p-2 text-[11px] text-gray-600 bg-white border border-gray-200 rounded-lg shadow-lg right-0 top-7">
                  AI가 프롬프트를 개선해드려요
                  <div className="absolute -top-1 right-2 w-2 h-2 bg-white border-l border-t border-gray-200 rotate-45" />
                </div>
              </div>
            </div>
            <VariableTokenEditor
              className="min-h-24"
              ariaLabel="어시스턴트 프롬프트"
              placeholder="예: 분석 결과를 다음과 같이 정리하겠습니다:"
              value={data.assistant_prompt || ''}
              onChange={(value) => handleFieldChange('assistant_prompt', value)}
              onDropOutput={handlePromptDropOutput}
              tokenLabels={tokenLabels}
            />
          </div>

          {validationErrors.length > 0 && (
            <UnregisteredVariablesAlert variables={validationErrors} />
          )}
        </div>
      </CollapsibleSection>

        </div>
        </>
      )}

      {/* 프롬프트 마법사 모달 */}
      <PromptWizardModal
        isOpen={wizardOpen}
        onClose={() => setWizardOpen(false)}
        promptType={wizardField}
        organizationId={wizardOrganizationId}
        originalPrompt={
          wizardField === 'system'
            ? data.system_prompt || ''
            : wizardField === 'user'
              ? data.user_prompt || ''
              : data.assistant_prompt || ''
        }
        onApply={handleApplyImproved}
      />
    </div>
  );
}
