"""Final demo database seed helpers.

이 모듈은 최종 시연용 로컬 DB 상태를 재현하기 위한 전용 seed다.
기본 실행은 upsert로 동작하고, reset 실행은 demo seed가 관리하는 row만
삭제/복원한다.
"""

from __future__ import annotations

import copy
import gzip
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from apps.memory.adapters.persistence import (
    delete_conversation_sessions_for_resources,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.agent_builder import (
    AgentBuilderDraft,
    AgentBuilderRequest,
    AgentBuilderSession,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
    KnowledgeDocumentIngestionJob,
    KnowledgeIngestionOutbox,
    SourceType,
)
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMProvider,
    LLMRelCredentialModel,
    LLMUsageLog,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
    OrganizationMembership,
)
from apps.shared.db.models.permission_request import (
    PERMISSION_REQUEST_APPROVED,
    REQUESTED_PERMISSION_APP_CREATE,
    PermissionRequest,
)
from apps.shared.db.models.security_alert import (
    SecurityAlert,
    SecurityAlertAuditEvent,
)
from apps.shared.db.models.team import (
    Team,
    TeamAuditPermission,
    TeamKnowledgeCollectionPermission,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import (
    UserAppCreationPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    TracePayload,
    TracePayloadAccessEvent,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
    app_auth_secret_verifier_state_is_valid,
)
from apps.shared.services.llm_client.openai_client import OpenAIClient
from apps.shared.services.model_routing_global_profile_catalog import (
    catalog_metadata_for_model_id,
)
from apps.shared.services.password_hashing import hash_password
from apps.shared.services.security_alert_rule_evaluator import (
    build_security_alert_detection_key,
)
from apps.shared.services.workflow_layout import calculate_workflow_auto_layout
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

DEMO_SEED_VERSION = "final-demo-2026-07-21"
DEMO_PASSWORD = "123123"
DEMO_CHAT_MODEL = "gpt-5.4"
DEMO_CHAT_MINI_MODEL = "gpt-5.4-mini"
DEMO_MODEL_ROUTER_BASE_MODEL = "gpt-5-mini"
DEMO_MODEL_ROUTER_FALLBACK_MODEL = "gpt-4.1"
DEMO_MODEL_ROUTER_CHEAP_MODEL = "gpt-4o-mini"
DEMO_MODEL_ROUTER_BALANCED_MODEL = "gpt-4.1-mini"
# 최신 GPT-5.6 계열은 workflow 자동 라우팅 allowlist와 같은 범위로 runtime
# credential에 연결한다. 특정 모델의 선택 여부는 seed가 아닌 정책/실행 단계가 결정한다.
DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL = "gpt-5.6-luna"
DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL = "gpt-5.6-terra"
DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL = "gpt-5.6"
DEMO_MODEL_ROUTER_LATEST_SOL_MODEL = "gpt-5.6-sol"
DEMO_MODEL_ROUTER_OMNI_MODEL = "gpt-4o"
DEMO_MODEL_ROUTER_REASONING_MODEL = "o3"
# 이 RAG 실험은 낮은 비용 후보를 검증하는 흐름이 목적이다. Responses API의
# reasoning token이 900 token 출력 예산을 먼저 소진하지 않는 안정적인 기준 모델로
# 시작해, 후보 품질 gate와 입력군 routing 자체를 검증한다.
DEMO_ONBOARDING_ROUTER_MODEL = "gpt-4.1"
DEMO_EMBEDDING_MODEL = "text-embedding-3-small"
DEMO_EMBEDDING_DIMENSION = 1536
DEMO_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEMO_REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_LEGAL_DOCS_LABOR_DIR = DEMO_REPO_ROOT / "local" / "legal-docs-labor"
DEMO_ONBOARDING_PDF_DIR = DEMO_REPO_ROOT / "demodata"
DEMO_KNOWLEDGE_FIXTURE_PATH = (
    DEMO_REPO_ROOT / "apps" / "shared" / "db" / "fixtures" / "demo_knowledge_chunks.jsonl.gz"
)
DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV = "NODEASE_DEMO_REGENERATE_KNOWLEDGE_FIXTURE"
DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV = (
    "NODEASE_DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL"
)
# Docker gateway 컨테이너 경로를 우선하고, 로컬 venv 실행에서는 repo 루트의
# gitignore된 uploads/ 아래로 폴백한다 (docs/demo/local-demo-db.md 실행 방식 참고).
DEMO_UPLOAD_DIRS = (
    Path("/app/uploads/demo_seed"),
    DEMO_REPO_ROOT / "uploads" / "demo_seed",
)


def _uuid(suffix: int) -> uuid.UUID:
    return uuid.UUID(f"10200000-0000-0000-0000-{suffix:012d}")


ORG_ID = _uuid(100)

USER_IDS = {
    "admin": _uuid(1),
    "rookie": _uuid(2),
    "author": _uuid(3),
    "tester_manager": _uuid(4),
    "tester_builder": _uuid(5),
    "tester_member": _uuid(6),
    "invited": _uuid(7),
    "suspended": _uuid(8),
    "removed": _uuid(9),
    "developer": _uuid(10),
    "planning": _uuid(11),
    "onboarding_platform_rookie": _uuid(12),
    "onboarding_sales_rookie": _uuid(13),
    "onboarding_people_manager": _uuid(14),
}

DEMO_SECURITY_ALERT_PERMISSION_AUDIT_IDS = tuple(
    _uuid(4100 + index) for index in range(10)
)
DEMO_SECURITY_ALERT_DETECTED_AUDIT_ID = _uuid(4110)
DEMO_SECURITY_ALERT_ID = uuid.UUID("10200000-0000-4000-8000-000000004200")
DEMO_SECURITY_ALERT_EVIDENCE_IDS = tuple(_uuid(4300 + index) for index in range(10))

TEAM_IDS = {
    "platform_admin": _uuid(200),
    "ai_builder_onboarding": _uuid(201),
    "customer_support_ops": _uuid(202),
    "hr_knowledge_users": _uuid(203),
    "finance_restricted": _uuid(204),
    "tester_builder": _uuid(205),
    "tester_member": _uuid(206),
    "department_development": _uuid(207),
    "department_planning": _uuid(208),
    "onboarding_platform": _uuid(209),
    "onboarding_sales": _uuid(210),
    "onboarding_people": _uuid(211),
    "onboarding_finance": _uuid(212),
}

KB_IDS = {
    "hr": _uuid(300),
    "finance": _uuid(301),
    "legal_labor_standards": _uuid(320),
    "legal_equal_employment": _uuid(321),
    "legal_equal_employment_enforcement_decree": _uuid(322),
    "legal_privacy": _uuid(323),
    "legal_occupational_safety": _uuid(324),
    "legal_retirement_benefits": _uuid(325),
    "legal_fair_hiring": _uuid(326),
    "onboarding_platform": _uuid(339),
    "onboarding_sales": _uuid(340),
    "onboarding_finance": _uuid(341),
    "hr_welfare": _uuid(342),
}

RETIRED_INTERNAL_DOCUMENT_KB_IDS = {
    "internal_onboarding": _uuid(327),
    "internal_leave_attendance": _uuid(328),
    "internal_benefits": _uuid(329),
    "internal_privacy_hr_records": _uuid(330),
    "internal_budget_alert_runbook": _uuid(331),
    "internal_cost_optimization_playbook": _uuid(332),
    "internal_developer_onboarding_rules": _uuid(333),
    "internal_developer_commit_convention": _uuid(334),
    "internal_developer_compensation_band": _uuid(335),
    "internal_compensation_access_policy": _uuid(336),
    "internal_planning_onboarding_guide": _uuid(337),
    "onboarding_company_common": _uuid(338),
}

COLLECTION_IDS = {
    "legal_public": _uuid(360),
    "team_onboarding_access_control": _uuid(362),
    "hr_policies": _uuid(363),
}

RETIRED_INTERNAL_DOCUMENT_COLLECTION_IDS = {
    "internal_onboarding": _uuid(361),
}

# author의 승인된 App 생성 권한 신청 이력 (ADR-0016).
# rookie 신청은 라이브 데모(시나리오 1)의 제출 흐름과 충돌하므로 seed하지 않는다.
PERMISSION_REQUEST_IDS = {
    "author_app_create": _uuid(900),
}

APP_CREATION_PERMISSION_IDS = {
    "author": _uuid(910),
}

# 데모 조직 공용 LLM credential. 스키마상 credential은 user_id 소유가 필수이고
# organization_id는 nullable scope라(data_model.md), 라이브 생성 경로와 같게
# org manager(admin) 소유 + 데모 조직 scope로 만든다. apiKey는 실제 키가 아닌
# 명백한 더미 값이다 — 비용 탭/요약 카드 집계용이며 provider 호출은 실패한다.
# LEGACY_DEMO_LLM_CREDENTIAL_ID(_uuid(700))는 과거 seed 정리 대상이라 재사용하지 않는다.
LLM_CREDENTIAL_IDS = {
    "demo_openai": _uuid(920),
}

CREDENTIAL_MODEL_REL_IDS = {
    DEMO_CHAT_MODEL: _uuid(921),
    DEMO_CHAT_MINI_MODEL: _uuid(922),
    DEMO_EMBEDDING_MODEL: _uuid(923),
    DEMO_MODEL_ROUTER_BASE_MODEL: _uuid(924),
    DEMO_MODEL_ROUTER_FALLBACK_MODEL: _uuid(925),
    DEMO_MODEL_ROUTER_CHEAP_MODEL: _uuid(926),
    DEMO_MODEL_ROUTER_BALANCED_MODEL: _uuid(927),
    DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL: _uuid(928),
    DEMO_ONBOARDING_ROUTER_MODEL: _uuid(929),
    DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL: _uuid(942),
    DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL: _uuid(943),
    DEMO_MODEL_ROUTER_LATEST_SOL_MODEL: _uuid(944),
    DEMO_MODEL_ROUTER_OMNI_MODEL: _uuid(945),
    DEMO_MODEL_ROUTER_REASONING_MODEL: _uuid(946),
}

TEAM_LLM_PERMISSION_IDS = {
    "platform_admin": _uuid(930),
    "ai_builder_onboarding": _uuid(931),
    "hr_knowledge_users": _uuid(932),
    "customer_support_ops": _uuid(933),
    "department_development": _uuid(934),
    "department_planning": _uuid(935),
    "onboarding_platform": _uuid(936),
    "onboarding_sales": _uuid(937),
    "onboarding_people": _uuid(938),
    "onboarding_finance": _uuid(939),
}

USER_LLM_PERMISSION_IDS = {
    "author": _uuid(940),
    "tester_builder": _uuid(941),
}

DOCUMENT_IDS = {
    "hr_leave": _uuid(310),
    "hr_welfare": _uuid(311),
    "finance_sensitive": _uuid(312),
    "legal_labor_standards": _uuid(340),
    "legal_equal_employment": _uuid(341),
    "legal_equal_employment_enforcement_decree": _uuid(342),
    "legal_privacy": _uuid(343),
    "legal_occupational_safety": _uuid(344),
    "legal_retirement_benefits": _uuid(345),
    "legal_fair_hiring": _uuid(346),
    "onboarding_platform": _uuid(359),
    "onboarding_sales": _uuid(360),
    "onboarding_finance": _uuid(361),
}

RETIRED_INTERNAL_DOCUMENT_IDS = {
    "internal_onboarding": _uuid(347),
    "internal_leave_attendance": _uuid(348),
    "internal_benefits": _uuid(349),
    "internal_privacy_hr_records": _uuid(350),
    "internal_budget_alert_runbook": _uuid(351),
    "internal_cost_optimization_playbook": _uuid(352),
    "internal_developer_onboarding_rules": _uuid(353),
    "internal_developer_commit_convention": _uuid(354),
    "internal_developer_compensation_band": _uuid(355),
    "internal_compensation_access_policy": _uuid(356),
    "internal_planning_onboarding_guide": _uuid(357),
    "onboarding_company_common": _uuid(358),
}

COLLECTION_ITEM_IDS = {
    "legal_labor_standards": _uuid(370),
    "legal_equal_employment": _uuid(371),
    "legal_equal_employment_enforcement_decree": _uuid(372),
    "legal_privacy": _uuid(373),
    "legal_occupational_safety": _uuid(374),
    "legal_retirement_benefits": _uuid(375),
    "legal_fair_hiring": _uuid(376),
    "onboarding_platform": _uuid(389),
    "onboarding_sales": _uuid(390),
    "onboarding_finance": _uuid(391),
    "hr_leave": _uuid(392),
    "hr_welfare": _uuid(393),
}

RETIRED_INTERNAL_DOCUMENT_COLLECTION_ITEM_IDS = {
    "internal_onboarding": _uuid(377),
    "internal_leave_attendance": _uuid(378),
    "internal_benefits": _uuid(379),
    "internal_privacy_hr_records": _uuid(380),
    "internal_budget_alert_runbook": _uuid(381),
    "internal_cost_optimization_playbook": _uuid(382),
    "internal_developer_onboarding_rules": _uuid(383),
    "internal_developer_commit_convention": _uuid(384),
    "internal_developer_compensation_band": _uuid(385),
    "internal_compensation_access_policy": _uuid(386),
    "internal_planning_onboarding_guide": _uuid(387),
    "onboarding_company_common": _uuid(388),
}

LEGACY_DEMO_DOCUMENT_KB_KEYS = {
    "hr_leave": "hr",
    "hr_welfare": "hr_welfare",
    "finance_sensitive": "finance",
}

HR_POLICY_COLLECTION_ITEMS = (
    ("hr_leave", "hr"),
    ("hr_welfare", "hr_welfare"),
)

APP_IDS = {
    "internal_it_helpdesk_routing": uuid.UUID(
        "94000000-0000-0000-0000-000000000001"
    ),
    "onboarding_chatbot": uuid.UUID("95000000-0000-0000-0000-000000000001"),
    "new_employee_onboarding_chatbot": uuid.UUID(
        "97000000-0000-0000-0000-000000000001"
    ),
}

RETIRED_DEMO_APP_IDS = {
    "hr_bot_example": _uuid(400),
    "ticket_ops": _uuid(401),
    "ticket_ops_warning": _uuid(402),
    "ticket_ops_risk": _uuid(403),
    "ticket_ops_paused": _uuid(404),
    "test_inquiry": _uuid(405),
    "department_onboarding_chatbot": _uuid(406),
    "team_onboarding_access_control": _uuid(407),
    "model_router_ticket_ops": uuid.UUID("91000000-0000-0000-0000-000000000001"),
    "team_onboarding_adaptive_routing": _uuid(408),
    "enterprise_request_routing": _uuid(409),
}

WORKFLOW_IDS = {
    "internal_it_helpdesk_routing": uuid.UUID(
        "94000000-0000-0000-0000-000000000002"
    ),
    "onboarding_chatbot": uuid.UUID("95000000-0000-0000-0000-000000000002"),
    "new_employee_onboarding_chatbot": uuid.UUID(
        "97000000-0000-0000-0000-000000000002"
    ),
}

RETIRED_DEMO_WORKFLOW_IDS = {
    "hr_bot_example": _uuid(500),
    "ticket_ops": _uuid(501),
    "ticket_ops_warning": _uuid(502),
    "ticket_ops_risk": _uuid(503),
    "ticket_ops_paused": _uuid(504),
    "test_inquiry": _uuid(505),
    "department_onboarding_chatbot": _uuid(506),
    "team_onboarding_access_control": _uuid(507),
    "model_router_ticket_ops": uuid.UUID(
        "91000000-0000-0000-0000-000000000002"
    ),
    "team_onboarding_adaptive_routing": _uuid(508),
    "enterprise_request_routing": _uuid(510),
}

DEPLOYMENT_IDS = {
    "internal_it_helpdesk_routing": uuid.UUID(
        "94000000-0000-0000-0000-000000000003"
    ),
    "new_employee_onboarding_chatbot": uuid.UUID(
        "97000000-0000-0000-0000-000000000003"
    ),
}

RETIRED_DEMO_DEPLOYMENT_IDS = {
    "hr_bot_example": _uuid(600),
    "ticket_ops": _uuid(601),
    "ticket_ops_warning": _uuid(602),
    "ticket_ops_risk": _uuid(603),
    "ticket_ops_paused": _uuid(604),
    "test_inquiry": _uuid(605),
    "department_onboarding_chatbot": _uuid(606),
    "team_onboarding_access_control": _uuid(607),
    "model_router_ticket_ops": uuid.UUID(
        "91000000-0000-0000-0000-000000000003"
    ),
    "team_onboarding_adaptive_routing": _uuid(608),
    "enterprise_request_routing": _uuid(610),
}

LEGACY_DEMO_LLM_CREDENTIAL_ID = _uuid(700)

TEAM_PERMISSION_IDS = {
    "internal_it_helpdesk_routing": _uuid(814),
    "onboarding_chatbot_platform": _uuid(815),
    "onboarding_chatbot_sales": _uuid(816),
    "new_employee_onboarding_chatbot_platform": _uuid(817),
    "new_employee_onboarding_chatbot_sales": _uuid(818),
}

RETIRED_DEMO_TEAM_WORKFLOW_PERMISSION_IDS = {
    "customer_ticket": _uuid(800),
    "hr_bot": _uuid(801),
    "test_builder": _uuid(802),
    "test_member": _uuid(803),
    "model_router_ticket": _uuid(804),
    "department_onboarding_development": _uuid(805),
    "department_onboarding_planning": _uuid(806),
    "team_onboarding_platform": _uuid(807),
    "team_onboarding_sales": _uuid(808),
    "team_onboarding_people": _uuid(809),
    "team_onboarding_adaptive_platform": _uuid(810),
    "team_onboarding_adaptive_sales": _uuid(811),
    "team_onboarding_adaptive_people": _uuid(812),
    "enterprise_request_routing": _uuid(813),
}

RETIRED_DEMO_USER_WORKFLOW_PERMISSION_IDS = {
    "ticket_ops_warning": _uuid(830),
    "ticket_ops_risk": _uuid(831),
    "ticket_ops_paused": _uuid(832),
}

RETIRED_DEMO_RUN_IDS = tuple(_uuid(2000 + index) for index in range(12))


@dataclass(frozen=True)
class DemoUserSpec:
    key: str
    email: str
    name: str
    membership_state: str
    organization_auth_state: str
    teams: tuple[str, ...]


@dataclass(frozen=True)
class InternalITHelpdeskRunSpec:
    run_id: uuid.UUID
    department: str
    message: str
    model_name: str
    duration: float
    total_tokens: int
    total_cost: Decimal
    prompt_tokens: int
    completion_tokens: int
    node_cost: Decimal
    request_type: str | None = None
    answer: str | None = None
    status: RunStatus = RunStatus.SUCCESS


def _it_run_spec(
    run_id: str,
    department: str,
    message: str,
    model_name: str,
    duration: float,
    total_tokens: int,
    total_cost: str,
    prompt_tokens: int,
    completion_tokens: int,
    node_cost: str,
    request_type: str | None = None,
    answer: str | None = None,
) -> InternalITHelpdeskRunSpec:
    return InternalITHelpdeskRunSpec(
        run_id=uuid.UUID(run_id),
        department=department,
        message=message,
        model_name=model_name,
        duration=duration,
        total_tokens=total_tokens,
        total_cost=Decimal(total_cost),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        node_cost=Decimal(node_cost),
        request_type=request_type,
        answer=answer,
    )


IT_VPN_GUIDE = "VPN 설치 가이드는 어디 있나요?"
IT_VPN_ONBOARDING = "신규 입사자용 VPN 설치 문서 링크와 설치 순서만 알려주세요."
IT_MFA_DIAGNOSIS = (
    "MFA 재등록 후에도 SSO 로그인이 안 됩니다. 원인을 확인하고 순서대로 해결 절차를 알려주세요."
)
IT_COMPOUND_ACCESS = (
    "VPN은 연결되지만 Git 저장소는 권한 거부가 나고, SSO 세션도 계속 끊깁니다. "
    "계정, MFA, VPN 설정 중 무엇부터 점검해야 할지 판단해 주세요."
)
IT_OFFBOARDING_INCIDENT = (
    "퇴사자 관리자 계정이 아직 활성화되어 있고 고객 정보에 접근한 흔적이 있습니다. "
    "즉시 조치와 증거 보존 순서를 판단해 주세요."
)
IT_ADMIN_COMPROMISE = (
    "공용 관리자 계정 탈취가 의심되며 야간에 고객 DB 조회 기록이 발견됐습니다. "
    "서비스 영향을 최소화하면서 권한 차단과 포렌식 증거 보존을 어떤 순서로 해야 하나요?"
)


# 2026-07-20 사내 IT 문의 자동 라우팅 시연에서 실제로 실행한 9개 성공
# 기록을 재현한다. 단순 안내부터 계정 침해 의심까지 난이도가 달라질 때
# 선택 모델이 달라지는 흐름을 실행 이력 화면에서 확인할 수 있다.
INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS = (
    _it_run_spec(
        "f79d3a83-59ce-4c6f-aa11-d6112ae275ab",
        "전체",
        "신규 입사자용 VPN 설치 가이드 문서와 설치 순서만 간단히 알려 주세요.",
        "gpt-4o-mini",
        5.502066,
        4587,
        "0.003605",
        1153,
        78,
        "0.00014295",
        request_type="VPN 설치 안내",
    ),
    _it_run_spec(
        "a2897c47-101a-4121-a37f-e5b7b34d2d8d",
        "전체",
        "사내 SSO 계정을 처음 활성화하는 방법과 보안 교육을 확인할 위치를 알려 주세요.",
        "gpt-4o-mini",
        5.223074,
        5076,
        "0.004288",
        1510,
        68,
        "0.0001905",
        request_type="사내 SSO 계정 활성화 및 보안 교육 확인",
    ),
    _it_run_spec(
        "9931acb4-d7e5-4b9a-aa35-de91b71582f7",
        "플랫폼개발",
        "Git 조직 초대를 받았는지 확인하고 저장소 접근 권한을 확인하는 방법을 알려 주세요.",
        "gpt-4o-mini",
        4.685946,
        4219,
        "0.003545",
        786,
        96,
        "0.0001755",
        request_type="팀별 업무 권한",
    ),
    _it_run_spec(
        "2c5bea98-9184-4c6f-b387-8aeed6412da0",
        "플랫폼개발",
        "MFA 재등록 후 SSO 로그인이 되지 않습니다. 온보딩 문서를 기준으로 점검 순서를 알려 주세요.",
        "gpt-4.1-mini",
        5.68513,
        4701,
        "0.004389",
        1159,
        112,
        "0.0006428",
        request_type="계정과 인증",
    ),
    _it_run_spec(
        "a5bee81b-a14b-4080-89d2-30a87a73760c",
        "플랫폼개발",
        "VPN은 연결됐지만 Git 저장소는 권한 거부가 나고 SSO 세션도 끊깁니다. 계정, MFA, VPN, Git 권한 중 무엇부터 확인해야 하나요?",
        "gpt-4.1",
        5.050837,
        4765,
        "0.007271",
        1181,
        175,
        "0.003762",
        request_type="접근 권한 및 인증 장애",
    ),
    _it_run_spec(
        "880c0bd4-cd46-42f9-a555-fbb0d8e7db91",
        "플랫폼개발",
        "신입 개발자의 운영 조회 권한과 배포 권한 신청이 동시에 필요합니다. 업무 분리 원칙을 지키는 승인 순서를 정리해 주세요.",
        "gpt-4.1",
        6.815708,
        5252,
        "0.008704",
        1509,
        172,
        "0.004394",
        request_type="운영 권한 신청 절차",
    ),
    _it_run_spec(
        "64940b85-0178-494c-a637-a92eda037354",
        "보안",
        "퇴사자의 관리자 계정이 아직 활성화되어 VPN과 Git 접근이 가능한 것으로 보입니다. 차단, 증거 보존, 에스컬레이션 순서를 판단해 주세요.",
        "gpt-5.4",
        9.626818,
        5447,
        "0.015320",
        1524,
        537,
        "0.011865",
        request_type="퇴사자 권한 잔존 및 무단 접근 위험",
    ),
    _it_run_spec(
        "2c19f120-02f7-4a6f-a983-a73c01bf425f",
        "정보보안팀",
        "운영 조회 권한이 외부 접속에서 사용된 정황이 있습니다. MFA, VPN 세션, Git 토큰 중 어떤 조치를 먼저 해야 하는지 근거와 함께 정리해 주세요.",
        "gpt-5.6-terra",
        11.921443,
        5868,
        "0.018618",
        1528,
        675,
        "0.013945",
        request_type="보안 사고 의심 - 운영 조회 권한의 외부 접속 정황",
    ),
    _it_run_spec(
        "f4c671f9-5c32-4e00-95ca-e11f5fe560eb",
        "플랫폼개발",
        "보안 교육을 아직 완료하지 않은 신규 입사자가 운영 저장소 접근과 배포 권한을 요청했습니다. SSO, VPN, Git 권한, 승인 절차를 함께 고려해 허용 여부를 판단해 주세요.",
        "gpt-5.6-terra",
        6.93603,
        5328,
        "0.012234",
        1549,
        306,
        "0.0084625",
        request_type="계정·권한 관리 / 운영 저장소 및 배포 권한 요청",
    ),
)


@dataclass(frozen=True)
class DemoKnowledgeSeedSpec:
    key: str
    name: str
    description: str
    filename: str
    summary: str
    source_tier: str
    classification: str
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    collection_key: str | None
    content: str | None = None
    legal_filename_pattern: str | None = None
    legal_required_tokens: tuple[str, ...] = ()
    chunk_size: int = 1000
    chunk_overlap: int = 150
    source_page_indexes: tuple[int, ...] | None = None


ONBOARDING_PDF_SPECS = (
    DemoKnowledgeSeedSpec(
        key="onboarding_platform",
        name="온보딩 문서: 플랫폼개발팀",
        description="플랫폼개발팀 개발환경과 접근 신청 절차",
        filename="platform_team_onboarding_v4.pdf",
        summary="플랫폼개발팀 첫 주 일정, Git, VPN과 운영 조회 권한 신청",
        source_tier="private",
        classification="internal",
        tags=("온보딩", "플랫폼", "개발환경", "접근 권한"),
        keywords=("Git", "VPN", "운영 조회", "배포 권한"),
        collection_key="team_onboarding_access_control",
        chunk_size=800,
        chunk_overlap=100,
        # 마지막 페이지는 role_acl=manager다. 현재 runtime은 chunk ACL을
        # 강제하지 않으므로 employee KB 복사본에서는 제외한다.
        source_page_indexes=(0, 1, 2),
    ),
    DemoKnowledgeSeedSpec(
        key="onboarding_sales",
        name="온보딩 문서: 영업팀",
        description="영업팀 CRM과 고객 데이터 취급 온보딩 절차",
        filename="sales_team_onboarding_v2.pdf",
        summary="영업팀 첫 주 일정, CRM 접근, 견적 승인과 고객 데이터 취급",
        source_tier="private",
        classification="internal",
        tags=("온보딩", "영업", "CRM", "고객 데이터"),
        keywords=("CRM", "고객 계정", "견적 승인", "세일즈 플레이북"),
        collection_key="team_onboarding_access_control",
        chunk_size=800,
        chunk_overlap=100,
    ),
    DemoKnowledgeSeedSpec(
        key="onboarding_finance",
        name="온보딩 문서: 재무팀",
        description="재무팀 회계 시스템과 지급 승인 온보딩 절차",
        filename="finance_team_onboarding_v3.pdf",
        summary="재무팀 첫 주 일정, 회계 시스템, 결산과 지급 승인 절차",
        source_tier="private",
        classification="confidential",
        tags=("온보딩", "재무", "회계", "지급 승인"),
        keywords=("회계 시스템", "결산", "지급 요청", "업무 분리"),
        collection_key="team_onboarding_access_control",
        chunk_size=800,
        chunk_overlap=100,
    ),
)

ONBOARDING_KB_SPECS = {
    spec.key: (spec.name, spec.filename) for spec in ONBOARDING_PDF_SPECS
}


USER_SPECS = [
    DemoUserSpec(
        "admin",
        "admin@nodease.demo",
        "관리자 김도윤",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MANAGER,
        ("platform_admin",),
    ),
    DemoUserSpec(
        "rookie",
        "rookie@nodease.demo",
        "신입사원 이서연",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("ai_builder_onboarding", "hr_knowledge_users"),
    ),
    DemoUserSpec(
        "author",
        "author@nodease.demo",
        "운영자 박민준",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("customer_support_ops",),
    ),
    DemoUserSpec(
        "tester_manager",
        "tester.manager@nodease.demo",
        "테스트 관리자",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MANAGER,
        ("platform_admin",),
    ),
    DemoUserSpec(
        "tester_builder",
        "tester.builder@nodease.demo",
        "테스트 빌더",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("tester_builder",),
    ),
    DemoUserSpec(
        "tester_member",
        "tester.member@nodease.demo",
        "테스트 멤버",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("tester_member",),
    ),
    DemoUserSpec(
        "developer",
        "dev@nodease.demo",
        "개발팀 사용자 정개발",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("department_development",),
    ),
    DemoUserSpec(
        "planning",
        "planning@nodease.demo",
        "기획팀 사용자 김기획",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("department_planning",),
    ),
    DemoUserSpec(
        "invited",
        "invited@nodease.demo",
        "초대대기 한지민",
        ORGANIZATION_MEMBERSHIP_INVITED,
        ORGANIZATION_AUTH_MEMBER,
        (),
    ),
    DemoUserSpec(
        "suspended",
        "suspended@nodease.demo",
        "정지회원 최유진",
        ORGANIZATION_MEMBERSHIP_SUSPENDED,
        ORGANIZATION_AUTH_MEMBER,
        (),
    ),
    DemoUserSpec(
        "removed",
        "removed@nodease.demo",
        "제거회원 정하늘",
        ORGANIZATION_MEMBERSHIP_REMOVED,
        ORGANIZATION_AUTH_MEMBER,
        (),
    ),
    DemoUserSpec(
        "onboarding_platform_rookie",
        "seoyeon.kim@nodease.demo",
        "김서연",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("onboarding_platform",),
    ),
    DemoUserSpec(
        "onboarding_sales_rookie",
        "junho.lee@nodease.demo",
        "이준호",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("onboarding_sales",),
    ),
    DemoUserSpec(
        "onboarding_people_manager",
        "jimin.park@nodease.demo",
        "박지민",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MANAGER,
        ("onboarding_people",),
    ),
]

DEMO_EMAILS = tuple(spec.email for spec in USER_SPECS)

TEAM_SPECS = {
    "platform_admin": ("플랫폼 관리팀", "관리자 권한, 감사 로그, 운영 지표를 확인하는 팀"),
    "ai_builder_onboarding": (
        "AI 빌더 온보딩팀",
        "신입사원이 권한 신청 후 AI 빌더를 사용하는 온보딩 팀",
    ),
    "customer_support_ops": (
        "고객지원 운영팀",
        "고객 티켓 처리 워크플로우를 운영하고 비용 최적화를 수행하는 팀",
    ),
    "hr_knowledge_users": (
        "인사 지식 활용팀",
        "휴가, 복지, 인사 정책 문서를 검색해 답변을 받는 팀",
    ),
    "finance_restricted": ("재무 제한 문서팀", "민감 재무 문서 권한 대조용 팀"),
    "tester_builder": ("테스트 빌더팀", "자유 기능 확인용 빌더 팀"),
    "tester_member": ("테스트 일반팀", "일반 멤버 권한 제한 확인용 팀"),
    "department_development": (
        "개발팀",
        "플랫폼개발팀 온보딩 Knowledge를 사용하는 데모 팀",
    ),
    "department_planning": (
        "기획팀",
        "영업팀 온보딩 Knowledge를 사용하는 데모 팀",
    ),
    "onboarding_platform": (
        "플랫폼개발팀",
        "플랫폼개발팀 온보딩 문서를 사용하는 데모 팀",
    ),
    "onboarding_sales": (
        "영업팀",
        "영업팀 온보딩 문서를 사용하는 데모 팀",
    ),
    "onboarding_people": (
        "People 팀",
        "팀별 온보딩 문서와 권한 및 감사 로그를 관리하는 데모 팀",
    ),
    "onboarding_finance": (
        "재무팀",
        "재무팀 온보딩 문서를 사용하는 권한 경계 데모 팀",
    ),
}


TEST_ORG_ID = _uuid(9000)
TEST_USER_IDS = {
    "admin": _uuid(9001),
    "builder": _uuid(9002),
    "member": _uuid(9003),
    "invited": _uuid(9004),
    "suspended": _uuid(9005),
}
TEST_TEAM_IDS = {
    "qa_admin": _uuid(9010),
    "qa_builder": _uuid(9011),
    "qa_member": _uuid(9012),
}
TEST_APP_ID = _uuid(9020)
TEST_WORKFLOW_ID = _uuid(9021)
TEST_DEPLOYMENT_ID = _uuid(9022)
TEST_PERMISSION_IDS = {
    "builder": _uuid(9030),
    "member": _uuid(9031),
}

TEST_USER_SPECS = [
    DemoUserSpec(
        "admin",
        "test.admin@test.nodease.demo",
        "테스트 관리자",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MANAGER,
        ("qa_admin",),
    ),
    DemoUserSpec(
        "builder",
        "test.builder@test.nodease.demo",
        "테스트 빌더",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("qa_builder",),
    ),
    DemoUserSpec(
        "member",
        "test.member@test.nodease.demo",
        "테스트 멤버",
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_AUTH_MEMBER,
        ("qa_member",),
    ),
    DemoUserSpec(
        "invited",
        "test.invited@test.nodease.demo",
        "테스트 초대대기",
        ORGANIZATION_MEMBERSHIP_INVITED,
        ORGANIZATION_AUTH_MEMBER,
        (),
    ),
    DemoUserSpec(
        "suspended",
        "test.suspended@test.nodease.demo",
        "테스트 정지회원",
        ORGANIZATION_MEMBERSHIP_SUSPENDED,
        ORGANIZATION_AUTH_MEMBER,
        (),
    ),
]

TEST_EMAILS = {spec.email for spec in TEST_USER_SPECS}
TEST_TEAM_SPECS = {
    "qa_admin": ("QA 관리자팀", "테스트 manager 권한 확인용 팀"),
    "qa_builder": ("QA 빌더팀", "테스트 workflow 생성/편집 확인용 팀"),
    "qa_member": ("QA 일반팀", "테스트 viewer 권한 확인용 팀"),
}

LEGAL_DOCUMENT_SPECS = (
    DemoKnowledgeSeedSpec(
        key="legal_labor_standards",
        name="공개 법령: 근로기준법",
        description="휴가, 근로시간, 임금 등 온보딩 질의에 참조하는 공개 법령 KB",
        filename="근로기준법.pdf",
        summary="근로기준법 공개 법령 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "labor", "onboarding"),
        keywords=("근로기준법", "연차", "휴가", "근로시간", "임금", "가족돌봄"),
        collection_key="legal_public",
        legal_filename_pattern="근로기준법",
    ),
    DemoKnowledgeSeedSpec(
        key="legal_equal_employment",
        name="공개 법령: 남녀고용평등법",
        description="일·가정 양립, 가족돌봄 제도 질의에 참조하는 공개 법령 KB",
        filename="남녀고용평등과 일가정 양립 지원에 관한 법률.pdf",
        summary="남녀고용평등과 일·가정 양립 지원에 관한 법률 공개 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "labor", "family-care"),
        keywords=("남녀고용평등", "일가정", "가족돌봄휴가", "육아휴직", "배우자 출산휴가"),
        collection_key="legal_public",
        legal_filename_pattern="지원에 관한 법률(",
        legal_required_tokens=("남녀고용평등",),
    ),
    DemoKnowledgeSeedSpec(
        key="legal_equal_employment_enforcement_decree",
        name="공개 법령: 남녀고용평등법 시행령",
        description="일·가정 양립 제도 세부 기준 질의에 참조하는 공개 시행령 KB",
        filename="남녀고용평등과 일가정 양립 지원에 관한 법률 시행령.pdf",
        summary="남녀고용평등과 일·가정 양립 지원에 관한 법률 시행령 공개 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "labor", "family-care", "decree"),
        keywords=("시행령", "가족돌봄", "육아기", "근로시간 단축", "일가정"),
        collection_key="legal_public",
        legal_filename_pattern="지원에 관한 법률 시행령",
        legal_required_tokens=("남녀고용평등",),
    ),
    DemoKnowledgeSeedSpec(
        key="legal_privacy",
        name="공개 법령: 개인정보 보호법",
        description="인사기록과 개인정보 처리 기준 질의에 참조하는 공개 법령 KB",
        filename="개인정보 보호법.pdf",
        summary="개인정보 보호법 공개 법령 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "privacy", "hr-records"),
        keywords=("개인정보", "민감정보", "처리", "보존", "접근권한", "동의"),
        collection_key="legal_public",
        legal_filename_pattern="개인정보 보호법",
    ),
    DemoKnowledgeSeedSpec(
        key="legal_occupational_safety",
        name="공개 법령: 산업안전보건법",
        description="안전보건 교육과 작업장 안전 질의에 참조하는 공개 법령 KB",
        filename="산업안전보건법.pdf",
        summary="산업안전보건법 공개 법령 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "safety", "onboarding"),
        keywords=("산업안전보건", "안전교육", "보건", "위험성", "근로자"),
        collection_key="legal_public",
        legal_filename_pattern="산업안전보건법",
    ),
    DemoKnowledgeSeedSpec(
        key="legal_retirement_benefits",
        name="공개 법령: 근로자퇴직급여 보장법",
        description="퇴직급여 제도 질의에 참조하는 공개 법령 KB",
        filename="근로자퇴직급여 보장법.pdf",
        summary="근로자퇴직급여 보장법 공개 법령 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "retirement", "benefits"),
        keywords=("퇴직급여", "퇴직연금", "근로자", "급여", "보장"),
        collection_key="legal_public",
        legal_filename_pattern="근로자퇴직급여 보장법",
    ),
    DemoKnowledgeSeedSpec(
        key="legal_fair_hiring",
        name="공개 법령: 채용절차의 공정화에 관한 법률",
        description="채용 절차와 입사 서류 질의에 참조하는 공개 법령 KB",
        filename="채용절차의 공정화에 관한 법률.pdf",
        summary="채용절차의 공정화에 관한 법률 공개 PDF",
        source_tier="public",
        classification="public_law",
        tags=("law", "hiring", "onboarding"),
        keywords=("채용절차", "공정화", "입사지원", "채용서류", "구직자"),
        collection_key="legal_public",
        legal_filename_pattern="채용절차의 공정화에 관한 법률",
    ),
)

DEMO_DOCUMENT_SPECS = LEGAL_DOCUMENT_SPECS

HR_DOCUMENT_SPECS = (
    DemoKnowledgeSeedSpec(
        key="hr_leave",
        name="사내 휴가 정책 지식베이스",
        description="휴가 신청과 근태 유의 사항을 담은 데모 지식베이스",
        filename="휴가 제도 안내.md",
        summary="가족돌봄휴가는 연차와 이어서 사용할 수 있으며, 사내 인사 포털에서 신청합니다.",
        source_tier="private",
        classification="internal",
        tags=("인사", "휴가", "근태"),
        keywords=("가족돌봄휴가", "연차휴가", "휴가 신청"),
        collection_key="hr_policies",
        content="""# 휴가 제도 안내

## 가족돌봄휴가

가족의 질병, 사고, 노령 또는 자녀 양육으로 돌봄이 필요한 경우 가족돌봄휴가를 신청할 수 있습니다.
가족돌봄휴가는 연차휴가와 이어서 사용할 수 있으며, 신청 시 사유와 예상 사용 기간을 함께 입력합니다.

## 신청 경로

휴가는 사내 인사 포털 > 근태/휴가 > 휴가 신청 메뉴에서 신청합니다.
긴급한 사유가 아니라면 사용 예정일 전까지 팀 리더 승인을 받아야 합니다.

## 유의 사항

개인의 병가 기록, 타인의 근태 현황, 인사평가 결과는 일반 사내 문서 검색 권한으로 조회할 수 없습니다.
""",
    ),
    DemoKnowledgeSeedSpec(
        key="hr_welfare",
        name="사내 복지 정책 지식베이스",
        description="복지 포인트와 경조사 지원을 담은 데모 지식베이스",
        filename="복지 제도 안내.md",
        summary="복지 포인트와 경조사 지원은 인사 지식 활용팀 권한으로 조회할 수 있습니다.",
        source_tier="private",
        classification="internal",
        tags=("인사", "복지", "경조사"),
        keywords=("복지 포인트", "경조사 지원", "복지 포털"),
        collection_key="hr_policies",
        content="""# 복지 제도 안내

## 복지 포인트

복지 포인트는 매년 초 재직 상태와 근속 조건에 따라 지급됩니다.
사용 가능 항목은 건강관리, 자기계발, 가족 지원, 문화생활로 구분됩니다.

## 경조사 지원

경조사 지원은 사내 복지 포털에서 신청하며, 증빙 서류가 필요한 항목은 신청 후 7일 이내에 제출해야 합니다.

## 문의

복지 제도 일반 문의는 인사 지식 활용팀 채널을 통해 접수합니다.
""",
    ),
)

INDEXED_DEMO_DOCUMENT_SPECS = (*DEMO_DOCUMENT_SPECS, *HR_DOCUMENT_SPECS)


def demo_summary(profile: str = "demo") -> dict[str, Any]:
    """Return a lightweight summary for --dry-run output."""
    if profile == "test":
        return {
            "profile": "test",
            "seed_version": DEMO_SEED_VERSION,
            "organization": "노디즈 테스트 조직",
            "users": [spec.email for spec in TEST_USER_SPECS],
            "teams": [name for name, _ in TEST_TEAM_SPECS.values()],
            "apps": ["테스트용 기능 검증 워크플로우"],
            "reset_scope": "test profile fixed UUID rows only",
            "credentials": "not seeded",
            "knowledge_documents": "not seeded",
        }
    return {
        "profile": "demo",
        "seed_version": DEMO_SEED_VERSION,
        "organization": "노디즈 데모 조직",
        "users": [spec.email for spec in USER_SPECS],
        "teams": [name for name, _ in TEAM_SPECS.values()],
        "apps": [
            "온보딩 챗봇",
            "사내 IT 문의 자동 처리",
            "신입 사원 온보딩 챗봇",
        ],
        "reset_scope": "demo seed fixed UUID rows only",
        "credentials": (
            "non-secret demo metadata row by default; pass "
            "--enable-runtime-openai-credential to validate OPENAI_API_KEY or "
            "prompt securely before seeding a runtime credential"
        ),
        "knowledge_documents": {
            "public_law_pdfs": len(LEGAL_DOCUMENT_SPECS),
            "internal_markdown_docs": len(HR_DOCUMENT_SPECS),
            "bundled_onboarding_pdfs": len(ONBOARDING_PDF_SPECS),
            "embedding_model": DEMO_EMBEDDING_MODEL,
            "fixture": DEMO_KNOWLEDGE_FIXTURE_PATH.as_posix(),
        },
        "bundled_onboarding_pdf_sources": {
            spec.key: (DEMO_ONBOARDING_PDF_DIR / spec.filename).as_posix()
            for spec in ONBOARDING_PDF_SPECS
        },
    }


def _adopt_existing_demo_user_ids(db: Session) -> None:
    """Reuse existing local users with the demo emails instead of deleting them."""
    for spec in USER_SPECS:
        existing = db.query(User).filter(User.email == spec.email).first()
        if existing is not None:
            USER_IDS[spec.key] = existing.id


def _demo_options(key: str) -> dict[str, Any]:
    return {
        "demo_seed": True,
        "demo_seed_version": DEMO_SEED_VERSION,
        "demo_seed_key": key,
    }


def _write_demo_document(filename: str, content: str) -> str:
    last_error: OSError | None = None
    for base_dir in DEMO_UPLOAD_DIRS:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            path = base_dir / filename
            path.write_text(content, encoding="utf-8")
            return path.as_posix()
        except OSError as error:
            last_error = error
    raise RuntimeError(
        f"demo 문서를 저장할 수 있는 upload 경로가 없습니다: {DEMO_UPLOAD_DIRS}"
    ) from last_error


def _copy_demo_source_file(source_path: Path, target_filename: str) -> str:
    last_error: OSError | None = None
    for base_dir in DEMO_UPLOAD_DIRS:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            target_path = base_dir / target_filename
            shutil.copyfile(source_path, target_path)
            return target_path.as_posix()
        except OSError as error:
            last_error = error
    raise RuntimeError(
        f"demo 원본 파일을 저장할 수 있는 upload 경로가 없습니다: {DEMO_UPLOAD_DIRS}"
    ) from last_error


def _copy_onboarding_pdf(spec: DemoKnowledgeSeedSpec, source_path: Path) -> str:
    if spec.source_page_indexes is None:
        return _copy_demo_source_file(source_path, spec.filename)

    try:
        import fitz
    except ImportError as error:
        raise RuntimeError(
            "페이지 제한 온보딩 PDF를 만들려면 PyMuPDF가 필요합니다."
        ) from error

    last_error: OSError | None = None
    for base_dir in DEMO_UPLOAD_DIRS:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            target_path = base_dir / spec.filename
            with fitz.open(source_path) as source_document, fitz.open() as target_document:
                for page_index in spec.source_page_indexes:
                    if page_index < 0 or page_index >= source_document.page_count:
                        raise ValueError(
                            f"{spec.filename} page index가 범위를 벗어났습니다: {page_index}"
                        )
                    target_document.insert_pdf(
                        source_document,
                        from_page=page_index,
                        to_page=page_index,
                    )
                target_path.write_bytes(target_document.tobytes())
            return target_path.as_posix()
        except OSError as error:
            last_error = error
    raise RuntimeError(
        f"demo 온보딩 PDF를 저장할 수 있는 upload 경로가 없습니다: {DEMO_UPLOAD_DIRS}"
    ) from last_error


def _resolve_legal_pdf(spec: DemoKnowledgeSeedSpec) -> Path:
    if not DEMO_LEGAL_DOCS_LABOR_DIR.exists():
        raise FileNotFoundError(
            "법령 PDF 디렉터리가 없습니다: "
            f"{DEMO_LEGAL_DOCS_LABOR_DIR.as_posix()}"
        )
    pattern = spec.legal_filename_pattern or spec.name
    candidates = []
    for candidate in sorted(DEMO_LEGAL_DOCS_LABOR_DIR.glob("*.pdf")):
        name = candidate.name
        if pattern not in name:
            continue
        if all(token in name for token in spec.legal_required_tokens):
            candidates.append(candidate)
    if candidates:
        return max(candidates, key=lambda candidate: (_legal_pdf_date(candidate), candidate.name))
    available = ", ".join(candidate.name for candidate in DEMO_LEGAL_DOCS_LABOR_DIR.glob("*.pdf")) or "none"
    raise FileNotFoundError(
        f"{spec.name} PDF를 찾지 못했습니다. pattern={pattern!r}, available={available}"
    )


def _resolve_onboarding_pdf(spec: DemoKnowledgeSeedSpec) -> Path:
    source_path = DEMO_ONBOARDING_PDF_DIR / spec.filename
    if not source_path.is_file():
        raise FileNotFoundError(
            f"온보딩 PDF를 찾지 못했습니다: {source_path.as_posix()}"
        )
    return source_path


def _legal_pdf_date(path: Path) -> str:
    matches = re.findall(r"\((\d{8})\)", path.name)
    return matches[-1] if matches else "00000000"


def _regenerate_knowledge_fixture_requested() -> bool:
    return os.getenv(DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV) == "1"


def _runtime_openai_credential_requested() -> bool:
    return os.getenv(DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV) == "1"


def _should_index_onboarding_pdfs() -> bool:
    return (
        _regenerate_knowledge_fixture_requested()
        or _runtime_openai_credential_requested()
    )


def _demo_knowledge_fixture_exists() -> bool:
    return DEMO_KNOWLEDGE_FIXTURE_PATH.exists()


def _read_demo_knowledge_fixture() -> dict[str, Any]:
    if not _demo_knowledge_fixture_exists():
        raise FileNotFoundError(
            f"demo Knowledge fixture가 없습니다: {DEMO_KNOWLEDGE_FIXTURE_PATH.as_posix()}"
        )

    header: dict[str, Any] | None = None
    documents: dict[str, dict[str, Any]] = {}
    chunks_by_document: dict[str, list[dict[str, Any]]] = {}

    with gzip.open(DEMO_KNOWLEDGE_FIXTURE_PATH, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record_type = record.get("record_type")
            if record_type == "header":
                header = record
            elif record_type == "document":
                key = str(record["key"])
                documents[key] = record
            elif record_type == "chunk":
                key = str(record["document_key"])
                chunks_by_document.setdefault(key, []).append(record)
            else:
                raise ValueError(
                    f"Unknown demo Knowledge fixture record at line {line_number}: {record_type}"
                )

    if header is None:
        raise ValueError("demo Knowledge fixture header가 없습니다.")
    if header.get("embedding_model") != DEMO_EMBEDDING_MODEL:
        raise ValueError(
            "demo Knowledge fixture embedding_model이 seed 설정과 다릅니다: "
            f"{header.get('embedding_model')}"
        )
    if header.get("embedding_dimension") != DEMO_EMBEDDING_DIMENSION:
        raise ValueError(
            "demo Knowledge fixture embedding_dimension이 seed 설정과 다릅니다: "
            f"{header.get('embedding_dimension')}"
        )

    expected_keys = {spec.key for spec in INDEXED_DEMO_DOCUMENT_SPECS}
    missing_documents = sorted(expected_keys - set(documents))
    if missing_documents:
        raise ValueError(
            f"demo Knowledge fixture document 누락: {', '.join(missing_documents)}"
        )
    missing_chunks = sorted(
        key for key in expected_keys if not chunks_by_document.get(key)
    )
    if missing_chunks:
        raise ValueError(
            f"demo Knowledge fixture chunk 누락: {', '.join(missing_chunks)}"
        )

    for key, chunks in chunks_by_document.items():
        chunks.sort(key=lambda item: int(item.get("chunk_index", 0)))
        for chunk in chunks:
            embedding = chunk.get("embedding")
            if not isinstance(embedding, list) or len(embedding) != DEMO_EMBEDDING_DIMENSION:
                raise ValueError(
                    f"demo Knowledge fixture embedding 차원 오류: {key}#{chunk.get('chunk_index')}"
                )

    return {
        "header": header,
        "documents": documents,
        "chunks_by_document": chunks_by_document,
    }


def _demo_knowledge_fixture_or_none() -> dict[str, Any] | None:
    if _regenerate_knowledge_fixture_requested():
        return None
    if not _demo_knowledge_fixture_exists():
        return None
    return _read_demo_knowledge_fixture()


def _write_demo_knowledge_fixture(records: list[dict[str, Any]]) -> None:
    DEMO_KNOWLEDGE_FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "record_type": "header",
        "fixture_version": DEMO_SEED_VERSION,
        "embedding_model": DEMO_EMBEDDING_MODEL,
        "embedding_dimension": DEMO_EMBEDDING_DIMENSION,
        "document_keys": [spec.key for spec in INDEXED_DEMO_DOCUMENT_SPECS],
        "storage": "plaintext_chunks_with_precomputed_embeddings",
    }
    with gzip.open(DEMO_KNOWLEDGE_FIXTURE_PATH, "wt", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(header, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _load_seed_env() -> None:
    env_path = DEMO_REPO_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_path, override=False)
        except ImportError:
            pass


def _require_seed_env(name: str) -> str:
    _load_seed_env()
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} 환경변수가 필요합니다. repo root .env 또는 실행 환경에 설정하세요."
        )
    return value


