"""Judge-first 자동 모델 라우팅의 2단계 학습·경제성 실험.

첫 단계는 자동 라우팅만 100회 실행해 학습기를 만들고, 두 번째 단계는
학습에 쓰지 않은 20개 요청을 자동·중간 고정·고가 고정 방식으로 실행한다.

* ``automatic``: 배포된 ``judge_bootstrap_incremental_v1`` 정책을 사용한다.
* ``mid_fixed``: 중간 모델 ``gpt-5.4-mini``를 고정한다.
* ``high_fixed``: 고가 모델 ``gpt-5.6-sol``를 고정한다.
* ``low_fixed``: 저가 모델 ``gpt-4o-mini``를 고정한다.

모든 실행은 실제 ``WorkflowEngine``과 provider credential을 사용한다. 품질은
실행 모델이 아닌 별도 Judge가 익명화된 세 출력을 한 번에 평가한다. 품질 Judge
비용은 제품 운영비가 아니라 실험 측정 비용으로 분리해 보고한다.

기본 모드는 provider를 호출하지 않는 ``--dry-run``이다. 실제 비용이 발생하는
실험은 반드시 ``--execute``를 붙여야 한다.
"""

# 이 스크립트는 repo root를 sys.path에 추가한 뒤 애플리케이션 모듈을 import한다.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import random
import statistics
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENT_OF_ROOT = ROOT.parent
EXPERIMENT_RUNS_ROOT = pathlib.Path("reports/model-routing/runs/judge-first")
for path in (ROOT, PARENT_OF_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

# 이 스크립트는 Gateway/worker 진입점 없이 직접 실행되므로, 저장된 credential을
# 복호화할 수 있도록 애플리케이션과 동일한 .env를 먼저 읽는다.
load_dotenv(ROOT / ".env", override=False)

from apps.log_system import tasks as log_tasks
from apps.shared.celery_app import celery_app
from apps.shared.db.demo_seed import _ticket_ops_graph
from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingLearner,
    LLMNodeModelRoutingLearnerVersion,
    LLMNodeModelRoutingPerformance,
    LLMNodeModelRoutingPolicy,
    LLMNodeModelRoutingPolicyRunEvent,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from apps.shared.db.session import SessionLocal
from apps.shared.services.model_routing_global_profile_catalog import (
    CATALOG_SOURCE,
    canonical_model_routing_id,
    catalog_metadata_for_model_id,
)
from apps.workflow_engine.services.llm_service import (
    LLMService,
)
from apps.workflow_engine.services.model_routing_bootstrap import (
    downstream_contract_from_graph,
)
from apps.workflow_engine.services.model_routing_learner_store import (
    ModelRoutingLearnerStore,
)
from apps.workflow_engine.services.model_routing_learning_batch import (
    ModelRoutingLearningBatchService,
)
from apps.workflow_engine.services.model_routing_policy_store import (
    ModelRoutingPolicyStore,
)
from apps.workflow_engine.services.model_routing_runtime_judge import (
    ModelRoutingRuntimeJudge,
)
from scripts.model_routing_benchmark_cases_v22 import V22_HOLDOUT_CASE_POOLS


ORG_ID = uuid.UUID("10200000-0000-0000-0000-000000000100")
USER_ID = uuid.UUID("10200000-0000-0000-0000-000000000001")
APP_ID = uuid.UUID("98000000-0000-0000-0000-000000000001")
WORKFLOW_ID = uuid.UUID("98000000-0000-0000-0000-000000000002")
AUTO_DEPLOYMENT_ID = uuid.UUID("98000000-0000-0000-0000-000000000003")
HIGH_DEPLOYMENT_ID = uuid.UUID("98000000-0000-0000-0000-000000000004")
LOW_DEPLOYMENT_ID = uuid.UUID("98000000-0000-0000-0000-000000000005")
MID_DEPLOYMENT_ID = uuid.UUID("98000000-0000-0000-0000-000000000006")
NAMESPACE = uuid.UUID("98000000-0000-0000-0000-000000000100")
NODE_ID = "llm-triage"

AUTO_ARM = "automatic"
MID_ARM = "mid_fixed"
HIGH_ARM = "high_fixed"
LOW_ARM = "low_fixed"
LEARNING_PHASE = "learning"
BENCHMARK_PHASE = "benchmark"
LEARNING_CASE_COUNT = 100
BENCHMARK_CASE_COUNT = 20
# 현재 실행 단계가 사용하는 arm 목록이다. main에서 phase plan 기준으로 바꾼다.
ARMS = (AUTO_ARM, MID_ARM, HIGH_ARM, LOW_ARM)
MID_MODEL = "gpt-5.4-mini"
HIGH_MODEL = "gpt-5.6-sol"
LOW_MODEL = "gpt-4o-mini"
ROUTING_JUDGE_MODEL = "gpt-5.4-mini"
# 품질 평가는 라우팅 Judge와 분리한다. 라우팅 Judge만 바꿔도 동일한 품질 평가
# 기준으로 결과를 비교할 수 있어야 한다.
QUALITY_JUDGE_MODEL = "gpt-5-mini"
LOCAL_CONFIDENCE_THRESHOLD = 0.78
QUALITY_JUDGE_MAX_ATTEMPTS = 3
QUALITY_JUDGE_REQUEST_TIMEOUT_SECONDS = 45


MODEL_EXPECTATIONS_BY_DIFFICULTY: dict[str, dict[str, tuple[str, ...]]] = {
    "economy": {
        "acceptable": ("gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini", "gpt-5.6-luna"),
        "underpowered": (),
        "overprovisioned": ("gpt-5.4", "gpt-5.6-sol", "o3"),
    },
    "balanced": {
        "acceptable": ("gpt-4.1-mini", "gpt-4o", "gpt-5-mini", "gpt-5.4-mini", "gpt-5.6-terra"),
        "underpowered": ("gpt-4o-mini", "gpt-5.6-luna"),
        "overprovisioned": ("gpt-5.6-sol", "o3"),
    },
    "advanced": {
        "acceptable": ("gpt-5.4", "gpt-5.6-sol", "o3"),
        "underpowered": ("gpt-4o-mini", "gpt-4.1-mini", "gpt-5.6-luna"),
        "overprovisioned": (),
    },
}


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    category: str
    expected_difficulty: str
    customer_tier: str
    message: str
    context: str
    constraints: tuple[str, ...]
    output_mode: str
    acceptable_model_ids: tuple[str, ...]
    underpowered_model_ids: tuple[str, ...]
    overprovisioned_model_ids: tuple[str, ...]


@dataclass
class ArmResult:
    arm: str
    selected_model: str | None
    task_cost_usd: float
    task_latency_ms: int | None
    workflow_latency_ms: int | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    output_text: str
    schema_pass: bool
    workflow_success: bool
    error: str | None
    routing: dict[str, Any]
    routing_judge_cost_usd: float = 0.0
    routing_judge_tokens: int = 0
    routing_judge_latency_ms: int | None = None


@dataclass(frozen=True)
class ExperimentPhasePlan:
    phase: str
    cases: tuple[ExperimentCase, ...]
    arms: tuple[str, ...]
    evaluate_quality: bool
    reset_learning: bool
    requires_ready_learner: bool
    use_policy_preview: bool


EXTRA_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("service_reliability", "balanced", "enterprise", "배포 직후 일부 고객만 주문 조회 API에서 502를 받고 있습니다. 최근 설정 변경과 지역별 트래픽 차이를 함께 확인해 우선 조치를 정리해 주세요."),
    ("service_reliability", "advanced", "enterprise", "결제 승인 지연과 메시지 큐 적체가 동시에 발생했습니다. 데이터 정합성을 훼손하지 않는 복구 순서와 고객 공지 초안을 제안해 주세요."),
    ("service_reliability", "balanced", "business", "주간 백업은 성공으로 표시됐지만 복구 점검 결과 일부 첨부 파일이 누락됐습니다. 영향 범위를 확인하기 위한 절차가 필요합니다."),
    ("service_reliability", "advanced", "enterprise", "두 리전에 걸친 장애 때문에 SLA 위반 가능성이 있습니다. 보상 승인 전에 반드시 확인해야 할 사실과 임시 고객 안내를 분리해 주세요."),
    ("data_governance", "advanced", "enterprise", "삭제 요청을 받은 고객의 데이터가 분석용 집계와 백업 보존 정책에 동시에 남아 있습니다. 법무 검토가 필요한 지점과 처리 순서를 정리해 주세요."),
    ("data_governance", "balanced", "business", "새로운 데이터 보존 기간 정책이 적용됐는데 기존 보고서 다운로드가 가능한지 운영팀이 문의했습니다. 확인할 항목을 안내해 주세요."),
    ("data_governance", "advanced", "enterprise", "외부 분석 도구에 전송되는 이벤트에서 식별자 일부가 가명 처리되지 않은 정황이 있습니다. 즉시 차단 여부와 조사 계획을 제시해 주세요."),
    ("data_governance", "balanced", "business", "월간 사용량 CSV를 내려받을 때 팀별 합계와 전체 합계가 다릅니다. 고객 답변 전에 확인할 데이터 검증 절차를 알려 주세요."),
    ("contract_compliance", "advanced", "enterprise", "계약서에는 EU 내 처리만 허용돼 있는데 지원 요청 해결을 위해 미국 리전 로그를 조회해야 할 수 있습니다. 가능한 대응 범위와 승인 필요 여부를 판단해 주세요."),
    ("contract_compliance", "advanced", "enterprise", "고객이 사고 보상과 서비스 크레딧을 동시에 요구했습니다. 원인 확정 전 약속하면 안 되는 표현을 피하면서 회신 초안을 작성해 주세요."),
    ("contract_compliance", "balanced", "business", "계약 갱신일이 다가오는데 현재 사용량이 약정 한도를 넘었는지 어디에서 확인하는지 안내해 주세요."),
    ("contract_compliance", "advanced", "enterprise", "개인정보 처리 위탁사 변경과 관련해 고객별 동의 상태가 다릅니다. 일괄 전환 전에 어떤 위험을 검토해야 하는지 정리해 주세요."),
    ("analytics_reporting", "balanced", "business", "경영 대시보드의 이번 달 활성 사용자 수가 지난주 보고서보다 작습니다. 지표 정의 변경 여부를 먼저 확인하는 점검 순서를 알려 주세요."),
    ("analytics_reporting", "advanced", "enterprise", "분기별 매출 분석에서 환불·크레딧·환율 조정이 서로 다른 기준일로 반영된 것 같습니다. 숫자를 확정하기 전 검증 계획을 작성해 주세요."),
    ("analytics_reporting", "balanced", "business", "팀장이 이번 주 실행 실패율만 빠르게 확인하고 싶어 합니다. 화면에서 확인할 위치와 해석 방법을 짧게 안내해 주세요."),
    ("analytics_reporting", "advanced", "enterprise", "보안 감사용 보고서에 접근 권한 변경, credential 사용, 배포 이력을 하나의 타임라인으로 제출해야 합니다. 누락 위험이 큰 항목을 우선순위로 정리해 주세요."),
    ("integration_support", "balanced", "business", "웹훅 요청은 200을 받았는데 후속 워크플로우가 실행되지 않습니다. 고객에게 요청할 재현 정보와 내부 확인 순서를 안내해 주세요."),
    ("integration_support", "advanced", "enterprise", "파트너 연동이 중복 결제를 유발했을 가능성이 있습니다. 재시도 로그, idempotency key, 정산 상태를 어떤 순서로 조사해야 하는지 작성해 주세요."),
    ("integration_support", "balanced", "business", "Slack 알림은 오는데 담당자 멘션이 빠집니다. 사용자가 바로 확인할 수 있는 설정 항목을 간단히 설명해 주세요."),
    ("integration_support", "advanced", "enterprise", "서로 다른 고객사의 OAuth 연결이 같은 서비스 계정을 공유한 정황이 있습니다. 토큰 회수와 서비스 영향 최소화를 함께 고려한 조치안을 제시해 주세요."),
    ("algorithmic_reasoning", "advanced", "enterprise", "세 서비스 A, B, C의 배포 순서에는 A 이전 DB migration, B와 C의 동시 배포 금지, C 이전 A health check 통과 조건이 있습니다. 가능한 순서를 검증하고 실패 시 되돌리는 절차를 JSON으로 정리해 주세요."),
    ("concurrency_code_review", "advanced", "enterprise", "다음 의사코드는 잔액을 읽고 차감한 뒤 저장합니다. 동시에 두 요청이 오면 잔액이 음수가 될 수 있습니다. 경쟁 조건의 원인과 트랜잭션 또는 낙관적 잠금으로 고치는 방법을 설명해 주세요."),
    ("formal_policy_reasoning", "advanced", "enterprise", "규정상 EU 고객 데이터는 EU 리전에서만 처리해야 하지만 장애 대응을 위해 미국 리전 로그를 30분 조회해야 할 수 있습니다. 사실 확인, 승인 조건, 금지되는 조치를 구분한 결정을 제시해 주세요."),
    ("data_reconciliation", "balanced", "business", "주문 120건, 승인 118건, 취소 3건, 환불 2건이라는 집계가 있습니다. 서로 동시에 성립할 수 있는지 먼저 검산하고, 불일치가 있으면 확인 순서를 작성해 주세요."),
    ("refund_intent", "economy", "business", "환불 정책과 신청 가능한 기간을 설명해 주세요. 실제 환불 처리는 하지 마세요."),
    ("refund_intent", "advanced", "enterprise", "고객의 결제 건을 확인하고 환불을 지금 승인해 주세요. 승인 결과를 고객에게 전송해야 합니다."),
    ("product_guidance", "economy", "startup", "실행 로그에서 성공한 결과만 필터링하는 메뉴가 어디인지 알려 주세요."),
    ("product_guidance", "economy", "business", "LLM credential 동기화 상태가 실패로 보일 때 가장 먼저 무엇을 확인하나요?"),
    ("risk_triage", "advanced", "enterprise", "의심스러운 파일이 지식 베이스에 업로드된 뒤 여러 워크플로우가 해당 문서를 참조했습니다. 실행 중지 여부, 영향 범위, 고객 공지를 동시에 결정해야 합니다."),
    ("risk_triage", "advanced", "enterprise", "한 고객의 데이터 삭제 요청과 법적 보존 명령이 충돌합니다. 자동 삭제를 중단해야 하는지와 담당 부서 승인 흐름을 정리해 주세요."),
    ("risk_triage", "balanced", "business", "계정 권한 변경 요청이 들어왔지만 요청자가 팀 리더인지 확인되지 않습니다. 필요한 확인 정보와 보류 안내를 작성해 주세요."),
    ("risk_triage", "advanced", "enterprise", "생산 환경에서 비정상적으로 많은 API 키가 발급됐고 같은 시간대에 대량 데이터 다운로드도 있었습니다. 즉시 대응과 증거 보존을 구분해 주세요."),
)


