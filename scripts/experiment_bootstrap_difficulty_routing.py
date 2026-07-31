"""Bootstrap 난이도 자동 모델 라우팅을 실제 배포 API로 검증한다.

실제 provider 호출은 ``--confirm-live``가 있을 때만 수행한다. 보고서에는 합성
질문과 safe routing summary만 남기며 credential, prompt 원문, 답변 원문은 남기지 않는다.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

Difficulty = Literal["economy", "balanced", "advanced"]
SOURCE_APP_ID = "10200000-0000-0000-0000-000000000408"
ORGANIZATION_ID = "10200000-0000-0000-0000-000000000100"
NODE_ID = "llm-answer"
DEFAULT_EMAIL = "admin@nodease.demo"
DEFAULT_PASSWORD_ENV = "NODEASE_DEMO_PASSWORD"
DEFAULT_MODEL_ID = "gpt-4.1"
DEFAULT_FALLBACK_MODEL_ID = "gpt-4.1-mini"
# 이 실험은 private onboarding KB를 같은 조직 사용자 권한으로 조회한다. 공개
# webhook은 execution subject가 없어서 preflight가 의도적으로 차단하므로, 실제
# 호출 경로(`/deployments/{id}/run`)와 같은 인증 내부 배포로 만든다.
AUTHENTICATED_EXPERIMENT_DEPLOYMENT_TYPE = "internal_chatbot"
TASK_DESCRIPTION = (
    "이 노드는 사내 공통·플랫폼·영업·재무 온보딩 문서를 검색해 한국어 질문에 근거 기반으로 "
    "답합니다. 경제형은 메뉴·문서·일정·역할의 위치 또는 이름처럼 한 가지 사실이나 짧은 "
    "경로를 찾는 요청입니다. 균형형은 두 역할 또는 정책을 비교하거나, 계정 설정·교육·권한 "
    "신청처럼 2~4단계 절차를 함께 정리하는 요청입니다. 고성능형은 서로 충돌하는 규정, "
    "보안·개인정보·재무 위험, 긴급 예외 승인, 여러 제약을 함께 판단해야 하는 요청입니다. "
    "모든 응답은 검색 근거가 없으면 추측하지 않고 확인할 정보를 안내해야 합니다."
)
RAG_NODE_DATA_KEYS = (
    "knowledgeBases",
    "knowledgeCollections",
    "topK",
    "scoreThreshold",
    "dedupeRetrievedContext",
    "retrievedContextMaxChars",
    "retrievedContextCompression",
    "answerGroundingCheck",
    "ragFailurePolicy",
    # 구 버전 graph나 외부 importer가 넣은 source metadata 표시 옵션도
    # 실험 재현성 차원에서 보존한다. 현재 runtime은 이 값을 사용하지 않아도 된다.
    "includeSourceMetadata",
)


def _safe_detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    return (
        f"HTTP {response.status_code}: {detail}"
        if isinstance(detail, str) and len(detail) <= 160
        else f"HTTP {response.status_code}"
    )


def _local_http_auth_cookie_header(
    base_url: str,
    cookies: Any,
) -> str | None:
    """로컬 Docker HTTP 경로에서만 Secure 로그인 쿠키를 명시 전달한다.

    Gateway 로그인은 production HTTPS를 전제로 ``auth_token``에 Secure 속성을
    붙인다. 실험 스크립트는 Docker network의 ``http://gateway:8000``을 직접
    호출하므로 requests가 이 쿠키를 자동 전송하지 않는다. 원격 HTTP 주소로
    인증 토큰을 흘리지 않도록 loopback/Docker service 이름에만 이 보완을 둔다.
    """

    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "gateway",
    }:
        return None
    token = cookies.get("auth_token") if hasattr(cookies, "get") else None
    return f"auth_token={token}" if isinstance(token, str) and token else None


class ExperimentClient:
    """현재 난이도 라우팅 실험만을 위한 인증 API client."""

    def __init__(
        self,
        *,
        base_url: str,
        organization_id: str,
        email: str,
        password: str,
        timeout_seconds: int,
    ) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        response = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"데모 계정 로그인 실패: {_safe_detail(response)}")
        self.session.headers.update({"X-Organization-Id": organization_id})
        auth_cookie = _local_http_auth_cookie_header(base_url, self.session.cookies)
        if auth_cookie:
            self.session.headers.update({"Cookie": auth_cookie})

    def request_object(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=body,
            timeout=timeout or self.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"{method} {path} 실패: {_safe_detail(response)}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{method} {path} 응답이 object가 아닙니다.")
        return payload

    def get_json(
        self, path: str, *, timeout: int | None = None
    ) -> dict[str, Any]:
        return self.request_object("GET", path, timeout=timeout)

    def get_json_list(
        self, path: str, *, timeout: int | None = None
    ) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}{path}", timeout=timeout or self.timeout_seconds
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"GET {path} 실패: {_safe_detail(response)}")
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"GET {path} 응답이 list가 아닙니다.")
        return [item for item in payload if isinstance(item, dict)]


def _find_source_filenames(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"filename", "source_filename"} and isinstance(item, str):
                if item.lower().endswith((".pdf", ".md", ".txt")):
                    found.add(Path(item).name)
            else:
                found.update(_find_source_filenames(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_source_filenames(item))
    elif isinstance(value, str):
        for match in re.finditer(
            r"(?<![\w.-])([\w.-]+\.(?:pdf|md|txt))(?![\w.-])",
            value,
            flags=re.IGNORECASE,
        ):
            found.add(Path(match.group(1)).name)
    return found


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _wait_for_run_detail(
    client: ExperimentClient,
    *,
    workflow_id: str,
    run_id: str,
    node_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"
    while time.monotonic() < deadline:
        run = client.get_json(
            f"/api/v1/workflows/{workflow_id}/runs/{run_id}", timeout=30
        )
        node_runs = run.get("node_runs")
        node_runs = node_runs if isinstance(node_runs, list) else []
        node_run = next(
            (
                item
                for item in node_runs
                if isinstance(item, dict) and item.get("node_id") == node_id
            ),
            None,
        )
        run_status = str(run.get("status") or "").lower()
        node_status = str((node_run or {}).get("status") or "").lower()
        last_status = f"run={run_status or 'unknown'}, node={node_status or 'missing'}"
        terminal = {"success", "failed", "cancelled", "canceled", "skipped"}
        if node_run is not None and run_status in terminal and node_status in terminal:
            return run
        if run_status in {"failed", "cancelled", "canceled"} and node_run is None:
            return run
        time.sleep(max(0.0, poll_interval_seconds))
    raise TimeoutError(f"run detail 최종 상태 대기 시간 초과: {last_status}")


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    expected_difficulty: Difficulty
    question: str
    rationale: str
    expected_rag_source: str | None


@dataclass
class RoutingObservation:
    sequence: int
    case: ExperimentCase
    run_id: str
    run_status: str
    node_status: str
    selected_model: str | None
    predicted_difficulty: str | None
    confidence: float | None
    reason_code: str | None
    classification_status: str | None
    fallback_used: bool
    cost: float | None
    total_tokens: int | None
    duration_seconds: float | None
    rag_document_count: int
    error_code: str | None = None


@dataclass(frozen=True)
class RoutingAssessment:
    passed: bool
    successful_runs: int
    total_runs: int
    difficulty_accuracy: float
    distinct_model_count: int
    classifier_fallback_rate: float
    rag_hit_rate: float
    reasons: tuple[str, ...]


ECONOMY_QUESTIONS = (
    ("신입사원이 첫날 SSO 비밀번호를 설정하는 메뉴 위치만 알려 주세요.", "단일 문서의 한 절차를 바로 찾는 요청", "company_common_onboarding.pdf"),
    ("휴가 신청 화면으로 이동하는 경로를 짧게 안내해 주세요.", "단일 사용 경로 확인", "company_common_onboarding.pdf"),
    ("플랫폼팀 Git 저장소 접근 신청은 어디에서 시작하나요?", "한 가지 신청 시작점 확인", "platform_team_onboarding_v4.pdf"),
    ("영업팀 CRM 첫 로그인 전에 완료할 교육 이름만 알려 주세요.", "한 문서의 단일 사실 조회", "sales_team_onboarding_v2.pdf"),
    ("재무팀 결산 체크리스트가 있는 문서 이름을 알려 주세요.", "단순 문서 위치 조회", "finance_team_onboarding_v3.pdf"),
    ("휴가 신청 메뉴는 어디에 있나요?", "짧은 메뉴 위치 확인", "company_common_onboarding.pdf"),
    ("신입 개발자의 첫 주 회고는 어느 요일에 하나요?", "일정 한 항목 조회", "platform_team_onboarding_v4.pdf"),
    ("CRM 고객 메모를 남기는 기본 메뉴 경로만 알려 주세요.", "단일 기능 경로 확인", "sales_team_onboarding_v2.pdf"),
    ("회계 시스템 조회 권한 신청서에 적을 필수 항목은 무엇인가요?", "짧은 목록 조회", "finance_team_onboarding_v3.pdf"),
    ("온보딩 완료 여부는 어디에서 확인하나요?", "단일 상태 확인 경로", "company_common_onboarding.pdf"),
    ("개발환경 샘플 서비스 실행 명령을 확인할 문서는 무엇인가요?", "문서 위치 확인", "platform_team_onboarding_v4.pdf"),
    ("영업 신입의 연습 견적 제출 위치를 알려 주세요.", "단일 제출 위치 조회", "sales_team_onboarding_v2.pdf"),
    ("지급 요청 증빙을 올리는 메뉴는 어디인가요?", "단일 메뉴 조회", "finance_team_onboarding_v3.pdf"),
    ("피싱 의심 메일 신고 채널 이름만 알려 주세요.", "짧은 신고 경로 조회", "company_common_onboarding.pdf"),
    ("VPN 신청 전 확인할 교육 하나를 알려 주세요.", "단일 전제 조건 조회", "platform_team_onboarding_v4.pdf"),
    ("CRM 최소 권한의 기본 역할 이름을 알려 주세요.", "단일 역할 조회", "sales_team_onboarding_v2.pdf"),
    ("법인카드 영수증 제출 마감일을 어디서 확인하나요?", "단일 일정 조회", "finance_team_onboarding_v3.pdf"),
)

BALANCED_QUESTIONS = (
    ("신입사원이 SSO와 MFA를 설정한 뒤 팀 채널에 참여하기까지 순서를 정리해 주세요.", "여러 절차를 순서대로 종합", "company_common_onboarding.pdf"),
    ("플랫폼팀 신입이 Git 권한을 받은 뒤 로컬 개발환경과 샘플 테스트를 완료하는 과정을 설명해 주세요.", "한 문서의 여러 단계를 종합", "platform_team_onboarding_v4.pdf"),
    ("영업팀 CRM 최소 권한 신청과 첫 견적 검토 절차를 함께 알려 주세요.", "두 관련 절차를 결합", "sales_team_onboarding_v2.pdf"),
    ("재무팀 전표 작성자와 승인자의 역할을 비교하고 지급 전 점검 순서를 알려 주세요.", "역할 비교와 절차 종합", "finance_team_onboarding_v3.pdf"),
    ("원격 근무를 시작할 때 장비, SSO, 보안 교육을 어떤 순서로 확인해야 하나요?", "여러 공통 규칙 종합", "company_common_onboarding.pdf"),
    ("운영 로그 읽기 권한과 배포 권한의 차이를 온보딩 절차와 함께 설명해 주세요.", "두 권한 계약 비교", "platform_team_onboarding_v4.pdf"),
    ("고객 담당자가 바뀌었을 때 CRM 접근을 조정하고 인수인계 기록을 남기는 순서를 알려 주세요.", "권한과 기록 절차 결합", "sales_team_onboarding_v2.pdf"),
    ("월말 결산에서 증빙 금액이 다를 때 재검토와 승인 기록을 어떻게 남기는지 정리해 주세요.", "예외가 있는 다단계 절차", "finance_team_onboarding_v3.pdf"),
    ("첫 주 온보딩 완료 기준을 계정, 교육, 장비, 팀 리뷰로 나눠 설명해 주세요.", "여러 기준 구조화", "company_common_onboarding.pdf"),
    ("플랫폼팀이 비운영 환경에서 배포 실습을 하고 운영 조회 권한을 신청하기까지 과정을 요약해 주세요.", "여러 단계의 기술 절차", "platform_team_onboarding_v4.pdf"),
    ("영업 신입이 고객 데이터를 다루면서 견적 예외 승인을 받는 안전한 절차를 설명해 주세요.", "업무와 보안 규칙 종합", "sales_team_onboarding_v2.pdf"),
    ("재무팀 조회, 전표 작성, 검토, 승인 권한을 역할별 표처럼 구분해 주세요.", "다수 역할 비교", "finance_team_onboarding_v3.pdf"),
    ("MFA 기기 교체 시 본인 확인부터 재등록과 복구 수단 점검까지 순서를 알려 주세요.", "다단계 계정 복구", "company_common_onboarding.pdf"),
    ("Git 접근이 과도하게 열렸을 때 증거를 남기고 권한을 조정하는 절차를 설명해 주세요.", "상태 확인과 조치 결합", "platform_team_onboarding_v4.pdf"),
    ("CRM 활동 기록에 남길 수 있는 정보와 남기면 안 되는 정보를 비교해 주세요.", "허용·금지 기준 비교", "sales_team_onboarding_v2.pdf"),
    ("지급 승인 한도를 넘는 요청에서 추가 결재와 증빙 검토 순서를 정리해 주세요.", "조건부 다단계 절차", "finance_team_onboarding_v3.pdf"),
    ("의심스러운 링크를 열었을 때와 열지 않았을 때의 신고 절차 차이를 설명해 주세요.", "두 상황의 절차 비교", "company_common_onboarding.pdf"),
)

ADVANCED_QUESTIONS = (
    ("공통 온보딩 문서는 개인 이메일을 복구 수단으로 금지하지만 외부 협력사 정책은 개인 메일 확인을 요구합니다. 두 규정이 충돌할 때 어떤 원칙과 승인 절차를 적용해야 하나요?", "상충 규정과 예외 승인 판단", "company_common_onboarding.pdf"),
    ("장애 대응 때문에 신입 개발자에게 운영 변경 권한을 즉시 줘야 한다는 요청이 왔습니다. 최소 권한, 시간 제한, 감사 기록을 모두 고려해 판단해 주세요.", "고위험 예외와 여러 안전 조건", "platform_team_onboarding_v4.pdf"),
    ("대형 고객이 계약 전 원본 고객 데이터를 CRM에 올려 달라고 요구했습니다. 영업 속도와 개인정보 원칙이 충돌할 때 가능한 대안을 근거와 함께 제시해 주세요.", "사업 요구와 개인정보 위험 종합", "sales_team_onboarding_v2.pdf"),
    ("결산 마감 직전 작성자와 승인자가 같은 전표를 발견했고 지급은 이미 예약됐습니다. 업무 분리, 지급 중단, 감사 기록 순서를 판단해 주세요.", "재무 위험과 시간 제약 복합 판단", "finance_team_onboarding_v3.pdf"),
    ("퇴사 예정 관리자의 계정에서 고객 파일 외부 공유와 권한 변경이 동시에 발견됐습니다. 계정 차단, 증거 보존, 개인정보 신고 순서를 근거 중심으로 설명해 주세요.", "보안·개인정보 사고 복합 판단", "company_common_onboarding.pdf"),
    ("운영 장애를 복구하려면 공유 관리자 계정을 써야 한다는 주장이 있지만 문서는 개인 계정만 허용합니다. 서비스 복구와 감사 가능성을 함께 만족하는 방안을 제시해 주세요.", "운영 연속성과 통제 충돌", "platform_team_onboarding_v4.pdf"),
    ("고객이 비표준 할인과 데이터 보관 예외를 동시에 요구합니다. 영업 승인, 법무 검토, 고객 데이터 취급 순서를 위험도별로 판단해 주세요.", "여러 부서 계약과 위험 판단", "sales_team_onboarding_v2.pdf"),
    ("해외 공급사의 계좌 변경 요청이 결산 마감일에 들어왔고 환율 증빙도 누락됐습니다. 사기 위험과 마감 일정을 함께 고려해 지급 여부를 판단해 주세요.", "사기 위험과 결산 제약 복합 판단", "finance_team_onboarding_v3.pdf"),
    ("온보딩 문서에는 근거가 없는 긴급 권한 요청입니다. 추측하지 말고 확인해야 할 정보, 임시 보호 조치, 승인 경로를 구분해 답해 주세요.", "근거 없음 안전 응답과 고위험 요청", None),
    ("개인정보가 포함된 파일이 외부 메일과 공개 링크로 동시에 유출된 정황이 있습니다. 서로 다른 대응 문서를 종합해 우선순위와 통지 조건을 제시해 주세요.", "다중 근거 종합과 사고 대응", "company_common_onboarding.pdf"),
    ("플랫폼 신입의 운영 권한 요청이 승인됐지만 보안 교육 기록은 누락됐고 장애는 진행 중입니다. 배포를 허용할지 조건부 대안을 포함해 판단해 주세요.", "불완전 증거와 긴급 운영 판단", "platform_team_onboarding_v4.pdf"),
    ("고객 계약은 즉시 견적 발송을 요구하지만 할인 예외 승인과 개인정보 검토가 완료되지 않았습니다. 가능한 행동과 금지 행동을 근거별로 나눠 주세요.", "계약·승인·개인정보 복합 판단", "sales_team_onboarding_v2.pdf"),
    ("같은 세금계산서로 중복 지급 의심이 있고 승인자 계정도 비정상 로그인했습니다. 지급 통제와 보안 사고 대응을 함께 설계해 주세요.", "재무·보안 교차 위험", "finance_team_onboarding_v3.pdf"),
    ("공통 문서와 팀 문서의 권한 회수 시점이 다릅니다. 퇴사자 접근을 즉시 막으면서 업무 인수인계를 보존할 수 있는 결정을 근거와 함께 설명해 주세요.", "여러 문서의 상충 시점 조정", "company_common_onboarding.pdf"),
    ("운영 로그에 API 키가 노출됐고 배포 파이프라인도 실패 중입니다. 키 폐기, 서비스 복구, 감사 증거 보존의 순서를 판단해 주세요.", "보안과 복구의 복합 우선순위", "platform_team_onboarding_v4.pdf"),
    ("고객 데이터 삭제 요청과 법정 보존 의무가 충돌합니다. 영업 담당자가 고객에게 안내할 내용과 내부 승인 절차를 분리해 작성해 주세요.", "법적 보존과 고객 대응 충돌", "sales_team_onboarding_v2.pdf"),
    ("지급 승인 이후 계약 위조 정황과 계좌 변경이 함께 발견됐습니다. 지급 보류, 독립 확인, 감사·법무 보고 기준을 단계적으로 판단해 주세요.", "고위험 재무 사기 복합 판단", "finance_team_onboarding_v3.pdf"),
)


def build_cases(*, count: int, shuffle_seed: int) -> list[ExperimentCase]:
    if not 1 <= count <= 50:
        raise ValueError("실험 입력 수는 1~50건이어야 합니다.")
    pools = {
        "economy": ECONOMY_QUESTIONS,
        "balanced": BALANCED_QUESTIONS,
        "advanced": ADVANCED_QUESTIONS,
    }
    selected: list[ExperimentCase] = []
    for index in range(count):
        tier: Difficulty = ("economy", "balanced", "advanced")[index % 3]  # type: ignore[assignment]
        row = pools[tier][index // 3]
        selected.append(
            ExperimentCase(
                case_id=f"{tier}-{index // 3 + 1:02d}",
                expected_difficulty=tier,
                question=row[0],
                rationale=row[1],
                expected_rag_source=row[2],
            )
        )
    random.Random(shuffle_seed).shuffle(selected)
    return selected


def evaluate_routing_attempt(
    observations: Iterable[RoutingObservation],
) -> RoutingAssessment:
    rows = list(observations)
    total = len(rows)
    successful = sum(row.run_status.lower() == "success" for row in rows)
    classified = [row for row in rows if row.predicted_difficulty]
    correct = sum(
        row.predicted_difficulty == row.case.expected_difficulty for row in classified
    )
    accuracy = correct / total if total else 0.0
    model_count = len({row.selected_model for row in rows if row.selected_model})
    # trace writer가 일부 Planner rule의 decision_factors를 생략할 수 있다. 반면
    # reason_code는 routing 결정 계약으로 항상 남으므로 둘을 함께 읽어야 실제
    # 난이도 모델 선택을 기본 모델 fallback으로 잘못 집계하지 않는다.
    routed_reason_codes = {
        f"bootstrap_difficulty_{difficulty}"
        for difficulty in ("economy", "balanced", "advanced")
    } | {
        f"bootstrap_planner_rule_{difficulty}"
        for difficulty in ("economy", "balanced", "advanced")
    }
    fallback_count = sum(
        row.classification_status
        not in {"matched", "planner_rule", "validated_planner_rule"}
        and row.reason_code not in routed_reason_codes
        for row in rows
    )
    fallback_rate = fallback_count / total if total else 1.0
    rag_expected = [row for row in rows if row.case.expected_rag_source]
    rag_hits = sum(row.rag_document_count > 0 for row in rag_expected)
    rag_hit_rate = rag_hits / len(rag_expected) if rag_expected else 1.0
    reasons: list[str] = []
    if successful / total < 0.9 if total else True:
        reasons.append("성공 실행 비율이 90% 미만입니다.")
    if model_count < 2:
        reasons.append("서로 다른 모델이 2개 이상 선택되지 않았습니다.")
    if accuracy < 0.7:
        reasons.append("기대 난이도 일치율이 70% 미만입니다.")
    if fallback_rate > 0.2:
        reasons.append("정책 규칙 또는 분류기 매칭 실패로 기본 모델을 사용한 비율이 20%를 넘습니다.")
    if rag_hit_rate < 0.7:
        reasons.append("RAG 근거가 필요한 입력의 검색 성공률이 70% 미만입니다.")
    return RoutingAssessment(
        passed=not reasons,
        successful_runs=successful,
        total_runs=total,
        difficulty_accuracy=accuracy,
        distinct_model_count=model_count,
        classifier_fallback_rate=fallback_rate,
        rag_hit_rate=rag_hit_rate,
        reasons=tuple(reasons),
    )


def _llm_node(graph: dict[str, Any]) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("id") == NODE_ID:
            return node
    raise RuntimeError(f"source workflow에 {NODE_ID} 노드가 없습니다.")


def _configure_graph(
    graph: dict[str, Any], *, default_model_id: str, fallback_model_id: str
) -> dict[str, Any]:
    configured = copy.deepcopy(graph)
    data = _llm_node(configured).setdefault("data", {})
    data.update(
        {
            "model_id": default_model_id,
            "fallback_model_id": fallback_model_id,
            "auto_model_routing": True,
            "model_routing_strategy": "bootstrap_mdeberta_difficulty_v1",
            "model_routing_task_description": TASK_DESCRIPTION,
        }
    )
    data.pop("model_routing_bootstrap_id", None)
    data.pop("model_routing_bootstrap_fingerprint", None)
    return configured


def _copy_llm_rag_configuration(
    source_graph: dict[str, Any], target_graph: dict[str, Any]
) -> dict[str, Any]:
    """앱 복제 과정에서 제거된 RAG 계약을 실험용 graph에만 복원한다.

    앱 복제 API가 private reference를 보수적으로 비우는 것은 제품 경계로 유지한다.
    이 실험은 같은 관리자 권한으로 원본 workflow를 읽어 동일한 RAG 실행 조건을
    재현해야 하므로, 저장 직전에 LLM node의 RAG 관련 설정만 명시적으로 복사한다.
    모델·프롬프트·라우팅 정책은 복제본이 새로 설정한 값을 그대로 사용한다.
    """
    restored = copy.deepcopy(target_graph)
    source_data = _llm_node(source_graph).get("data")
    target_data = _llm_node(restored).setdefault("data", {})
    if not isinstance(source_data, dict) or not isinstance(target_data, dict):
        raise RuntimeError("실험용 LLM 노드의 RAG 설정 형식이 올바르지 않습니다.")
    for key in RAG_NODE_DATA_KEYS:
        if key in source_data:
            target_data[key] = copy.deepcopy(source_data[key])
    return restored


def _knowledge_reference_id(reference: Any) -> str | None:
    if isinstance(reference, str):
        return reference.strip() or None
    if not isinstance(reference, dict):
        return None
    for key in ("id", "knowledge_base_id", "knowledgeBaseId"):
        value = reference.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _configured_rag_knowledge_base_ids(
    client: ExperimentClient, graph: dict[str, Any]
) -> set[str]:
    """LLM node가 직접 또는 collection으로 참조한 KB를 API 기준으로 푼다."""
    data = _llm_node(graph).get("data")
    if not isinstance(data, dict):
        return set()

    knowledge_base_ids = {
        reference_id
        for reference_id in (
            _knowledge_reference_id(reference)
            for reference in data.get("knowledgeBases") or []
        )
        if reference_id is not None
    }
    collection_ids = {
        reference_id
        for reference_id in (
            _knowledge_reference_id(reference)
            for reference in data.get("knowledgeCollections") or []
        )
        if reference_id is not None
    }
    for collection_id in collection_ids:
        response = client.get_json(f"/api/v1/knowledge/collections/{collection_id}/items")
        items = response.get("items") if isinstance(response, dict) else []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            knowledge_base_id = item.get("knowledge_base_id")
            if knowledge_base_id is not None and str(knowledge_base_id).strip():
                knowledge_base_ids.add(str(knowledge_base_id).strip())
    return knowledge_base_ids


def assert_rag_embedding_models_available(
    client: ExperimentClient, graph: dict[str, Any]
) -> set[str]:
    """실험 시작 전에 RAG query embedding 권한을 API 기준으로 확인한다.

    RAG는 검색 벡터 생성에 실패하면 안전 응답으로 종료되어 LLM과 모델 라우터가
    실행되지 않는다. 이 검사는 그런 경우에 실제 provider 비용을 쓰지 않고 실험을
    중단하기 위한 준비 단계다.
    """
    knowledge_base_ids = _configured_rag_knowledge_base_ids(client, graph)
    if not knowledge_base_ids:
        return set()

    required_models: set[str] = set()
    for knowledge_base_id in knowledge_base_ids:
        knowledge_base = client.get_json(f"/api/v1/knowledge/{knowledge_base_id}")
        model_id = (
            knowledge_base.get("embedding_model")
            if isinstance(knowledge_base, dict)
            else None
        )
        if isinstance(model_id, str) and model_id.strip():
            required_models.add(model_id.strip())
        else:
            raise RuntimeError(
                "RAG 실험에 필요한 지식 베이스의 embedding model을 확인하지 못했습니다."
            )

    response = client.get_json_list("/api/v1/llm/my-embedding-models")
    available_models = {
        str(item.get("model_id_for_api_call")).strip()
        for item in response
        if item.get("model_id_for_api_call") is not None
        and str(item.get("model_id_for_api_call")).strip()
    }
    missing_models = sorted(required_models - available_models)
    if missing_models:
        raise RuntimeError(
            "RAG 검색에 필요한 embedding model 권한이 없습니다: "
            + ", ".join(missing_models)
        )
    return required_models


def _deployment_create_body(
    *, app_id: str, name: str, graph_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Private KB 실험에 필요한 인증 실행 surface의 배포 요청을 만든다."""
    return {
        "app_id": app_id,
        "type": AUTHENTICATED_EXPERIMENT_DEPLOYMENT_TYPE,
        "description": name,
        "config": {"experiment": "bootstrap-difficulty-routing"},
        "is_active": True,
        "graph_snapshot": graph_snapshot,
    }