def _mask_demo_api_key(api_key: str) -> str:
    if len(api_key) <= 10:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def _embed_text_batches(texts: list[str]) -> list[list[float]]:
    api_key = _require_seed_env("OPENAI_API_KEY")
    client = OpenAIClient(
        model_id=DEMO_EMBEDDING_MODEL,
        credentials={
            "apiKey": api_key,
            "baseUrl": DEMO_OPENAI_BASE_URL,
        },
    )
    embeddings: list[list[float]] = []
    batch_size = 64
    for start in range(0, len(texts), batch_size):
        batch = [
            text if text and text.strip() else " "
            for text in texts[start : start + batch_size]
        ]
        batch_embeddings = client.embed_batch_sync(batch)
        if len(batch_embeddings) != len(batch):
            raise RuntimeError(
                "embedding 응답 개수가 batch 입력과 다릅니다: "
                f"expected={len(batch)}, actual={len(batch_embeddings)}"
            )
        for embedding in batch_embeddings:
            vector = list(embedding)
            if len(vector) != DEMO_EMBEDDING_DIMENSION:
                raise RuntimeError(
                    f"{DEMO_EMBEDDING_MODEL} embedding 차원이 "
                    f"{DEMO_EMBEDDING_DIMENSION}이 아닙니다: {len(vector)}"
                )
            embeddings.append(vector)
    if len(embeddings) != len(texts):
        raise RuntimeError(
            f"embedding 응답 개수가 입력과 다릅니다: "
            f"expected={len(texts)}, actual={len(embeddings)}"
        )
    return embeddings