LEARNING_EXTENSION_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("customer_operations", "economy", "business", "지난주에 종료한 자동화 실행의 결과 파일을 다시 내려받는 위치를 알려 주세요."),
    ("customer_operations", "balanced", "enterprise", "같은 문의가 여러 팀으로 중복 배정됐습니다. 기존 담당 기록을 보존하면서 하나의 처리 건으로 정리하는 절차를 제안해 주세요."),
    ("customer_operations", "advanced", "enterprise", "고객에게 이미 잘못 안내된 환불 금액을 정정해야 합니다. 추가 피해를 막는 순서와 승인받아야 할 결정을 구분해 주세요."),
    ("release_operations", "economy", "business", "게시한 모듈의 설명 문구만 수정했을 때 사용자에게 언제 반영되는지 알려 주세요."),
    ("release_operations", "balanced", "business", "새 배포 뒤 일부 요청에서만 이전 프롬프트 결과가 나옵니다. 캐시와 배포 버전을 확인하는 순서를 정리해 주세요."),
    ("release_operations", "advanced", "enterprise", "배포 직후 결제 승인 workflow가 두 번 실행된 정황이 있습니다. 중복 부수효과를 멈추고 데이터 정합성을 확인할 계획을 제시해 주세요."),
    ("identity_operations", "economy", "business", "내 계정에 연결된 팀과 역할을 확인하는 화면 위치가 궁금합니다."),
    ("identity_operations", "balanced", "enterprise", "조직 이동한 직원이 새 팀 workflow는 수정하되 이전 팀 자료는 읽지 못하도록 권한 변경안을 작성해 주세요."),
    ("identity_operations", "advanced", "enterprise", "퇴사자 계정으로 운영 credential이 사용된 기록이 발견됐습니다. 즉시 차단과 영향 조사 순서를 제시해 주세요."),
    ("billing_operations", "economy", "business", "월별 모델 사용 비용을 워크플로우별로 확인하는 방법을 알려 주세요."),
    ("billing_operations", "balanced", "business", "예산 알림은 정상인데 실제 사용액이 한도를 넘었습니다. 집계 지연과 누락 로그를 확인할 항목을 정리해 주세요."),
    ("billing_operations", "advanced", "enterprise", "잘못 적용된 요금제로 여러 고객에게 과금됐습니다. 환불 범위 산정과 회계 승인 전에 확인할 근거를 정리해 주세요."),
    ("data_operations", "economy", "business", "지식 베이스 문서의 마지막 동기화 시간을 어디에서 확인하는지 알려 주세요."),
    ("data_operations", "balanced", "enterprise", "같은 문서의 두 버전이 검색 결과에 함께 노출됩니다. 최신 버전을 유지하면서 출처 기록을 보존하는 방법을 제안해 주세요."),
    ("data_operations", "advanced", "enterprise", "삭제 요청 데이터가 검색 인덱스와 백업에는 남아 있습니다. 보존 의무와 삭제 권리를 함께 고려한 처리 순서를 제시해 주세요."),
    ("incident_response", "economy", "business", "실패한 webhook 요청의 재시도 횟수를 확인하는 방법을 알려 주세요."),
    ("incident_response", "balanced", "business", "외부 API 지연으로 workflow가 간헐적으로 timeout 됩니다. 재시도와 fallback을 조정하기 전에 확인할 지표를 알려 주세요."),
    ("incident_response", "advanced", "enterprise", "여러 리전에서 인증 실패율이 동시에 증가하고 관리자 로그 일부가 누락됐습니다. 고객 영향과 보안 사고 가능성을 나눠 조사해 주세요."),
    ("compliance_operations", "balanced", "business", "감사 보고서에 사용자 이름 대신 식별자를 표시하려 합니다. 추적성을 유지하면서 노출을 줄이는 방법을 알려 주세요."),
    ("compliance_operations", "advanced", "enterprise", "법적 보존 대상 로그와 고객 삭제 요청이 충돌합니다. 자동 삭제를 중단할 범위와 검토 주체를 제안해 주세요."),
)


BENCHMARK_HOLDOUT_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("holdout_product_help", "economy", "business", "실행 비교 화면에서 기준 실행을 바꾸는 방법을 알려 주세요."),
    ("holdout_product_help", "economy", "business", "모듈 목록에서 오류가 난 실행만 찾아보려면 어떤 필터를 써야 하나요?"),
    ("holdout_product_help", "economy", "business", "등록한 지식 문서의 처리 완료 여부를 확인하고 싶습니다."),
    ("holdout_product_help", "economy", "business", "워크플로우 노드의 입력 변수 이름을 수정한 뒤 저장하는 절차가 궁금합니다."),
    ("holdout_access", "balanced", "enterprise", "감사 담당자에게 이번 달 실행 로그만 읽을 수 있는 임시 권한을 부여하려 합니다."),
    ("holdout_access", "balanced", "business", "프로젝트가 끝난 외부 사용자의 팀 접근을 회수하되 기존 감사 기록은 유지해 주세요."),
    ("holdout_access", "balanced", "enterprise", "서비스 계정이 배포는 실행할 수 있지만 credential 설정은 바꾸지 못하도록 권한을 나누고 싶습니다."),
    ("holdout_access", "advanced", "enterprise", "권한 회수 후에도 이전 토큰으로 관리자 API 호출이 성공했습니다. 차단과 조사 순서를 제시해 주세요."),
    ("holdout_finance", "balanced", "business", "사용량 보고서와 청구서의 토큰 합계가 다릅니다. 고객에게 답하기 전 확인할 자료를 정리해 주세요."),
    ("holdout_finance", "advanced", "enterprise", "중복 청구된 구독료를 여러 법인에 환불해야 합니다. 승인 범위와 회계 반영 순서를 제안해 주세요."),
    ("holdout_finance", "advanced", "enterprise", "SLA 위반 보상액이 계약별 상한을 넘을 수 있습니다. 고객 안내 전에 필요한 판단 근거를 정리해 주세요."),
    ("holdout_finance", "economy", "business", "이번 달 모델별 비용 합계를 내려받는 위치를 알려 주세요."),
    ("holdout_security", "advanced", "enterprise", "운영 로그에 API 키 일부가 노출됐고 외부 접근 흔적도 있습니다. 즉시 조치와 사후 조사 단계를 나눠 주세요."),
    ("holdout_security", "advanced", "enterprise", "고객 문서가 인증 없이 검색되는 링크를 발견했습니다. 공개 차단과 영향 통지 판단에 필요한 사실을 정리해 주세요."),
    ("holdout_security", "balanced", "business", "MFA 재등록 요청이 본인 요청인지 확인하기 위한 안전한 절차를 알려 주세요."),
    ("holdout_reliability", "balanced", "business", "특정 시간대에만 webhook 재시도가 늘어납니다. 원인을 좁힐 지표와 확인 순서를 제안해 주세요."),
    ("holdout_reliability", "advanced", "enterprise", "장애 복구 중 동일 주문이 두 번 처리될 가능성이 있습니다. 데이터 손상을 막는 복구 순서를 정리해 주세요."),
    ("holdout_data", "balanced", "business", "검색 결과에 폐기된 문서 조각이 섞여 있습니다. 현재 문서만 사용하도록 확인할 항목을 알려 주세요."),
    ("holdout_data", "advanced", "enterprise", "개인정보 삭제가 완료됐지만 분석용 파생 데이터에서 다시 식별될 가능성이 있습니다. 검증과 대응 방안을 제시해 주세요."),
    ("holdout_governance", "balanced", "enterprise", "AI 답변이 내부 정책과 다를 때 자동 발송을 막고 사람 검토로 넘기는 기준을 설계해 주세요."),
)


def _case_contract(category: str, difficulty: str) -> tuple[str, tuple[str, ...], str]:
    """각 요청이 동일한 무RAG workflow 계약으로 실행되도록 입력 부가 정보를 만든다."""

    context_by_category = {
        "algorithmic_reasoning": "제공된 조건만 사용하고, 누락된 전제는 가정으로 분리해야 합니다.",
        "concurrency_code_review": "외부 코드 실행 없이 의사코드와 제약만 분석합니다.",
        "formal_policy_reasoning": "정책 문서 원문은 제공하지 않으며, 요청에 드러난 사실만 사용합니다.",
        "data_reconciliation": "집계 숫자는 입력값일 뿐, 서로 일치한다는 보장은 없습니다.",
    }
    context = context_by_category.get(
        category,
        "지식 베이스 검색이나 외부 문서 조회 없이, 요청에 포함된 정보만 사용합니다.",
    )
    constraints = [
        "근거 없는 사실을 확정하지 않습니다.",
        "누락된 정보는 assumptions에 분리합니다.",
        "필수 JSON 필드를 모두 반환합니다.",
    ]
    if difficulty == "advanced":
        constraints.append("위험하거나 되돌리기 어려운 조치는 승인 전제로 보수적으로 제안합니다.")
    output_mode = "analysis" if difficulty == "advanced" else "checklist"
    return context, tuple(constraints), output_mode


def _model_expectations(category: str, difficulty: str) -> dict[str, tuple[str, ...]]:
    expected = dict(MODEL_EXPECTATIONS_BY_DIFFICULTY[difficulty])
    if category in {"algorithmic_reasoning", "concurrency_code_review", "formal_policy_reasoning"}:
        expected["acceptable"] = ("o3", "gpt-5.6-sol", "gpt-5.4")
        expected["underpowered"] = ("gpt-4o-mini", "gpt-4.1-mini", "gpt-5.6-luna")
    return expected


def _experiment_case(
    *,
    case_id: str,
    category: str,
    difficulty: str,
    customer_tier: str,
    message: str,
) -> ExperimentCase:
    context, constraints, output_mode = _case_contract(category, difficulty)
    expectations = _model_expectations(category, difficulty)
    return ExperimentCase(
        case_id=case_id,
        category=category,
        expected_difficulty=difficulty,
        customer_tier=customer_tier,
        message=message,
        context=context,
        constraints=constraints,
        output_mode=output_mode,
        acceptable_model_ids=expectations["acceptable"],
        underpowered_model_ids=expectations["underpowered"],
        overprovisioned_model_ids=expectations["overprovisioned"],
    )