def wait_for_bootstrap_classifier(
    client: ExperimentClient,
    *,
    workflow_id: str,
    node_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """분류 artifact가 준비되기 전에는 실험용 deployment를 만들지 않는다.

    bootstrap API는 Planner 결과를 먼저 저장한 뒤 CPU 분류기를 비동기로 만든다.
    이 대기 단계가 없으면 첫 배포 실행은 빈 artifact를 읽어 기본 모델로 떨어질 수
    있으므로, 실제 첫 실행 라우팅을 검증하는 실험에서는 ready 상태를 보장한다.
    """
    deadline = clock() + max(1, timeout_seconds)
    path = f"/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap"
    last_status = "unknown"
    while True:
        bootstrap = client.get_json(path)
        if not isinstance(bootstrap, dict):
            raise RuntimeError("bootstrap 조회 응답이 object가 아닙니다.")
        summary = bootstrap.get("generation_summary")
        summary = summary if isinstance(summary, dict) else {}
        last_status = str(summary.get("classifier_status") or "pending")
        if last_status == "ready":
            return bootstrap
        if last_status == "failed":
            raise RuntimeError("초기 난이도 분류기 생성이 실패했습니다.")
        if clock() >= deadline:
            raise RuntimeError(
                "초기 난이도 분류기 준비 시간이 초과되었습니다: "
                f"classifier_status={last_status}"
            )
        sleep(max(0.0, poll_interval_seconds))


def _bootstrap_request_body(
    *,
    draft_metadata: dict[str, Any],
    default_model_id: str,
    fallback_model_id: str,
    initial_budget_usd: float,
) -> dict[str, Any]:
    """직전 draft write의 canonical CAS metadata로 bootstrap 요청을 만든다."""

    graph_hash = draft_metadata.get("graph_hash")
    updated_at = draft_metadata.get("updated_at")
    if (
        not isinstance(graph_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", graph_hash) is None
        or not isinstance(updated_at, str)
        or not updated_at.strip()
    ):
        raise RuntimeError("draft 저장 응답에 canonical CAS metadata가 없습니다.")
    try:
        parsed_updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(
            "draft 저장 응답의 canonical updated_at이 올바르지 않습니다."
        ) from exc
    if parsed_updated_at.tzinfo is None:
        raise RuntimeError("draft 저장 응답의 canonical updated_at에 timezone이 없습니다.")

    return {
        "task_description": TASK_DESCRIPTION,
        "default_model_id": default_model_id,
        "fallback_model_id": fallback_model_id,
        "initial_budget_usd": initial_budget_usd,
        "expected_graph_hash": graph_hash,
        "expected_updated_at": updated_at,
    }


def create_automatic_target(
    client: ExperimentClient,
    *,
    source_app_id: str,
    name: str,
    default_model_id: str,
    fallback_model_id: str,
    initial_budget_usd: float,
) -> tuple[dict[str, str], dict[str, Any]]:
    source_app = client.get_json(f"/api/v1/apps/{source_app_id}")
    source_workflow_id = str(source_app.get("workflow_id") or "")
    if not source_workflow_id:
        raise RuntimeError("원본 앱에서 workflow_id를 찾지 못했습니다.")
    source_draft = client.get_json(f"/api/v1/workflows/{source_workflow_id}/draft")
    assert_rag_embedding_models_available(client, source_draft)
    app = client.request_object("POST", f"/api/v1/apps/{source_app_id}/clone")
    app_id = str(app["id"])
    workflow_id = str(app["workflow_id"])
    client.request_object("PATCH", f"/api/v1/apps/{app_id}", body={"name": name})
    draft = client.get_json(f"/api/v1/workflows/{workflow_id}/draft")
    configured = _configure_graph(
        draft,
        default_model_id=default_model_id,
        fallback_model_id=fallback_model_id,
    )
    configured = _copy_llm_rag_configuration(source_draft, configured)
    saved_draft = client.request_object(
        "POST", f"/api/v1/workflows/{workflow_id}/draft", body=configured
    )
    client.request_object(
        "POST",
        f"/api/v1/workflows/{workflow_id}/llm-nodes/{NODE_ID}/model-routing/bootstrap",
        body=_bootstrap_request_body(
            draft_metadata=saved_draft,
            default_model_id=default_model_id,
            fallback_model_id=fallback_model_id,
            initial_budget_usd=initial_budget_usd,
        ),
        timeout=1200,
    )
    bootstrap = wait_for_bootstrap_classifier(
        client,
        workflow_id=workflow_id,
        node_id=NODE_ID,
        timeout_seconds=1200,
    )
    final_graph = client.get_json(f"/api/v1/workflows/{workflow_id}/draft")
    deployment = client.request_object(
        "POST",
        "/api/v1/deployments",
        body=_deployment_create_body(
            app_id=app_id,
            name=name,
            graph_snapshot=final_graph,
        ),
        timeout=180,
    )
    return (
        {
            "app_id": app_id,
            "workflow_id": workflow_id,
            "deployment_id": str(deployment["id"]),
        },
        bootstrap,
    )


def _run_case(
    client: ExperimentClient,
    *,
    target: dict[str, str],
    case: ExperimentCase,
    sequence: int,
    timeout_seconds: int,
) -> RoutingObservation:
    response = client.session.post(
        f"{client.base_url}/api/v1/deployments/{target['deployment_id']}/run",
        json={"inputs": {"question": case.question}},
        headers={"X-Correlation-Id": f"bootstrap-routing-{case.case_id}-{sequence}"},
        timeout=max(600, timeout_seconds),
    )
    if response.status_code != 200:
        return RoutingObservation(
            sequence, case, "", "failed", "unknown", None, None, None, None,
            None, False, None, None, None, 0, f"deployment_http_{response.status_code}"
        )
    run_id = str(response.json().get("run_id") or "")
    run = _wait_for_run_detail(
        client,
        workflow_id=target["workflow_id"],
        run_id=run_id,
        node_id=NODE_ID,
        timeout_seconds=timeout_seconds,
    )
    node_runs = run.get("node_runs") if isinstance(run.get("node_runs"), list) else []
    node_run = next(
        (item for item in node_runs if isinstance(item, dict) and item.get("node_id") == NODE_ID),
        {},
    )
    outputs = node_run.get("outputs") if isinstance(node_run.get("outputs"), dict) else {}
    trace = node_run.get("trace_metadata") if isinstance(node_run.get("trace_metadata"), dict) else {}
    llm = trace.get("llm") if isinstance(trace.get("llm"), dict) else {}
    routing = llm.get("model_routing") if isinstance(llm.get("model_routing"), dict) else llm
    factors = routing.get("decision_factors") if isinstance(routing.get("decision_factors"), dict) else {}
    rag = trace.get("rag") if isinstance(trace.get("rag"), dict) else {}
    source_names = _find_source_filenames({"outputs": outputs, "trace": trace})
    rag_count = int(rag.get("retrieved_chunk_count") or len(source_names) or 0)
    return RoutingObservation(
        sequence=sequence,
        case=case,
        run_id=str(run.get("id") or run_id),
        run_status=str(run.get("status") or ""),
        node_status=str(node_run.get("status") or ""),
        selected_model=str(routing.get("selected_model") or llm.get("model") or outputs.get("model") or "") or None,
        predicted_difficulty=str(factors.get("difficulty") or "") or None,
        confidence=_number(factors.get("confidence")),
        reason_code=str(routing.get("reason_code") or "") or None,
        classification_status=str(factors.get("classification_status") or "") or None,
        fallback_used=bool(routing.get("fallback_used")),
        cost=_number(llm.get("total_cost") or outputs.get("cost")),
        total_tokens=_integer(llm.get("total_tokens") or (outputs.get("usage") or {}).get("total_tokens")),
        duration_seconds=_number(node_run.get("duration")),
        rag_document_count=rag_count,
    )


def build_report(
    *,
    observations: list[RoutingObservation],
    assessment: RoutingAssessment,
    target: dict[str, str],
    bootstrap: dict[str, Any],
    attempt: int,
    started_at: str,
    finished_at: str,
) -> str:
    models = Counter(row.selected_model or "확인 불가" for row in observations)
    difficulties = Counter(row.predicted_difficulty or "기본 모델" for row in observations)
    status = "통과" if assessment.passed else "실패"
    lines = [
        f"# Bootstrap 난이도 라우팅 실제 실행 보고서 - {attempt}차",
        "",
        "## 한눈에 보는 결과",
        "",
        f"- 판정: **{status}**",
        f"- 실행 성공: **{assessment.successful_runs}/{assessment.total_runs}건**",
        f"- 기대 난이도 일치율: **{assessment.difficulty_accuracy * 100:.1f}%**",
        f"- 사용 모델 수: **{assessment.distinct_model_count}개** `{dict(models)}`",
        f"- 기본 모델 fallback 비율: **{assessment.classifier_fallback_rate * 100:.1f}%**",
        f"- RAG 검색 확인 비율: **{assessment.rag_hit_rate * 100:.1f}%**",
        f"- 예측 난이도 분포: `{dict(difficulties)}`",
        "",
        "이 실험은 자동 라우팅을 켠 새 RAG workflow가 첫 배포부터 서로 다른 난이도의 요청을 "
        "서로 다른 모델로 보내는지 확인한다. 실행마다 Planner/Judge를 호출하지 않고, 초안에서 한 번 "
        "만든 mDeBERTa prototype 분류기를 우선 적용하며, 확신이 낮을 때만 Planner 규칙을 보조로 사용한다.",
        "",
        "## 통과 기준",
        "",
        "- 성공 실행 90% 이상",
        "- 서로 다른 모델 2개 이상",
        "- 기대 난이도 일치율 70% 이상",
        "- 정책 규칙 또는 분류기 매칭 실패에 따른 기본 모델 fallback 20% 이하",
        "- RAG 근거가 필요한 입력의 검색 확인 70% 이상",
        "",
    ]
    if assessment.reasons:
        lines.extend(["## 실패 이유", ""])
        lines.extend(f"- {reason}" for reason in assessment.reasons)
        lines.append("")
    lines.extend(
        [
            "## 실험 환경",
            "",
            f"- 시작/종료: `{started_at}` / `{finished_at}`",
            f"- workflow: `{target['workflow_id']}`",
            f"- deployment: `{target['deployment_id']}`",
            f"- bootstrap source: `{bootstrap.get('source')}`",
            f"- bootstrap sample 수: `{len(bootstrap.get('samples') or [])}`",
            f"- Planner 비용: `${float(bootstrap.get('planner_cost_usd') or 0):.6f}`",
            "- 배포 surface: private KB를 인증 실행 주체로 조회하는 internal chatbot",
            "- RAG 소스: 사내 공통·플랫폼·영업·재무 온보딩 문서",
            "- credential과 답변 원문은 보고서에 기록하지 않음",
            "",
            "## 실행 상세",
            "",
            "| # | 입력 ID | 기대 | 예측 | confidence | 모델 | 근거 | 상태 | 비용 | 시간 | RAG |",
            "| ---: | --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in observations:
        lines.append(
            f"| {row.sequence} | {row.case.case_id} | {row.case.expected_difficulty} | "
            f"{row.predicted_difficulty or '-'} | "
            f"{row.confidence if row.confidence is not None else 0:.3f} | "
            f"{row.selected_model or '-'} | {row.reason_code or row.error_code or '-'} | "
            f"{row.run_status}/{row.node_status} | "
            f"${row.cost if row.cost is not None else 0:.6f} | "
            f"{row.duration_seconds if row.duration_seconds is not None else 0:.3f}s | "
            f"{row.rag_document_count} |"
        )
    lines.extend(["", "## 데이터셋", ""])
    for row in observations:
        lines.extend(
            [
                f"### {row.case.case_id} - {row.case.expected_difficulty}",
                "",
                f"- 질문: {row.case.question}",
                f"- 기대 근거: {row.case.rationale}",
                f"- 기대 문서: {row.case.expected_rag_source or '근거 없음 안전 응답'}",
                "",
            ]
        )
    return "\n".join(lines)


def run_routing_attempt(args: argparse.Namespace) -> tuple[Path, Path, RoutingAssessment]:
    if not args.confirm_live:
        raise RuntimeError("실제 provider 호출은 --confirm-live를 명시해야 합니다.")
    password = os.getenv(args.password_env)
    if not password:
        raise RuntimeError(f"{args.password_env} 환경변수가 필요합니다.")
    client = ExperimentClient(
        base_url=args.base_url,
        organization_id=args.organization_id,
        email=args.email,
        password=password,
        timeout_seconds=args.timeout_seconds,
    )
    started_at = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir / f"attempt-{args.attempt}-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    target, bootstrap = create_automatic_target(
        client,
        source_app_id=args.source_app_id,
        name=f"Bootstrap 난이도 라우팅 E2E {args.attempt}차 {stamp}",
        default_model_id=args.default_model_id,
        fallback_model_id=args.fallback_model_id,
        initial_budget_usd=args.initial_budget_usd,
    )
    observations: list[RoutingObservation] = []
    for sequence, case in enumerate(
        build_cases(count=args.routing_case_count, shuffle_seed=args.shuffle_seed), start=1
    ):
        observation = _run_case(
            client,
            target=target,
            case=case,
            sequence=sequence,
            timeout_seconds=args.timeout_seconds,
        )
        observations.append(observation)
        print(
            f"[{sequence:02d}/{args.routing_case_count}] {case.case_id} "
            f"expected={case.expected_difficulty} predicted={observation.predicted_difficulty or '-'} "
            f"model={observation.selected_model or '-'} confidence={observation.confidence or 0:.3f}",
            flush=True,
        )
    assessment = evaluate_routing_attempt(observations)
    finished_at = datetime.now(timezone.utc).isoformat()
    state_path = output_dir / "state.json"
    report_path = output_dir / "report.md"
    state_path.write_text(
        json.dumps(
            {
                "target": target,
                "bootstrap": bootstrap,
                "assessment": asdict(assessment),
                "observations": [asdict(row) for row in observations],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        build_report(
            observations=observations,
            assessment=assessment,
            target=target,
            bootstrap=bootstrap,
            attempt=args.attempt,
            started_at=started_at,
            finished_at=finished_at,
        ),
        encoding="utf-8",
    )
    return state_path, report_path, assessment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--source-app-id", default=SOURCE_APP_ID)
    parser.add_argument("--organization-id", default=ORGANIZATION_ID)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)
    parser.add_argument("--default-model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--fallback-model-id", default=DEFAULT_FALLBACK_MODEL_ID)
    parser.add_argument("--initial-budget-usd", type=float, default=1.0)
    parser.add_argument("--routing-case-count", type=int, default=20)
    parser.add_argument("--shuffle-seed", type=int, default=451)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "model-routing" / "bootstrap-difficulty",
    )
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    try:
        state_path, report_path, assessment = run_routing_attempt(args)
        print(f"state={state_path}")
        print(f"report={report_path}")
        print(f"passed={assessment.passed}")
        return 0 if assessment.passed else 2
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