def _document_meta_base(
    spec: DemoKnowledgeSeedSpec,
    source_path: str,
    *,
    source_available: bool = True,
) -> dict[str, Any]:
    legal_effective_date = None
    if spec.source_tier == "public":
        match = re.search(r"\((\d{8})\)", Path(source_path).name)
        legal_effective_date = match.group(1) if match else None
    metadata = {
        **_demo_options(f"document-{spec.key}"),
        "summary": spec.summary,
        "classification": spec.classification,
        "tags": list(spec.tags),
        "source_tier": spec.source_tier,
        "source_type": "FILE",
        "source_filename": Path(source_path).name,
        "source_available": source_available,
        "legal_effective_date": legal_effective_date,
        "remove_whitespace": True,
        "selection_mode": "all",
    }
    if spec.source_page_indexes is not None:
        metadata["included_source_pages"] = [
            page_index + 1 for page_index in spec.source_page_indexes
        ]
        metadata["page_filter_reason"] = "unsupported_chunk_role_acl"
    return metadata


def _preserve_indexing_meta(
    existing: Document | None,
    base_meta: dict[str, Any],
) -> dict[str, Any]:
    if existing is None or not isinstance(existing.meta_info, dict):
        return base_meta
    preserved = {
        key: existing.meta_info[key]
        for key in ("chunking_mode", "chunking_fingerprint_hash", "hierarchy_version")
        if key in existing.meta_info
    }
    return {**base_meta, **preserved}


def _extract_raw_blocks(db: Session, doc: Document) -> list[dict[str, Any]]:
    from apps.gateway.services.ingestion.processors.file_processor import FileProcessor

    processor = FileProcessor(db, USER_IDS["admin"])
    result = processor.process({"document_id": str(doc.id), "file_path": doc.file_path})
    if result.metadata.get("error"):
        raise RuntimeError(f"{doc.filename} 파싱 실패: {result.metadata['error']}")
    if not result.chunks:
        raise RuntimeError(f"{doc.filename}에서 추출 가능한 텍스트가 없습니다.")
    return result.chunks


def _insert_demo_chunks_from_fixture(
    db: Session,
    specs: tuple[DemoKnowledgeSeedSpec, ...],
    fixture: dict[str, Any],
) -> None:
    _require_seed_env("ENCRYPTION_KEY")

    from apps.shared.utils.encryption import encryption_manager

    documents = fixture["documents"]
    chunks_by_document = fixture["chunks_by_document"]
    for spec in specs:
        doc = db.get(Document, DOCUMENT_IDS[spec.key])
        if doc is None:
            raise RuntimeError(f"demo 문서 row가 없습니다: {spec.key}")
        fixture_doc = documents[spec.key]
        chunks = chunks_by_document[spec.key]

        existing_chunk_count = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .count()
        )
        if (
            existing_chunk_count > 0
            and doc.status == "completed"
            and doc.content_hash == fixture_doc["content_hash"]
            and doc.embedding_model == DEMO_EMBEDDING_MODEL
            and (doc.meta_info or {}).get("chunking_fingerprint_hash")
            == fixture_doc["chunking_fingerprint_hash"]
        ):
            continue

        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
            synchronize_session=False
        )
        for chunk in chunks:
            content = str(chunk["content"])
            metadata = dict(chunk.get("metadata") or {})
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    knowledge_base_id=doc.knowledge_base_id,
                    content=encryption_manager.encrypt(content),
                    embedding=chunk["embedding"],
                    chunk_index=int(chunk["chunk_index"]),
                    chunk_level=chunk.get("chunk_level") or "flat",
                    section_path=chunk.get("section_path"),
                    heading=chunk.get("heading"),
                    token_count=int(chunk.get("token_count") or 0),
                    metadata_=metadata,
                )
            )

        doc.status = "completed"
        doc.error_message = None
        doc.content_hash = fixture_doc["content_hash"]
        doc.embedding_model = DEMO_EMBEDDING_MODEL
        doc.updated_at = _now()
        meta = dict(doc.meta_info or {})
        meta["chunking_mode"] = fixture_doc["chunking_mode"]
        meta["chunking_fingerprint_hash"] = fixture_doc["chunking_fingerprint_hash"]
        meta["fixture_source"] = DEMO_KNOWLEDGE_FIXTURE_PATH.name
        doc.meta_info = meta
        db.add(doc)
        db.flush()


def _index_demo_documents_from_sources(
    db: Session,
    specs: tuple[DemoKnowledgeSeedSpec, ...],
    *,
    persist_fixture: bool = True,
) -> None:
    _require_seed_env("ENCRYPTION_KEY")

    from apps.gateway.services.ingestion.service import IngestionOrchestrator
    from apps.shared.utils.encryption import encryption_manager
    from apps.shared.utils.template_utils import count_tokens

    fixture_records: list[dict[str, Any]] = []
    for spec in specs:
        doc = db.get(Document, DOCUMENT_IDS[spec.key])
        if doc is None:
            raise RuntimeError(f"demo 문서 row가 없습니다: {spec.key}")

        orchestrator = IngestionOrchestrator(
            db,
            user_id=USER_IDS["admin"],
            chunk_size=doc.chunk_size,
            chunk_overlap=doc.chunk_overlap,
            ai_model=DEMO_EMBEDDING_MODEL,
        )
        raw_blocks = _extract_raw_blocks(db, doc)
        chunking_result = orchestrator._build_document_chunks(doc, raw_blocks)
        texts = [chunk["content"] for chunk in chunking_result.chunks]
        embeddings = _embed_text_batches(texts)

        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
            synchronize_session=False
        )
        fixture_records.append(
            {
                "record_type": "document",
                "key": spec.key,
                "filename": doc.filename,
                "content_hash": chunking_result.content_hash,
                "chunking_mode": chunking_result.chunking_mode,
                "chunking_fingerprint_hash": chunking_result.chunking_fingerprint,
                "chunk_size": doc.chunk_size,
                "chunk_overlap": doc.chunk_overlap,
                "source_filename": (doc.meta_info or {}).get("source_filename"),
                "source_available": bool((doc.meta_info or {}).get("source_available")),
                "legal_effective_date": (doc.meta_info or {}).get("legal_effective_date"),
            }
        )
        for index, (chunk, embedding) in enumerate(
            zip(chunking_result.chunks, embeddings)
        ):
            content = chunk["content"]
            metadata = dict(chunk.get("metadata") or {})
            metadata.update(
                {
                    "classification": spec.classification,
                    "tags": list(spec.tags),
                    "keywords": list(dict.fromkeys((*spec.keywords, spec.name))),
                    "source_type": "FILE",
                    "source_tier": spec.source_tier,
                    "source_hash": chunking_result.content_hash,
                    "chunk_level": chunk.get("chunk_level") or "flat",
                }
            )
            token_count = chunk.get("token_count") or count_tokens(content)
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    knowledge_base_id=doc.knowledge_base_id,
                    content=encryption_manager.encrypt(content),
                    embedding=embedding,
                    chunk_index=index,
                    chunk_level=chunk.get("chunk_level") or "flat",
                    section_path=chunk.get("section_path"),
                    heading=chunk.get("heading"),
                    token_count=token_count,
                    metadata_=metadata,
                )
            )
            fixture_records.append(
                {
                    "record_type": "chunk",
                    "document_key": spec.key,
                    "chunk_index": index,
                    "content": content,
                    "embedding": embedding,
                    "token_count": token_count,
                    "metadata": metadata,
                    "chunk_level": chunk.get("chunk_level") or "flat",
                    "section_path": chunk.get("section_path"),
                    "heading": chunk.get("heading"),
                }
            )

        doc.status = "completed"
        doc.error_message = None
        doc.content_hash = chunking_result.content_hash
        doc.embedding_model = DEMO_EMBEDDING_MODEL
        doc.updated_at = _now()
        meta = dict(doc.meta_info or {})
        meta["chunking_mode"] = chunking_result.chunking_mode
        meta["chunking_fingerprint_hash"] = chunking_result.chunking_fingerprint
        if persist_fixture:
            meta["fixture_source"] = DEMO_KNOWLEDGE_FIXTURE_PATH.name
        else:
            meta.pop("fixture_source", None)
            meta["indexed_from_bundled_source"] = True
        doc.meta_info = meta
        db.add(doc)
        db.flush()

    if persist_fixture:
        _write_demo_knowledge_fixture(fixture_records)


def _index_demo_documents(
    db: Session,
    specs: tuple[DemoKnowledgeSeedSpec, ...],
    fixture: dict[str, Any] | None,
) -> None:
    if fixture is not None:
        _insert_demo_chunks_from_fixture(db, specs, fixture)
        return
    _index_demo_documents_from_sources(db, specs)


def validate_demo_seed_prerequisites() -> None:
    _require_seed_env("ENCRYPTION_KEY")
    for spec in ONBOARDING_PDF_SPECS:
        _resolve_onboarding_pdf(spec)
    if _runtime_openai_credential_requested():
        _require_seed_env("OPENAI_API_KEY")
    if _demo_knowledge_fixture_or_none() is not None:
        return
    _require_seed_env("OPENAI_API_KEY")
    for spec in LEGAL_DOCUMENT_SPECS:
        _resolve_legal_pdf(spec)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_by_id(db: Session, model: type, row_id: uuid.UUID, values: dict[str, Any]):
    # setattr는 모델에 없는 key도 조용히 받아들이므로 seed 오타를 여기서 즉시 실패시킨다.
    unknown_keys = sorted(set(values) - set(sa_inspect(model).attrs.keys()))
    if unknown_keys:
        raise ValueError(
            f"{model.__name__} seed values contain unmapped attributes: {unknown_keys}"
        )
    row = db.get(model, row_id)
    if row is None:
        row = model(id=row_id)
        db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    return row


_MANAGED_APP_SECRET_STATE_FIELDS = (
    "auth_secret_verifier",
    "auth_secret_verifier_version",
    "auth_secret_generation",
    "auth_secret_previous_verifier",
    "auth_secret_previous_verifier_version",
    "auth_secret_previous_valid_until",
    "auth_secret_rotated_at",
)


def _has_valid_managed_app_secret_state(app: App) -> bool:
    generation = app.auth_secret_generation
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
        or not app_auth_secret_verifier_state_is_valid(
            app.auth_secret_verifier,
            app.auth_secret_verifier_version,
        )
        or not isinstance(app.auth_secret_rotated_at, datetime)
        or app.auth_secret_rotated_at.tzinfo is None
    ):
        return False

    previous_fields = (
        app.auth_secret_previous_verifier,
        app.auth_secret_previous_verifier_version,
        app.auth_secret_previous_valid_until,
    )
    if all(value is None for value in previous_fields):
        return True
    return (
        app_auth_secret_verifier_state_is_valid(
            app.auth_secret_previous_verifier,
            app.auth_secret_previous_verifier_version,
        )
        and isinstance(app.auth_secret_previous_valid_until, datetime)
        and app.auth_secret_previous_valid_until.tzinfo is not None
    )