def _base_learning_cases() -> list[ExperimentCase]:
    """기존 80개 데이터셋을 학습 표본으로 유지한다."""

    pool_specs = (
        ("routine_usage_guidance", "economy"),
        ("account_access_request", "balanced"),
        ("finance_closing_approval", "advanced"),
        ("security_privacy_incident", "advanced"),
    )
    cases: list[ExperimentCase] = []
    for category, difficulty in pool_specs:
        for message, _team, role in V22_HOLDOUT_CASE_POOLS[category]:
            cases.append(
                _experiment_case(
                    case_id=f"{category}-{len(cases) + 1:02d}",
                    category=category,
                    difficulty=difficulty,
                    customer_tier=("enterprise" if difficulty == "advanced" else "business"),
                    message=message,
                )
            )
    for category, difficulty, customer_tier, message in EXTRA_CASES:
        cases.append(
            _experiment_case(
                case_id=f"{category}-{len(cases) + 1:02d}",
                category=category,
                difficulty=difficulty,
                customer_tier=customer_tier,
                message=message,
            )
        )
    if len(cases) != 80:
        raise AssertionError(f"expected 80 base learning cases, got {len(cases)}")
    return cases


def _cases_from_specs(
    specs: Iterable[tuple[str, str, str, str]],
    *,
    id_prefix: str,
) -> list[ExperimentCase]:
    return [
        _experiment_case(
            case_id=f"{id_prefix}-{index:03d}",
            category=category,
            difficulty=difficulty,
            customer_tier=customer_tier,
            message=message,
        )
        for index, (category, difficulty, customer_tier, message) in enumerate(
            specs,
            start=1,
        )
    ]


def build_learning_cases() -> list[ExperimentCase]:
    """로컬 학습 50건과 독립 검증 50건에 사용할 고정 100건."""

    cases = [
        *_base_learning_cases(),
        *_cases_from_specs(
            LEARNING_EXTENSION_CASES,
            id_prefix="learning-extension",
        ),
    ]
    if len(cases) != LEARNING_CASE_COUNT:
        raise AssertionError(
            f"expected {LEARNING_CASE_COUNT} learning cases, got {len(cases)}"
        )
    random.Random(20260717).shuffle(cases)
    # 같은 주제의 설명 요청과 실제 상태 변경 요청을 30건 smoke에서도 반드시
    # 비교해 decision_impact가 단순 의미 유사도에 끌려가지 않는지 확인한다.
    cases.sort(key=lambda case: 0 if case.category == "refund_intent" else 1)
    return cases


def build_benchmark_cases() -> list[ExperimentCase]:
    """학습에 노출하지 않고 세 arm에 똑같이 전달할 고정 holdout 20건."""

    cases = _cases_from_specs(
        BENCHMARK_HOLDOUT_CASES,
        id_prefix="benchmark-holdout",
    )
    if len(cases) != BENCHMARK_CASE_COUNT:
        raise AssertionError(
            f"expected {BENCHMARK_CASE_COUNT} benchmark cases, got {len(cases)}"
        )
    random.Random(20260722).shuffle(cases)
    return cases


def build_cases() -> list[ExperimentCase]:
    """실험 manifest 전체 120건. 실행 단계에서는 두 집합을 섞지 않는다."""

    return [*build_learning_cases(), *build_benchmark_cases()]


def build_phase_plan(phase: str) -> ExperimentPhasePlan:
    normalized = str(phase or "").strip().lower()
    if normalized == LEARNING_PHASE:
        return ExperimentPhasePlan(
            phase=LEARNING_PHASE,
            cases=tuple(build_learning_cases()),
            arms=(AUTO_ARM,),
            evaluate_quality=False,
            reset_learning=True,
            requires_ready_learner=False,
            use_policy_preview=False,
        )
    if normalized == BENCHMARK_PHASE:
        return ExperimentPhasePlan(
            phase=BENCHMARK_PHASE,
            cases=tuple(build_benchmark_cases()),
            arms=(AUTO_ARM, MID_ARM, HIGH_ARM),
            evaluate_quality=True,
            reset_learning=False,
            requires_ready_learner=True,
            use_policy_preview=True,
        )
    raise ValueError(f"알 수 없는 실험 단계입니다: {phase}")


def benchmark_readiness_error(
    learning_state: dict[str, Any],
    *,
    completed_learning_case_count: int,
) -> str | None:
    """학습되지 않은 자동 arm으로 경제성 비교를 시작하지 않게 한다."""

    if completed_learning_case_count < LEARNING_CASE_COUNT:
        return "incomplete_learning_phase"
    if learning_state.get("active_version") is None:
        return "active_learner_version_missing"
    return None


def _completed_learning_case_count(benchmark_output_dir: pathlib.Path) -> int:
    """같은 run 폴더의 1단계 보고서에서 완료 요청 수를 읽는다."""

    learning_result_path = benchmark_output_dir.parent / LEARNING_PHASE / "result.json"
    if not learning_result_path.exists():
        return 0
    payload = json.loads(learning_result_path.read_text(encoding="utf-8"))
    if payload.get("phase") != LEARNING_PHASE:
        return 0
    return int(payload.get("case_count") or 0)


def _arm_execution_order(case_id: str) -> tuple[str, ...]:
    """같은 case는 재개해도 같은 arm 순서를 쓰고, case 간 순서는 섞는다."""

    order = list(ARMS)
    random.Random(f"judge-first-economics-arm-order:{case_id}").shuffle(order)
    return tuple(order)


def _append_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    """Provider 비용이 발생한 직후 결과를 복구 가능한 단위로 남긴다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _routing_accuracy(case: ExperimentCase, selected_model: str | None) -> dict[str, Any]:
    model_id = str(selected_model or "")
    canonical_model_id = canonical_model_routing_id(model_id)
    capability = catalog_metadata_for_model_id(model_id)
    ceiling_rank = {
        "routine": 1,
        "multi_constraint": 2,
        "complex_professional": 3,
    }
    required_rank = {"economy": 1, "balanced": 2, "advanced": 3}.get(
        case.expected_difficulty
    )
    model_rank = ceiling_rank.get(str(capability.get("complexity_ceiling") or ""))
    acceptable = {canonical_model_routing_id(item) for item in case.acceptable_model_ids}
    underpowered = {
        canonical_model_routing_id(item) for item in case.underpowered_model_ids
    }
    overprovisioned = {
        canonical_model_routing_id(item) for item in case.overprovisioned_model_ids
    }
    if model_rank is not None and required_rank is not None:
        if model_rank < required_rank:
            classification = "underpowered"
        elif (
            model_rank > required_rank
            and str(capability.get("cost_position") or "") == "premium"
        ):
            classification = "overprovisioned"
        else:
            classification = "appropriate"
    elif canonical_model_id in acceptable:
        classification = "appropriate"
    elif canonical_model_id in underpowered:
        classification = "underpowered"
    elif canonical_model_id in overprovisioned:
        classification = "overprovisioned"
    else:
        classification = "unclassified"
    return {
        "classification": classification,
        "selected_model": model_id or None,
        "canonical_model_id": canonical_model_id or None,
        "capability_source": CATALOG_SOURCE if capability else None,
        "capability_tier": capability.get("capability_tier"),
        "complexity_ceiling": capability.get("complexity_ceiling"),
        "acceptable_model_ids": list(case.acceptable_model_ids),
        "underpowered_model_ids": list(case.underpowered_model_ids),
        "overprovisioned_model_ids": list(case.overprovisioned_model_ids),
    }


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        start = str(text or "").find("{")
        end = str(text or "").rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(str(text)[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage(row: LLMUsageLog | None) -> tuple[int, int, int, float, int | None]:
    if row is None:
        return 0, 0, 0, 0.0, None
    prompt_tokens = int(row.prompt_tokens or 0)
    completion_tokens = int(row.completion_tokens or 0)
    return (
        prompt_tokens,
        completion_tokens,
        prompt_tokens + completion_tokens,
        float(row.total_cost or 0),
        int(row.latency_ms) if row.latency_ms is not None else None,
    )


def _schema_pass(text: str) -> bool:
    payload = _json_from_text(text)
    return (
        isinstance(payload.get("분류"), str)
        and isinstance(payload.get("우선순위"), str)
        and isinstance(payload.get("승인필요"), bool)
        and isinstance(payload.get("대응계획"), list)
        and isinstance(payload.get("답변초안"), str)
    )


def _node_data(*, auto_routing: bool, model_id: str, fallback_model_id: str | None) -> dict[str, Any]:
    return {
        "provider": "openai",
        "model_id": model_id,
        "fallback_model_id": fallback_model_id,
        "auto_model_routing": auto_routing,
        "model_routing_policy": {
            # 이번 실험은 정책 refresh가 아니라 Judge label을 받은 local router의
            # 전환 시점을 측정한다. 80회 안에 별도 refresh가 정책을 교체하지 않도록
            # refresh 주기를 실험 범위 밖으로 둔다.
            "refresh": {"refresh_every_runs": 100},
        },
        "system_prompt": (
            "당신은 기업용 AI 워크플로우의 범용 요청 처리 노드입니다. 요청, 제공된 문맥, 제약을 "
            "함께 검토하고 필요한 판단·계획·답변을 작성하세요. 반드시 JSON object 하나만 반환하세요. "
            "필수 필드는 분류(string), 우선순위(low|medium|high|critical), 승인필요(boolean), "
            "대응계획(string 배열), 답변초안(string)입니다. 보안, 개인정보, 보상, 법무, 결제, "
            "장애는 사실이 불명확하면 보수적으로 설명하되 근거 없는 확정 약속은 하지 마세요."
        ),
        "user_prompt": (
            "요청자 등급: {{ customerTier }}\n요청: {{ request }}\n"
            "제공 문맥: {{ context }}\n제약: {{ constraints }}\n출력 모드: {{ outputMode }}"
        ),
        "referenced_variables": [
            {"name": "customerTier", "value_selector": ["webhook-ticket", "customerTier"]},
            {"name": "request", "value_selector": ["webhook-ticket", "request"]},
            {"name": "context", "value_selector": ["webhook-ticket", "context"]},
            {"name": "constraints", "value_selector": ["webhook-ticket", "constraints"]},
            {"name": "outputMode", "value_selector": ["webhook-ticket", "outputMode"]},
        ],
        "knowledgeBases": [],
        "parameters": {
            "temperature": 0,
            # gpt-5.4 계열은 reasoning token도 출력 한도에 포함한다. 420은
            # 실제 JSON을 만들기 전에 incomplete가 될 수 있어 모든 arm에 같은
            # 충분한 상한을 준다. 짧은 응답은 모델이 스스로 일찍 끝낸다.
            "max_tokens": 1400,
            "response_format": {"type": "json_object"},
        },
        "output_format": {
            "type": "json",
            "schema": {
                "type": "object",
                "properties": {
                    "분류": {"type": "string"},
                    "우선순위": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "승인필요": {"type": "boolean"},
                    "대응계획": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "답변초안": {"type": "string"},
                },
                "required": [
                    "분류",
                    "우선순위",
                    "승인필요",
                    "대응계획",
                    "답변초안",
                ],
            },
        },
    }


def graph_for_arm(arm: str) -> dict[str, Any]:
    graph = copy.deepcopy(_ticket_ops_graph())
    for node in graph["nodes"]:
        if node["id"] == "webhook-ticket":
            node["data"].update(
                {
                    "title": "무RAG 기업 요청 수신",
                    "description": "범용 기업 요청 payload를 수신합니다.",
                    "variable_mappings": [
                        {"json_path": "customerTier", "variable_name": "customerTier"},
                        {"json_path": "request", "variable_name": "request"},
                        {"json_path": "context", "variable_name": "context"},
                        {"json_path": "constraints", "variable_name": "constraints"},
                        {"json_path": "outputMode", "variable_name": "outputMode"},
                    ],
                }
            )
    for node in graph["nodes"]:
        if node["id"] == NODE_ID:
            if arm == AUTO_ARM:
                node["data"].update(
                    _node_data(
                        auto_routing=True,
                        model_id=ROUTING_JUDGE_MODEL,
                        fallback_model_id="gpt-4.1",
                    )
                )
            elif arm == HIGH_ARM:
                node["data"].update(
                    _node_data(auto_routing=False, model_id=HIGH_MODEL, fallback_model_id=None)
                )
            elif arm == MID_ARM:
                node["data"].update(
                    _node_data(auto_routing=False, model_id=MID_MODEL, fallback_model_id=None)
                )
            else:
                node["data"].update(
                    _node_data(auto_routing=False, model_id=LOW_MODEL, fallback_model_id=None)
                )
            node["data"]["title"] = "무RAG 기업 요청 처리"
            node["data"]["description"] = "다양한 기업 요청을 같은 JSON 계약으로 처리합니다."
    # 기존 demo extractor가 실험 output 계약과 일치하도록 맞춘다.
    for node in graph["nodes"]:
        if node["id"] == "extract-ticket":
            node["data"]["mappings"] = [
                {"name": "approvalRequired", "json_path": "승인필요"},
                {"name": "mailDraft", "json_path": "답변초안"},
            ]
    return graph


def _deployment_id_for(arm: str) -> uuid.UUID:
    return {
        AUTO_ARM: AUTO_DEPLOYMENT_ID,
        MID_ARM: MID_DEPLOYMENT_ID,
        HIGH_ARM: HIGH_DEPLOYMENT_ID,
        LOW_ARM: LOW_DEPLOYMENT_ID,
    }[arm]


def _run_id(arm: str, case_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"judge-first-economics:{arm}:{case_id}")


def _ensure_runtime_models(
    db,
    *,
    include_quality_judge: bool,
) -> list[str]:
    available = LLMService.get_runtime_available_model_ids_for_user(
        db, user_id=USER_ID, organization_id=ORG_ID
    )
    wanted = [
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4o",
        "gpt-5-mini",
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "o3",
    ]
    selected = [model_id for model_id in wanted if model_id in available]
    fixed_model_by_arm = {
        MID_ARM: MID_MODEL,
        HIGH_ARM: HIGH_MODEL,
        LOW_ARM: LOW_MODEL,
    }
    required = {fixed_model_by_arm[arm] for arm in ARMS if arm in fixed_model_by_arm}
    if AUTO_ARM in ARMS:
        required.add(ROUTING_JUDGE_MODEL)
    if include_quality_judge:
        required.add(QUALITY_JUDGE_MODEL)
    missing = required - set(selected)
    if missing:
        raise RuntimeError(f"실험에 필요한 실행 가능 모델이 없습니다: {sorted(missing)}")
    for model_id in selected:
        LLMService.get_runtime_client_for_user(db, USER_ID, model_id, ORG_ID)
    return selected


def _upsert_workflow_and_deployments(
    db,
    available_models: list[str],
    *,
    reset_policy_state: bool,
) -> None:
    auto_graph = graph_for_arm(AUTO_ARM)
    app = db.get(App, APP_ID)
    if app is None:
        app = App(
            id=APP_ID,
            organization_id=ORG_ID,
            name="무RAG 자동 모델 라우팅 블라인드 경제성 실험",
            description="동일한 무RAG 기업 요청 workflow를 자동·고가·중가·저가 고정 방식으로 비교합니다.",
            icon={"type": "emoji", "content": "🧪", "background_color": "#E0F2FE"},
            url_slug="judge-first-routing-economics-80",
            auth_secret="experiment-routing-economics-secret",
            is_api_enabled=True,
            api_req_per_minute=600,
            api_req_per_hour=3600,
            is_market=False,
            created_by=USER_ID,
        )
        db.add(app)
        db.flush()

    workflow = db.get(Workflow, WORKFLOW_ID)
    if workflow is None:
        workflow = Workflow(
            id=WORKFLOW_ID,
            organization_id=ORG_ID,
            app_id=APP_ID,
            graph=auto_graph,
            features={},
            env_variables=[],
            runtime_variables=[],
            created_by=USER_ID,
            updated_by=USER_ID,
        )
        db.add(workflow)
    else:
        workflow.organization_id = ORG_ID
        workflow.app_id = APP_ID
        workflow.graph = auto_graph
        workflow.updated_by = USER_ID
        workflow.updated_at = datetime.now(timezone.utc)
    # apps.workflow_id는 workflows.id를 참조한다. 새 workflow를 먼저 flush하지
    # 않으면 PostgreSQL이 아직 없는 workflow를 가리키는 UPDATE를 거절한다.
    db.flush()
    app.workflow_id = WORKFLOW_ID

    for arm in ARMS:
        deployment_id = _deployment_id_for(arm)
        deployment = db.get(WorkflowDeployment, deployment_id)
        graph = graph_for_arm(arm)
        if deployment is None:
            deployment = WorkflowDeployment(
                id=deployment_id,
                app_id=APP_ID,
                version=1,
                type=DeploymentType.WEBHOOK,
                graph_snapshot=graph,
                config={"experiment": "judge-first-norag-blind-80", "arm": arm},
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                description=f"무RAG 2단계 블라인드 경제성 실험: {arm}",
                created_by=USER_ID,
                is_active=True,
            )
            db.add(deployment)
        else:
            deployment.app_id = APP_ID
            deployment.graph_snapshot = graph
            deployment.config = {"experiment": "judge-first-norag-blind-80", "arm": arm}
            deployment.is_active = True

    db.flush()
    app.active_deployment_id = AUTO_DEPLOYMENT_ID

    policy = (
        db.query(LLMNodeModelRoutingPolicy)
        .filter(LLMNodeModelRoutingPolicy.workflow_id == WORKFLOW_ID)
        .filter(LLMNodeModelRoutingPolicy.deployment_id == AUTO_DEPLOYMENT_ID)
        .filter(LLMNodeModelRoutingPolicy.node_id == NODE_ID)
        .first()
    )
    active_policy = {
        "strategy_id": "judge_bootstrap_incremental_v1",
        "default_model_id": ROUTING_JUDGE_MODEL,
        "fallback_model_id": "gpt-4.1",
        "judge_model_id": ROUTING_JUDGE_MODEL,
        "global_profile_catalog": {
            "candidates": [{"model_id": model_id} for model_id in available_models]
        },
    }
    if policy is None:
        policy = LLMNodeModelRoutingPolicy(
            organization_id=ORG_ID,
            workflow_id=WORKFLOW_ID,
            deployment_id=AUTO_DEPLOYMENT_ID,
            node_id=NODE_ID,
            enabled=True,
            status="active",
            policy_version="judge-first-economics-v1",
            active_policy=active_policy,
            refresh_every_runs=100,
            judge_user_id=USER_ID,
            execution_subject_user_id=USER_ID,
            validation_budget_usd=3,
        )
        db.add(policy)
    else:
        policy.enabled = True
        policy.status = "active"
        policy.judge_user_id = USER_ID
        policy.execution_subject_user_id = USER_ID
        if reset_policy_state:
            policy.policy_version = "judge-first-economics-v1"
            policy.active_policy = active_policy
            policy.eligible_runs_since_last_refresh = 0
            policy.refresh_requested_at = None
            policy.last_refresh_result = None

    auto_node = next(
        node for node in auto_graph["nodes"] if str(node.get("id")) == NODE_ID
    )
    learner = ModelRoutingLearnerStore.get_or_create(
        db,
        organization_id=ORG_ID,
        workflow_id=WORKFLOW_ID,
        node_id=NODE_ID,
        node_data=dict(auto_node.get("data") or {}),
        downstream_contract=downstream_contract_from_graph(auto_graph, NODE_ID),
    )
    learner_version = ModelRoutingLearnerStore.latest_version(
        db,
        learner_id=learner.id,
    )
    policy.learner_id = learner.id
    policy.active_learner_version_id = (
        learner_version.id if learner_version is not None else None
    )
    db.flush()


def _clear_prior_experiment_learning(db) -> None:
    """실험 workflow의 기존 학습 계보를 제거해 새 비교에 섞이지 않게 한다."""

    policies = (
        db.query(LLMNodeModelRoutingPolicy)
        .filter(LLMNodeModelRoutingPolicy.workflow_id == WORKFLOW_ID)
        .all()
    )
    for policy in policies:
        policy.learner_id = None
        policy.active_learner_version_id = None
    db.flush()
    (
        db.query(LLMNodeModelRoutingLearner)
        .filter(LLMNodeModelRoutingLearner.workflow_id == WORKFLOW_ID)
        .delete(synchronize_session=False)
    )
    db.flush()


def _clear_prior_experiment_runs(db) -> None:
    from apps.shared.db.models.workflow_run import WorkflowRun

    # 이 script가 소유한 고정 workflow/deployment의 policy evidence만 비운다.
    # policy row 자체는 _upsert_workflow_and_deployments가 재사용하지만, run event와
    # 성적 표본을 남기면 다음 batch가 과거 실험을 학습한 것처럼 보인다.
    policy_ids = [
        row[0]
        for row in db.query(LLMNodeModelRoutingPolicy.id)
        .filter(LLMNodeModelRoutingPolicy.workflow_id == WORKFLOW_ID)
        .all()
    ]
    if policy_ids:
        db.query(LLMNodeModelRoutingPolicyRunEvent).filter(
            LLMNodeModelRoutingPolicyRunEvent.policy_id.in_(policy_ids)
        ).delete(synchronize_session=False)
        db.query(LLMNodeModelRoutingPerformance).filter(
            LLMNodeModelRoutingPerformance.policy_id.in_(policy_ids)
        ).delete(synchronize_session=False)
        db.query(LLMNodeModelRoutingPolicyUpdate).filter(
            LLMNodeModelRoutingPolicyUpdate.policy_id.in_(policy_ids)
        ).delete(synchronize_session=False)

    run_ids = [
        row[0]
        for row in db.query(WorkflowRun.id)
        .filter(WorkflowRun.workflow_id == WORKFLOW_ID)
        .all()
    ]
    db.query(LLMUsageLog).filter(LLMUsageLog.workflow_id == WORKFLOW_ID).delete(
        synchronize_session=False
    )
    if run_ids:
        db.query(WorkflowNodeRun).filter(
            WorkflowNodeRun.workflow_run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).delete(
            synchronize_session=False
        )
    db.flush()


def _clear_case_runs(db, cases: list[ExperimentCase]) -> None:
    """재개하려는 batch의 중단된 run만 제거한다.

    run id는 case/arm 조합으로 결정적이다. process가 중간에 종료된 뒤 같은 batch를
    재시도하면 예전 started_at과 새 node log가 합쳐질 수 있으므로 재실행 전에 해당
    run만 비운다. 이전에 완료된 batch의 run과 학습 증거는 건드리지 않는다.
    """

    run_ids = [_run_id(arm, case.case_id) for case in cases for arm in ARMS]
    if not run_ids:
        return
    db.query(LLMUsageLog).filter(LLMUsageLog.workflow_run_id.in_(run_ids)).delete(
        synchronize_session=False
    )
    db.query(WorkflowNodeRun).filter(
        WorkflowNodeRun.workflow_run_id.in_(run_ids)
    ).delete(synchronize_session=False)
    db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).delete(
        synchronize_session=False
    )
    db.flush()


def _policy_checkpoint() -> dict[str, Any]:
    """완료 batch를 다시 실행할 수 있게 policy와 성적 누계를 안전한 JSON으로 저장한다."""

    db = SessionLocal()
    try:
        policy = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == AUTO_DEPLOYMENT_ID)
            .filter(LLMNodeModelRoutingPolicy.node_id == NODE_ID)
            .first()
        )
        if policy is None:
            return {}
        performances = (
            db.query(LLMNodeModelRoutingPerformance)
            .filter(LLMNodeModelRoutingPerformance.policy_id == policy.id)
            .all()
        )
        return {
            "policy": {
                "status": policy.status,
                "policy_version": policy.policy_version,
                "active_policy": copy.deepcopy(policy.active_policy or {}),
                "performance_checkpoint": copy.deepcopy(policy.performance_checkpoint or {}),
                "pending_policy": copy.deepcopy(policy.pending_policy),
                "refresh_every_runs": policy.refresh_every_runs,
                "eligible_runs_since_last_refresh": policy.eligible_runs_since_last_refresh,
                "refresh_requested_at": policy.refresh_requested_at.isoformat()
                if policy.refresh_requested_at
                else None,
                "last_refresh_result": policy.last_refresh_result,
            },
            "performances": [
                {
                    "model_id": row.model_id,
                    "input_profile": row.input_profile,
                    "run_count": row.run_count,
                    "success_count": row.success_count,
                    "schema_pass_count": row.schema_pass_count,
                    "schema_eval_count": row.schema_eval_count,
                    "downstream_success_count": row.downstream_success_count,
                    "downstream_eval_count": row.downstream_eval_count,
                    "fallback_count": row.fallback_count,
                    "retry_count": row.retry_count,
                    "total_cost": float(row.total_cost or 0),
                    "total_tokens": row.total_tokens,
                    "total_latency_ms": row.total_latency_ms,
                }
                for row in performances
            ],
        }
    finally:
        db.close()


def _restore_policy_checkpoint(db, checkpoint: dict[str, Any]) -> None:
    """이전 완료 batch의 policy state로 되돌려 중단 batch의 학습 오염을 제거한다."""

    snapshot = checkpoint.get("policy") if isinstance(checkpoint, dict) else None
    if not isinstance(snapshot, dict):
        raise RuntimeError("--resume 보고서에 policy checkpoint가 없습니다.")
    policy = (
        db.query(LLMNodeModelRoutingPolicy)
        .filter(LLMNodeModelRoutingPolicy.deployment_id == AUTO_DEPLOYMENT_ID)
        .filter(LLMNodeModelRoutingPolicy.node_id == NODE_ID)
        .first()
    )
    if policy is None:
        raise RuntimeError("resume 대상 자동 라우팅 policy가 없습니다.")
    policy.status = str(snapshot.get("status") or "active")
    policy.policy_version = snapshot.get("policy_version")
    policy.active_policy = copy.deepcopy(snapshot.get("active_policy") or {})
    policy.performance_checkpoint = copy.deepcopy(snapshot.get("performance_checkpoint") or {})
    policy.pending_policy = copy.deepcopy(snapshot.get("pending_policy"))
    policy.refresh_every_runs = int(snapshot.get("refresh_every_runs") or 100)
    policy.eligible_runs_since_last_refresh = int(
        snapshot.get("eligible_runs_since_last_refresh") or 0
    )
    # 이 실험에서는 refresh task를 의도적으로 실행하지 않는다.
    policy.refresh_requested_at = None
    policy.last_refresh_result = snapshot.get("last_refresh_result")

    db.query(LLMNodeModelRoutingPerformance).filter(
        LLMNodeModelRoutingPerformance.policy_id == policy.id
    ).delete(synchronize_session=False)
    for item in checkpoint.get("performances") or []:
        if not isinstance(item, dict):
            continue
        db.add(
            LLMNodeModelRoutingPerformance(
                policy_id=policy.id,
                model_id=str(item.get("model_id") or "unknown"),
                input_profile=str(item.get("input_profile") or "unknown"),
                run_count=int(item.get("run_count") or 0),
                success_count=int(item.get("success_count") or 0),
                schema_pass_count=int(item.get("schema_pass_count") or 0),
                schema_eval_count=int(item.get("schema_eval_count") or 0),
                downstream_success_count=int(item.get("downstream_success_count") or 0),
                downstream_eval_count=int(item.get("downstream_eval_count") or 0),
                fallback_count=int(item.get("fallback_count") or 0),
                retry_count=int(item.get("retry_count") or 0),
                total_cost=float(item.get("total_cost") or 0),
                total_tokens=int(item.get("total_tokens") or 0),
                total_latency_ms=int(item.get("total_latency_ms") or 0),
            )
        )
    db.flush()


@contextmanager
def synchronous_experiment_tasks():
    """실험 중 log task는 동기 반영하고 background policy refresh는 막는다."""

    original_send_task = celery_app.send_task
    task_map = {
        "log.create_run": log_tasks.create_run_log,
        "log.update_run_finish": log_tasks.update_run_log_finish,
        "log.update_run_error": log_tasks.update_run_log_error,
        "log.create_node": log_tasks.create_node_log,
        "log.update_node_finish": log_tasks.update_node_log_finish,
        "log.update_node_error": log_tasks.update_node_log_error,
    }

    def send_task(task_name, args=None, kwargs=None, **options):
        if task_name in task_map:
            value = task_map[task_name].run(*(args or []), **(kwargs or {}))
            return type("SyncTaskResult", (), {"get": lambda self, timeout=None: value})()
        if task_name.startswith("workflow.model_routing."):
            return type("SkippedTaskResult", (), {"get": lambda self, timeout=None: {"status": "skipped"}})()
        return original_send_task(task_name, args=args, kwargs=kwargs, **options)

    celery_app.send_task = send_task
    try:
        yield
    finally:
        celery_app.send_task = original_send_task


def _learning_batch_due(*, completed_case_count: int, total_case_count: int) -> bool:
    """운영과 같은 10건 batch를 쓰되 마지막 불완전 batch도 처리한다."""

    return completed_case_count % 10 == 0 or completed_case_count == total_case_count


def _train_automatic_learner() -> dict[str, Any]:
    """완료된 auto arm 라벨을 새 learner에 동기 학습한다."""

    db = SessionLocal()
    try:
        policy = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == AUTO_DEPLOYMENT_ID)
            .filter(LLMNodeModelRoutingPolicy.node_id == NODE_ID)
            .first()
        )
        if policy is None or policy.learner_id is None:
            raise RuntimeError("자동 라우팅 실험 policy에 learner가 연결되지 않았습니다.")

        processed_count = 0
        while True:
            result = ModelRoutingLearningBatchService.train_pending(
                db,
                learner_id=str(policy.learner_id),
                force=True,
            )
            processed_count += result.processed_count
            db.commit()
            if result.remaining_count <= 0 or result.processed_count <= 0:
                return {
                    "processed_count": processed_count,
                    "remaining_count": result.remaining_count,
                }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _execute_case(
    arm: str,
    case: ExperimentCase,
    *,
    include_in_learning: bool,
    use_policy_preview: bool,
) -> ArmResult:
    # 이 스크립트의 데이터셋/설정 검증은 root CI에서도 실행된다. gevent가
    # 필요한 실제 workflow 실행 엔진은 --execute 경로에서만 늦게 import한다.
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    graph = graph_for_arm(arm)
    run_id = _run_id(arm, case.case_id)
    execution_context = {
        "workflow_id": str(WORKFLOW_ID),
        "workflow_run_id": str(run_id),
        "app_id": str(APP_ID),
        "deployment_id": str(_deployment_id_for(arm)),
        "workflow_version": 1,
        "user_id": str(USER_ID),
        "organization_id": str(ORG_ID),
        "trigger_mode": "webhook",
        "execution_subject": {"subject_type": "user", "subject_id": str(USER_ID)},
    }
    if arm == AUTO_ARM and not include_in_learning and use_policy_preview:
        # 경제성 holdout은 검증된 learner version을 읽되 새 학습 label이나 운영
        # 성적을 만들지 않는다. benchmark 도중 라우터 자체가 바뀌면 세 arm 비교가
        # 동일한 정책 snapshot을 비교한 것이 아니게 된다.
        execution_context.update(
            {
                "routing_policy_preview": True,
                "routing_policy_preview_node_ids": [NODE_ID],
                "routing_policy_deployment_id": str(AUTO_DEPLOYMENT_ID),
                "routing_policy_deployment_node_ids": [NODE_ID],
            }
        )
    engine = WorkflowEngine(
        graph=graph,
        user_input={
            "customerTier": case.customer_tier,
            "request": case.message,
            "context": case.context,
            "constraints": list(case.constraints),
            "outputMode": case.output_mode,
        },
        execution_context=execution_context,
        is_deployed=True,
        workflow_timeout=120,
    )
    try:
        engine.execute()
    except Exception as exc:  # workflow error is a measured outcome, not a script abort.
        engine_error = f"{type(exc).__name__}: {exc}"
    else:
        engine_error = None
    finally:
        engine.cleanup()

    db = SessionLocal()
    try:
        # WorkflowEngine의 마지막 gevent log write가 execute() 반환 직후에
        # 완료될 수 있다. 저장 전 읽으면 실제 호출했어도 비용/모델이 0으로
        # 기록되므로, terminal node와 task usage가 보일 때까지 짧게 기다린다.
        workflow_run = None
        node_run = None
        task_usage = None
        for _attempt in range(30):
            db.expire_all()
            workflow_run = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.id == run_id)
                .first()
            )
            node_run = (
                db.query(WorkflowNodeRun)
                .filter(WorkflowNodeRun.workflow_run_id == run_id)
                .filter(WorkflowNodeRun.node_id == NODE_ID)
                .first()
            )
            task_usage = (
                db.query(LLMUsageLog)
                .filter(LLMUsageLog.workflow_run_id == run_id)
                .filter(LLMUsageLog.node_id == NODE_ID)
                .order_by(LLMUsageLog.created_at.desc())
                .first()
            )
            if node_run is not None and (task_usage is not None or engine_error is not None):
                break
            time.sleep(0.2)
        judge_usage = (
            db.query(LLMUsageLog)
            .filter(LLMUsageLog.workflow_run_id == run_id)
            .filter(LLMUsageLog.node_id == f"{NODE_ID}:routing_judge")
            .order_by(LLMUsageLog.created_at.desc())
            .first()
        )
        prompt_tokens, completion_tokens, total_tokens, task_cost, _usage_latency = _usage(task_usage)
        _jp, _jc, judge_tokens, judge_cost, judge_latency = _usage(judge_usage)
        outputs = node_run.outputs if node_run is not None and isinstance(node_run.outputs, dict) else {}
        output_text = str(outputs.get("text") or "")
        trace = node_run.trace_metadata if node_run is not None and isinstance(node_run.trace_metadata, dict) else {}
        llm_trace = trace.get("llm") if isinstance(trace.get("llm"), dict) else {}
        output_metadata = outputs.get("metadata") if isinstance(outputs.get("metadata"), dict) else {}
        routing = (
            llm_trace.get("model_routing")
            if isinstance(llm_trace.get("model_routing"), dict)
            else output_metadata.get("model_routing")
            if isinstance(output_metadata.get("model_routing"), dict)
            else llm_trace
            if "decision_source" in llm_trace
            else {}
        )
        # WorkflowNodeRun.duration은 LLM node가 시작한 뒤 routing resolver와
        # Runtime Judge, 최종 provider 호출이 모두 끝날 때까지의 실제 경과 시간이다.
        # Usage log latency와 Judge usage latency를 더하면 Judge 시간이 중복될 수 있어
        # 보고서의 기준 시간에는 사용하지 않는다.
        task_latency = (
            int(float(node_run.duration or 0) * 1000)
            if node_run is not None and node_run.duration is not None
            else int(llm_trace.get("latency_ms") or 0) or None
        )
        workflow_latency = (
            int(float(workflow_run.duration or 0) * 1000)
            if workflow_run is not None and workflow_run.duration is not None
            else None
        )
        # trace 모델명은 날짜 suffix가 redaction될 수 있다. 실제 실행 모델은
        # 안전한 catalog FK가 있는 usage log를 우선해 비교 집계의 정확도를 지킨다.
        selected_model = str(
            (task_usage.model.model_id_for_api_call if task_usage and task_usage.model else "")
            or (outputs.get("model") if isinstance(outputs, dict) else "")
            or routing.get("selected_model")
        ) or None
        success = engine_error is None and node_run is not None and str(node_run.status).lower().endswith("success")
        return ArmResult(
            arm=arm,
            selected_model=selected_model,
            task_cost_usd=task_cost,
            task_latency_ms=task_latency,
            workflow_latency_ms=workflow_latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            output_text=output_text,
            schema_pass=_schema_pass(output_text),
            workflow_success=success,
            error=engine_error,
            routing=routing,
            routing_judge_cost_usd=judge_cost,
            routing_judge_tokens=judge_tokens,
            routing_judge_latency_ms=judge_latency,
        )
    finally:
        db.close()


def _record_automatic_operational_result(case: ExperimentCase) -> None:
    """실제 배포 완료 훅과 같은 방식으로 auto arm의 학습/성적을 누적한다.

    Celery task 자체는 실험 중 policy refresh를 예약하지 않도록 막아 두지만,
    이 기록 단계는 생략하지 않는다. 생략하면 Judge label은 저장되어도 운영 성공률과
    JSON 계약 통과율이 policy에 반영되지 않아 local-first 전환을 측정할 수 없다.
    """

    db = SessionLocal()
    try:
        scheduled_policy_ids = ModelRoutingPolicyStore.record_completed_deployed_run(
            db,
            workflow_run_id=_run_id(AUTO_ARM, case.case_id),
        )
        # 운영 코드에서는 이 id를 Celery refresh task로 넘긴다. 이 경제성 실험은
        # runtime Judge label과 local learner의 전환만 비교하므로, refresh task를
        # 실행하지 않는 대신 예약 상태를 즉시 해제해 다음 run이 멈추지 않게 한다.
        for policy_id in scheduled_policy_ids:
            policy = db.get(LLMNodeModelRoutingPolicy, policy_id)
            if policy is not None:
                policy.status = "active"
                policy.refresh_requested_at = None
                policy.last_refresh_result = "experiment_refresh_suppressed"
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _retry_quality_evaluation(evaluate, *, max_attempts: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """평가 시스템의 일시 오류만 재시도하고 workflow 결과는 다시 실행하지 않는다."""

    last_metadata: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        payload, metadata = evaluate()
        metadata = dict(metadata or {})
        metadata["attempt_count"] = attempt
        if payload is not None and not metadata.get("error"):
            metadata["evaluation_status"] = "completed"
            return payload, metadata
        last_metadata = metadata
    last_metadata["evaluation_status"] = "failed"
    return None, last_metadata


def _quality_judge(case: ExperimentCase, results: dict[str, ArmResult]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """모델명과 Arm 이름을 숨긴 한 번의 비교 평가로 네 Arm 품질을 비교한다."""

    mapping = list(ARMS)
    random.Random(case.case_id).shuffle(mapping)
    anonymous = {
        f"output_{index + 1}": results[arm].output_text
        for index, arm in enumerate(mapping)
    }
    prompt = {
        "task": "무RAG 기업 요청 처리 workflow의 여러 JSON 출력을 품질만으로 비교하세요.",
        "input": {
            "customerTier": case.customer_tier,
            "request": case.message,
            "context": case.context,
            "constraints": list(case.constraints),
            "outputMode": case.output_mode,
        },
        "required_contract": {
            "fields": ["분류", "우선순위", "승인필요", "대응계획", "답변초안"],
            "rules": [
                "분류와 우선순위는 요청 내용에 맞아야 합니다.",
                "보안, 개인정보, 결제, SLA, 법무 위험은 과도한 확정 약속 없이 보수적으로 처리해야 합니다.",
                "대응계획은 실행 가능한 단계여야 합니다.",
                "답변초안은 사용자가 이해할 수 있어야 합니다.",
                "비용, 속도, 출력 순서, 모델 이름을 평가에 반영하지 마세요.",
            ],
        },
        "anonymous_outputs": anonymous,
        "response_schema": {
            key: {"quality_score": "0..100", "contract_pass": "boolean", "reason": "short Korean"}
            for key in anonymous
        },
    }
    def evaluate_once() -> tuple[dict[str, Any] | None, dict[str, Any]]:
        db = SessionLocal()
        try:
            selection = LLMService.get_runtime_client_for_user(
                db, USER_ID, QUALITY_JUDGE_MODEL, ORG_ID
            )
            started = datetime.now(timezone.utc)
            response = selection.client.invoke_sync(
                [
                    {"role": "system", "content": "당신은 엄격하고 공정한 workflow output 품질 평가자입니다. 반드시 JSON object 하나만 반환하세요."},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=0,
                max_tokens=800,
                response_format={"type": "json_object"},
                request_timeout_seconds=QUALITY_JUDGE_REQUEST_TIMEOUT_SECONDS,
            )
            elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            payload = _json_from_text(str(response.get("choices", [{}])[0].get("message", {}).get("content", "")))
            if not payload:
                return None, {"error": "quality_judge_invalid_json"}
            usage = response.get("usage") if isinstance(response, dict) else {}
            prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
            completion_tokens = int((usage or {}).get("completion_tokens") or 0)
            return payload, {
                "model": QUALITY_JUDGE_MODEL,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": LLMService.calculate_cost(db, QUALITY_JUDGE_MODEL, prompt_tokens, completion_tokens),
                "latency_ms": elapsed_ms,
            }
        except Exception as exc:
            return None, {"error": f"{type(exc).__name__}:{str(exc)[:180]}"}
        finally:
            db.close()

    payload, metadata = _retry_quality_evaluation(
        evaluate_once,
        max_attempts=QUALITY_JUDGE_MAX_ATTEMPTS,
    )
    if payload is None:
        return (
            {
                arm: {
                    "quality_score": None,
                    "contract_pass": None,
                    "reason": "품질 평가 시스템 오류",
                    "evaluation_status": "failed",
                }
                for arm in ARMS
            },
            metadata,
        )

    judged: dict[str, dict[str, Any]] = {}
    for index, arm in enumerate(mapping):
        anonymous_key = f"output_{index + 1}"
        row = payload.get(anonymous_key) if isinstance(payload, dict) else None
        row = row if isinstance(row, dict) else {}
        try:
            score = max(0.0, min(100.0, float(row.get("quality_score"))))
        except (TypeError, ValueError):
            score = 0.0
        judged[arm] = {
            "quality_score": score,
            "contract_pass": bool(row.get("contract_pass")),
            "reason": str(row.get("reason") or "품질 Judge 응답 없음")[:240],
            "evaluation_status": "completed",
        }
    return judged, metadata


def _quality_not_evaluated(
    results: dict[str, ArmResult],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """학습 단계에서 독립 품질 Judge를 호출하지 않았음을 명시한다."""

    return (
        {
            arm: {
                "quality_score": None,
                "contract_pass": None,
                "reason": "학습 단계에서는 독립 품질 평가를 실행하지 않습니다.",
                "evaluation_status": "not_evaluated",
            }
            for arm in results
        },
        {
            "evaluation_status": "not_requested",
            "attempt_count": 0,
            "cost_usd": 0.0,
        },
    )


def _mean(values: Iterable[float | int | None]) -> float | None:
    normalized = [float(value) for value in values if value is not None]
    return statistics.mean(normalized) if normalized else None


def _p95(values: Iterable[float | int | None]) -> float | None:
    normalized = sorted(float(value) for value in values if value is not None)
    if not normalized:
        return None
    return normalized[min(len(normalized) - 1, max(0, round((len(normalized) - 1) * 0.95)))]


def _arm_summary(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    arm_rows = [row["arms"][arm] for row in rows]
    completed_quality = [
        row["quality"][arm]
        for row in rows
        if row["quality"][arm].get("evaluation_status") == "completed"
    ]
    task_cost = sum(float(item["task_cost_usd"] or 0) for item in arm_rows)
    route_cost = sum(float(item["routing_judge_cost_usd"] or 0) for item in arm_rows)
    return {
        "run_count": len(arm_rows),
        "task_cost_usd": task_cost,
        "routing_judge_cost_usd": route_cost,
        "total_product_cost_usd": task_cost + route_cost,
        "average_task_latency_ms": _mean(item["task_latency_ms"] for item in arm_rows),
        "average_end_to_end_latency_ms": _mean(
            item["workflow_latency_ms"] for item in arm_rows
        ),
        "p95_task_latency_ms": _p95(item["task_latency_ms"] for item in arm_rows),
        "total_tokens": sum(int(item["total_tokens"] or 0) for item in arm_rows),
        "schema_pass_rate": sum(1 for item in arm_rows if item["schema_pass"]) / len(arm_rows),
        "workflow_success_rate": sum(1 for item in arm_rows if item["workflow_success"]) / len(arm_rows),
        "quality_score_average": _mean(item["quality_score"] for item in completed_quality),
        "quality_pass_rate": (
            sum(1 for item in completed_quality if item["contract_pass"]) / len(completed_quality)
            if completed_quality
            else None
        ),
        "quality_evaluation_failure_count": sum(
            1
            for row in rows
            if row["quality"][arm].get("evaluation_status") == "failed"
        ),
        "model_distribution": dict(Counter(item["selected_model"] or "unknown" for item in arm_rows)),
        "runtime_judge_call_count": sum(1 for item in arm_rows if item["routing_judge_tokens"] > 0),
    }


def _learning_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    auto_rows = [row["arms"][AUTO_ARM] for row in rows]
    confidences = [
        float(item["routing"].get("judge", {}).get("confidence"))
        for item in auto_rows
        if isinstance(item["routing"].get("judge"), dict)
        and item["routing"].get("judge", {}).get("confidence") is not None
    ]
    sources = Counter(str(item["routing"].get("decision_source") or "unknown") for item in auto_rows)
    local_rows = [item for item in auto_rows if item["routing"].get("decision_source") == "local_router"]
    uncertain_rows = [item for item in auto_rows if item["routing"].get("reason_code") == "local_router_uncertain"]
    selected = [item["selected_model"] for item in auto_rows if item["selected_model"]]
    return {
        "judge_label_count": sum(1 for item in auto_rows if item["routing_judge_tokens"] > 0),
        "distinct_selected_model_count": len(set(selected)),
        "selected_models": dict(Counter(selected)),
        "decision_source_distribution": dict(sources),
        "local_router_takeover_count": len(local_rows),
        "local_router_uncertain_count": len(uncertain_rows),
        "local_confidence_threshold": LOCAL_CONFIDENCE_THRESHOLD,
        "judge_confidence_average": _mean(confidences),
        "judge_confidence_p95": _p95(confidences),
        "interpretation": (
            "local_router_takeover_count가 0이면, 현재 학습 표본 또는 confidence가 "
            "임계값에 도달하지 않아 모든 요청이 Runtime Judge로 처리된 것입니다."
        ),
    }


def _learner_report_payload(
    *,
    policy: Any,
    learner: Any,
    version: Any,
    label_summary: dict[str, Any],
) -> dict[str, Any]:
    """민감한 vector와 가중치를 제외한 learner 보고서 값을 만든다."""

    if policy is None or learner is None:
        return {
            "policy_status": getattr(policy, "status", None),
            "policy_version": getattr(policy, "policy_version", None),
            "learner_id": None,
            "learning_mode": "judge_first",
            "judged_request_count": 0,
            "operational_run_count": 0,
            "selected_model_ids": [],
            "selected_model_counts": {},
            "active_version": None,
            "pending_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "artifact_kind": None,
            "artifact_encoder": None,
            "artifact_label_models": [],
            "artifact_trained_example_count": 0,
            "recent_evaluation": {},
            "last_learning_error": None,
        }

    artifact = dict(getattr(learner, "candidate_artifact", None) or {})
    selected_model_counts = dict(
        getattr(learner, "selected_model_counts", None) or {}
    )
    accepted_count = int(label_summary.get("accepted_count") or 0)
    rejected_count = int(label_summary.get("rejected_count") or 0)
    return {
        "policy_status": getattr(policy, "status", None),
        "policy_version": getattr(policy, "policy_version", None),
        "learner_id": str(getattr(learner, "id", "") or "") or None,
        "learner_status": getattr(learner, "status", None),
        "task_fingerprint": getattr(learner, "task_fingerprint", None),
        "learning_mode": "local_first" if version is not None else "judge_first",
        "judged_request_count": int(
            getattr(learner, "judged_request_count", 0) or 0
        ),
        "operational_run_count": accepted_count + rejected_count,
        "selected_model_ids": sorted(selected_model_counts),
        "selected_model_counts": selected_model_counts,
        "active_version": (
            int(getattr(version, "version", 0) or 0) if version is not None else None
        ),
        "pending_count": int(label_summary.get("pending_count") or 0),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "artifact_kind": artifact.get("kind"),
        "artifact_encoder": artifact.get("encoder_model_id"),
        "artifact_label_models": sorted(selected_model_counts),
        "artifact_trained_example_count": int(
            artifact.get("trained_example_count")
            or getattr(learner, "judged_request_count", 0)
            or 0
        ),
        "recent_evaluation": dict(
            getattr(learner, "recent_evaluation", None) or {}
        ),
        "last_learning_error": artifact.get("last_learning_error"),
    }


def _persisted_learning_state() -> dict[str, Any]:
    """policy와 분리된 learner/label/version의 최종 상태를 보고서에 남긴다."""

    db = SessionLocal()
    try:
        policy = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == AUTO_DEPLOYMENT_ID)
            .filter(LLMNodeModelRoutingPolicy.node_id == NODE_ID)
            .first()
        )
        learner = (
            db.get(LLMNodeModelRoutingLearner, policy.learner_id)
            if policy is not None and policy.learner_id is not None
            else None
        )
        version = (
            db.get(
                LLMNodeModelRoutingLearnerVersion,
                policy.active_learner_version_id,
            )
            if policy is not None and policy.active_learner_version_id is not None
            else None
        )
        label_summary = (
            ModelRoutingLearnerStore.label_summary(db, learner_id=learner.id)
            if learner is not None
            else {}
        )
        return _learner_report_payload(
            policy=policy,
            learner=learner,
            version=version,
            label_summary=label_summary,
        )
    finally:
        db.close()


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.6f}"


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _score(value: float | None) -> str:
    return "평가 실패" if value is None else f"{value:.1f}"


def _quality_score_text(value: float | None, *, enabled: bool) -> str:
    if not enabled:
        return "미평가"
    return _score(value)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["arm_summary"]
    phase = str(report.get("phase") or BENCHMARK_PHASE)
    automatic = summary[AUTO_ARM]
    mid = summary.get(MID_ARM)
    high = summary.get(HIGH_ARM)
    low = summary.get(LOW_ARM)
    auto_vs_high = (
        high["total_product_cost_usd"] - automatic["total_product_cost_usd"]
        if high is not None
        else None
    )
    comparison_labels = {
        AUTO_ARM: "자동 라우팅",
        MID_ARM: "중간 모델 고정",
        HIGH_ARM: "고가 모델 고정",
        LOW_ARM: "저가 모델 고정",
    }
    compared_methods = ", ".join(comparison_labels[arm] for arm in ARMS)
    report_kind = "학습 검증" if phase == LEARNING_PHASE else "경제성 비교"
    lines = [
        f"# Judge-first 자동 모델 라우팅 {report['case_count']}회 {report_kind} 보고서",
        "",
        "## 한눈에 보는 결론",
        "",
        (
            f"이 단계는 자동 라우팅만 {report['case_count']}회 실행해 첫 50건으로 학습하고 "
            "다음 50건으로 일반화 성능과 학습 버전 발행 여부를 확인합니다."
            if phase == LEARNING_PHASE
            else f"이 단계는 학습에 쓰지 않은 {report['case_count']}개 요청을 "
            f"{compared_methods}에 똑같이 보내 비용·속도·품질을 비교합니다."
        ),
        "",
        f"- 자동 라우팅 총 제품 비용: {_money(automatic['total_product_cost_usd'])}",
        (
            f"- 고가 고정 대비 자동 라우팅 순절감: {_money(auto_vs_high)} "
            f"({'절감' if auto_vs_high >= 0 else '추가 비용'})"
            if auto_vs_high is not None
            else "- 고가 고정 비교: 이번 실행에서 제외"
        ),
        (
            f"- 중간 고정 모델: `{MID_MODEL}` / 중간 고정 대비 자동 품질 차이: "
            f"{((automatic['quality_score_average'] or 0) - (mid['quality_score_average'] or 0)):+.2f}점"
            if mid is not None and automatic['quality_score_average'] is not None and mid['quality_score_average'] is not None
            else "- 중간 고정 비교: 이번 실행에서 제외"
        ),
        (
            f"- 저가 고정 대비 자동 라우팅 평균 품질 차이: "
            f"{((automatic['quality_score_average'] or 0) - (low['quality_score_average'] or 0)):+.2f}점"
            if low is not None and automatic['quality_score_average'] is not None and low['quality_score_average'] is not None
            else "- 저가 고정 비교: 이번 실행에서 제외"
        ),
        f"- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: {automatic['runtime_judge_call_count']}/{report['case_count']}",
        "",
        "자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.",
        "",
        "## 실험 조건",
        "",
        f"- 실행 시각: {report['executed_at']}",
        f"- workflow: `{report['workflow_id']}` / LLM node: `{NODE_ID}`",
        "- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록",
        "- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`",
        f"- 자동 라우팅 후보: {', '.join(report['available_models'])}",
        "- 고정 비교 모델: " + ", ".join(
            label for arm, label in (
                (HIGH_ARM, f"고가 `{HIGH_MODEL}`"),
                (MID_ARM, f"중간 `{MID_MODEL}`"),
                (LOW_ARM, f"저가 `{LOW_MODEL}`"),
            ) if arm in ARMS
        ),
        f"- 라우팅 Judge: `{report['routing_judge_model']}`",
        (
            "- 독립 품질 평가 Judge: 학습 단계에서는 호출하지 않음"
            if not report.get("quality_evaluation_enabled")
            else f"- 독립 품질 평가 Judge: `{report['quality_judge_model']}`"
        ),
        "- RAG: 미사용. 이번 비교에서는 KB 검색 품질 변수를 빼고 모델 라우팅 자체의 비용·속도·출력 품질만 측정했습니다.",
        (
            f"- 학습 상태: {report['case_count']}회 동안 Judge label을 누적하고 local router 전환 가능성을 측정했습니다."
            if phase == LEARNING_PHASE
            else "- 정책·학습 상태: 비교 중에는 고정했습니다. 이 요청들은 학습 label이나 정책 갱신 횟수에 포함하지 않았습니다."
        ),
        "",
        "## 데이터셋",
        "",
        f"이 단계의 {report['case_count']}개 요청은 여러 업무 유형과 난이도를 섞었고, 동일 문장을 반복하지 않았습니다.",
        "",
        "| 예상 난이도 | 건수 |",
        "| --- | ---: |",
    ]
    for difficulty, count in sorted(Counter(row["expected_difficulty"] for row in report["runs"]).items()):
        lines.append(f"| {difficulty} | {count} |")
    lines.extend([
        "",
        "## 비용·속도·품질 비교",
        "",
        "| 방식 | 처리 모델 비용 | 라우팅 Judge 비용 | 총 제품 비용 | 평균 LLM 노드 시간 | 평균 전체 시간 | 평균 품질 점수 | JSON 계약 통과 | 품질 통과 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    labels = {
        AUTO_ARM: "자동 라우팅",
        MID_ARM: f"중간 고정 ({MID_MODEL})",
        HIGH_ARM: f"고가 고정 ({HIGH_MODEL})",
        LOW_ARM: f"저가 고정 ({LOW_MODEL})",
    }
    for arm in ARMS:
        item = summary[arm]
        lines.append(
            f"| {labels[arm]} | {_money(item['task_cost_usd'])} | {_money(item['routing_judge_cost_usd'])} | "
            f"{_money(item['total_product_cost_usd'])} | {item['average_task_latency_ms'] or 0:.0f}ms | "
            f"{item['average_end_to_end_latency_ms'] or 0:.0f}ms | "
            f"{_quality_score_text(item['quality_score_average'], enabled=bool(report.get('quality_evaluation_enabled')))} | "
            f"{_percent(item['schema_pass_rate'])} | {_percent(item['quality_pass_rate'])} |"
        )
    lines.extend([
        "",
        "## 자동 라우팅이 실제로 고른 모델",
        "",
    ])
    for model_id, count in sorted(automatic["model_distribution"].items()):
        lines.append(f"- `{model_id}`: {count}회")
    routing_classes = Counter(
        str(row.get("routing_accuracy", {}).get("classification") or "unclassified")
        for row in report["runs"]
    )
    lines.extend([
        f"- 사전 가설 기준 적중: {routing_classes.get('appropriate', 0)}/{report['case_count']}건",
        f"- 성능 부족 선택: {routing_classes.get('underpowered', 0)}건 / 과도한 선택: {routing_classes.get('overprovisioned', 0)}건 / 미분류: {routing_classes.get('unclassified', 0)}건",
    ])
    lines.extend([
        "",
        "## 로컬 라우터 학습 수준",
        "",
    ])
    learning = report["learning_summary"]
    lines.extend([
        (
            f"- Judge 학습 label 누적: {learning['judge_label_count']}건"
            if phase == LEARNING_PHASE
            else f"- Runtime Judge 판단: {learning['judge_label_count']}건 (이번 비교에서는 학습 label로 저장하지 않음)"
        ),
        f"- 실제 선택 모델 종류: {learning['distinct_selected_model_count']}개 ({learning['selected_models']})",
        f"- 처리 출처 분포: {learning['decision_source_distribution']}",
        f"- 로컬 라우터가 Judge 없이 직접 선택한 횟수: {learning['local_router_takeover_count']}회",
        f"- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: {learning['local_router_uncertain_count']}회",
        f"- Judge 신뢰도 평균 / P95: {(learning['judge_confidence_average'] or 0):.3f} / {(learning['judge_confidence_p95'] or 0):.3f}",
        f"- 로컬 takeover 최소 신뢰도: {learning['local_confidence_threshold']:.2f}",
        f"- 최종 저장 정책 학습 모드: {learning['persisted']['learning_mode'] or '없음'}",
        f"- 최종 저장 학습 표본: Judge {learning['persisted']['judged_request_count'] or 0}건 / 운영 완료 {learning['persisted']['operational_run_count'] or 0}건",
        f"- 학습 artifact: {learning['persisted']['artifact_kind'] or '없음'} ({learning['persisted']['artifact_encoder'] or '-'})",
        f"- artifact 학습 예시 수: {learning['persisted']['artifact_trained_example_count'] or 0}건 / artifact 모델 label: {learning['persisted']['artifact_label_models']}",
        "",
        "이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.",
        "",
        "## 실험 평가 비용",
        "",
        f"- 독립 품질 Judge 총비용: {_money(report['quality_judge_total_cost_usd'])}",
        f"- 독립 품질 Judge 총호출: {report['quality_judge_call_count']}회",
        (
            "- 학습 단계에는 품질 평가 비용이 발생하지 않습니다."
            if not report.get("quality_evaluation_enabled")
            else f"- 이 비용은 {len(ARMS)}개 방식의 결과를 공정하게 비교하기 위한 측정 비용이며 제품 운영비 비교에는 포함하지 않았습니다."
        ),
        "",
        "## 요청별 결과",
        "",
        "| # | 주제 | 예상 난이도 | "
        + " | ".join(
            [
                *(["자동 선택 모델", "자동 품질", "자동 총비용"] if AUTO_ARM in ARMS else []),
                *(["중간 품질", "중간 비용"] if MID_ARM in ARMS else []),
                *(["고가 품질", "고가 비용"] if HIGH_ARM in ARMS else []),
                *(["저가 품질", "저가 비용"] if LOW_ARM in ARMS else []),
            ]
        )
        + " |",
        "| ---: | --- | --- | "
        + " | ".join(
            [
                *( ["---", "---:", "---:"] if AUTO_ARM in ARMS else []),
                *( ["---:", "---:"] if MID_ARM in ARMS else []),
                *( ["---:", "---:"] if HIGH_ARM in ARMS else []),
                *( ["---:", "---:"] if LOW_ARM in ARMS else []),
            ]
        )
        + " |",
    ])
    for index, row in enumerate(report["runs"], start=1):
        cells = [str(index), row["category"], row["expected_difficulty"]]
        if AUTO_ARM in ARMS:
            auto = row["arms"][AUTO_ARM]
            cells.extend([
                auto["selected_model"] or "-",
                _quality_score_text(
                    row['quality'][AUTO_ARM]['quality_score'],
                    enabled=bool(report.get('quality_evaluation_enabled')),
                ),
                _money(auto["task_cost_usd"] + auto["routing_judge_cost_usd"]),
            ])
        for arm in (MID_ARM, HIGH_ARM, LOW_ARM):
            if arm not in ARMS:
                continue
            fixed = row["arms"][arm]
            cells.extend([
                _quality_score_text(
                    row['quality'][arm]['quality_score'],
                    enabled=bool(report.get('quality_evaluation_enabled')),
                ),
                _money(fixed["task_cost_usd"]),
            ])
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend([
        "",
        "## 해석 시 주의점",
        "",
        "- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.",
        "- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.",
        "- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.",
    ])
    return "\n".join(lines) + "\n"


def _report_paths(
    output_dir: pathlib.Path,
    *,
    report_name: str | None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """새 run은 고정 파일명, 기존 run은 호환용 이름을 유지한다."""

    if report_name is None:
        return output_dir / "result.json", output_dir / "report.md"
    return output_dir / f"{report_name}.json", output_dir / f"{report_name}.md"


def _write_run_config(
    output_dir: pathlib.Path,
    *,
    run_id: str,
    report_name: str | None,
    batch_size: int,
) -> pathlib.Path:
    """실험 조건을 결과와 같은 폴더에 남겨 나중 비교 기준을 고정한다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "run-config.json"
    learning_plan = build_phase_plan(LEARNING_PHASE)
    benchmark_plan = build_phase_plan(BENCHMARK_PHASE)
    config = {
        "schema_version": 2,
        "run_id": run_id,
        "experiment": "judge-first-norag-blind-economics",
        "strategy_id": "judge_bootstrap_incremental_v1",
        "routing_judge_model": ROUTING_JUDGE_MODEL,
        "routing_judge_max_output_tokens": ModelRoutingRuntimeJudge.MAX_OUTPUT_TOKENS,
        "quality_judge_model": QUALITY_JUDGE_MODEL,
        "candidate_model_ids": [
            "gpt-4o-mini",
            "gpt-4.1-mini",
            "gpt-4.1",
            "gpt-4o",
            "gpt-5-mini",
            "gpt-5.4-mini",
            "gpt-5.4",
            "gpt-5.6-sol",
            "gpt-5.6-luna",
            "gpt-5.6-terra",
            "o3",
        ],
        "comparison_arms": {
            arm: {
                AUTO_ARM: "automatic routing",
                MID_ARM: MID_MODEL,
                HIGH_ARM: HIGH_MODEL,
                LOW_ARM: LOW_MODEL,
            }[arm]
            for arm in benchmark_plan.arms
        },
        "dataset": {
            "name": "enterprise-norag-routing-two-stage-v3",
            "total_case_count": len(build_cases()),
        },
        "phases": {
            LEARNING_PHASE: {
                "case_count": len(learning_plan.cases),
                "arms": list(learning_plan.arms),
                "quality_judge": learning_plan.evaluate_quality,
            },
            BENCHMARK_PHASE: {
                "case_count": len(benchmark_plan.cases),
                "arms": list(benchmark_plan.arms),
                "quality_judge": benchmark_plan.evaluate_quality,
                "requires_ready_learner": True,
            },
        },
        "batch_size": batch_size,
        "artifact_files": {
            LEARNING_PHASE: {
                "report": "learning/report.md",
                "result": "learning/result.json",
                "batches": "learning/batches/",
                "append_only_events": "learning/execution-events.jsonl",
            },
            BENCHMARK_PHASE: {
                "report": "benchmark/report.md",
                "result": "benchmark/result.json",
                "batches": "benchmark/batches/",
                "append_only_events": "benchmark/execution-events.jsonl",
            },
        },
    }
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        for key in (
            "run_id",
            "routing_judge_model",
            "routing_judge_max_output_tokens",
            "quality_judge_model",
            "strategy_id",
            "candidate_model_ids",
            "comparison_arms",
            "dataset",
            "phases",
        ):
            if previous.get(key) != config[key]:
                raise RuntimeError(
                    f"동일 run 폴더의 실험 조건이 다릅니다: {key}. "
                    "새 --run-id를 사용하세요."
                )
        return config_path
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def resolve_artifact_target(
    *,
    output_dir: str,
    run_id: str | None,
) -> tuple[pathlib.Path, str | None]:
    """새 실험은 run-id 폴더를, 기존 명령은 기존 output-dir 계약을 사용한다."""

    if run_id:
        normalized = run_id.strip()
        if not normalized or any(char in normalized for char in "\\/:*?\"<>|"):
            raise ValueError("--run-id는 경로 구분자와 예약 문자를 포함할 수 없습니다.")
        return EXPERIMENT_RUNS_ROOT / normalized, None
    return pathlib.Path(output_dir), "judge_first_economics_80"