def _app_seed_values(
    existing_app: App | None,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Keep a user-rotated verifier state when a normal seed upsert reruns."""

    if existing_app is None or not _has_valid_managed_app_secret_state(existing_app):
        return values

    preserved = values.copy()
    for field in _MANAGED_APP_SECRET_STATE_FIELDS:
        preserved.pop(field, None)
    # A valid verifier is the authority. Clearing a leftover legacy raw value is safe.
    preserved["auth_secret"] = None
    return preserved


def _icon(content: str, background_color: str = "#EFF6FF") -> dict[str, str]:
    return {
        "type": "emoji",
        "content": content,
        "background_color": background_color,
    }


def _base_node_data(
    title: str,
    description: str,
    display_number: int,
    visible_properties: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "displayNumber": display_number,
        "visibleProperties": visible_properties or [],
    }


def _node(
    node_id: str,
    node_type: str,
    x: int,
    y: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": y},
        "data": data,
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    source_handle: str | None = None,
) -> dict[str, Any]:
    edge = {"id": edge_id, "source": source, "target": target}
    if source_handle:
        edge["sourceHandle"] = source_handle
    return edge


def _knowledge_base_ref(key: str) -> dict[str, str]:
    if key == "hr":
        return {"id": str(KB_IDS[key]), "name": "사내 휴가 정책 지식베이스"}
    if key == "hr_welfare":
        return {"id": str(KB_IDS[key]), "name": "사내 복지 정책 지식베이스"}
    onboarding_spec = ONBOARDING_KB_SPECS.get(key)
    if onboarding_spec is not None:
        return {"id": str(KB_IDS[key]), "name": onboarding_spec[0]}
    spec = next((item for item in DEMO_DOCUMENT_SPECS if item.key == key), None)
    if spec is None:
        raise KeyError(f"Unknown demo knowledge base key: {key}")
    return {"id": str(KB_IDS[key]), "name": spec.name}


def _hr_bot_knowledge_base_refs() -> list[dict[str, str]]:
    return [
        _knowledge_base_ref(key)
        for key in (
            "hr",
            "hr_welfare",
            "legal_labor_standards",
            "legal_equal_employment",
            "legal_equal_employment_enforcement_decree",
            "legal_privacy",
        )
    ]


def _hr_bot_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "start-question",
                "startNode",
                120,
                120,
                {
                    **_base_node_data("직원 질문 입력", "직원의 사내 문서 질문을 입력받습니다.", 1),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "label": "질문",
                            "type": "paragraph",
                            "required": True,
                            "maxLength": 1200,
                            "max_length": 1200,
                        }
                    ],
                },
            ),
            _node(
                "llm-answer",
                "llmNode",
                540,
                120,
                {
                    **_base_node_data(
                        "사내 문서 기반 답변",
                        "인사 지식베이스를 참고해 답변을 생성합니다.",
                        2,
                        ["model_id", "knowledgeBases", "user_prompt"],
                    ),
                    "provider": "openai",
                    "model_id": DEMO_CHAT_MINI_MODEL,
                    "system_prompt": "사내 복지, 휴가, 인사 정책 문서를 근거로 간결하게 답변합니다.",
                    "user_prompt": "질문: {{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start-question", "question"],
                        }
                    ],
                    "knowledgeBases": _hr_bot_knowledge_base_refs(),
                    "scoreThreshold": 0.3,
                    "topK": 4,
                    "parameters": {"temperature": 0.2, "max_tokens": 800},
                },
            ),
            _node(
                "answer",
                "answerNode",
                960,
                120,
                {
                    **_base_node_data("응답", "최종 답변을 반환합니다.", 3),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["llm-answer", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-start-llm", "start-question", "llm-answer"),
            _edge("edge-llm-answer", "llm-answer", "answer"),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def _department_onboarding_knowledge_base_refs() -> list[dict[str, str]]:
    return [
        _knowledge_base_ref(key)
        for key in (
            "onboarding_platform",
            "onboarding_sales",
        )
    ]


def _department_onboarding_chatbot_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "start-question",
                "startNode",
                120,
                120,
                {
                    **_base_node_data(
                        "온보딩 질문 입력",
                        "로그인 사용자의 부서별 온보딩 질문을 입력받습니다.",
                        1,
                    ),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "label": "질문",
                            "type": "paragraph",
                            "required": True,
                            "maxLength": 1200,
                            "max_length": 1200,
                        }
                    ],
                },
            ),
            _node(
                "llm-answer",
                "llmNode",
                540,
                120,
                {
                    **_base_node_data(
                        "권한 기반 온보딩 답변",
                        "현재 사용자가 사용할 수 있는 공통·부서 문서만 근거로 답변합니다.",
                        2,
                        ["model_id", "knowledgeBases", "user_prompt"],
                    ),
                    "provider": "openai",
                    "model_id": DEMO_CHAT_MINI_MODEL,
                    "system_prompt": (
                        "현재 사용자에게 허용된 온보딩 문서만 근거로 간결하게 답변합니다. "
                        "근거가 없으면 추측하지 말고 확인 가능한 문서가 없다고 안내합니다."
                    ),
                    "user_prompt": "질문: {{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start-question", "question"],
                        }
                    ],
                    "knowledgeBases": _department_onboarding_knowledge_base_refs(),
                    "scoreThreshold": 0.3,
                    "topK": 3,
                    "answerGroundingCheck": "basic",
                    "parameters": {"temperature": 0.2, "max_tokens": 700},
                },
            ),
            _node(
                "answer",
                "answerNode",
                960,
                120,
                {
                    **_base_node_data("응답", "최종 답변을 반환합니다.", 3),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["llm-answer", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-start-llm", "start-question", "llm-answer"),
            _edge("edge-llm-answer", "llm-answer", "answer"),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def _team_onboarding_access_control_graph() -> dict[str, Any]:
    knowledge_bases = [
        _knowledge_base_ref(spec.key) for spec in ONBOARDING_PDF_SPECS
    ]
    return {
        "nodes": [
            _node(
                "start-question",
                "startNode",
                120,
                120,
                {
                    **_base_node_data(
                        "온보딩 질문 입력",
                        "로그인한 사용자의 팀별 온보딩 질문을 입력받습니다.",
                        1,
                    ),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "label": "질문",
                            "type": "paragraph",
                            "required": True,
                            "maxLength": 1200,
                            "max_length": 1200,
                        }
                    ],
                },
            ),
            _node(
                "llm-answer",
                "llmNode",
                540,
                120,
                {
                    **_base_node_data(
                        "권한 기반 온보딩 검색 및 답변",
                        "실행 사용자의 KB 권한으로 검색 후보를 제한한 뒤 답변합니다.",
                        2,
                        ["model_id", "knowledgeBases", "user_prompt"],
                    ),
                    "provider": "openai",
                    "model_id": DEMO_CHAT_MINI_MODEL,
                    "system_prompt": (
                        "현재 로그인한 사용자에게 허용된 온보딩 문서만 근거로 답변합니다. "
                        "첫 주 일정과 접근 권한 신청 절차를 구분하고, 사용한 문서의 파일명을 "
                        "출처로 표시합니다. 검색 근거가 없으면 추측하지 말고 현재 권한으로 "
                        "확인 가능한 문서가 없다고 안전하게 안내합니다. 권한이 없는 다른 팀 "
                        "문서의 존재나 세부 내용을 추론하거나 노출하지 않습니다."
                    ),
                    "user_prompt": "질문: {{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start-question", "question"],
                        }
                    ],
                    "knowledgeBases": knowledge_bases,
                    "scoreThreshold": 0.3,
                    "topK": 4,
                    "parameters": {"temperature": 0.1, "max_tokens": 900},
                },
            ),
            _node(
                "answer",
                "answerNode",
                960,
                120,
                {
                    **_base_node_data("응답", "권한이 적용된 최종 답변을 반환합니다.", 3),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["llm-answer", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-start-llm", "start-question", "llm-answer"),
            _edge("edge-llm-answer", "llm-answer", "answer"),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def _team_onboarding_adaptive_routing_graph() -> dict[str, Any]:
    """RAG 기반 팀 온보딩 안내 workflow에 자동 모델 라우팅을 설정한다."""
    graph = copy.deepcopy(_team_onboarding_access_control_graph())
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-answer")
    data = llm_node["data"]
    data["model_id"] = DEMO_ONBOARDING_ROUTER_MODEL
    data["fallback_model_id"] = None
    data["auto_model_routing"] = True
    data["model_routing_context"] = {
        "customer_facing": False,
        "node_task": "employee_onboarding_guidance",
        "risk_level": "low",
    }
    data["model_routing_policy"] = {
        "refresh": {"refresh_every_runs": 5},
        "validation_budget_usd": 3.0,
        "excluded_model_ids": ["gpt-5.6-sol"],
    }
    return graph



def _ticket_ops_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "webhook-ticket",
                "webhookTrigger",
                120,
                220,
                {
                    **_base_node_data("고객 티켓 수신", "고객지원 티켓 payload를 수신합니다.", 1),
                    "variable_mappings": [
                        {"json_path": "message", "variable_name": "message"},
                        {"json_path": "customerTier", "variable_name": "customerTier"},
                    ],
                },
            ),
            _node(
                "llm-triage",
                "llmNode",
                540,
                220,
                {
                    **_base_node_data("티켓 처리 판단", "티켓 유형, 심각도, 승인 필요 여부를 판단합니다.", 2),
                    "provider": "openai",
                    "model_id": DEMO_CHAT_MODEL,
                    "system_prompt": (
                        "고객지원 티켓을 처리하는 AI입니다. 반드시 JSON object 하나만 출력하세요. "
                        "필드는 \"긴급도\" boolean, \"답변 초안\" string 두 개만 사용합니다. "
                        "답변 초안은 고객에게 보낼 수 있는 3문장 이내의 간결한 문장으로 작성하세요."
                    ),
                    "user_prompt": (
                        "고객 등급: {{ customerTier }}\n"
                        "문의: {{ message }}\n"
                        "승인 필요 여부와 고객 답변 초안을 작성하세요."
                    ),
                    "referenced_variables": [
                        {
                            "name": "customerTier",
                            "value_selector": ["webhook-ticket", "customerTier"],
                        },
                        {
                            "name": "message",
                            "value_selector": ["webhook-ticket", "message"],
                        },
                    ],
                    "parameters": {"temperature": 0.2, "max_tokens": 700},
                    "output_format": {
                        "type": "json",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "긴급도": {"type": "boolean"},
                                "답변 초안": {"type": "string"},
                            },
                            "required": ["긴급도", "답변 초안"],
                        },
                    },
                },
            ),
            _node(
                "extract-ticket",
                "variableExtractionNode",
                960,
                220,
                {
                    **_base_node_data("처리 결과 추출", "LLM JSON 문자열을 후속 분기 변수로 추출합니다.", 3),
                    "source_selector": ["llm-triage", "text"],
                    "mappings": [
                        {"name": "approvalRequired", "json_path": "긴급도"},
                        {"name": "mailDraft", "json_path": "답변 초안"},
                    ],
                },
            ),
            _node(
                "condition-approval",
                "conditionNode",
                1380,
                220,
                {
                    **_base_node_data("승인 필요 분기", "승인 요청 여부로 분기합니다.", 4),
                    "cases": [
                        {
                            "id": "approval",
                            "case_name": "승인 필요",
                            "logical_operator": "or",
                            "conditions": [
                                {
                                    "id": "cond-approval-required",
                                    "variable_selector": [
                                        "extract-ticket",
                                        "approvalRequired",
                                    ],
                                    "operator": "equals",
                                    "value": True,
                                }
                            ],
                        }
                    ],
                },
            ),
            _node(
                "template-approval",
                "templateNode",
                1800,
                80,
                {
                    **_base_node_data("승인 요청 메시지", "CS 리드 승인 요청 메시지를 만듭니다.", 5),
                    "template": (
                        "안녕하세요,\n\n"
                        "아래 고객 요청에 대한 확인을 부탁드립니다.\n\n"
                        "**고객 등급:** {{ customerTier }}  \n"
                        "**고객 문의:** {{ message }}  \n"
                        "**고객 초안:**  \n"
                        "{{ mailDraft }}\n\n"
                        "감사합니다."
                    ),
                    "variables": [
                        {
                            "name": "mailDraft",
                            "value_selector": ["extract-ticket", "mailDraft"],
                        },
                        {
                            "name": "customerTier",
                            "value_selector": ["webhook-ticket", "customerTier"],
                        },
                        {
                            "name": "message",
                            "value_selector": ["webhook-ticket", "message"],
                        },
                    ],
                },
            ),
            _node(
                "template-reply",
                "templateNode",
                1800,
                360,
                {
                    **_base_node_data("고객 답변 초안", "고객에게 보낼 답변을 정리합니다.", 6),
                    "template": (
                        "안녕하세요,\n\n"
                        "고객님의 소중한 의견에 감사드립니다. 저희는 항상 고객님의 목소리를 귀 기울여 듣고 있습니다.\n\n"
                        "아래 내용을 확인하시고, 추가적인 질문이나 요청사항이 있으시면 언제든지 연락 주시기 바랍니다.\n\n"
                        "{{ mailDraft }}\n\n"
                        "감사합니다.\n\n"
                        "좋은 하루 되세요!"
                    ),
                    "variables": [
                        {
                            "name": "mailDraft",
                            "value_selector": ["extract-ticket", "mailDraft"],
                        }
                    ],
                },
            ),
            _node(
                "answer-approval",
                "answerNode",
                2220,
                80,
                {
                    **_base_node_data("승인 요청 결과", "승인 요청 branch 결과를 반환합니다.", 7),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "처리 결과",
                            "value_selector": ["template-approval", "text"],
                        }
                    ],
                },
            ),
            _node(
                "answer-reply",
                "answerNode",
                2220,
                360,
                {
                    **_base_node_data("고객 답변 결과", "고객 답변 branch 결과를 반환합니다.", 8),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "처리 결과",
                            "value_selector": ["template-reply", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-webhook-llm", "webhook-ticket", "llm-triage"),
            _edge("edge-llm-extract", "llm-triage", "extract-ticket"),
            _edge("edge-extract-condition", "extract-ticket", "condition-approval"),
            _edge(
                "edge-condition-approval",
                "condition-approval",
                "template-approval",
                "approval",
            ),
            _edge("edge-condition-reply", "condition-approval", "template-reply", "default"),
            _edge("edge-approval-answer", "template-approval", "answer-approval"),
            _edge("edge-reply-answer", "template-reply", "answer-reply"),
        ],
        "viewport": {"x": 20, "y": 70, "zoom": 0.48},
    }


def _model_router_ticket_ops_graph() -> dict[str, Any]:
    """고객 티켓 처리 workflow에 자동 모델 라우팅을 설정한다."""
    graph = _ticket_ops_graph()
    for node in graph["nodes"]:
        if node["id"] != "llm-triage":
            continue
        node["data"].update(
            {
                "title": "보상/SLA 위험 판단",
                "description": "고객 문의, SLA, 보상 위험을 분류하고 처리 방향을 판단합니다.",
                "model_id": DEMO_MODEL_ROUTER_FALLBACK_MODEL,
                "fallback_model_id": DEMO_MODEL_ROUTER_BALANCED_MODEL,
                # 모델 라우팅 검증은 티켓 내용의 난이도에 집중한다. 기반
                # workflow의 비용 최적화 문서는 고객 문의 근거로 사용할 수 없다.
                "knowledgeBases": [],
                "auto_model_routing": True,
                "model_routing_context": {
                    "customer_facing": True,
                    "node_task": "customer_support_triage",
                    "risk_level": "medium",
                },
                "model_routing_policy": {
                    "refresh": {"refresh_every_runs": 10},
                    "validation_budget_usd": 3.0,
                    "excluded_model_ids": ["gpt-5.6-sol"],
                },
            }
        )
    return graph


def _internal_it_helpdesk_routing_graph() -> dict[str, Any]:
    """사내 IT 문의를 RAG와 요청 난이도에 맞는 모델로 처리한다."""
    graph = copy.deepcopy(_ticket_ops_graph())
    nodes = {node["id"]: node for node in graph["nodes"]}

    nodes["webhook-ticket"]["data"].update(
        {
            "title": "직원 IT 문의 수신",
            "description": "직원의 부서와 IT 문의를 수신합니다.",
            "variable_mappings": [
                {
                    "label": "부서",
                    "json_path": "department",
                    "variable_name": "department",
                },
                {
                    "label": "문의",
                    "json_path": "message",
                    "variable_name": "message",
                },
            ],
        }
    )

    nodes["llm-triage"]["data"].update(
        {
            "title": "IT 문의 판단 및 답변",
            "description": (
                "사내 IT 문의를 처리합니다. 단일 문서 위치나 설치 안내는 경제형, "
                "복수 인증·접속 증상 진단은 균형형, 보안 사고 대응은 고성능형 "
                "모델이 필요한 작업입니다."
            ),
            "model_id": DEMO_MODEL_ROUTER_FALLBACK_MODEL,
            "fallback_model_id": DEMO_MODEL_ROUTER_BALANCED_MODEL,
            "auto_model_routing": True,
            "model_routing_context": {
                "customer_facing": False,
                "node_task": "internal_it_helpdesk",
                "risk_level": "medium",
            },
            "model_routing_task_description": (
                "사내 IT 문의를 처리합니다. 단일 문서 위치·설치 안내·단순 조회는 "
                "경제형 능력, 둘 이상의 인증·접속 증상을 조합해 원인을 진단하거나 "
                "단계별 해결 절차를 설계하는 요청은 균형형 능력, 관리자 계정 탈취·"
                "퇴사자 권한·고객 정보 접근 사고의 차단·증거 보존·통지 판단은 "
                "고성능형 능력이 필요합니다."
            ),
            "model_routing_policy": {
                "refresh": {"refresh_every_runs": 20},
                "validation_budget_usd": 3.0,
                "excluded_model_ids": [DEMO_MODEL_ROUTER_LATEST_SOL_MODEL],
            },
            "system_prompt": (
                "사내 IT 헬프데스크 AI입니다. 연결된 사내 문서를 우선 근거로 "
                "사용하세요. 단순 사용 안내는 짧고 직접적으로 답하고, 여러 증상이 "
                "얽힌 장애는 확인 순서와 해결 절차를 제시하세요. 계정 탈취, 퇴사자 "
                "권한, 개인정보 접근, 관리자 권한처럼 보안 위험이 있으면 긴급으로 "
                "판정하고 증거 보존, 접근 차단, 보안 담당자 에스컬레이션 순서를 "
                "포함하세요. 반드시 JSON object 하나만 출력하세요. 필드는 "
                "'문의 유형' string, '긴급도' boolean, '답변 초안' string 세 개만 "
                "사용합니다. 무단 접근 근거가 없는 일반 MFA·SSO·VPN 접속 장애는 "
                "긴급도를 false로 판정하세요."
            ),
            "user_prompt": (
                "요청 부서: {{ department }}\n"
                "직원 문의: {{ message }}\n"
                "문의 유형, 긴급 여부와 직원에게 제공할 답변을 작성하세요."
            ),
            "assistant_prompt": "",
            "referenced_variables": [
                {
                    "name": "department",
                    "value_selector": ["webhook-ticket", "department"],
                },
                {
                    "name": "message",
                    "value_selector": ["webhook-ticket", "message"],
                },
            ],
            "parameters": {"temperature": 0.2, "max_tokens": 900},
            "knowledgeBases": [
                _knowledge_base_ref("onboarding_platform"),
            ],
            "output_format": {
                "type": "json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "문의 유형": {"type": "string"},
                        "긴급도": {"type": "boolean"},
                        "답변 초안": {"type": "string"},
                    },
                    "required": ["문의 유형", "긴급도", "답변 초안"],
                },
            },
        }
    )
    nodes["extract-ticket"]["data"].update(
        {
            "title": "문의 유형·긴급도 추출",
            "description": "LLM JSON 결과에서 문의 유형, 긴급도와 답변을 추출합니다.",
            "source_selector": ["llm-triage", "text"],
            "mappings": [
                {"name": "requestType", "json_path": "문의 유형"},
                {"name": "approvalRequired", "json_path": "긴급도"},
                {"name": "mailDraft", "json_path": "답변 초안"},
            ],
        }
    )
    nodes["condition-approval"]["data"].update(
        {
            "title": "긴급 보안 문의 분기",
            "description": "즉시 보안 담당자 대응이 필요한 문의인지 분기합니다.",
        }
    )
    nodes["condition-approval"]["data"]["cases"][0]["case_name"] = (
        "보안 에스컬레이션"
    )
    nodes["template-approval"]["data"].update(
        {
            "title": "보안 담당자 에스컬레이션",
            "description": "긴급 보안 문의를 담당자에게 전달할 메시지를 만듭니다.",
            "template": (
                "[긴급 IT 보안 에스컬레이션]\n\n"
                "부서: {{ department }}\n문의: {{ message }}\n"
                "문의 유형: {{ requestType }}\n\n초기 대응 안내:\n{{ mailDraft }}"
            ),
            "variables": [
                {
                    "name": "department",
                    "value_selector": ["webhook-ticket", "department"],
                },
                {
                    "name": "message",
                    "value_selector": ["webhook-ticket", "message"],
                },
                {
                    "name": "requestType",
                    "value_selector": ["extract-ticket", "requestType"],
                },
                {
                    "name": "mailDraft",
                    "value_selector": ["extract-ticket", "mailDraft"],
                },
            ],
        }
    )
    nodes["template-reply"]["data"].update(
        {
            "title": "일반 IT 안내",
            "description": "직원에게 전달할 IT 안내를 정리합니다.",
            "template": "{{ mailDraft }}",
        }
    )
    nodes["answer-approval"]["data"].update(
        {
            "title": "보안 대응 결과",
            "description": "보안 에스컬레이션 결과를 반환합니다.",
            "displayNumber": 8,
        }
    )
    nodes["answer-reply"]["data"].update(
        {
            "title": "IT 안내 결과",
            "description": "일반 IT 안내 결과를 반환합니다.",
            "displayNumber": 7,
        }
    )
    graph["nodes"].sort(key=lambda node: node["data"]["displayNumber"])
    return calculate_workflow_auto_layout(graph)


def _onboarding_chatbot_graph() -> dict[str, Any]:
    """신규 workflow 편집기와 동일한 단일 입력 노드 canvas를 만든다."""
    return {
        "nodes": [
            _node(
                "start-onboarding-chatbot",
                "startNode",
                250,
                250,
                {
                    "title": "입력",
                    "displayNumber": 1,
                    "triggerType": "manual",
                    "variables": [],
                },
            )
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _new_employee_onboarding_chatbot_graph() -> dict[str, Any]:
    """팀 권한으로 온보딩 Collection과 하위 KB를 검색하는 3-node graph."""
    return {
        "nodes": [
            _node(
                "start-onboarding-question",
                "startNode",
                120,
                120,
                {
                    **_base_node_data(
                        "입력",
                        "신입 사원의 온보딩 질문을 입력받습니다.",
                        1,
                    ),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "label": "온보딩 질문",
                            "type": "paragraph",
                            "required": True,
                            "maxLength": 1200,
                            "max_length": 1200,
                        }
                    ],
                },
            ),
            _node(
                "llm-onboarding-answer",
                "llmNode",
                700,
                120,
                {
                    **_base_node_data(
                        "온보딩 문서 답변",
                        "팀 권한으로 허용된 온보딩 문서를 검색해 답변합니다.",
                        2,
                        [
                            "model_id",
                            "knowledgeCollections",
                            "knowledgeBases",
                            "user_prompt",
                        ],
                    ),
                    "provider": "openai",
                    "model_id": DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL,
                    "fallback_model_id": DEMO_CHAT_MODEL,
                    "auto_model_routing": True,
                    "system_prompt": (
                        "신입 사원 온보딩 안내 AI입니다. 현재 실행 사용자의 팀 권한으로 "
                        "조회 가능한 온보딩 문서만 근거로 답변하고, 근거가 없으면 "
                        "추측하지 않습니다."
                    ),
                    "user_prompt": "온보딩 질문: {{ question }}",
                    "context_variable": "question",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": [
                                "start-onboarding-question",
                                "question",
                            ],
                        }
                    ],
                    "knowledgeCollections": [
                        {
                            "id": str(
                                COLLECTION_IDS["team_onboarding_access_control"]
                            ),
                            "safeLabel": "팀별 온보딩 접근 제어 문서",
                        }
                    ],
                    "knowledgeBases": [
                        _knowledge_base_ref(spec.key)
                        for spec in ONBOARDING_PDF_SPECS
                    ],
                    "scoreThreshold": 0.3,
                    "topK": 5,
                    "parameters": {"temperature": 0.2, "max_tokens": 900},
                },
            ),
            _node(
                "answer-onboarding",
                "answerNode",
                1280,
                120,
                {
                    **_base_node_data(
                        "출력",
                        "온보딩 문서 기반 답변을 반환합니다.",
                        3,
                    ),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["llm-onboarding-answer", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge(
                "edge-onboarding-input-llm",
                "start-onboarding-question",
                "llm-onboarding-answer",
            ),
            _edge(
                "edge-onboarding-llm-output",
                "llm-onboarding-answer",
                "answer-onboarding",
            ),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def _enterprise_request_routing_graph() -> dict[str, Any]:
    """사내 업무 요청을 RAG와 입력군별 자동 모델 라우팅으로 처리한다."""
    graph = copy.deepcopy(_ticket_ops_graph())
    webhook_node = next(
        node for node in graph["nodes"] if node["id"] == "webhook-ticket"
    )
    webhook_node["id"] = "webhook-request"
    webhook_node["data"].update(
        {
            "title": "사내 업무 요청 수신",
            "description": "부서, 요청자 역할과 업무 문의를 수신합니다.",
            "variable_mappings": [
                {"json_path": "query", "variable_name": "query"},
                {"json_path": "department", "variable_name": "department"},
                {"json_path": "requesterRole", "variable_name": "requesterRole"},
                {"json_path": "locale", "variable_name": "locale"},
            ],
        }
    )

    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-triage")
    llm_node["id"] = "llm-request"
    llm_node["data"].update(
        {
            "title": "업무 요청 분류 및 답변",
            "description": "사내 문서를 검색하고 요청 위험도에 맞는 모델로 답변합니다.",
            "model_id": DEMO_ONBOARDING_ROUTER_MODEL,
            "fallback_model_id": None,
            "auto_model_routing": True,
            "model_routing_context": {
                "customer_facing": False,
                "node_task": "enterprise_internal_request",
                "risk_level": "medium",
            },
            "model_routing_policy": {
                "refresh": {"refresh_every_runs": 10},
                "validation_budget_usd": 3.0,
                "excluded_model_ids": ["gpt-5.6-sol"],
            },
            "knowledgeBases": [
                _knowledge_base_ref(key)
                for key in (
                    "legal_privacy",
                    "onboarding_platform",
                    "onboarding_sales",
                    "onboarding_finance",
                )
            ],
            "scoreThreshold": 0.3,
            "topK": 5,
            "system_prompt": (
                "기업 내부 업무 요청을 처리하는 AI입니다. 현재 실행 주체에게 허용된 사내 문서만 "
                "근거로 답변하고, 근거가 없으면 추측하지 않습니다. 반드시 JSON object 하나만 "
                "출력하며 필드는 '입력군', '승인 필요', '답변'만 사용합니다. 보안·개인정보 사고와 "
                "재무 지급 승인은 보수적으로 판단합니다."
            ),
            "user_prompt": (
                "부서: {{ department }}\n"
                "요청자 역할: {{ requesterRole }}\n"
                "언어: {{ locale }}\n"
                "업무 요청: {{ query }}\n"
                "요청 유형을 분류하고 승인 필요 여부와 실행 가능한 답변을 작성하세요."
            ),
            "referenced_variables": [
                {
                    "name": name,
                    "value_selector": ["webhook-request", name],
                }
                for name in ("department", "requesterRole", "locale", "query")
            ],
            "parameters": {"temperature": 0.15, "max_tokens": 900},
            "output_format": {
                "type": "json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "입력군": {"type": "string"},
                        "승인 필요": {"type": "boolean"},
                        "답변": {"type": "string"},
                    },
                    "required": ["입력군", "승인 필요", "답변"],
                },
            },
        }
    )

    extract_node = next(
        node for node in graph["nodes"] if node["id"] == "extract-ticket"
    )
    extract_node["data"].update(
        {
            "title": "업무 처리 결과 추출",
            "description": "LLM JSON에서 승인 여부와 답변을 추출합니다.",
            "source_selector": ["llm-request", "text"],
            "mappings": [
                {"name": "approvalRequired", "json_path": "승인 필요"},
                {"name": "mailDraft", "json_path": "답변"},
            ],
        }
    )

    condition_node = next(
        node for node in graph["nodes"] if node["id"] == "condition-approval"
    )
    condition_node["data"].update(
        {
            "title": "검토 필요 분기",
            "description": "보안·재무 등 승인 필요 요청을 검토 경로로 분기합니다.",
        }
    )

    approval_template = next(
        node for node in graph["nodes"] if node["id"] == "template-approval"
    )
    approval_template["data"].update(
        {
            "title": "담당 부서 검토 요청",
            "description": "승인이 필요한 업무 요청을 담당 부서에 전달합니다.",
            "template": (
                "담당 부서 검토가 필요한 요청입니다.\n\n"
                "부서: {{ department }}\n"
                "요청자 역할: {{ requesterRole }}\n"
                "요청: {{ query }}\n\n"
                "AI 검토 결과:\n{{ mailDraft }}"
            ),
            "variables": [
                {"name": "mailDraft", "value_selector": ["extract-ticket", "mailDraft"]},
                {"name": "department", "value_selector": ["webhook-request", "department"]},
                {
                    "name": "requesterRole",
                    "value_selector": ["webhook-request", "requesterRole"],
                },
                {"name": "query", "value_selector": ["webhook-request", "query"]},
            ],
        }
    )

    reply_template = next(
        node for node in graph["nodes"] if node["id"] == "template-reply"
    )
    reply_template["data"].update(
        {
            "title": "사내 업무 안내",
            "description": "승인 없이 처리할 수 있는 업무 답변을 정리합니다.",
            "template": "{{ mailDraft }}",
        }
    )

    for edge in graph["edges"]:
        if edge["source"] == "webhook-ticket":
            edge["source"] = "webhook-request"
        if edge["source"] == "llm-triage":
            edge["source"] = "llm-request"
        if edge["target"] == "llm-triage":
            edge["target"] = "llm-request"
    return graph


def _test_inquiry_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "start-test",
                "startNode",
                120,
                120,
                {
                    **_base_node_data("테스트 입력", "자유 테스트용 문의를 입력받습니다.", 1),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "message",
                            "name": "message",
                            "label": "메시지",
                            "type": "paragraph",
                            "required": True,
                        }
                    ],
                },
            ),
            _node(
                "template-test",
                "templateNode",
                540,
                120,
                {
                    **_base_node_data("테스트 응답", "입력 내용을 응답으로 정리합니다.", 2),
                    "template": "테스트 응답입니다.\n입력: {{ message }}",
                    "variables": [
                        {
                            "name": "message",
                            "value_selector": ["start-test", "message"],
                        }
                    ],
                },
            ),
            _node(
                "answer-test",
                "answerNode",
                960,
                120,
                {
                    **_base_node_data("응답", "테스트 응답을 반환합니다.", 3),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["template-test", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-start-template", "start-test", "template-test"),
            _edge("edge-template-answer", "template-test", "answer-test"),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def _input_schema(variable_name: str, label: str = "입력") -> dict[str, Any]:
    return {"variables": [{"name": variable_name, "type": "text", "label": label}]}


def _input_schema_from_graph(graph: dict[str, Any]) -> dict[str, Any] | None:
    """배포 입력 schema가 graph의 시작 노드 계약과 어긋나지 않게 생성한다."""
    for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict):
            continue
        node_data = node.get("data")
        if not isinstance(node_data, dict):
            continue

        if node.get("type") == "startNode":
            variables = node_data.get("variables") or []
            normalized = [
                {
                    "name": variable.get("name", ""),
                    "type": variable.get("type", "string"),
                    "label": variable.get("label", variable.get("name", "")),
                }
                for variable in variables
                if isinstance(variable, dict) and variable.get("name")
            ]
            return {"variables": normalized} if normalized else None

        if node.get("type") == "webhookTrigger":
            mappings = node_data.get("variable_mappings") or []
            normalized = [
                {
                    "name": mapping.get("variable_name", ""),
                    "type": "text",
                    "label": mapping.get("label", mapping.get("variable_name", "")),
                }
                for mapping in mappings
                if isinstance(mapping, dict) and mapping.get("variable_name")
            ]
            return {"variables": normalized} if normalized else None

    return None


def _output_schema() -> dict[str, Any]:
    return {"outputs": [{"variable": "answer_text", "label": "답변"}]}


def _seed_users_and_org(db: Session) -> None:
    _adopt_existing_demo_user_ids(db)
    hashed_password = hash_password(DEMO_PASSWORD)
    for spec in USER_SPECS:
        user = _upsert_by_id(
            db,
            User,
            USER_IDS[spec.key],
            {
                "email": spec.email,
                "name": spec.name,
                "password": hashed_password,
                "social_provider": "none",
                "social_id": None,
                "avatar_url": None,
                "deactivated_at": None,
            },
        )
        if spec.membership_state == ORGANIZATION_MEMBERSHIP_SUSPENDED:
            user.deactivated_at = None

    _upsert_by_id(
        db,
        Organization,
        ORG_ID,
        {
            "name": "노디즈 데모 조직",
            "options": _demo_options("organization"),
            "flags": 0,
            "created_by": USER_IDS["admin"],
            "managed_by": USER_IDS["admin"],
            "is_active": True,
            "deactivated_at": None,
        },
    )
    db.flush()

    for spec in USER_SPECS:
        membership_id = _uuid(1000 + list(USER_IDS).index(spec.key))
        accepted_at = (
            _now()
            if spec.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
            else None
        )
        removed_at = (
            _now()
            if spec.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
            else None
        )
        _upsert_by_id(
            db,
            OrganizationMembership,
            membership_id,
            {
                "organization_id": ORG_ID,
                "user_id": USER_IDS[spec.key],
                "membership_state": spec.membership_state,
                "organization_auth_state": spec.organization_auth_state,
                "invited_by": USER_IDS["admin"],
                "invited_at": _now() - timedelta(days=7),
                "accepted_at": accepted_at,
                "removed_at": removed_at,
                "options": _demo_options(f"organization-membership-{spec.key}"),
                "flags": 0,
            },
        )


def _seed_teams_and_memberships(db: Session) -> None:
    for key, (name, description) in TEAM_SPECS.items():
        _upsert_by_id(
            db,
            Team,
            TEAM_IDS[key],
            {
                "organization_id": ORG_ID,
                "name": name,
                "description": description,
                "created_by": USER_IDS["admin"],
                "managed_by": USER_IDS["admin"],
                "is_active": True,
                "is_auto_add": False,
                "deactivated_at": None,
                "options": _demo_options(f"team-{key}"),
                "flags": 0,
            },
        )
    db.flush()

    membership_index = 0
    for spec in USER_SPECS:
        for team_key in spec.teams:
            _upsert_by_id(
                db,
                TeamMembership,
                _uuid(1100 + membership_index),
                {
                    "grantee_organization_id": ORG_ID,
                    "team_id": TEAM_IDS[team_key],
                    "user_id": USER_IDS[spec.key],
                    "assigned_by": USER_IDS["admin"],
                    "options": _demo_options(f"team-membership-{spec.key}-{team_key}"),
                    "flags": 0,
                },
            )
            membership_index += 1


def _demo_team_knowledge_permission_specs() -> list[tuple[str, str, str]]:
    knowledge_permission_specs: list[tuple[str, str, str]] = []
    for kb_key in (
        "legal_labor_standards",
        "legal_equal_employment",
        "legal_equal_employment_enforcement_decree",
        "legal_privacy",
        "legal_occupational_safety",
        "legal_retirement_benefits",
        "legal_fair_hiring",
    ):
        knowledge_permission_specs.extend(
            [
                (kb_key, "platform_admin", "manager"),
                (kb_key, "hr_knowledge_users", "operator"),
                (kb_key, "ai_builder_onboarding", "operator"),
                (kb_key, "customer_support_ops", "operator"),
            ]
        )
    knowledge_permission_specs.extend(
        [
            ("onboarding_platform", "onboarding_platform", "operator"),
            ("onboarding_platform", "onboarding_people", "manager"),
            ("onboarding_sales", "onboarding_sales", "operator"),
            ("onboarding_sales", "onboarding_people", "manager"),
            ("onboarding_finance", "onboarding_finance", "operator"),
            ("onboarding_finance", "onboarding_people", "manager"),
            ("onboarding_platform", "department_development", "operator"),
            ("onboarding_sales", "department_planning", "operator"),
        ]
    )
    knowledge_permission_specs.extend(
        [
            ("hr_welfare", "hr_knowledge_users", "operator"),
            ("hr_welfare", "platform_admin", "manager"),
        ]
    )
    return knowledge_permission_specs


def _demo_team_knowledge_collection_permission_specs() -> list[tuple[str, str, str]]:
    collection_permission_specs: list[tuple[str, str, str]] = []
    for team_key in (
        "platform_admin",
        "hr_knowledge_users",
        "ai_builder_onboarding",
        "customer_support_ops",
    ):
        for action in ("read", "route"):
            collection_permission_specs.append(("legal_public", team_key, action))
    for team_key in (
        "onboarding_platform",
        "onboarding_sales",
        "onboarding_finance",
    ):
        collection_permission_specs.extend(
            ("team_onboarding_access_control", team_key, action)
            for action in ("read", "route")
        )
    collection_permission_specs.extend(
        ("team_onboarding_access_control", "onboarding_people", action)
        for action in ("read", "route", "manage", "sync")
    )
    for team_key in ("hr_knowledge_users", "platform_admin"):
        collection_permission_specs.extend(
            ("hr_policies", team_key, action) for action in ("read", "route")
        )
    return collection_permission_specs


def _delete_retired_internal_knowledge(db: Session) -> None:
    retired_kb_ids = list(RETIRED_INTERNAL_DOCUMENT_KB_IDS.values())
    retired_collection_ids = list(
        RETIRED_INTERNAL_DOCUMENT_COLLECTION_IDS.values()
    )
    retired_document_ids = list(RETIRED_INTERNAL_DOCUMENT_IDS.values())
    retired_item_ids = list(
        RETIRED_INTERNAL_DOCUMENT_COLLECTION_ITEM_IDS.values()
    )

    db.query(TeamKnowledgePermission).filter(
        TeamKnowledgePermission.knowledge_base_id.in_(retired_kb_ids)
    ).delete(synchronize_session=False)
    db.query(UserKnowledgePermission).filter(
        UserKnowledgePermission.knowledge_base_id.in_(retired_kb_ids)
    ).delete(synchronize_session=False)
    db.query(TeamKnowledgeCollectionPermission).filter(
        TeamKnowledgeCollectionPermission.knowledge_collection_id.in_(
            retired_collection_ids
        )
    ).delete(synchronize_session=False)
    db.query(KnowledgeDocumentIngestionJob).filter(
        KnowledgeDocumentIngestionJob.knowledge_base_id.in_(retired_kb_ids)
    ).delete(synchronize_session=False)
    db.query(KnowledgeIngestionOutbox).filter(
        KnowledgeIngestionOutbox.knowledge_base_id.in_(retired_kb_ids)
    ).delete(synchronize_session=False)
    db.query(KnowledgeCollectionItem).filter(
        or_(
            KnowledgeCollectionItem.id.in_(retired_item_ids),
            KnowledgeCollectionItem.collection_id.in_(retired_collection_ids),
            KnowledgeCollectionItem.knowledge_base_id.in_(retired_kb_ids),
        )
    ).delete(synchronize_session=False)
    db.query(KnowledgeCollection).filter(
        KnowledgeCollection.id.in_(retired_collection_ids)
    ).delete(synchronize_session=False)
    db.query(DocumentChunk).filter(
        or_(
            DocumentChunk.knowledge_base_id.in_(retired_kb_ids),
            DocumentChunk.document_id.in_(retired_document_ids),
        )
    ).delete(synchronize_session=False)
    db.query(Document).filter(
        or_(
            Document.knowledge_base_id.in_(retired_kb_ids),
            Document.id.in_(retired_document_ids),
        )
    ).delete(synchronize_session=False)
    db.query(KnowledgeBase).filter(
        KnowledgeBase.id.in_(retired_kb_ids)
    ).delete(synchronize_session=False)
    db.flush()


def _seed_knowledge(db: Session) -> None:
    _delete_retired_internal_knowledge(db)
    knowledge_fixture = _demo_knowledge_fixture_or_none()
    fixture_documents = (
        knowledge_fixture["documents"] if knowledge_fixture is not None else {}
    )

    _upsert_by_id(
        db,
        KnowledgeBase,
        KB_IDS["hr"],
        {
            "organization_id": ORG_ID,
            "name": "사내 휴가 정책 지식베이스",
            "description": "휴가 신청과 근태 유의 사항을 담은 데모 지식베이스",
            "embedding_model": "text-embedding-3-small",
            "top_k": 5,
            "similarity_threshold": 0.7,
            "sync_state": "manual",
            "lifecycle_state": "active",
            "user_id": USER_IDS["admin"],
        },
    )
    _upsert_by_id(
        db,
        KnowledgeBase,
        KB_IDS["hr_welfare"],
        {
            "organization_id": ORG_ID,
            "name": "사내 복지 정책 지식베이스",
            "description": "복지 포인트와 경조사 지원을 담은 데모 지식베이스",
            "embedding_model": "text-embedding-3-small",
            "top_k": 5,
            "similarity_threshold": 0.7,
            "sync_state": "manual",
            "lifecycle_state": "active",
            "user_id": USER_IDS["admin"],
        },
    )
    _upsert_by_id(
        db,
        KnowledgeBase,
        KB_IDS["finance"],
        {
            "organization_id": ORG_ID,
            "name": "재무 민감 문서 지식베이스",
            "description": "권한 대조를 위한 민감 문서 지식베이스",
            "embedding_model": "text-embedding-3-small",
            "top_k": 5,
            "similarity_threshold": 0.7,
            "user_id": USER_IDS["admin"],
        },
    )
    for spec in DEMO_DOCUMENT_SPECS:
        _upsert_by_id(
            db,
            KnowledgeBase,
            KB_IDS[spec.key],
            {
                "organization_id": ORG_ID,
                "name": spec.name,
                "description": spec.description,
                "embedding_model": DEMO_EMBEDDING_MODEL,
                "top_k": 5,
                "similarity_threshold": 0.7,
                "sync_state": "manual",
                "lifecycle_state": "active",
                "user_id": USER_IDS["admin"],
            },
        )
    for spec in ONBOARDING_PDF_SPECS:
        _upsert_by_id(
            db,
            KnowledgeBase,
            KB_IDS[spec.key],
            {
                "organization_id": ORG_ID,
                "name": spec.name,
                "description": spec.description,
                "safe_metadata": {
                    **_demo_options(f"bundled-onboarding-kb-{spec.key}"),
                    "safe_label": spec.name,
                    "source_filename": spec.filename,
                    "document_seed_mode": "bundled_pdf",
                },
                "embedding_model": DEMO_EMBEDDING_MODEL,
                "top_k": 5,
                "similarity_threshold": 0.3,
                "sync_state": "manual",
                "lifecycle_state": "active",
                "user_id": USER_IDS["onboarding_people_manager"],
            },
        )
    db.flush()

    _upsert_by_id(
        db,
        KnowledgeCollection,
        COLLECTION_IDS["legal_public"],
        {
            "organization_id": ORG_ID,
            "name": "공개 노동·온보딩 법령 컬렉션",
            "description": "익명 public-only 후보로 노출 가능한 국가법령정보센터 PDF 모음",
            "source_identity_id": None,
            "source_connector_ref": "local.legal-docs-labor",
            "is_system_managed": True,
            "sync_state": "manual",
            "lifecycle_state": "active",
            "safe_metadata": {
                **_demo_options("collection-legal-public"),
                "safe_label": "공개 노동·온보딩 법령 컬렉션",
                "visibility": "public",
                "approved_by": str(USER_IDS["admin"]),
                "approved_source": "demo_seed",
                "revocation_behavior": "remove_from_public_collection",
            },
            "created_by": USER_IDS["admin"],
        },
    )
    _upsert_by_id(
        db,
        KnowledgeCollection,
        COLLECTION_IDS["team_onboarding_access_control"],
        {
            "organization_id": ORG_ID,
            "name": "팀별 온보딩 접근 제어 문서",
            "description": "팀별 온보딩 PDF를 권한 경계별로 묶은 데모 컬렉션",
            "source_identity_id": None,
            "source_connector_ref": "local.demodata.team-onboarding-access-control",
            "is_system_managed": False,
            "sync_state": "manual",
            "lifecycle_state": "active",
            "safe_metadata": {
                **_demo_options("collection-team-onboarding-access-control"),
                "safe_label": "팀별 온보딩 접근 제어 문서",
                "visibility": "private",
                "document_seed_mode": "bundled_pdf",
            },
            "created_by": USER_IDS["onboarding_people_manager"],
        },
    )
    _upsert_by_id(
        db,
        KnowledgeCollection,
        COLLECTION_IDS["hr_policies"],
        {
            "organization_id": ORG_ID,
            "name": "사내 휴가·복지 정책 컬렉션",
            "description": "독립된 휴가 정책과 복지 정책 지식베이스를 함께 검색하는 데모 컬렉션",
            "source_identity_id": None,
            "source_connector_ref": "local.demo.hr-policies",
            "is_system_managed": True,
            "sync_state": "manual",
            "lifecycle_state": "active",
            "safe_metadata": {
                **_demo_options("collection-hr-policies"),
                "visibility": "private",
            },
            "created_by": USER_IDS["admin"],
        },
    )
    db.flush()

    for rank, (item_key, kb_key) in enumerate(HR_POLICY_COLLECTION_ITEMS):
        _upsert_by_id(
            db,
            KnowledgeCollectionItem,
            COLLECTION_ITEM_IDS[item_key],
            {
                "organization_id": ORG_ID,
                "collection_id": COLLECTION_IDS["hr_policies"],
                "knowledge_base_id": KB_IDS[kb_key],
                "safe_source_path_ref": f"demo://hr-policies/{item_key}",
                "rank": rank,
                "safe_metadata": {
                    **_demo_options(f"collection-item-{item_key}"),
                    "source_tier": "private",
                },
            },
        )

    for rank, spec in enumerate(DEMO_DOCUMENT_SPECS):
        if spec.collection_key is None:
            continue
        _upsert_by_id(
            db,
            KnowledgeCollectionItem,
            COLLECTION_ITEM_IDS[spec.key],
            {
                "organization_id": ORG_ID,
                "collection_id": COLLECTION_IDS[spec.collection_key],
                "knowledge_base_id": KB_IDS[spec.key],
                "safe_source_path_ref": spec.filename,
                "rank": rank,
                "safe_metadata": {
                    **_demo_options(f"collection-item-{spec.key}"),
                    "classification": spec.classification,
                    "source_tier": spec.source_tier,
                },
            },
        )

    for rank, spec in enumerate(ONBOARDING_PDF_SPECS):
        _upsert_by_id(
            db,
            KnowledgeCollectionItem,
            COLLECTION_ITEM_IDS[spec.key],
            {
                "organization_id": ORG_ID,
                "collection_id": COLLECTION_IDS[
                    "team_onboarding_access_control"
                ],
                "knowledge_base_id": KB_IDS[spec.key],
                "safe_source_path_ref": spec.filename,
                "rank": rank,
                "safe_metadata": {
                    **_demo_options(f"collection-item-{spec.key}"),
                    "source_filename": spec.filename,
                    "document_seed_mode": "bundled_pdf",
                },
            },
        )

    hr_document_specs = {spec.key: spec for spec in HR_DOCUMENT_SPECS}
    document_specs = {
        "hr_leave": (
            KB_IDS[LEGACY_DEMO_DOCUMENT_KB_KEYS["hr_leave"]],
            hr_document_specs["hr_leave"].filename,
            hr_document_specs["hr_leave"].summary,
            hr_document_specs["hr_leave"].content,
        ),
        "hr_welfare": (
            KB_IDS[LEGACY_DEMO_DOCUMENT_KB_KEYS["hr_welfare"]],
            hr_document_specs["hr_welfare"].filename,
            hr_document_specs["hr_welfare"].summary,
            hr_document_specs["hr_welfare"].content,
        ),
        "finance_sensitive": (
            KB_IDS[LEGACY_DEMO_DOCUMENT_KB_KEYS["finance_sensitive"]],
            "임원 보상 정책.md",
            "민감 재무 문서 예시입니다. 일반 팀에는 검색 권한을 부여하지 않습니다.",
            """# 임원 보상 정책

이 문서는 재무 제한 문서팀 전용 민감 문서 예시입니다.
임원 보상, 보너스 산정 기준, 비공개 예산 항목은 일반 구성원에게 공개하지 않습니다.

## 접근 정책

재무 제한 문서팀 권한이 없는 사용자는 이 문서의 원문과 검색 결과를 조회할 수 없습니다.
""",
        ),
    }
    for key, (knowledge_base_id, filename, summary, content) in document_specs.items():
        file_path = _write_demo_document(filename, content)
        _upsert_by_id(
            db,
            Document,
            DOCUMENT_IDS[key],
            {
                "knowledge_base_id": knowledge_base_id,
                "filename": filename,
                "file_path": file_path,
                "source_type": SourceType.FILE,
                "content_hash": f"demo-{key}",
                "status": "completed",
                "error_message": None,
                "chunk_size": 500,
                "chunk_overlap": 50,
                "meta_info": {
                    **_demo_options(f"document-{key}"),
                    "summary": summary,
                },
                "embedding_model": DEMO_EMBEDDING_MODEL,
            },
        )

    for spec in DEMO_DOCUMENT_SPECS:
        existing = db.get(Document, DOCUMENT_IDS[spec.key])
        if spec.content is not None:
            file_path = _write_demo_document(spec.filename, spec.content)
            document_filename = spec.filename
            source_path_for_meta = file_path
            source_available = True
        elif knowledge_fixture is not None:
            fixture_doc = fixture_documents[spec.key]
            file_path = None
            document_filename = fixture_doc["filename"]
            source_path_for_meta = fixture_doc.get("source_filename") or document_filename
            source_available = False
        else:
            legal_source_path = _resolve_legal_pdf(spec)
            file_path = _copy_demo_source_file(legal_source_path, legal_source_path.name)
            document_filename = legal_source_path.name
            source_path_for_meta = legal_source_path.as_posix()
            source_available = True
        _upsert_by_id(
            db,
            Document,
            DOCUMENT_IDS[spec.key],
            {
                "knowledge_base_id": KB_IDS[spec.key],
                "filename": document_filename,
                "file_path": file_path,
                "source_type": SourceType.FILE,
                "content_hash": existing.content_hash if existing else None,
                "status": "completed",
                "error_message": None,
                "chunk_size": spec.chunk_size,
                "chunk_overlap": spec.chunk_overlap,
                "meta_info": _preserve_indexing_meta(
                    existing,
                    _document_meta_base(
                        spec,
                        source_path_for_meta,
                        source_available=source_available,
                    ),
                ),
                "embedding_model": existing.embedding_model
                if existing and existing.embedding_model
                else DEMO_EMBEDDING_MODEL,
            },
        )

    for spec in ONBOARDING_PDF_SPECS:
        existing = db.get(Document, DOCUMENT_IDS[spec.key])
        source_path = _resolve_onboarding_pdf(spec)
        file_path = _copy_onboarding_pdf(spec, source_path)
        _upsert_by_id(
            db,
            Document,
            DOCUMENT_IDS[spec.key],
            {
                "knowledge_base_id": KB_IDS[spec.key],
                "filename": spec.filename,
                "file_path": file_path,
                "source_type": SourceType.FILE,
                "content_hash": existing.content_hash if existing else None,
                "status": existing.status if existing else "pending",
                "error_message": None,
                "chunk_size": spec.chunk_size,
                "chunk_overlap": spec.chunk_overlap,
                "meta_info": _preserve_indexing_meta(
                    existing,
                    _document_meta_base(spec, source_path.as_posix()),
                ),
                "embedding_model": existing.embedding_model
                if existing and existing.embedding_model
                else DEMO_EMBEDDING_MODEL,
            },
        )

    db.flush()
    _index_demo_documents(db, INDEXED_DEMO_DOCUMENT_SPECS, knowledge_fixture)
    if _should_index_onboarding_pdfs():
        _index_demo_documents_from_sources(
            db,
            ONBOARDING_PDF_SPECS,
            persist_fixture=False,
        )


def _ensure_openai_provider_and_models(db: Session) -> tuple[LLMProvider, dict[str, LLMModel]]:
    provider = db.query(LLMProvider).filter(LLMProvider.name == "openai").first()
    if provider is None:
        provider = LLMProvider(
            name="openai",
            description="OpenAI default provider",
            base_url=DEMO_OPENAI_BASE_URL,
            type="system",
            auth_type="api_key",
            doc_url="https://platform.openai.com/api-keys",
        )
        db.add(provider)
        db.flush()

    model_specs = {
        DEMO_CHAT_MODEL: ("chat", Decimal("0.002500"), Decimal("0.015000"), 400000),
        DEMO_CHAT_MINI_MODEL: (
            "chat",
            Decimal("0.000750"),
            Decimal("0.004500"),
            400000,
        ),
        DEMO_MODEL_ROUTER_BASE_MODEL: (
            "chat",
            Decimal("0.000250"),
            Decimal("0.002000"),
            400000,
        ),
        DEMO_MODEL_ROUTER_FALLBACK_MODEL: (
            "chat",
            Decimal("0.002000"),
            Decimal("0.008000"),
            1000000,
        ),
        DEMO_MODEL_ROUTER_CHEAP_MODEL: (
            "chat",
            Decimal("0.000150"),
            Decimal("0.000600"),
            128000,
        ),
        DEMO_MODEL_ROUTER_BALANCED_MODEL: (
            "chat",
            Decimal("0.000400"),
            Decimal("0.001600"),
            1000000,
        ),
        DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL: (
            "chat",
            Decimal("0.001000"),
            Decimal("0.006000"),
            400000,
        ),
        DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL: (
            "chat",
            Decimal("0.002500"),
            Decimal("0.015000"),
            400000,
        ),
        DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL: (
            "chat",
            Decimal("0.005000"),
            Decimal("0.030000"),
            400000,
        ),
        DEMO_MODEL_ROUTER_LATEST_SOL_MODEL: (
            "chat",
            Decimal("0.005000"),
            Decimal("0.030000"),
            400000,
        ),
        DEMO_MODEL_ROUTER_OMNI_MODEL: (
            "chat",
            Decimal("0.002500"),
            Decimal("0.010000"),
            128000,
        ),
        DEMO_MODEL_ROUTER_REASONING_MODEL: (
            "chat",
            Decimal("0.002000"),
            Decimal("0.008000"),
            200000,
        ),
        DEMO_ONBOARDING_ROUTER_MODEL: (
            "chat",
            Decimal("0.001000"),
            Decimal("0.006000"),
            400000,
        ),
        DEMO_EMBEDDING_MODEL: (
            "embedding",
            Decimal("0.000020"),
            Decimal("0.000000"),
            8191,
        ),
    }
    models: dict[str, LLMModel] = {}
    for model_id, (
        model_type,
        input_price,
        output_price,
        context_window,
    ) in model_specs.items():
        model = (
            db.query(LLMModel)
            .filter(
                LLMModel.provider_id == provider.id,
                LLMModel.model_id_for_api_call == model_id,
            )
            .first()
        )
        if model is None:
            model = LLMModel(
                provider_id=provider.id,
                model_id_for_api_call=model_id,
                name=model_id,
                type=model_type,
                context_window=context_window,
                input_price_1k=input_price,
                output_price_1k=output_price,
                is_active=True,
                model_metadata={"demo_seed": True},
            )
            db.add(model)
            db.flush()
        else:
            model.name = model_id
            model.type = model_type
            model.context_window = context_window
            model.input_price_1k = input_price
            model.output_price_1k = output_price
            model.is_active = True
        models[model_id] = model
    return provider, models


def _upsert_app_workflow(
    db: Session,
    key: str,
    name: str,
    description: str,
    owner_key: str,
    graph: dict[str, Any],
    *,
    deployed: bool,
    deployment_type: DeploymentType = DeploymentType.API,
    list_updated_at: datetime | None = None,
) -> Workflow:
    app_secret = f"sk-demo-{key}"
    app_values = {
        "organization_id": ORG_ID,
        "name": name,
        "description": description,
        "icon": _icon("🧭" if key == "internal_it_helpdesk_routing" else "📘"),
        "url_slug": (
            "internal-it-helpdesk-routing-demo"
            if key == "internal_it_helpdesk_routing"
            else f"demo-{key.replace('_', '-')}"
        ),
        "auth_secret": None,
        "auth_secret_verifier": app_auth_secret_verifier(app_secret),
        "auth_secret_verifier_version": APP_AUTH_SECRET_VERIFIER_VERSION,
        "auth_secret_generation": 1,
        "auth_secret_previous_verifier": None,
        "auth_secret_previous_verifier_version": None,
        "auth_secret_previous_valid_until": None,
        "auth_secret_rotated_at": _now(),
        "is_api_enabled": True,
        "api_req_per_minute": 60,
        "api_req_per_hour": 3600,
        "is_market": False,
        "forked_from": None,
        "created_by": USER_IDS[owner_key],
        "workflow_id": None,
        "active_deployment_id": None,
    }
    app = _upsert_by_id(
        db,
        App,
        APP_IDS[key],
        _app_seed_values(db.get(App, APP_IDS[key]), app_values),
    )
    db.flush()

    workflow = _upsert_by_id(
        db,
        Workflow,
        WORKFLOW_IDS[key],
        {
            "organization_id": ORG_ID,
            "app_id": app.id,
            "graph": graph,
            "features": _demo_options(f"workflow-{key}"),
            "env_variables": [],
            "runtime_variables": [],
            "created_by": USER_IDS[owner_key],
            "updated_by": USER_IDS[owner_key],
        },
    )
    db.flush()
    app.workflow_id = workflow.id

    if deployed:
        deployment = _upsert_by_id(
            db,
            WorkflowDeployment,
            DEPLOYMENT_IDS[key],
            {
                "app_id": app.id,
                "version": 1,
                "type": deployment_type,
                "graph_snapshot": graph,
                "config": _demo_options(f"deployment-{key}"),
                "input_schema": _input_schema_from_graph(graph),
                "output_schema": _output_schema(),
                "description": "최종 시연용 배포 버전",
                "created_by": USER_IDS[owner_key],
                "is_active": True,
            },
        )
        db.flush()
        app.active_deployment_id = deployment.id
    else:
        app.active_deployment_id = None
    db.flush()

    if list_updated_at is not None:
        app.updated_at = list_updated_at
        db.flush()

    return workflow


def _delete_retired_demo_workflows(db: Session) -> None:
    retired_app_ids = list(RETIRED_DEMO_APP_IDS.values())
    retired_workflow_ids = list(RETIRED_DEMO_WORKFLOW_IDS.values())
    retired_deployment_ids = list(RETIRED_DEMO_DEPLOYMENT_IDS.values())
    retired_run_ids = {
        *RETIRED_DEMO_RUN_IDS,
        *(
            row[0]
            for row in db.query(WorkflowRun.id)
            .filter(WorkflowRun.workflow_id.in_(retired_workflow_ids))
            .all()
        ),
    }

    if retired_run_ids:
        db.query(TracePayloadAccessEvent).filter(
            TracePayloadAccessEvent.workflow_run_id.in_(retired_run_ids)
        ).delete(synchronize_session=False)
        db.query(TracePayload).filter(
            TracePayload.workflow_run_id.in_(retired_run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowNodeRun).filter(
            WorkflowNodeRun.workflow_run_id.in_(retired_run_ids)
        ).delete(synchronize_session=False)
    delete_conversation_sessions_for_resources(
        db,
        app_ids=retired_app_ids,
        workflow_ids=retired_workflow_ids,
        deployment_ids=retired_deployment_ids,
    )
    db.query(LLMUsageLog).filter(
        or_(
            LLMUsageLog.workflow_id.in_(retired_workflow_ids),
            LLMUsageLog.workflow_run_id.in_(retired_run_ids),
        )
    ).delete(synchronize_session=False)
    db.query(WorkflowRun).filter(
        WorkflowRun.workflow_id.in_(retired_workflow_ids)
    ).delete(synchronize_session=False)
    db.query(TeamWorkflowPermission).filter(
        or_(
            TeamWorkflowPermission.id.in_(
                list(RETIRED_DEMO_TEAM_WORKFLOW_PERMISSION_IDS.values())
            ),
            TeamWorkflowPermission.workflow_id.in_(retired_workflow_ids),
        )
    ).delete(synchronize_session=False)
    db.query(UserWorkflowPermission).filter(
        or_(
            UserWorkflowPermission.id.in_(
                list(RETIRED_DEMO_USER_WORKFLOW_PERMISSION_IDS.values())
            ),
            UserWorkflowPermission.workflow_id.in_(retired_workflow_ids),
        )
    ).delete(synchronize_session=False)

    db.query(App).filter(App.id.in_(retired_app_ids)).update(
        {"workflow_id": None, "active_deployment_id": None},
        synchronize_session=False,
    )
    db.flush()
    db.query(WorkflowDeployment).filter(
        or_(
            WorkflowDeployment.id.in_(retired_deployment_ids),
            WorkflowDeployment.app_id.in_(retired_app_ids),
        )
    ).delete(synchronize_session=False)
    db.query(Workflow).filter(
        Workflow.id.in_(retired_workflow_ids)
    ).delete(synchronize_session=False)
    db.query(App).filter(App.id.in_(retired_app_ids)).delete(
        synchronize_session=False
    )
    db.flush()


def _seed_apps_and_workflows(db: Session) -> dict[str, Workflow]:
    list_updated_at = _now()
    return {
        "internal_it_helpdesk_routing": _upsert_app_workflow(
            db,
            "internal_it_helpdesk_routing",
            "사내 IT 문의 자동 처리",
            "RAG와 자동 모델 라우팅으로 사내 IT 문의의 비용과 품질을 함께 관리하는 시연 workflow",
            "admin",
            _internal_it_helpdesk_routing_graph(),
            deployed=True,
            deployment_type=DeploymentType.WEBHOOK,
            list_updated_at=list_updated_at - timedelta(seconds=1),
        ),
        "onboarding_chatbot": _upsert_app_workflow(
            db,
            "onboarding_chatbot",
            "온보딩 챗봇",
            "새 workflow의 기본 입력 노드만 유지한 온보딩 챗봇 draft",
            "admin",
            _onboarding_chatbot_graph(),
            deployed=False,
            list_updated_at=list_updated_at,
        ),
        "new_employee_onboarding_chatbot": _upsert_app_workflow(
            db,
            "new_employee_onboarding_chatbot",
            "신입 사원 온보딩 챗봇",
            "팀별 온보딩 문서를 RAG로 검색하는 신입 사원 안내 workflow",
            "admin",
            _new_employee_onboarding_chatbot_graph(),
            deployed=True,
            deployment_type=DeploymentType.INTERNAL_CHATBOT,
            list_updated_at=list_updated_at - timedelta(seconds=2),
        ),
    }


def _seed_permissions(db: Session) -> None:
    _upsert_by_id(
        db,
        TeamWorkflowPermission,
        TEAM_PERMISSION_IDS["internal_it_helpdesk_routing"],
        {
            "grantee_organization_id": ORG_ID,
            "team_id": TEAM_IDS["platform_admin"],
            "workflow_id": WORKFLOW_IDS["internal_it_helpdesk_routing"],
            "auth_state": "manager",
            "assigned_by": USER_IDS["admin"],
            "options": _demo_options("permission-internal-it-helpdesk-routing"),
            "flags": 0,
        },
    )

    for permission_key, team_key, workflow_key in (
        (
            "onboarding_chatbot_platform",
            "onboarding_platform",
            "onboarding_chatbot",
        ),
        (
            "onboarding_chatbot_sales",
            "onboarding_sales",
            "onboarding_chatbot",
        ),
        (
            "new_employee_onboarding_chatbot_platform",
            "onboarding_platform",
            "new_employee_onboarding_chatbot",
        ),
        (
            "new_employee_onboarding_chatbot_sales",
            "onboarding_sales",
            "new_employee_onboarding_chatbot",
        ),
    ):
        _upsert_by_id(
            db,
            TeamWorkflowPermission,
            TEAM_PERMISSION_IDS[permission_key],
            {
                "grantee_organization_id": ORG_ID,
                "team_id": TEAM_IDS[team_key],
                "workflow_id": WORKFLOW_IDS[workflow_key],
                "auth_state": "operator",
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(
                    f"permission-{permission_key.replace('_', '-')}"
                ),
                "flags": 0,
            },
        )

    for index, team_key in enumerate(["hr_knowledge_users", "platform_admin"]):
        _upsert_by_id(
            db,
            TeamKnowledgePermission,
            _uuid(850 + index),
            {
                "grantee_organization_id": ORG_ID,
                "team_id": TEAM_IDS[team_key],
                "knowledge_base_id": KB_IDS["hr"],
                "auth_state": "manager" if team_key == "platform_admin" else "operator",
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(f"knowledge-permission-{team_key}"),
                "flags": 0,
            },
        )

    _upsert_by_id(
        db,
        TeamKnowledgePermission,
        _uuid(852),
        {
            "grantee_organization_id": ORG_ID,
            "team_id": TEAM_IDS["finance_restricted"],
            "knowledge_base_id": KB_IDS["finance"],
            "auth_state": "viewer",
            "assigned_by": USER_IDS["admin"],
            "options": _demo_options("knowledge-permission-finance"),
            "flags": 0,
        },
    )

    for index, (kb_key, team_key, auth_state) in enumerate(
        _demo_team_knowledge_permission_specs()
    ):
        _upsert_by_id(
            db,
            TeamKnowledgePermission,
            _uuid(853 + index),
            {
                "grantee_organization_id": ORG_ID,
                "team_id": TEAM_IDS[team_key],
                "knowledge_base_id": KB_IDS[kb_key],
                "auth_state": auth_state,
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(f"knowledge-permission-{kb_key}-{team_key}"),
                "flags": 0,
            },
        )

    for index, (collection_key, team_key, action) in enumerate(
        _demo_team_knowledge_collection_permission_specs()
    ):
        _upsert_by_id(
            db,
            TeamKnowledgeCollectionPermission,
            _uuid(880 + index),
            {
                "grantee_organization_id": ORG_ID,
                "team_id": TEAM_IDS[team_key],
                "knowledge_collection_id": COLLECTION_IDS[collection_key],
                "permission_action": action,
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(
                    f"collection-permission-{collection_key}-{team_key}-{action}"
                ),
                "flags": 0,
            },
        )

    _upsert_by_id(
        db,
        TeamAuditPermission,
        _uuid(870),
        {
            "grantee_organization_id": ORG_ID,
            "team_id": TEAM_IDS["platform_admin"],
            "target_organization_id": ORG_ID,
            "auth_state": "manager",
            "assigned_by": USER_IDS["admin"],
            "options": _demo_options("audit-permission-admin"),
            "flags": 0,
        },
    )
    _upsert_by_id(
        db,
        TeamAuditPermission,
        _uuid(871),
        {
            "grantee_organization_id": ORG_ID,
            "team_id": TEAM_IDS["onboarding_people"],
            "target_organization_id": ORG_ID,
            "auth_state": "manager",
            "assigned_by": USER_IDS["admin"],
            "options": _demo_options("audit-permission-onboarding-people"),
            "flags": 0,
        },
    )


def _internal_it_helpdesk_result(
    spec: InternalITHelpdeskRunSpec,
) -> tuple[str, bool, str]:
    if spec.department in {"보안", "정보보안팀"}:
        return (
            spec.request_type or "보안 사고 대응",
            True,
            spec.answer
            or "관련 계정의 접근을 즉시 제한하고 로그와 증거를 보존한 뒤 보안 담당자에게 에스컬레이션하세요.",
        )
    if "MFA" in spec.message or "Git" in spec.message:
        return (
            "인증·접근 장애",
            False,
            "계정 상태와 MFA 등록을 먼저 확인한 뒤 SSO 세션, VPN, 저장소 권한 순서로 점검하세요.",
        )
    return (
        "VPN 사용 안내",
        False,
        "사내 People 포털에서 VPN 설치 가이드를 확인하고 안내된 순서대로 설치하세요.",
    )


def _internal_it_helpdesk_routing_reason(
    spec: InternalITHelpdeskRunSpec,
    *,
    approval_required: bool,
) -> tuple[str, str, str]:
    """Return demo routing evidence from the canonical model catalog."""
    catalog = catalog_metadata_for_model_id(spec.model_name)
    capability_tier = str(catalog.get("capability_tier") or "balanced")

    if approval_required:
        return (
            capability_tier,
            "security_incident_reasoning",
            "보안 사고 판단에 적합",
        )
    if capability_tier == "advanced":
        return (
            capability_tier,
            "complex_professional_reasoning",
            "복합 조건 판단에 적합",
        )
    if capability_tier == "balanced":
        return (
            capability_tier,
            "multi_constraint",
            "복수 조건 처리에 적합",
        )
    return capability_tier, "simple_response", "단순 안내에 충분한 모델"


def _internal_it_helpdesk_reason_factors(
    spec: InternalITHelpdeskRunSpec,
    *,
    approval_required: bool,
) -> list[str]:
    """Return the safe, UI-visible Judge factors for a seeded routing decision."""
    capability_tier = str(
        catalog_metadata_for_model_id(spec.model_name).get("capability_tier")
        or "balanced"
    )
    if approval_required:
        return [
            "high_decision_impact",
            "security_or_compliance_risk",
            "multi_step_reasoning",
        ]
    if capability_tier == "advanced":
        return ["high_decision_impact", "broad_context_synthesis"]
    if capability_tier == "balanced":
        return ["multi_step_reasoning", "strict_output_reliability"]
    return ["strict_output_reliability"]


def _internal_it_helpdesk_usage_node_id(usage_kind: str) -> str:
    """Keep Judge billing separate from the LLM node's execution billing."""

    return "llm-triage:routing_judge" if usage_kind == "judge" else "llm-triage"


def _seed_internal_it_helpdesk_run(
    db: Session,
    *,
    spec: InternalITHelpdeskRunSpec,
    started_at: datetime,
    models: dict[str, LLMModel],
) -> None:
    request_type, approval_required, answer = _internal_it_helpdesk_result(spec)
    llm_text = json.dumps(
        {
            "문의 유형": request_type,
            "긴급도": approval_required,
            "답변 초안": answer,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    final_text = (
        "[긴급 IT 보안 에스컬레이션]\n\n"
        f"부서: {spec.department}\n문의: {spec.message}\n"
        f"문의 유형: {request_type}\n\n초기 대응 안내:\n{answer}"
        if approval_required
        else answer
    )
    finished_at = started_at + timedelta(seconds=spec.duration)
    routing_tier, reason_code, reason_short = _internal_it_helpdesk_routing_reason(
        spec,
        approval_required=approval_required,
    )
    reason_factors = _internal_it_helpdesk_reason_factors(
        spec,
        approval_required=approval_required,
    )
    retrieved_chunks = 2 if approval_required or "MFA" in spec.message else 1
    context_tokens = 1024 if retrieved_chunks == 2 else 514
    judge_cost = max(spec.total_cost - spec.node_cost, Decimal("0"))
    judge_tokens = max(
        spec.total_tokens - spec.prompt_tokens - spec.completion_tokens,
        0,
    )
    judge_completion_tokens = min(judge_tokens, 153)
    judge_prompt_tokens = judge_tokens - judge_completion_tokens
    routing_metadata = {
        "enabled": True,
        "strategy_id": "judge_bootstrap_incremental_v1",
        "decision_source": "runtime_judge",
        "policy_source": "test_ephemeral",
        "policy_version": "test-ephemeral-judge-first-v1",
        "selected_model": spec.model_name,
        "fallback_model": DEMO_MODEL_ROUTER_BALANCED_MODEL,
        "reason_code": reason_code,
        "judge_called": True,
        "included_in_routing_learning": False,
        "judge": {
            "attempted": True,
            "status": "selected",
            "model": DEMO_CHAT_MINI_MODEL,
            "selected_model": spec.model_name,
            "confidence": 0.92 if routing_tier != "balanced" else 0.86,
            "reason_code": reason_code,
            "reason_short": reason_short,
            "reason_factors": reason_factors,
            "candidate_model_count": 10,
            "cost": float(judge_cost),
            "usage": {
                "prompt_tokens": judge_prompt_tokens,
                "completion_tokens": judge_completion_tokens,
                "total_tokens": judge_tokens,
            },
        },
        "runtime_context": {
            "intent": "internal_it_helpdesk",
            "node_task": "internal_it_helpdesk",
            "risk_level": "high" if approval_required else "medium",
            "output_format": "json",
            "schema_required": True,
            "knowledge_enabled": True,
            "input_length_bucket": "short" if len(spec.message) < 80 else "medium",
        },
        "rag_context": {
            "used": True,
            "retrieved_chunk_count": retrieved_chunks,
            "retrieved_context_token_estimate": context_tokens,
            "evidence_sufficient": True,
            "partial_result": False,
        },
    }
    llm_outputs = {
        "text": llm_text,
        "model": spec.model_name,
        "cost": float(spec.node_cost),
        "usage": {
            "prompt_tokens": spec.prompt_tokens,
            "completion_tokens": spec.completion_tokens,
            "total_tokens": spec.prompt_tokens + spec.completion_tokens,
        },
        "metadata": {
            "model_routing": routing_metadata,
            "fallback_used": False,
            "finish_reason": "stop",
            "schema_status": "passed",
            "rag": {
                "rag_mode": "explicit_kb",
                "retrieved_chunk_count": retrieved_chunks,
                "context_token_estimate": context_tokens,
                "evidence_sufficient": True,
                "permission_filter_applied": True,
            },
        },
    }
    run = _upsert_by_id(
        db,
        WorkflowRun,
        spec.run_id,
        {
            "workflow_id": WORKFLOW_IDS["internal_it_helpdesk_routing"],
            "user_id": USER_IDS["admin"],
            "app_id": APP_IDS["internal_it_helpdesk_routing"],
            "deployment_id": None,
            "workflow_version": 1,
            "status": RunStatus.SUCCESS,
            "trigger_mode": RunTriggerMode.MANUAL,
            "inputs": {"department": spec.department, "message": spec.message},
            "outputs": {"answer_text": final_text},
            "error_message": None,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration": spec.duration,
            "meta_info": _demo_options(f"run-internal-it-{spec.run_id}"),
            "total_tokens": spec.total_tokens,
            "total_cost": spec.total_cost,
            "trace_metadata": {
                "demo_seed": True,
                "scenario": "internal_it_helpdesk_model_routing",
            },
            "redaction_applied": False,
            "pii_detected": False,
            "payload_storage_mode": "redacted_only",
        },
    )
    db.flush()

    selected_handle = "approval" if approval_required else "default"
    template_node_id = "template-approval" if approval_required else "template-reply"
    answer_node_id = "answer-approval" if approval_required else "answer-reply"
    node_specs = (
        (
            "webhook-ticket",
            "webhookTrigger",
            0.02,
            {"department": spec.department, "message": spec.message},
            {"department": spec.department, "message": spec.message},
        ),
        (
            "llm-triage",
            "llmNode",
            max(spec.duration - 0.15, 0.5),
            {"department": spec.department, "message": spec.message},
            llm_outputs,
        ),
        (
            "extract-ticket",
            "variableExtractionNode",
            0.03,
            {"llm-triage": llm_outputs},
            {
                "requestType": request_type,
                "approvalRequired": approval_required,
                "mailDraft": answer,
            },
        ),
        (
            "condition-approval",
            "conditionNode",
            0.01,
            {"approvalRequired": approval_required},
            {"selected_handle": selected_handle},
        ),
        (template_node_id, "templateNode", 0.01, {"mailDraft": answer}, {"text": final_text}),
        (answer_node_id, "answerNode", 0.01, {"text": final_text}, {"answer_text": final_text}),
    )
    for sequence, (node_id, node_type, duration, inputs, outputs) in enumerate(
        node_specs, start=1
    ):
        node_trace = {
            "demo_seed": True,
            "cost_hotspot": node_id == "llm-triage",
        }
        if node_id == "llm-triage":
            node_trace.update(
                {
                    "llm": {
                        **routing_metadata,
                        "model": spec.model_name,
                        "total_cost": float(spec.node_cost),
                        "prompt_tokens": spec.prompt_tokens,
                        "completion_tokens": spec.completion_tokens,
                        "total_tokens": spec.prompt_tokens + spec.completion_tokens,
                        "schema_status": "passed",
                        "downstream_status": "passed",
                        "fallback_used": False,
                    },
                    "rag": {
                        "retrieved_chunk_count": retrieved_chunks,
                        "context_token_estimate": context_tokens,
                        "evidence_sufficient": True,
                    },
                }
            )
        _upsert_by_id(
            db,
            WorkflowNodeRun,
            uuid.uuid5(spec.run_id, f"internal-it-node:{node_id}"),
            {
                "workflow_run_id": run.id,
                "node_id": node_id,
                "node_type": node_type,
                "status": NodeRunStatus.SUCCESS,
                "inputs": inputs,
                "process_data": _demo_options(
                    f"internal-it-node-{spec.run_id}-{node_id}"
                ),
                "outputs": outputs,
                "error_message": None,
                "started_at": started_at + timedelta(milliseconds=100 * sequence),
                "finished_at": started_at
                + timedelta(milliseconds=100 * sequence)
                + timedelta(seconds=duration),
                "duration": duration,
                "trace_metadata": node_trace,
                "redaction_applied": False,
                "pii_detected": False,
                "sequence": sequence,
                "retry_count": 0,
            },
        )

    usage_specs = (
        (
            "execution",
            spec.model_name,
            spec.prompt_tokens,
            spec.completion_tokens,
            spec.node_cost,
            int(spec.duration * 1000),
        ),
        (
            "judge",
            DEMO_CHAT_MINI_MODEL,
            judge_prompt_tokens,
            judge_completion_tokens,
            judge_cost,
            max(int(spec.duration * 1000) - 500, 1),
        ),
    )
    for usage_kind, model_name, prompt, completion, cost, latency_ms in usage_specs:
        _upsert_by_id(
            db,
            LLMUsageLog,
            uuid.uuid5(spec.run_id, f"internal-it-usage:{usage_kind}"),
            {
                "user_id": USER_IDS["admin"],
                "organization_id": ORG_ID,
                "credential_id": LLM_CREDENTIAL_IDS["demo_openai"],
                "model_id": models[model_name].id,
                "workflow_id": WORKFLOW_IDS["internal_it_helpdesk_routing"],
                "workflow_run_id": spec.run_id,
                "node_id": _internal_it_helpdesk_usage_node_id(usage_kind),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_cost": cost,
                "latency_ms": latency_ms,
                "status": "success",
                "created_at": started_at,
            },
        )


def _seed_llm_credential(
    db: Session, provider: LLMProvider, models: dict[str, LLMModel]
) -> LLMCredential:
    """데모 조직 공용 credential과 verified model relation (FR-012 비용 집계용)."""
    from apps.shared.services.llm_credential_config import (
        protect_llm_credential_config,
    )

    runtime_credential_enabled = _runtime_openai_credential_requested()
    api_key = (
        _require_seed_env("OPENAI_API_KEY")
        if runtime_credential_enabled
        else "sk-demo-not-a-real-key"
    )
    envelope = protect_llm_credential_config(
        {"apiKey": api_key, "baseUrl": provider.base_url}
    )
    credential = _upsert_by_id(
        db,
        LLMCredential,
        LLM_CREDENTIAL_IDS["demo_openai"],
        {
            "provider_id": provider.id,
            "user_id": USER_IDS["admin"],
            "organization_id": ORG_ID,
            "credential_name": (
                "시연 OpenAI Runtime Credential"
                if runtime_credential_enabled
                else "데모 OpenAI Credential"
            ),
            "encrypted_config": envelope.ciphertext,
            "encryption_key_version": envelope.key_version,
            "encryption_algorithm": envelope.algorithm,
            "config_preview": (
                _mask_demo_api_key(api_key)
                if runtime_credential_enabled
                else "sk-****demo"
            ),
            "is_valid": True,
            "quota_type": "unlimited",
            "quota_limit": 0,
            "quota_used": 0,
        },
    )
    relation_model_names = [
        DEMO_CHAT_MODEL,
        DEMO_CHAT_MINI_MODEL,
        DEMO_MODEL_ROUTER_BASE_MODEL,
        DEMO_MODEL_ROUTER_FALLBACK_MODEL,
        DEMO_MODEL_ROUTER_CHEAP_MODEL,
        DEMO_MODEL_ROUTER_BALANCED_MODEL,
        DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL,
        DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL,
        DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL,
        DEMO_MODEL_ROUTER_LATEST_SOL_MODEL,
        DEMO_MODEL_ROUTER_OMNI_MODEL,
        DEMO_MODEL_ROUTER_REASONING_MODEL,
        DEMO_ONBOARDING_ROUTER_MODEL,
    ]
    if runtime_credential_enabled:
        relation_model_names.append(DEMO_EMBEDDING_MODEL)
    else:
        db.query(LLMRelCredentialModel).filter(
            LLMRelCredentialModel.id.in_(
                [
                    CREDENTIAL_MODEL_REL_IDS[DEMO_EMBEDDING_MODEL],
                ]
            )
        ).delete(synchronize_session=False)
        _delete_demo_runtime_llm_permissions(db)

    # A model can serve multiple demo roles (for example, fallback and onboarding
    # routing). Persist one credential relation per distinct model.
    for model_name in dict.fromkeys(relation_model_names):
        _upsert_by_id(
            db,
            LLMRelCredentialModel,
            CREDENTIAL_MODEL_REL_IDS[model_name],
            {
                "credential_id": LLM_CREDENTIAL_IDS["demo_openai"],
                "model_id": models[model_name].id,
                "is_verified": True,
                "priority": 0,
            },
        )
    if runtime_credential_enabled:
        _seed_runtime_llm_permissions(db)
    return credential


def _delete_demo_runtime_llm_permissions(db: Session) -> None:
    db.query(TeamLLMPermission).filter(
        TeamLLMPermission.id.in_(list(TEAM_LLM_PERMISSION_IDS.values()))
    ).delete(synchronize_session=False)
    db.query(UserLLMPermission).filter(
        UserLLMPermission.id.in_(list(USER_LLM_PERMISSION_IDS.values()))
    ).delete(synchronize_session=False)


def _seed_runtime_llm_permissions(db: Session) -> None:
    for team_key, permission_id in TEAM_LLM_PERMISSION_IDS.items():
        _upsert_by_id(
            db,
            TeamLLMPermission,
            permission_id,
            {
                "grantee_organization_id": ORG_ID,
                "team_id": TEAM_IDS[team_key],
                "llm_credential_id": LLM_CREDENTIAL_IDS["demo_openai"],
                "auth_state": "manager" if team_key == "platform_admin" else "operator",
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(f"llm-permission-{team_key}"),
                "flags": 0,
            },
        )

    for user_key, permission_id in USER_LLM_PERMISSION_IDS.items():
        _upsert_by_id(
            db,
            UserLLMPermission,
            permission_id,
            {
                "grantee_organization_id": ORG_ID,
                "user_id": USER_IDS[user_key],
                "llm_credential_id": LLM_CREDENTIAL_IDS["demo_openai"],
                "auth_state": "operator",
                "assigned_by": USER_IDS["admin"],
                "options": _demo_options(f"llm-permission-{user_key}"),
                "flags": 0,
            },
        )


def _replace_internal_it_helpdesk_seed_runs(db: Session) -> None:
    """Replace only this demo's historical seed logs with the canonical set.

    The stable IDs in the current specs can also exist after a local manual
    reproduction. Removing their dependent rows first prevents a default seed
    run from leaving duplicated node-run details under the same workflow run.
    """
    current_run_ids = [spec.run_id for spec in INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS]
    legacy_seeded_run_ids = [
        row[0]
        for row in (
            db.query(WorkflowRun.id)
            .filter(
                WorkflowRun.workflow_id
                == WORKFLOW_IDS["internal_it_helpdesk_routing"],
                WorkflowRun.trace_metadata["demo_seed"].astext == "true",
                WorkflowRun.trace_metadata["scenario"].astext
                == "internal_it_helpdesk_model_routing",
            )
            .all()
        )
    ]
    run_ids = list({*current_run_ids, *legacy_seeded_run_ids})
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


def _seed_runs_and_usage(db: Session, models: dict[str, LLMModel]) -> None:
    now = _now()
    _replace_internal_it_helpdesk_seed_runs(db)
    internal_it_started_at = now - timedelta(minutes=10)
    for index, spec in enumerate(INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS):
        _seed_internal_it_helpdesk_run(
            db,
            spec=spec,
            started_at=internal_it_started_at + timedelta(minutes=index),
            models=models,
        )

def _seed_permission_requests(db: Session) -> None:
    """author의 승인된 App 생성 권한 신청과 부여 결과 (ADR-0016, FR-014)."""
    now = _now()
    requested_at = now - timedelta(days=7)
    decided_at = requested_at + timedelta(hours=1)
    _upsert_by_id(
        db,
        PermissionRequest,
        PERMISSION_REQUEST_IDS["author_app_create"],
        {
            "organization_id": ORG_ID,
            "user_id": USER_IDS["author"],
            "requested_permission": REQUESTED_PERMISSION_APP_CREATE,
            "reason": "고객지원 운영 workflow를 직접 만들고 배포해야 합니다.",
            "status": PERMISSION_REQUEST_APPROVED,
            "created_at": requested_at,
            "decided_by": USER_IDS["admin"],
            "decided_at": decided_at,
            "options": _demo_options("permission-request-author"),
        },
    )
    _upsert_by_id(
        db,
        UserAppCreationPermission,
        APP_CREATION_PERMISSION_IDS["author"],
        {
            "grantee_organization_id": ORG_ID,
            "user_id": USER_IDS["author"],
            "assigned_by": USER_IDS["admin"],
            "assigned_at": decided_at,
            "options": _demo_options("app-creation-permission-author"),
        },
    )


def _seed_audit_logs(db: Session) -> None:
    now = _now()
    db.query(AuditLog).filter(
        AuditLog.audit_metadata["demo_seed"].astext == "true"
    ).delete(synchronize_session=False)
    # ADR-0008 canonical action 기준. occurred_at은 index가 클수록 과거이므로
    # 최신 이벤트를 앞에 둔다 (신청 -> 승인 -> 권한 부여가 시간순이 되도록).
    logs = [
        (AuditAction.USER_LOGIN, "rookie", "user", USER_IDS["rookie"], "신입사원 로그인"),
        (AuditAction.LLM_CALL, "admin", "workflow", str(WORKFLOW_IDS["internal_it_helpdesk_routing"]), "사내 IT 문의 LLM 비용 사용 기록"),
        (AuditAction.WORKFLOW_EXECUTE, "admin", "workflow", str(WORKFLOW_IDS["internal_it_helpdesk_routing"]), "사내 IT 문의 workflow 실행"),
        (AuditAction.WORKFLOW_DEPLOY, "admin", "workflow", str(WORKFLOW_IDS["internal_it_helpdesk_routing"]), "사내 IT 문의 자동 처리 배포"),
        (AuditAction.POLICY_BLOCK, "admin", "knowledge", str(KB_IDS["finance"]), "민감 문서 접근 정책 차단"),
        (AuditAction.PERMISSION_DENIED, "author", "workflow", str(WORKFLOW_IDS["internal_it_helpdesk_routing"]), "권한 없는 사내 IT workflow 접근 차단"),
        (AuditAction.WORKFLOW_CREATE, "admin", "workflow", str(WORKFLOW_IDS["internal_it_helpdesk_routing"]), "사내 IT 문의 자동 처리 생성"),
        (AuditAction.USER_APP_CREATION_PERMISSION_CREATED, "admin", "user_app_creation_permission", str(APP_CREATION_PERMISSION_IDS["author"]), "운영자 App 생성 권한 부여"),
        (AuditAction.PERMISSION_REQUEST_APPROVED, "admin", "permission_request", str(PERMISSION_REQUEST_IDS["author_app_create"]), "운영자 권한 신청 승인"),
        (AuditAction.PERMISSION_REQUEST_CREATED, "author", "permission_request", str(PERMISSION_REQUEST_IDS["author_app_create"]), "운영자 workflow 생성/배포 권한 신청"),
    ]
    for index, (action, actor_key, target_type, target_id, summary) in enumerate(
        logs
    ):
        _upsert_by_id(
            db,
            AuditLog,
            _uuid(4000 + index),
            {
                "occurred_at": now - timedelta(minutes=index * 7),
                "actor_id": USER_IDS[actor_key],
                "actor_type": ActorType.USER,
                "category": AuditCategory.ACTION,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "before": None,
                "after": None,
                "status": AuditStatus.SUCCESS
                if action != AuditAction.PERMISSION_DENIED
                else AuditStatus.FAILURE,
                "audit_metadata": {
                    **_demo_options(f"audit-{index}"),
                    "summary": summary,
                    "organization_id": str(ORG_ID),
                },
            },
        )


def _demo_security_alert_detection_key() -> str:
    return build_security_alert_detection_key(
        organization_id=ORG_ID,
        actor_id=USER_IDS["rookie"],
        rule_id="repeated_permission_denied",
        rule_version="v1",
    )


def _seed_security_alert(db: Session) -> None:
    now = _now()
    detection_key = _demo_security_alert_detection_key()
    first_occurred_at = now - timedelta(minutes=4, seconds=30)
    operations = ("list",) * 5 + ("resolve",) * 5
    occurred_at_by_audit_id: dict[uuid.UUID, datetime] = {}

    for index, (audit_id, operation) in enumerate(
        zip(DEMO_SECURITY_ALERT_PERMISSION_AUDIT_IDS, operations, strict=True)
    ):
        occurred_at = first_occurred_at + timedelta(seconds=index * 30)
        occurred_at_by_audit_id[audit_id] = occurred_at
        _upsert_by_id(
            db,
            AuditLog,
            audit_id,
            {
                "occurred_at": occurred_at,
                "actor_id": USER_IDS["rookie"],
                "actor_type": ActorType.USER,
                "category": AuditCategory.ACTION,
                "action": AuditAction.PERMISSION_DENIED,
                "target_type": "organization",
                "target_id": str(ORG_ID),
                "before": None,
                "after": None,
                "status": AuditStatus.FAILURE,
                "audit_metadata": {
                    **_demo_options(f"security-alert-permission-denied-{index}"),
                    "organization_id": str(ORG_ID),
                    "required_permission": "security_alert.manage",
                    "requested_operation": operation,
                    "denial_reason": "organization_manager_required",
                    "permission_action": "manage",
                    "policy_result": "deny",
                },
            },
        )

    db.query(SecurityAlert).filter(
        SecurityAlert.detection_key == detection_key,
        SecurityAlert.id != DEMO_SECURITY_ALERT_ID,
        SecurityAlert.status.in_(("open", "acknowledged")),
    ).delete(synchronize_session=False)
    db.query(AuditLog).filter(
        AuditLog.target_type == "security_alert",
        AuditLog.target_id == str(DEMO_SECURITY_ALERT_ID),
    ).delete(synchronize_session=False)
    db.query(SecurityAlertAuditEvent).filter(
        SecurityAlertAuditEvent.security_alert_id == DEMO_SECURITY_ALERT_ID
    ).delete(synchronize_session=False)

    threshold_detected_at = occurred_at_by_audit_id[
        DEMO_SECURITY_ALERT_PERMISSION_AUDIT_IDS[4]
    ]
    _upsert_by_id(
        db,
        SecurityAlert,
        DEMO_SECURITY_ALERT_ID,
        {
            "organization_id": ORG_ID,
            "subject_actor_id": USER_IDS["rookie"],
            "rule_id": "repeated_permission_denied",
            "rule_version": "v1",
            "severity": "medium",
            "status": "open",
            "policy_reason": None,
            "detection_key": detection_key,
            "occurrence_count": len(DEMO_SECURITY_ALERT_PERMISSION_AUDIT_IDS),
            "episode_count": 1,
            "first_detected_at": first_occurred_at,
            "last_detected_at": now,
            "last_episode_started_at": threshold_detected_at,
            "lifecycle_version": 1,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "resolution_type": None,
            "resolution_reason": None,
            "resolved_by": None,
            "resolved_at": None,
            "created_at": threshold_detected_at,
            "updated_at": now,
        },
    )
    db.flush()

    for evidence_id, audit_id in zip(
        DEMO_SECURITY_ALERT_EVIDENCE_IDS,
        DEMO_SECURITY_ALERT_PERMISSION_AUDIT_IDS,
        strict=True,
    ):
        _upsert_by_id(
            db,
            SecurityAlertAuditEvent,
            evidence_id,
            {
                "security_alert_id": DEMO_SECURITY_ALERT_ID,
                "audit_log_id": audit_id,
                "linked_at": occurred_at_by_audit_id[audit_id],
            },
        )

    _upsert_by_id(
        db,
        AuditLog,
        DEMO_SECURITY_ALERT_DETECTED_AUDIT_ID,
        {
            "occurred_at": threshold_detected_at,
            "actor_id": None,
            "actor_type": ActorType.SYSTEM,
            "category": AuditCategory.ACTION,
            "action": "security_alert.detected",
            "target_type": "security_alert",
            "target_id": str(DEMO_SECURITY_ALERT_ID),
            "before": None,
            "after": None,
            "status": AuditStatus.SUCCESS,
            "audit_metadata": {
                "organization_id": str(ORG_ID),
                "rule_id": "repeated_permission_denied",
                "rule_version": "v1",
                "severity": "medium",
            },
        },
    )


def _adopt_existing_test_user_ids(db: Session) -> None:
    """Reuse existing local users with the test profile emails."""
    for spec in TEST_USER_SPECS:
        existing = db.query(User).filter(User.email == spec.email).first()
        if existing is not None:
            TEST_USER_IDS[spec.key] = existing.id


def _test_feature_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "start-test",
                "startNode",
                120,
                120,
                {
                    **_base_node_data("테스트 입력", "기능 검증용 입력을 받습니다.", 1),
                    "triggerType": "manual",
                    "trigger_type": "manual",
                    "variables": [
                        {
                            "id": "message",
                            "name": "message",
                            "label": "테스트 메시지",
                            "type": "paragraph",
                            "required": True,
                            "maxLength": 1000,
                            "max_length": 1000,
                        }
                    ],
                },
            ),
            _node(
                "template-test",
                "templateNode",
                540,
                120,
                {
                    **_base_node_data("테스트 응답 생성", "입력 값을 템플릿으로 반환합니다.", 2),
                    "template": "테스트 응답: {{ message }}",
                    "variables": [
                        {
                            "name": "message",
                            "value_selector": ["start-test", "message"],
                        }
                    ],
                },
            ),
            _node(
                "answer-test",
                "answerNode",
                960,
                120,
                {
                    **_base_node_data("응답", "테스트 결과를 반환합니다.", 3),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "답변",
                            "value_selector": ["template-test", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-test-start-template", "start-test", "template-test"),
            _edge("edge-test-template-answer", "template-test", "answer-test"),
        ],
        "viewport": {"x": 40, "y": 80, "zoom": 0.85},
    }


def seed_test_data(db: Session) -> None:
    """Upsert mutable local QA seed data without touching final demo rows."""
    _adopt_existing_test_user_ids(db)
    hashed_password = hash_password(DEMO_PASSWORD)

    for spec in TEST_USER_SPECS:
        _upsert_by_id(
            db,
            User,
            TEST_USER_IDS[spec.key],
            {
                "email": spec.email,
                "name": spec.name,
                "password": hashed_password,
                "social_provider": "none",
                "social_id": None,
                "avatar_url": None,
                "deactivated_at": None,
            },
        )
    db.flush()

    _upsert_by_id(
        db,
        Organization,
        TEST_ORG_ID,
        {
            "name": "노디즈 테스트 조직",
            "options": _demo_options("test-organization"),
            "flags": 0,
            "created_by": TEST_USER_IDS["admin"],
            "managed_by": TEST_USER_IDS["admin"],
            "is_active": True,
            "deactivated_at": None,
        },
    )
    db.flush()

    for index, spec in enumerate(TEST_USER_SPECS):
        _upsert_by_id(
            db,
            OrganizationMembership,
            _uuid(9040 + index),
            {
                "organization_id": TEST_ORG_ID,
                "user_id": TEST_USER_IDS[spec.key],
                "membership_state": spec.membership_state,
                "organization_auth_state": spec.organization_auth_state,
                "invited_by": TEST_USER_IDS["admin"],
                "invited_at": _now() - timedelta(days=7),
                "accepted_at": _now()
                if spec.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
                else None,
                "removed_at": _now()
                if spec.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
                else None,
                "options": _demo_options(f"test-org-membership-{spec.key}"),
                "flags": 0,
            },
        )

    for key, (name, description) in TEST_TEAM_SPECS.items():
        _upsert_by_id(
            db,
            Team,
            TEST_TEAM_IDS[key],
            {
                "organization_id": TEST_ORG_ID,
                "name": name,
                "description": description,
                "is_active": True,
                "created_by": TEST_USER_IDS["admin"],
                "options": _demo_options(f"test-team-{key}"),
                "flags": 0,
            },
        )

    membership_index = 0
    for spec in TEST_USER_SPECS:
        for team_key in spec.teams:
            _upsert_by_id(
                db,
                TeamMembership,
                _uuid(9050 + membership_index),
                {
                    "grantee_organization_id": TEST_ORG_ID,
                    "team_id": TEST_TEAM_IDS[team_key],
                    "user_id": TEST_USER_IDS[spec.key],
                    "assigned_by": TEST_USER_IDS["admin"],
                    "options": _demo_options(
                        f"test-team-membership-{spec.key}-{team_key}"
                    ),
                    "flags": 0,
                },
            )
            membership_index += 1

    graph = _test_feature_graph()
    test_app_secret = "sk-test-feature-workflow"
    test_app_values = {
        "organization_id": TEST_ORG_ID,
        "name": "테스트용 기능 검증 워크플로우",
        "description": "팀원이 기능 구현 중 자유롭게 변경해도 되는 테스트 workflow",
        "icon": _icon("🧪"),
        "url_slug": "test-feature-workflow",
        "auth_secret": None,
        "auth_secret_verifier": app_auth_secret_verifier(test_app_secret),
        "auth_secret_verifier_version": APP_AUTH_SECRET_VERIFIER_VERSION,
        "auth_secret_generation": 1,
        "auth_secret_previous_verifier": None,
        "auth_secret_previous_verifier_version": None,
        "auth_secret_previous_valid_until": None,
        "auth_secret_rotated_at": _now(),
        "is_api_enabled": True,
        "api_req_per_minute": 60,
        "api_req_per_hour": 3600,
        "is_market": False,
        "forked_from": None,
        "created_by": TEST_USER_IDS["builder"],
        "workflow_id": None,
        "active_deployment_id": None,
    }
    app = _upsert_by_id(
        db,
        App,
        TEST_APP_ID,
        _app_seed_values(db.get(App, TEST_APP_ID), test_app_values),
    )
    db.flush()
    workflow = _upsert_by_id(
        db,
        Workflow,
        TEST_WORKFLOW_ID,
        {
            "organization_id": TEST_ORG_ID,
            "app_id": app.id,
            "graph": graph,
            "features": _demo_options("test-workflow"),
            "env_variables": [],
            "runtime_variables": [],
            "created_by": TEST_USER_IDS["builder"],
            "updated_by": TEST_USER_IDS["builder"],
        },
    )
    db.flush()
    app.workflow_id = workflow.id

    deployment = _upsert_by_id(
        db,
        WorkflowDeployment,
        TEST_DEPLOYMENT_ID,
        {
            "app_id": app.id,
            "version": 1,
            "type": DeploymentType.API,
            "graph_snapshot": graph,
            "config": _demo_options("test-deployment"),
            "input_schema": _input_schema("message", "테스트 메시지"),
            "output_schema": _output_schema(),
            "description": "테스트 프로파일 기본 배포",
            "created_by": TEST_USER_IDS["builder"],
            "is_active": True,
        },
    )
    db.flush()
    app.active_deployment_id = deployment.id

    _upsert_by_id(
        db,
        TeamWorkflowPermission,
        TEST_PERMISSION_IDS["builder"],
        {
            "grantee_organization_id": TEST_ORG_ID,
            "team_id": TEST_TEAM_IDS["qa_builder"],
            "workflow_id": TEST_WORKFLOW_ID,
            "auth_state": "manager",
            "assigned_by": TEST_USER_IDS["admin"],
            "options": _demo_options("test-permission-builder"),
            "flags": 0,
        },
    )
    _upsert_by_id(
        db,
        TeamWorkflowPermission,
        TEST_PERMISSION_IDS["member"],
        {
            "grantee_organization_id": TEST_ORG_ID,
            "team_id": TEST_TEAM_IDS["qa_member"],
            "workflow_id": TEST_WORKFLOW_ID,
            "auth_state": "viewer",
            "assigned_by": TEST_USER_IDS["admin"],
            "options": _demo_options("test-permission-member"),
            "flags": 0,
        },
    )
    db.commit()


def reset_test_data(db: Session) -> None:
    """Delete fixed test profile rows, then recreate mutable QA seed data."""
    _adopt_existing_test_user_ids(db)
    user_ids = [
        row[0]
        for row in db.query(User.id)
        .filter(
            or_(User.id.in_(list(TEST_USER_IDS.values())), User.email.in_(TEST_EMAILS))
        )
        .all()
    ] or list(TEST_USER_IDS.values())

    # Test reset clears transient builder state child-first while preserving
    # demo and unrelated organizations.
    for model in (AgentBuilderDraft, AgentBuilderRequest, AgentBuilderSession):
        db.query(model).filter(model.organization_id == TEST_ORG_ID).delete(
            synchronize_session=False
        )

    db.query(AuditLog).filter(
        AuditLog.audit_metadata["demo_seed_key"].astext.like("test-%")
    ).delete(synchronize_session=False)
    db.query(LLMUsageLog).filter(LLMUsageLog.user_id.in_(user_ids)).delete(
        synchronize_session=False
    )
    db.query(App).filter(App.id == TEST_APP_ID).update(
        {"workflow_id": None, "active_deployment_id": None},
        synchronize_session=False,
    )
    db.flush()
    db.query(TeamWorkflowPermission).filter(
        TeamWorkflowPermission.id.in_(list(TEST_PERMISSION_IDS.values()))
    ).delete(synchronize_session=False)
    db.query(WorkflowDeployment).filter(
        WorkflowDeployment.id == TEST_DEPLOYMENT_ID
    ).delete(synchronize_session=False)
    db.query(Workflow).filter(Workflow.id == TEST_WORKFLOW_ID).delete(
        synchronize_session=False
    )
    db.query(App).filter(App.id == TEST_APP_ID).delete(synchronize_session=False)
    db.query(PermissionRequest).filter(
        or_(
            PermissionRequest.organization_id == TEST_ORG_ID,
            PermissionRequest.user_id.in_(user_ids),
            PermissionRequest.decided_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(UserAppCreationPermission).filter(
        or_(
            UserAppCreationPermission.grantee_organization_id == TEST_ORG_ID,
            UserAppCreationPermission.user_id.in_(user_ids),
            UserAppCreationPermission.assigned_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(TeamMembership).filter(
        or_(
            TeamMembership.grantee_organization_id == TEST_ORG_ID,
            TeamMembership.user_id.in_(user_ids),
            TeamMembership.assigned_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(OrganizationMembership).filter(
        or_(
            OrganizationMembership.organization_id == TEST_ORG_ID,
            OrganizationMembership.user_id.in_(user_ids),
            OrganizationMembership.invited_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(Team).filter(Team.id.in_(list(TEST_TEAM_IDS.values()))).delete(
        synchronize_session=False
    )
    db.commit()
    seed_test_data(db)


def seed_demo_data(db: Session) -> None:
    """Upsert final demo data without deleting unrelated local data."""
    validate_demo_seed_prerequisites()
    _seed_users_and_org(db)
    _seed_teams_and_memberships(db)
    _seed_knowledge(db)
    provider, models = _ensure_openai_provider_and_models(db)
    from apps.shared.services.model_routing_global_profile_catalog import (
        seed_model_routing_global_profiles,
    )

    seed_model_routing_global_profiles(db)
    _delete_retired_demo_workflows(db)
    _seed_apps_and_workflows(db)
    db.flush()
    _seed_permissions(db)
    _seed_permission_requests(db)
    _seed_llm_credential(db, provider, models)
    db.flush()
    _seed_runs_and_usage(db, models)
    _seed_audit_logs(db)
    _seed_security_alert(db)
    db.commit()


def reset_demo_data(db: Session) -> None:
    """Delete fixed demo rows, then recreate the final demo state."""
    validate_demo_seed_prerequisites()
    _adopt_existing_demo_user_ids(db)
    app_ids = [*APP_IDS.values(), *RETIRED_DEMO_APP_IDS.values()]
    workflow_ids = [*WORKFLOW_IDS.values(), *RETIRED_DEMO_WORKFLOW_IDS.values()]
    deployment_ids = [
        *DEPLOYMENT_IDS.values(),
        *RETIRED_DEMO_DEPLOYMENT_IDS.values(),
    ]
    team_ids = list(TEAM_IDS.values())
    kb_ids = [*KB_IDS.values(), *RETIRED_INTERNAL_DOCUMENT_KB_IDS.values()]
    collection_ids = [
        *COLLECTION_IDS.values(),
        *RETIRED_INTERNAL_DOCUMENT_COLLECTION_IDS.values(),
    ]
    credential_ids = [
        row[0]
        for row in db.query(LLMCredential.id)
        .filter(LLMCredential.organization_id == ORG_ID)
        .all()
    ]
    existing_demo_user_ids = [
        row[0]
        for row in db.query(User.id)
        .filter(or_(User.id.in_(list(USER_IDS.values())), User.email.in_(DEMO_EMAILS)))
        .all()
    ]
    user_ids = existing_demo_user_ids or list(USER_IDS.values())
    workflow_run_ids = {
        *[_uuid(2000 + i) for i in range(20)],
        *(spec.run_id for spec in INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS),
        *(
            row[0]
            for row in db.query(WorkflowRun.id)
            .filter(WorkflowRun.workflow_id.in_(workflow_ids))
            .all()
        ),
    }

    db.query(TracePayloadAccessEvent).filter(
        TracePayloadAccessEvent.workflow_run_id.in_(workflow_run_ids)
    ).delete(synchronize_session=False)
    db.query(TracePayload).filter(
        TracePayload.workflow_run_id.in_(workflow_run_ids)
    ).delete(synchronize_session=False)
    db.query(LLMUsageLog).filter(
        (LLMUsageLog.workflow_id.in_(workflow_ids))
        | (LLMUsageLog.user_id.in_(user_ids))
        | (LLMUsageLog.credential_id.in_(credential_ids))
    ).delete(synchronize_session=False)
    db.query(WorkflowNodeRun).filter(
        WorkflowNodeRun.workflow_run_id.in_(workflow_run_ids)
    ).delete(synchronize_session=False)
    db.query(WorkflowRun).filter(WorkflowRun.workflow_id.in_(workflow_ids)).delete(
        synchronize_session=False
    )
    delete_conversation_sessions_for_resources(
        db,
        app_ids=app_ids,
        workflow_ids=workflow_ids,
        deployment_ids=deployment_ids,
    )
    db.query(AuditLog).filter(
        AuditLog.audit_metadata["demo_seed"].astext == "true"
    ).delete(synchronize_session=False)

    # Break App <-> Workflow/Deployment references before deleting either side.
    db.query(App).filter(App.id.in_(app_ids)).update(
        {"workflow_id": None, "active_deployment_id": None},
        synchronize_session=False,
    )
    db.flush()

    for model in (
        TeamWorkflowPermission,
        UserWorkflowPermission,
        TeamKnowledgePermission,
        TeamKnowledgeCollectionPermission,
        TeamLLMPermission,
        TeamAuditPermission,
        UserLLMPermission,
    ):
        db.query(model).filter(
            or_(
                model.grantee_organization_id == ORG_ID,
                model.assigned_by.in_(user_ids),
            )
        ).delete(synchronize_session=False)

    db.query(UserKnowledgePermission).filter(
        or_(
            UserKnowledgePermission.knowledge_base_id.in_(kb_ids),
            UserKnowledgePermission.user_id.in_(user_ids),
            UserKnowledgePermission.assigned_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    # 시연 중 라이브로 만든 권한 신청/App 생성 권한도 함께 지워
    # 시나리오 1(차단 -> 신청 -> 승인)을 반복 시연할 수 있게 한다 (ADR-0016).
    db.query(PermissionRequest).filter(
        or_(
            PermissionRequest.organization_id == ORG_ID,
            PermissionRequest.user_id.in_(user_ids),
            PermissionRequest.decided_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(UserAppCreationPermission).filter(
        or_(
            UserAppCreationPermission.grantee_organization_id == ORG_ID,
            UserAppCreationPermission.user_id.in_(user_ids),
            UserAppCreationPermission.assigned_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)

    if credential_ids:
        db.query(TeamLLMPermission).filter(
            TeamLLMPermission.llm_credential_id.in_(credential_ids)
        ).delete(synchronize_session=False)
        db.query(UserLLMPermission).filter(
            UserLLMPermission.llm_credential_id.in_(credential_ids)
        ).delete(synchronize_session=False)
        if sa_inspect(db.bind).has_table("rag_answer_runs"):
            for credential_id in credential_ids:
                db.execute(
                    text(
                        "UPDATE rag_answer_runs "
                        "SET generation_credential_id = NULL, "
                        "generation_credential_ref = NULL "
                        "WHERE generation_credential_id = :credential_id"
                    ),
                    {"credential_id": credential_id},
                )
        db.query(LLMRelCredentialModel).filter(
            LLMRelCredentialModel.credential_id.in_(credential_ids)
        ).delete(synchronize_session=False)
        db.query(LLMCredential).filter(
            LLMCredential.id.in_(credential_ids)
        ).delete(synchronize_session=False)

    db.query(WorkflowDeployment).filter(
        or_(
            WorkflowDeployment.id.in_(deployment_ids),
            WorkflowDeployment.app_id.in_(app_ids),
        )
    ).delete(synchronize_session=False)
    db.query(Workflow).filter(Workflow.id.in_(workflow_ids)).delete(
        synchronize_session=False
    )
    db.query(App).filter(App.id.in_(app_ids)).delete(synchronize_session=False)

    db.query(LLMRelCredentialModel).filter(
        LLMRelCredentialModel.credential_id == LEGACY_DEMO_LLM_CREDENTIAL_ID
    ).delete(synchronize_session=False)
    db.query(LLMCredential).filter(
        LLMCredential.id == LEGACY_DEMO_LLM_CREDENTIAL_ID
    ).delete(synchronize_session=False)

    db.query(KnowledgeDocumentIngestionJob).filter(
        KnowledgeDocumentIngestionJob.knowledge_base_id.in_(kb_ids)
    ).delete(synchronize_session=False)
    db.query(KnowledgeIngestionOutbox).filter(
        KnowledgeIngestionOutbox.knowledge_base_id.in_(kb_ids)
    ).delete(synchronize_session=False)
    db.query(KnowledgeCollectionItem).filter(
        or_(
            KnowledgeCollectionItem.collection_id.in_(collection_ids),
            KnowledgeCollectionItem.id.in_(
                list(RETIRED_INTERNAL_DOCUMENT_COLLECTION_ITEM_IDS.values())
            ),
        )
    ).delete(synchronize_session=False)
    db.query(KnowledgeCollection).filter(
        KnowledgeCollection.id.in_(collection_ids)
    ).delete(synchronize_session=False)
    db.query(DocumentChunk).filter(
        or_(
            DocumentChunk.knowledge_base_id.in_(kb_ids),
            DocumentChunk.document_id.in_(
                list(RETIRED_INTERNAL_DOCUMENT_IDS.values())
            ),
        )
    ).delete(synchronize_session=False)
    db.query(Document).filter(
        or_(
            Document.knowledge_base_id.in_(kb_ids),
            Document.id.in_(list(RETIRED_INTERNAL_DOCUMENT_IDS.values())),
        )
    ).delete(synchronize_session=False)
    db.query(KnowledgeBase).filter(KnowledgeBase.id.in_(kb_ids)).delete(
        synchronize_session=False
    )

    db.query(TeamMembership).filter(
        or_(
            TeamMembership.grantee_organization_id == ORG_ID,
            TeamMembership.user_id.in_(user_ids),
            TeamMembership.assigned_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(OrganizationMembership).filter(
        or_(
            OrganizationMembership.organization_id == ORG_ID,
            OrganizationMembership.user_id.in_(user_ids),
            OrganizationMembership.invited_by.in_(user_ids),
        )
    ).delete(synchronize_session=False)
    db.query(Team).filter(Team.id.in_(team_ids)).delete(synchronize_session=False)

    db.commit()
    seed_demo_data(db)