def write_report(
    output_dir: pathlib.Path,
    report: dict[str, Any],
    *,
    report_name: str | None,
) -> tuple[pathlib.Path, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = _report_paths(output_dir, report_name=report_name)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    # 배치가 끝난 당시의 누적 결과도 고정한다. 누적 보고서만 덮어쓰면 10/20/…건에서
    # Judge 호출과 local router 전환이 어떻게 달라졌는지 나중에 재현할 수 없다.
    latest_batch = report.get("latest_batch") if isinstance(report.get("latest_batch"), dict) else {}
    start = int(latest_batch.get("offset") or 0) + 1
    end = int(latest_batch.get("completed_case_count") or 0)
    if end >= start:
        batch_dir = output_dir / "batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_name = f"batch-{start:02d}-{end:02d}"
        (batch_dir / f"{batch_name}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (batch_dir / f"{batch_name}.md").write_text(
            render_markdown(report),
            encoding="utf-8",
        )
    return json_path, markdown_path


def _read_existing_report(output_dir: pathlib.Path, *, report_name: str | None) -> dict[str, Any]:
    path, _ = _report_paths(output_dir, report_name=report_name)
    if not path.exists():
        raise RuntimeError("--resume에는 이전 실험 보고서가 필요합니다.")
    return json.loads(path.read_text(encoding="utf-8"))


def _experiment_report(
    *,
    phase_plan: ExperimentPhasePlan,
    rows: list[dict[str, Any]],
    available_models: list[str],
    batch_offset: int,
    batch_count: int,
    prior_quality_cost: float,
    prior_quality_calls: int,
    prior_quality_errors: list[dict[str, Any]],
    quality_judge_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase_plan.phase,
        "quality_evaluation_enabled": phase_plan.evaluate_quality,
        "workflow_id": str(WORKFLOW_ID),
        "node_id": NODE_ID,
        "case_count": len(rows),
        "latest_batch": {
            "offset": batch_offset,
            "count": batch_count,
            "completed_case_count": len(rows),
        },
        "available_models": available_models,
        "routing_judge_model": ROUTING_JUDGE_MODEL,
        "quality_judge_model": QUALITY_JUDGE_MODEL,
        "runs": rows,
        "arm_summary": {arm: _arm_summary(rows, arm) for arm in ARMS},
        "learning_summary": {
            **_learning_summary(rows),
            "persisted": _persisted_learning_state(),
        },
        "policy_checkpoint": _policy_checkpoint(),
        "quality_judge_total_cost_usd": prior_quality_cost + sum(
            float(metric.get("cost_usd") or 0) for metric in quality_judge_metrics
        ),
        "quality_judge_call_count": prior_quality_calls + sum(
            1 for metric in quality_judge_metrics if metric.get("evaluation_status") == "completed"
        ),
        "quality_judge_errors": prior_quality_errors
        + [metric for metric in quality_judge_metrics if metric.get("evaluation_status") == "failed"],
    }


def run_experiment(
    cases: list[ExperimentCase],
    output_dir: pathlib.Path,
    *,
    phase_plan: ExperimentPhasePlan,
    resume: bool,
    batch_offset: int,
    report_name: str | None,
) -> dict[str, Any]:
    if tuple(ARMS) != phase_plan.arms:
        raise RuntimeError("실행 arm과 phase plan이 일치하지 않습니다.")

    if resume:
        previous_report = _read_existing_report(output_dir, report_name=report_name)
        if previous_report.get("phase") != phase_plan.phase:
            raise RuntimeError("다른 실험 단계의 보고서는 이어서 실행할 수 없습니다.")
        rows = list(previous_report.get("runs") or [])
        prior_quality_cost = float(previous_report.get("quality_judge_total_cost_usd") or 0)
        prior_quality_calls = int(previous_report.get("quality_judge_call_count") or 0)
        prior_quality_errors = list(previous_report.get("quality_judge_errors") or [])
        with SessionLocal() as db:
            available_models = _ensure_runtime_models(
                db,
                include_quality_judge=phase_plan.evaluate_quality,
            )
            _restore_policy_checkpoint(db, previous_report.get("policy_checkpoint") or {})
            _clear_case_runs(db, cases)
            db.commit()
    else:
        with SessionLocal() as db:
            available_models = _ensure_runtime_models(
                db,
                include_quality_judge=phase_plan.evaluate_quality,
            )
            if phase_plan.reset_learning:
                _clear_prior_experiment_learning(db)
                _clear_prior_experiment_runs(db)
            _upsert_workflow_and_deployments(
                db,
                available_models,
                reset_policy_state=phase_plan.reset_learning,
            )
            _clear_case_runs(db, cases)
            db.commit()
        rows = []
        prior_quality_cost = 0.0
        prior_quality_calls = 0
        prior_quality_errors: list[dict[str, Any]] = []

    if phase_plan.requires_ready_learner:
        readiness_error = benchmark_readiness_error(
            _persisted_learning_state(),
            completed_learning_case_count=_completed_learning_case_count(output_dir),
        )
        if readiness_error is not None:
            raise RuntimeError(
                "경제성 비교를 시작할 수 없습니다. 먼저 learning 100건을 완료하고 "
                f"학습 버전을 발행해야 합니다: {readiness_error}"
            )

    quality_judge_metrics: list[dict[str, Any]] = []
    event_path = output_dir / "execution-events.jsonl"
    if not resume:
        event_path.unlink(missing_ok=True)
    with synchronous_experiment_tasks():
        for index, case in enumerate(cases, start=1):
            execution_order = _arm_execution_order(case.case_id)
            results = {
                arm: _execute_case(
                    arm,
                    case,
                    include_in_learning=phase_plan.phase == LEARNING_PHASE,
                    use_policy_preview=phase_plan.use_policy_preview,
                )
                for arm in execution_order
            }
            if phase_plan.phase == LEARNING_PHASE:
                _record_automatic_operational_result(case)
            if phase_plan.phase == LEARNING_PHASE and _learning_batch_due(
                completed_case_count=index,
                total_case_count=len(cases),
            ):
                _train_automatic_learner()
            if phase_plan.evaluate_quality:
                quality, quality_meta = _quality_judge(case, results)
            else:
                quality, quality_meta = _quality_not_evaluated(results)
            quality_judge_metrics.append(quality_meta)
            row = {
                "case_id": case.case_id,
                "category": case.category,
                "expected_difficulty": case.expected_difficulty,
                "customer_tier": case.customer_tier,
                "input": {
                    "request": case.message,
                    "context": case.context,
                    "constraints": list(case.constraints),
                    "output_mode": case.output_mode,
                },
                "execution_order": list(execution_order),
                "arms": {arm: asdict(result) for arm, result in results.items()},
                "routing_accuracy": _routing_accuracy(
                    case,
                    results[AUTO_ARM].selected_model,
                ),
                "quality": quality,
                "quality_evaluation": quality_meta,
            }
            _append_jsonl(event_path, row)
            rows.append(row)
            if index == 1 or index % 5 == 0 or index == len(cases):
                print(
                    f"[progress] batch {batch_offset + index}/{batch_offset + len(cases)} "
                    f"(this batch {index}/{len(cases)}) completed",
                    flush=True,
                )
            if index % 10 == 0 or index == len(cases):
                checkpoint = _experiment_report(
                    phase_plan=phase_plan,
                    rows=rows,
                    available_models=available_models,
                    batch_offset=batch_offset + index - min(index, 10),
                    batch_count=min(index, 10),
                    prior_quality_cost=prior_quality_cost,
                    prior_quality_calls=prior_quality_calls,
                    prior_quality_errors=prior_quality_errors,
                    quality_judge_metrics=quality_judge_metrics,
                )
                write_report(output_dir, checkpoint, report_name=report_name)

    report = _experiment_report(
        phase_plan=phase_plan,
        rows=rows,
        available_models=available_models,
        batch_offset=batch_offset,
        batch_count=len(cases),
        prior_quality_cost=prior_quality_cost,
        prior_quality_calls=prior_quality_calls,
        prior_quality_errors=prior_quality_errors,
        quality_judge_metrics=quality_judge_metrics,
    )
    json_path, markdown_path = write_report(output_dir, report, report_name=report_name)
    print(json.dumps({"json": str(json_path.resolve()), "markdown": str(markdown_path.resolve())}, ensure_ascii=False), flush=True)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Judge-first 자동 모델 라우팅 2단계 학습·경제성 실험"
    )
    parser.add_argument("--execute", action="store_true", help="실제 provider 호출과 DB 로그 기록을 실행합니다.")
    parser.add_argument(
        "--phase",
        choices=(LEARNING_PHASE, BENCHMARK_PHASE),
        default=LEARNING_PHASE,
        help="learning은 자동 100건, benchmark는 미사용 입력 20건의 3-arm 비교입니다.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="선택 단계에서 실행할 건수입니다. 생략하면 단계 전체를 실행합니다.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="선택 단계 데이터셋에서 시작할 0-base 위치입니다.",
    )
    parser.add_argument("--resume", action="store_true", help="이전 10건 batch의 DB 정책과 보고서를 이어서 누적합니다.")
    parser.add_argument(
        "--routing-judge-model",
        default=ROUTING_JUDGE_MODEL,
        help="자동 라우팅 판단에만 사용할 Judge 모델입니다. 독립 품질 평가는 별도 고정 모델을 사용합니다.",
    )
    parser.add_argument(
        "--report-name",
        default=None,
        help="기존 경로 호환용 출력 파일 이름입니다. 새 --run-id 실행에서는 사용하지 마세요.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "새 실험 폴더 이름입니다. 예: "
            "2026-07-18__ticket-json-v1__judge-gpt-5.4-mini__out-256"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="reports/model-routing/current/judge-first/economics-80/latest",
    )
    return parser.parse_args()


def main() -> None:
    global ROUTING_JUDGE_MODEL, ARMS
    args = parse_args()
    ROUTING_JUDGE_MODEL = str(args.routing_judge_model)
    phase_plan = build_phase_plan(args.phase)
    ARMS = phase_plan.arms
    run_root, report_name = resolve_artifact_target(
        output_dir=str(args.output_dir),
        run_id=args.run_id,
    )
    if args.run_id and args.report_name:
        raise SystemExit("--run-id와 --report-name은 함께 사용할 수 없습니다.")
    output_dir = run_root / phase_plan.phase
    report_name = (
        None
        if args.run_id
        else args.report_name or f"judge_first_economics_80_{phase_plan.phase}"
    )
    cases = list(phase_plan.cases)
    count = len(cases) - args.offset if args.count is None else args.count
    if count < 1 or args.offset < 0 or args.offset + count > len(cases):
        raise SystemExit(
            f"--offset/--count 범위는 {phase_plan.phase} 단계의 "
            f"0~{len(cases)} 안이어야 합니다."
        )
    selected_cases = cases[args.offset : args.offset + count]
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "phase": phase_plan.phase,
                    "arms": list(phase_plan.arms),
                    "case_count": len(selected_cases),
                    "quality_evaluation": phase_plan.evaluate_quality,
                    "requires_ready_learner": phase_plan.requires_ready_learner,
                    "categories": dict(Counter(case.category for case in selected_cases)),
                    "difficulty": dict(Counter(case.expected_difficulty for case in selected_cases)),
                    "message": "실제 호출은 --execute를 붙여야 시작합니다.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.run_id:
        _write_run_config(
            run_root,
            run_id=str(args.run_id).strip(),
            report_name=None,
            batch_size=len(selected_cases),
        )
    run_experiment(
        selected_cases,
        output_dir,
        phase_plan=phase_plan,
        resume=args.resume,
        batch_offset=args.offset,
        report_name=report_name,
    )


if __name__ == "__main__":
    main()
