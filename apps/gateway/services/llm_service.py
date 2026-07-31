import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from apps.gateway.application.agent_builder.model_recommendation_policy import (
    SUPPORTED_PROVIDER_ORDER,
    ModelRecommendationCandidate,
    sort_model_candidates,
)
from apps.gateway.services.organization_context import ensure_user_default_organization
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMProvider,
    LLMRelCredentialModel,
    LLMUsageLog,
)
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.schemas.llm import (
    LLMCredentialCreate,
    LLMCredentialModelOptionResponse,
    LLMCredentialOptionResponse,
    LLMCredentialResponse,
    LLMIntentModelProviderResponse,
    LLMModelResponse,
    LLMProviderResponse,
)
from apps.shared.services.llm_client import get_llm_client
from apps.shared.services.egress_guard import safe_http_request
from apps.shared.services.llm_credential_config import (
    LLMCredentialConfigError,
    load_llm_credential_config,
    materialize_llm_client_credentials,
    protect_llm_credential_config,
)
from apps.shared.services.llm_model_pricing import (
    calculate_text_token_cost,
    calculate_text_token_cost_from_rates,
    extract_cached_input_tokens,
    get_model_pricing,
    known_model_prices,
    normalize_model_pricing_id,
)
from apps.shared.services.llm_usage_context import resolve_llm_usage_context
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.outbound_operation_policy import LLM_MODEL_DISCOVERY
from apps.shared.services.permissions import (
    get_effective_llm_credential_auth_state,
    has_llm_credential_permission,
    has_organization_manager_permission,
)
from apps.shared.services.retrieval_embedding_model_projection import (
    EmbeddingModelBinding,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WizardLLMRuntime:
    """Wizard 실행에 사용할 permission-aware LLM runtime 상태입니다. MBA-43"""

    client: Any
    credential_id: uuid.UUID
    model_id: str
    model_db_id: uuid.UUID
    organization_id: uuid.UUID


class LLMCredentialNotAvailableError(ValueError):
    """사용 가능한 LLM credential runtime 후보가 없을 때 발생합니다. MBA-43"""

    def __init__(
        self,
        reason: str,
        message: str = "사용 가능한 LLM credential을 찾을 수 없습니다.",
        *,
        credential_id: Optional[uuid.UUID] = None,
        model_id: Optional[str] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.credential_id = credential_id
        self.model_id = model_id
        self.organization_id = organization_id


class LLMService:
    """
    LLM 공급자(프로바이더) 및 인증(크리덴셜) 관리 서비스.
    새로운 아키텍처:
    - 프로바이더는 시스템 정의 (전역)
    - 크리덴셜은 사용자 정의 (사용자별)
    """

    # 마이그레이션/개발용 플레이스홀더 유저 (인증 없음)
    PLACEHOLDER_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")

    # 사용자 친화적인 모델 표시 이름
    MODEL_DISPLAY_NAMES = {
        "gpt-5.6": "GPT-5.6 Sol",
        "gpt-5.6-sol": "GPT-5.6 Sol",
        "gpt-5.6-terra": "GPT-5.6 Terra",
        "gpt-5.6-luna": "GPT-5.6 Luna",
        "gpt-5.5": "GPT-5.5",
        "gpt-5.5-pro": "GPT-5.5 Pro",
        "gpt-5.4-pro": "GPT-5.4 Pro",
        "gpt-5.4": "GPT-5.4",
        "gpt-5.4-mini": "GPT-5.4 Mini",
        "gpt-5.4-nano": "GPT-5.4 Nano",
        "gpt-4o": "GPT-4o (Omni)",
        "gpt-4o-mini": "GPT-4o Mini",
        "claude-fable-5": "Claude Fable 5",
        "claude-opus-4-8": "Claude Opus 4.8",
        "claude-opus-4-7": "Claude Opus 4.7",
        "claude-opus-4-6": "Claude Opus 4.6",
        "claude-opus-4-5-20251101": "Claude Opus 4.5",
        "claude-sonnet-5": "Claude Sonnet 5",
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "claude-sonnet-4-5-20250929": "Claude Sonnet 4.5",
        "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
        "claude-haiku-4-5": "Claude Haiku 4.5",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
        "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
        "gemini-3-flash-preview": "Gemini 3 Flash Preview",
        "gemini-2.5-pro": "Gemini 2.5 Pro",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-2.5-flash-lite": "Gemini 2.5 Flash-Lite",
        "gemini-embedding-2": "Gemini Embedding 2",
        "gemini-embedding-001": "Gemini Embedding",
        "gemini-robotics-er-1.6-preview": "Gemini Robotics-ER 1.6 Preview",
    }

    # [신규] Provider별 가성비 모델 매핑 (Prompt Wizard, Query Rewriting 등에서 사용)
    EFFICIENT_MODELS = {
        "openai": "gpt-4o-mini",
        "google": "gemini-3.1-flash-lite",
        "anthropic": "claude-haiku-4-5-20251001",
    }

    AGENT_BUILDER_PROVIDER_ORDER = (*SUPPORTED_PROVIDER_ORDER, "llamaparse")
    # Pricing data lives in apps.shared.services.llm_model_pricing.
    # This compatibility shape is retained for model seed and admin APIs.
    KNOWN_MODEL_PRICES = known_model_prices()

    @staticmethod
    def _mask_plain(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:4]}****{value[-2:]}"

    @staticmethod
    def _fetch_remote_models(
        base_url: str, api_key: str, provider_type: str
    ) -> List[Dict[str, Any]]:
        """
        공급자 API에서 사용 가능한 모델 목록을 조회합니다.
        공급자에서 내려준 원본 모델 딕셔너리 리스트를 반환합니다.
        """
        remote_models = []

        provider = provider_type.lower()
        if not base_url:
            raise ValueError(f"Provider {provider_type} has no base_url configured.")

        # Google은 OpenAI 호환 /models 엔드포인트 검증을 지원
        if provider in ["openai", "google"]:
            url = base_url.rstrip("/") + "/models"
            try:
                resp = safe_http_request(
                    "GET",
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    operation_id=LLM_MODEL_DISCOVERY,
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        raise ValueError(
                            f"Invalid model response from {provider}"
                        ) from None
                    # OpenAI는 { "data": [ { "id": "model-id", ... }, ... ] } 형식으로 반환
                    remote_models = data.get("data", [])
                elif resp.status_code in [401, 403]:
                    # 인증 실패는 명시적으로 에러 처리
                    raise ValueError(
                        f"유효하지 않은 API Key입니다. 정확한 키를 입력했는지 확인해주세요. (Provider: {provider})"
                    )
                else:
                    # 그 외 상태 코드는 등록 단계 실패로 처리
                    raise ValueError(
                        f"Failed to fetch models from {provider}: "
                        f"status_code={resp.status_code}"
                    )
            except ValueError:
                raise  # 알려진 ValueError는 그대로 전달
            except Exception:
                # 네트워크/타임아웃 오류 처리
                raise ValueError(
                    f"Network error verifying {provider} key"
                ) from None

            if provider == "google" and remote_models:
                remote_models = LLMService._filter_google_models(
                    base_url=base_url,
                    api_key=api_key,
                    remote_models=remote_models,
                )

        # Anthropic (앤트로픽)
        elif provider == "anthropic":
            url = base_url.rstrip("/") + "/models"
            try:
                resp = safe_http_request(
                    "GET",
                    url,
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    operation_id=LLM_MODEL_DISCOVERY,
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        raise ValueError(
                            "Invalid model response from Anthropic"
                        ) from None
                    # Anthropic은 { "data": [ { "id": "claude-...", ... }, ... ] } 형식으로 반환
                    remote_models = data.get("data", [])
                elif resp.status_code in [401, 403]:
                    raise ValueError(
                        "유효하지 않은 API Key입니다. 정확한 키를 입력했는지 확인해주세요. (Provider: Anthropic)"
                    )
                else:
                    raise ValueError(
                        "Failed to fetch models from Anthropic: "
                        f"status_code={resp.status_code}"
                    )
            except ValueError:
                raise
            except Exception:
                raise ValueError("Network error verifying Anthropic key") from None

        # LlamaParse (라마파스)
        elif provider == "llamaparse":
            # LlamaCloud API 검증 (프로젝트 접근 확인)
            # base_url은 보통 https://api.cloud.llamaindex.ai
            url = base_url.rstrip("/") + "/api/v1/projects"
            try:
                resp = safe_http_request(
                    "GET",
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    operation_id=LLM_MODEL_DISCOVERY,
                )
                if resp.status_code == 200:
                    # 인증 성공
                    # LlamaParse는 LLM 노드용 "models"를 제공하지 않으므로 빈 리스트 반환
                    remote_models = []
                elif resp.status_code in [401, 403]:
                    raise ValueError(
                        "유효하지 않은 API Key입니다. 정확한 키를 입력했는지 확인해주세요. (Provider: LlamaParse)"
                    )
                else:
                    raise ValueError(
                        "Failed to verify LlamaParse key: "
                        f"status_code={resp.status_code}"
                    )
            except ValueError:
                raise
            except Exception:
                raise ValueError("Network error verifying LlamaParse key") from None
        # 현재는 빈 리스트를 반환하지만, 추후 지원 여부 검증 로직이 필요함.

        if not remote_models and provider in ["openai", "google", "anthropic"]:
            # 200 OK인데 모델이 없다면 이상하지만, 기술적으로는 성공 처리
            # 일반적으로는 모델이 있어야 함
            pass

        return remote_models

    @staticmethod
    def _filter_google_models(
        base_url: str, api_key: str, remote_models: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Google ListModels 결과를 기반으로 접근 가능한 모델만 남깁니다.
        - generateContent/embedContent 지원 여부를 확인합니다.
        - 실패 시 원본 목록을 그대로 반환합니다.
        """
        if not remote_models:
            return remote_models

        native_base = base_url.rstrip("/")
        if native_base.endswith("/openai"):
            native_base = native_base[: -len("/openai")]
        native_url = native_base.rstrip("/") + "/models"

        try:
            resp = safe_http_request(
                "GET",
                native_url,
                headers={"x-goog-api-key": api_key},
                operation_id=LLM_MODEL_DISCOVERY,
            )
        except Exception:
            return remote_models

        if resp.status_code != 200:
            return remote_models

        try:
            payload = resp.json()
        except ValueError:
            return remote_models

        allowed_ids = set()
        for model in payload.get("models", []):
            name = model.get("name") or ""
            if not name:
                continue
            methods = model.get("supportedGenerationMethods") or []
            if "generateContent" in methods or "embedContent" in methods:
                allowed_ids.add(name.replace("models/", ""))

        if not allowed_ids:
            return remote_models

        filtered = []
        for rm in remote_models:
            mid = rm.get("id") or ""
            clean_id = mid.replace("models/", "")
            if clean_id in allowed_ids:
                filtered.append(rm)

        return filtered

    @staticmethod
    def _sync_models_to_db(
        db: Session, provider: LLMProvider, remote_models: List[Dict[str, Any]]
    ) -> List[LLMModel]:
        """
        API에서 반환된 모델이 llm_models 테이블에 존재하는지 확인 및 동기화합니다.
        원격 모델에 해당하는 LLMModel 객체 리스트를 반환합니다.
        """
        synced_models = []
        existing_models = {
            m.model_id_for_api_call: m
            for m in db.query(LLMModel)
            .filter(LLMModel.provider_id == provider.id)
            .all()
        }

        for rm in remote_models:
            mid = rm.get("id")
            if not mid:
                continue

            # 표시 이름 결정 (Google의 경우 'models/' 접두사 제거)
            clean_id = mid.replace("models/", "")

            # 친화적인 이름 매핑이 있으면 적용, 없으면 ID 대문자화 등 사용
            display_name = LLMService.MODEL_DISPLAY_NAMES.get(clean_id, clean_id)

            # Shared catalog owns canonical aliases and current standard rates.
            pricing = get_model_pricing(mid)
            input_price = pricing.standard_input_per_1k if pricing else None
            output_price = pricing.standard_output_per_1k if pricing else None

            if mid in existing_models:
                # 메타데이터 및 이름 변경 시 업데이트
                model = existing_models[mid]
                changed = False
                if model.model_metadata != rm:
                    model.model_metadata = rm
                    changed = True
                if model.name != display_name:
                    model.name = display_name
                    changed = True

                if model.input_price_1k is None and input_price is not None:
                    model.input_price_1k = input_price
                    changed = True
                if model.output_price_1k is None and output_price is not None:
                    model.output_price_1k = output_price
                    changed = True

                if changed:
                    db.add(model)
                synced_models.append(model)
            else:
                # 신규 모델 생성
                # 모델 ID 기반으로 타입 감지
                mid_lower = mid.lower()
                m_type = "chat"  # 기본값

                # 임베딩 모델
                if "embedding" in mid_lower:
                    m_type = "embedding"
                # 오디오 모델 (TTS, Whisper, Audio)
                elif (
                    "tts" in mid_lower or "whisper" in mid_lower or "audio" in mid_lower
                ):
                    m_type = "audio"
                # 이미지 생성 모델
                elif "dall-e" in mid_lower or "image" in mid_lower:
                    m_type = "image"
                # 실시간 모델
                elif "realtime" in mid_lower:
                    m_type = "realtime"
                # 모더레이션 모델
                elif "moderation" in mid_lower:
                    m_type = "moderation"
                # 채팅 모델 (gpt, o1, o3, claude, gemini 등)
                # 그 외: 기본값 "chat" 유지

                # 동적 발견 시 기본 컨텍스트 윈도우는 알 수 없으므로, 안전한 기본값 또는 특정 규칙 사용
                ctx = 4096
                if "gpt-4" in mid:
                    ctx = 8192
                if "128k" in mid or "gpt-4o" in mid:
                    ctx = 128000

                new_model = LLMModel(
                    provider_id=provider.id,
                    model_id_for_api_call=mid,
                    name=display_name,  # 정리된 이름 사용
                    type=m_type,
                    context_window=ctx,
                    is_active=True,
                    model_metadata=rm,
                    input_price_1k=input_price,  # 신규
                    output_price_1k=output_price,  # 신규
                )
                db.add(new_model)
                synced_models.append(new_model)

        db.flush()  # 신규 모델 ID 확보용 flush
        return synced_models

    @staticmethod
    def get_system_providers(db: Session) -> List[LLMProviderResponse]:
        """시스템 정의 프로바이더 목록 조회."""
        providers = db.query(LLMProvider).options(joinedload(LLMProvider.models)).all()
        return [LLMProviderResponse.model_validate(p) for p in providers]

    @staticmethod
    def get_user_credentials(
        db: Session,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> List[LLMCredentialResponse]:
        """Active organization 안에서 읽을 수 있는 유효 credential을 조회합니다."""

        valid_credentials = (
            db.query(LLMCredential)
            .filter(
                LLMCredential.organization_id == organization_id,
                LLMCredential.is_valid.is_(True),
            )
            .all()
        )
        readable_credentials = [
            credential
            for credential in valid_credentials
            if has_llm_credential_permission(
                db,
                user_id,
                credential.id,
                "read",
                organization_id=organization_id,
            )
        ]
        return [
            LLMCredentialResponse.model_validate(c) for c in readable_credentials
        ]

    @staticmethod
    def register_credential(
        db: Session,
        user_id: uuid.UUID,
        request: LLMCredentialCreate,
        organization_id: uuid.UUID,
    ) -> LLMCredentialResponse:
        """
        사용자의 새 크리덴셜을 등록합니다.
        프로바이더 API 키 검증과 모델 동기화를 함께 수행합니다.
        """
        # 1. 프로바이더 조회
        provider = (
            db.query(LLMProvider).filter(LLMProvider.id == request.provider_id).first()
        )
        if not provider:
            raise ValueError(f"Provider {request.provider_id} not found")

        # [Defensive Check] 키 형식과 프로바이더 불일치 감지
        if provider.name == "google" and request.api_key.startswith("sk-"):
            raise ValueError(
                "입력하신 키는 OpenAI 형식을 따르고 있습니다. Google Provider가 아닌 OpenAI를 선택했는지 확인해주세요."
            )
        if provider.name == "openai" and request.api_key.startswith("AIza"):
            raise ValueError(
                "입력하신 키는 Google 형식을 따르고 있습니다. OpenAI Provider가 아닌 Google을 선택했는지 확인해주세요."
            )
        if provider.name == "openai" and request.api_key.startswith("sk-ant"):
            raise ValueError(
                "입력하신 키는 Anthropic 형식을 따르고 있습니다. OpenAI가 아닌 Anthropic을 선택했는지 확인해주세요."
            )

        if (
            request.organization_id is not None
            and request.organization_id != organization_id
        ):
            raise ValueError("Credential organization does not match active organization")
        if not has_organization_manager_permission(db, user_id, organization_id):
            raise PermissionError("Credential creation requires organization manager")

        envelope = protect_llm_credential_config(
            {"apiKey": request.api_key, "baseUrl": provider.base_url}
        )

        # 2. API 키 검증 및 모델 조회
        remote_models = LLMService._fetch_remote_models(
            provider.base_url, request.api_key, provider.name
        )

        # 3. 크리덴셜 생성
        new_cred = LLMCredential(
            provider_id=provider.id,
            user_id=user_id,
            organization_id=organization_id,
            credential_name=request.credential_name,
            encrypted_config=envelope.ciphertext,
            encryption_key_version=envelope.key_version,
            encryption_algorithm=envelope.algorithm,
            config_preview=LLMService._mask_plain(request.api_key),
            is_valid=True,
            quota_type="unlimited",
            quota_limit=0,
            quota_used=0,
        )
        db.add(new_cred)
        db.flush()

        # 4. 모델을 DB와 동기화
        db_models = LLMService._sync_models_to_db(db, provider, remote_models)

        # 5. 크리덴셜-모델 매핑
        for m in db_models:
            # 기존 매핑이 있는지 확인 (신규 크리덴셜이면 거의 없음)
            mapping = LLMRelCredentialModel(
                credential_id=new_cred.id,
                model_id=m.id,
                is_verified=True,  # _fetch_remote_models 검증 결과
            )
            db.add(mapping)

        db.commit()
        db.refresh(new_cred)
        return LLMCredentialResponse.model_validate(new_cred)

    @staticmethod
    def delete_credential(
        db: Session,
        credential_id: uuid.UUID,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> bool:
        """Active organization의 credential을 실제 삭제하지 않고 비활성화합니다."""

        cred = (
            db.query(LLMCredential)
            .filter(
                LLMCredential.id == credential_id,
                LLMCredential.organization_id == organization_id,
            )
            .first()
        )

        if not cred:
            return False
        if not has_llm_credential_permission(
            db,
            user_id,
            cred.id,
            "write",
            organization_id=organization_id,
        ):
            return False

        cred.is_valid = False
        db.commit()
        return True

    @staticmethod
    def sync_credential_models(
        db: Session,
        user_id: uuid.UUID,
        credential_id: uuid.UUID,
        organization_id: uuid.UUID,
        purge_unverified: bool = False,
    ) -> Dict[str, Any]:
        """
        특정 크리덴셜 기준으로 모델 매핑을 재동기화합니다.
        - 원격 모델 목록을 가져와 DB 모델을 동기화
        - 해당 크리덴셜의 모든 매핑을 unverified 처리 후,
          원격 모델에 포함된 것만 verified로 설정 (fail-closed)
        """
        cred = (
            db.query(LLMCredential)
            .options(joinedload(LLMCredential.provider))
            .filter(
                LLMCredential.id == credential_id,
                LLMCredential.organization_id == organization_id,
            )
            .first()
        )

        if not cred:
            raise ValueError("Credential not found")
        if not has_llm_credential_permission(
            db,
            user_id,
            cred.id,
            "write",
            organization_id=organization_id,
        ):
            raise ValueError("Credential not found")

        if not cred.is_valid:
            raise ValueError("Credential is not valid")

        try:
            credentials = materialize_llm_client_credentials(cred, cred.provider)
        except LLMCredentialConfigError as exc:
            raise ValueError("Invalid credential config") from exc

        remote_models = LLMService._fetch_remote_models(
            base_url=credentials["baseUrl"],
            api_key=credentials["apiKey"],
            provider_type=cred.provider.name,
        )
        db_models = LLMService._sync_models_to_db(db, cred.provider, remote_models)

        # 기존 매핑은 모두 비활성화 (fail-closed)
        db.query(LLMRelCredentialModel).filter(
            LLMRelCredentialModel.credential_id == cred.id
        ).update({LLMRelCredentialModel.is_verified: False}, synchronize_session=False)

        existing_links = {
            rel.model_id: rel
            for rel in db.query(LLMRelCredentialModel)
            .filter(LLMRelCredentialModel.credential_id == cred.id)
            .all()
        }

        for model in db_models:
            rel = existing_links.get(model.id)
            if rel:
                rel.is_verified = True
            else:
                db.add(
                    LLMRelCredentialModel(
                        credential_id=cred.id, model_id=model.id, is_verified=True
                    )
                )

        db.flush()

        purged = 0
        if purge_unverified:
            purged = (
                db.query(LLMRelCredentialModel)
                .filter(
                    LLMRelCredentialModel.credential_id == cred.id,
                    LLMRelCredentialModel.is_verified.is_(False),
                )
                .delete(synchronize_session=False)
            )

        db.commit()

        return {
            "credential_id": str(cred.id),
            "provider": cred.provider.name,
            "remote_models": len(remote_models),
            "verified_models": len(db_models),
            "purged_models": purged,
        }

    @staticmethod
    def get_client_for_user(
        db: Session,
        user_id: uuid.UUID,
        model_id: str,
        organization_id: Optional[uuid.UUID] = None,
    ):
        """
        주어진 model_id를 지원하는 유효한 크리덴셜을 찾습니다.
        우선순위:
        1. llm_rel_credential_models에서 명시적 권한 확인 (fail-closed)
        """
        # 1. 프로바이더를 알기 위해 모델 조회
        # 참고: model_id 문자열은 'gpt-4o'처럼 흔한 값일 수 있음.
        # 동일한 모델명을 제공하는 프로바이더가 여러 개일 수 있으므로(드물지만), 추가 정보가 필요할 수 있음.
        # 현재는 모델명이 충분히 유니크하거나 시스템 기본 프로바이더를 우선한다고 가정.

        target_model = (
            db.query(LLMModel)
            .options(joinedload(LLMModel.provider))
            .filter(LLMModel.model_id_for_api_call == model_id)
            .first()
        )
        if not target_model:
            # raise ValueError(f"Unknown model_id: {model_id}")
            # 시스템에 없는 모델명일 경우 처리 (커스텀 모델명 호환성)
            # 일단 에러 발생시키지 않고 진행하거나, Known 에러로 처리
            raise ValueError(f"Unknown model_id: {model_id}")

        # verified credential-model relation과 credential use 권한을 함께 평가한다.
        # relation이 없으면 fail-closed로 처리한다.
        cred = LLMService._get_valid_credential_for_user(
            db,
            user_id=user_id,
            model_db_id=target_model.id,
            organization_id=organization_id,
        )

        if not cred:
            logger.error(
                f"[LLMService] No valid credential found for user_id={user_id}, model_id='{model_id}'. "
                f"TargetModel: {target_model.name if target_model else 'None'} (ID: {target_model.id if target_model else 'None'}), "
                f"ProviderID: {target_model.provider_id if target_model else 'None'}"
            )
            raise ValueError(
                f"유효한 API 키를 찾을 수 없습니다. [설정 > 모델 키 관리]에서 '{model_id}' 모델을 지원하는 API Key를 등록해주세요."
            )

        # 설정 로드
        try:
            credentials = materialize_llm_client_credentials(cred, cred.provider)
        except LLMCredentialConfigError as exc:
            raise ValueError("Invalid credential config") from exc

        db.refresh(cred)
        provider_type = cred.provider.name

        return get_llm_client(
            provider=provider_type,
            model_id=model_id,
            credentials=credentials,
        )

    @staticmethod
    def get_client_for_model(
        db: Session,
        user_id: uuid.UUID,
        model: LLMModel,
        organization_id: Optional[uuid.UUID] = None,
    ):
        """
        DB model row 기준으로 사용할 수 있는 credential을 찾고 client를 생성한다.

        Agent answer처럼 preflight에서 이미 확인한 model row를 runtime에도 그대로
        사용해야 하는 경로에서 model_id 문자열 재조회로 다른 row가 선택되는 일을 막는다.
        """
        if not model or not model.is_active:
            raise ValueError("Unknown model")
        return LLMService._get_client_for_resolved_model(
            db,
            user_id=user_id,
            model_db_id=model.id,
            model_identifier=model.model_id_for_api_call,
            organization_id=organization_id,
        )

    @staticmethod
    def get_client_for_model_binding(
        db: Session,
        user_id: uuid.UUID,
        binding: EmbeddingModelBinding,
        organization_id: Optional[uuid.UUID] = None,
    ):
        """Create a client from a previously validated embedding model binding."""
        return LLMService._get_client_for_resolved_model(
            db,
            user_id=user_id,
            model_db_id=binding.model_id,
            model_identifier=binding.model_identifier,
            organization_id=organization_id,
        )

    @staticmethod
    def _get_client_for_resolved_model(
        db: Session,
        *,
        user_id: uuid.UUID,
        model_db_id: uuid.UUID,
        model_identifier: str,
        organization_id: Optional[uuid.UUID],
    ):
        cred = LLMService._get_valid_credential_for_user(
            db,
            user_id=user_id,
            model_db_id=model_db_id,
            organization_id=organization_id,
        )
        if not cred:
            logger.error(
                "[LLMService] No valid credential found for a resolved model."
            )
            raise ValueError("유효한 API 키를 찾을 수 없습니다.")

        try:
            credentials = materialize_llm_client_credentials(cred, cred.provider)
        except LLMCredentialConfigError as exc:
            raise ValueError("Invalid credential config") from exc

        db.refresh(cred)
        return get_llm_client(
            provider=cred.provider.name,
            model_id=model_identifier,
            credentials=credentials,
        )

    @staticmethod
    def get_client_with_any_credential(db: Session, model_id: Optional[str] = None):
        """
        [DEPRECATED] 안전성 문제로 비활성화되었습니다.
        user_id 없는 실행은 허용하지 않습니다.
        """
        raise ValueError(
            "Deprecated API: user_id 컨텍스트 없이 LLM 클라이언트를 생성할 수 없습니다."
        )

    @staticmethod
    def _list_llamaparse_credentials(
        db: Session,
        organization_id: uuid.UUID,
    ) -> list[LLMCredential]:
        """LlamaParse provider와 조직 범위가 일치하는 활성 credential만 조회한다."""
        return (
            db.query(LLMCredential)
            .options(joinedload(LLMCredential.provider))
            .join(LLMProvider, LLMCredential.provider_id == LLMProvider.id)
            .filter(
                LLMCredential.organization_id == organization_id,
                LLMCredential.is_valid.is_(True),
                LLMProvider.name == "llamaparse",
            )
            .order_by(LLMCredential.id.asc())
            .all()
        )

    @staticmethod
    def resolve_llamaparse_api_key(
        db: Session,
        *,
        user_id: Optional[uuid.UUID],
        organization_id: Optional[uuid.UUID],
    ) -> str:
        """문서 파싱에 사용할 단일 permission-aware LlamaParse credential을 해석한다."""
        try:
            user_uuid = uuid.UUID(str(user_id)) if user_id is not None else None
            organization_uuid = (
                uuid.UUID(str(organization_id))
                if organization_id is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise LLMCredentialNotAvailableError(
                "credential_context_missing",
                "LlamaParse credential context is unavailable.",
            ) from exc

        if user_uuid is None or organization_uuid is None:
            raise LLMCredentialNotAvailableError(
                "credential_context_missing",
                "LlamaParse credential context is unavailable.",
            )

        candidates = []
        permission_denials: list[tuple[LLMCredential, str]] = []
        for credential in LLMService._list_llamaparse_credentials(db, organization_uuid):
            provider_name = getattr(getattr(credential, "provider", None), "name", "")
            if (
                not credential.is_valid
                or provider_name.lower() != "llamaparse"
            ):
                continue
            if not has_llm_credential_permission(
                db,
                user_uuid,
                credential.id,
                "use",
                organization_id=organization_uuid,
            ):
                permission_denials.append(
                    (
                        credential,
                        get_effective_llm_credential_auth_state(
                            db,
                            user_uuid,
                            credential.id,
                            organization_id=organization_uuid,
                        ),
                    )
                )
                continue
            candidates.append(credential)

        if not candidates:
            if permission_denials:
                denied_resource_type = "organization"
                denied_resource_id = organization_uuid
                if len(permission_denials) == 1:
                    denied_resource_type = "llm_credential"
                    denied_resource_id = permission_denials[0][0].id
                record_resource_permission_denied(
                    user_id=user_uuid,
                    resource_type=denied_resource_type,
                    resource_id=denied_resource_id,
                    action="use",
                    effective_auth_state=permission_denials[0][1],
                    organization_id=organization_uuid,
                )
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "LlamaParse credential is unavailable.",
                organization_id=organization_uuid,
            )
        if len(candidates) != 1:
            raise LLMCredentialNotAvailableError(
                "credential_selection_ambiguous",
                "LlamaParse credential selection is ambiguous.",
                organization_id=organization_uuid,
            )

        try:
            config = load_llm_credential_config(candidates[0])
            api_key = config.get("apiKey")
        except LLMCredentialConfigError:
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "LlamaParse credential is unavailable.",
                organization_id=organization_uuid,
            ) from None

        if not isinstance(api_key, str) or not api_key:
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "LlamaParse credential is unavailable.",
                organization_id=organization_uuid,
            )
        return api_key

    @staticmethod
    def _get_valid_credential_for_user(
        db: Session,
        user_id: uuid.UUID,
        provider_id: Optional[uuid.UUID] = None,
        model_db_id: Optional[uuid.UUID] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> Optional[LLMCredential]:
        organization_uuid = None
        if organization_id:
            try:
                organization_uuid = uuid.UUID(str(organization_id))
            except (TypeError, ValueError):
                return None

        query = db.query(LLMCredential).filter(
            LLMCredential.is_valid.is_(True),
        )
        if organization_uuid:
            query = query.filter(LLMCredential.organization_id == organization_uuid)
        if provider_id:
            query = query.filter(LLMCredential.provider_id == provider_id)
        if model_db_id:
            query = (
                query.join(
                    LLMRelCredentialModel,
                    LLMRelCredentialModel.credential_id == LLMCredential.id,
                )
                .filter(
                    LLMRelCredentialModel.model_id == model_db_id,
                    LLMRelCredentialModel.is_verified.is_(True),
                )
                .order_by(LLMRelCredentialModel.priority.asc())
            )

        for credential in query.all():
            if has_llm_credential_permission(
                db,
                user_id,
                credential.id,
                "use",
                organization_id=organization_uuid,
            ):
                return credential
        return None

    @staticmethod
    def _resolve_runtime_organization_id(
        db: Session,
        user_id: uuid.UUID,
        organization_id: Optional[uuid.UUID] = None,
    ) -> uuid.UUID:
        """요청 organization이 없으면 기존 default organization fallback을 적용합니다. MBA-43"""
        if organization_id:
            try:
                return uuid.UUID(str(organization_id))
            except (TypeError, ValueError) as exc:
                raise LLMCredentialNotAvailableError(
                    "organization_scope_missing",
                    "유효한 organization_id가 필요합니다.",
                ) from exc

        try:
            return uuid.UUID(str(ensure_user_default_organization(db, user_id)))
        except Exception as exc:
            raise LLMCredentialNotAvailableError(
                "organization_scope_missing",
                "기본 organization scope를 확인할 수 없습니다.",
            ) from exc

    @staticmethod
    def _load_wizard_runtime(
        db: Session,
        user_id: uuid.UUID,
        provider_model_map: Dict[str, str],
        organization_id: Optional[uuid.UUID] = None,
    ) -> WizardLLMRuntime:
        """Wizard용 credential use 권한과 verified model relation을 함께 확인합니다. MBA-43"""
        organization_uuid = LLMService._resolve_runtime_organization_id(
            db, user_id, organization_id
        )
        credentials = (
            db.query(LLMCredential)
            .options(joinedload(LLMCredential.provider))
            .filter(
                LLMCredential.is_valid.is_(True),
                LLMCredential.organization_id == organization_uuid,
            )
            .order_by(LLMCredential.created_at.asc(), LLMCredential.id.asc())
            .all()
        )
        if not credentials:
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "LLM Provider가 등록되지 않았습니다. 설정에서 API Key를 등록해주세요.",
                organization_id=organization_uuid,
            )

        last_error: Optional[LLMCredentialNotAvailableError] = None
        for provider_name, model_id in provider_model_map.items():
            provider_credentials = [
                credential
                for credential in credentials
                if credential.provider
                and credential.provider.name.lower() == provider_name.lower()
            ]
            if not provider_credentials:
                continue

            verified_candidates = []
            for credential in provider_credentials:
                provider = credential.provider
                model = (
                    db.query(LLMModel)
                    .filter(
                        LLMModel.provider_id == credential.provider_id,
                        LLMModel.model_id_for_api_call == model_id,
                    )
                    .first()
                )
                if not model:
                    last_error = LLMCredentialNotAvailableError(
                        "model_relation_not_verified",
                        "사용 가능한 LLM 모델 관계를 찾을 수 없습니다.",
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    continue
                if not model.is_active:
                    last_error = LLMCredentialNotAvailableError(
                        "model_inactive",
                        "비활성화된 LLM 모델입니다.",
                        credential_id=credential.id,
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    continue

                relation = (
                    db.query(LLMRelCredentialModel)
                    .filter(
                        LLMRelCredentialModel.credential_id == credential.id,
                        LLMRelCredentialModel.model_id == model.id,
                        LLMRelCredentialModel.is_verified.is_(True),
                    )
                    .order_by(LLMRelCredentialModel.priority.asc())
                    .first()
                )
                if not relation:
                    last_error = LLMCredentialNotAvailableError(
                        "model_relation_not_verified",
                        "사용 가능한 LLM 모델 관계를 찾을 수 없습니다.",
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    continue

                verified_candidates.append(
                    (
                        relation.priority,
                        credential.created_at,
                        credential.id,
                        credential,
                        provider,
                        model_id,
                        model.id,
                    )
                )

            verified_candidates.sort(key=lambda item: (item[0], item[1], item[2]))

            for (
                _priority,
                _created_at,
                _id,
                credential,
                provider,
                model_id,
                model_db_id,
            ) in verified_candidates:
                if not has_llm_credential_permission(
                    db,
                    user_id,
                    credential.id,
                    "use",
                    organization_id=organization_uuid,
                ):
                    last_error = LLMCredentialNotAvailableError(
                        "credential_use_denied",
                        "LLM credential use 권한이 필요합니다.",
                        credential_id=credential.id,
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    continue

                try:
                    credentials = materialize_llm_client_credentials(
                        credential,
                        provider,
                    )
                except LLMCredentialConfigError:
                    last_error = LLMCredentialNotAvailableError(
                        "credential_not_available",
                        "LLM credential 설정을 읽을 수 없습니다.",
                        credential_id=credential.id,
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    logger.warning("[LLMService] Wizard credential config unavailable")
                    continue

                try:
                    client = get_llm_client(
                        provider=provider.name,
                        model_id=model_id,
                        credentials=credentials,
                    )
                except Exception:
                    last_error = LLMCredentialNotAvailableError(
                        "credential_not_available",
                        "LLM client를 생성할 수 없습니다.",
                        credential_id=credential.id,
                        model_id=model_id,
                        organization_id=organization_uuid,
                    )
                    logger.warning("[LLMService] Wizard client creation failed")
                    continue

                return WizardLLMRuntime(
                    client=client,
                    credential_id=credential.id,
                    model_id=model_id,
                    model_db_id=model_db_id,
                    organization_id=organization_uuid,
                )

        if last_error:
            raise last_error
        raise LLMCredentialNotAvailableError(
            "credential_not_available",
            "Wizard에서 사용할 수 있는 LLM Provider가 없습니다.",
            organization_id=organization_uuid,
        )

    @staticmethod
    def _record_wizard_runtime_block(
        user_id: uuid.UUID,
        error: LLMCredentialNotAvailableError,
        runtime_surface: str,
    ) -> None:
        """Wizard POST 최종 runtime 차단을 permission.denied audit으로 남깁니다. MBA-43"""
        resource_id = error.credential_id or "unknown"
        metadata: Dict[str, Any] = {
            "credential_id": str(error.credential_id) if error.credential_id else None,
            "reason": error.reason,
            "runtime_surface": runtime_surface,
        }
        if error.model_id:
            metadata["model_id"] = error.model_id

        record_resource_permission_denied(
            user_id=user_id,
            resource_type="llm_credential",
            resource_id=resource_id,
            action="use",
            effective_auth_state="none",
            organization_id=error.organization_id,
            metadata=metadata,
        )

    @staticmethod
    def get_wizard_client_for_user(
        db: Session,
        user_id: uuid.UUID,
        provider_model_map: Dict[str, str],
        organization_id: Optional[uuid.UUID] = None,
        *,
        runtime_surface: str = "wizard",
        audit_on_failure: bool = True,
    ) -> WizardLLMRuntime:
        """Wizard POST가 공통 LLM credential runtime 정책을 통과한 client를 받게 합니다. MBA-43"""
        try:
            return LLMService._load_wizard_runtime(
                db,
                user_id=user_id,
                provider_model_map=provider_model_map,
                organization_id=organization_id,
            )
        except LLMCredentialNotAvailableError as exc:
            if audit_on_failure:
                LLMService._record_wizard_runtime_block(
                    user_id=user_id,
                    error=exc,
                    runtime_surface=runtime_surface,
                )
            raise

    @staticmethod
    def _load_wizard_runtime_for_selection(
        db: Session,
        *,
        user_id: uuid.UUID,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        organization_id: Optional[uuid.UUID] = None,
    ) -> WizardLLMRuntime:
        organization_uuid = LLMService._resolve_runtime_organization_id(
            db, user_id, organization_id
        )
        credential = (
            db.query(LLMCredential)
            .options(joinedload(LLMCredential.provider))
            .filter(
                LLMCredential.id == credential_id,
                LLMCredential.organization_id == organization_uuid,
                LLMCredential.is_valid.is_(True),
            )
            .first()
        )
        if not credential:
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "The selected LLM credential is not available.",
                credential_id=credential_id,
                model_id=str(model_id),
                organization_id=organization_uuid,
            )
        if not has_llm_credential_permission(
            db,
            user_id,
            credential.id,
            "use",
            organization_id=organization_uuid,
        ):
            raise LLMCredentialNotAvailableError(
                "credential_use_denied",
                "LLM credential use permission is required.",
                credential_id=credential.id,
                model_id=str(model_id),
                organization_id=organization_uuid,
            )

        model = (
            db.query(LLMModel)
            .filter(
                LLMModel.id == model_id,
                LLMModel.provider_id == credential.provider_id,
                LLMModel.is_active.is_(True),
                LLMModel.type == "chat",
            )
            .first()
        )
        if not model:
            raise LLMCredentialNotAvailableError(
                "model_relation_not_verified",
                "The selected LLM model is not available for this credential.",
                credential_id=credential.id,
                model_id=str(model_id),
                organization_id=organization_uuid,
            )

        relation = (
            db.query(LLMRelCredentialModel)
            .filter(
                LLMRelCredentialModel.credential_id == credential.id,
                LLMRelCredentialModel.model_id == model.id,
                LLMRelCredentialModel.is_verified.is_(True),
            )
            .first()
        )
        if not relation:
            raise LLMCredentialNotAvailableError(
                "model_relation_not_verified",
                "The selected LLM model relation is not verified.",
                credential_id=credential.id,
                model_id=model.model_id_for_api_call,
                organization_id=organization_uuid,
            )

        try:
            credentials = materialize_llm_client_credentials(
                credential,
                credential.provider,
            )
        except LLMCredentialConfigError as exc:
            logger.warning("[LLMService] Selected credential config unavailable")
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "The selected LLM credential configuration is invalid.",
                credential_id=credential.id,
                model_id=model.model_id_for_api_call,
                organization_id=organization_uuid,
            ) from exc

        try:
            client = get_llm_client(
                provider=credential.provider.name,
                model_id=model.model_id_for_api_call,
                credentials=credentials,
            )
        except Exception as exc:
            logger.warning("[LLMService] Selected LLM client creation failed")
            raise LLMCredentialNotAvailableError(
                "credential_not_available",
                "The selected LLM client could not be created.",
                credential_id=credential.id,
                model_id=model.model_id_for_api_call,
                organization_id=organization_uuid,
            ) from exc

        return WizardLLMRuntime(
            client=client,
            credential_id=credential.id,
            model_id=model.model_id_for_api_call,
            model_db_id=model.id,
            organization_id=organization_uuid,
        )

    @staticmethod
    def get_wizard_client_for_selection(
        db: Session,
        *,
        user_id: uuid.UUID,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        organization_id: Optional[uuid.UUID] = None,
        runtime_surface: str = "wizard",
        audit_on_failure: bool = True,
    ) -> WizardLLMRuntime:
        try:
            return LLMService._load_wizard_runtime_for_selection(
                db,
                user_id=user_id,
                credential_id=credential_id,
                model_id=model_id,
                organization_id=organization_id,
            )
        except LLMCredentialNotAvailableError as exc:
            if audit_on_failure:
                LLMService._record_wizard_runtime_block(
                    user_id=user_id,
                    error=exc,
                    runtime_surface=runtime_surface,
                )
            raise

    @staticmethod
    def has_wizard_runtime_credential(
        db: Session,
        user_id: uuid.UUID,
        provider_model_map: Dict[str, str],
        organization_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Wizard GET credential check는 audit 없이 runtime 가능 여부만 반환합니다. MBA-43"""
        try:
            LLMService._load_wizard_runtime(
                db,
                user_id=user_id,
                provider_model_map=provider_model_map,
                organization_id=organization_id,
            )
            return True
        except LLMCredentialNotAvailableError:
            return False

    @staticmethod
    def get_my_available_models(
        db: Session, user_id: uuid.UUID
    ) -> List[LLMModelResponse]:
        """
        사용자의 등록된 크리덴셜을 기반으로 사용 가능한 모든 모델을 반환합니다.
        llm_rel_credential_models 기준으로 허용된 모델만 반환합니다.
        """
        rows = (
            db.query(LLMModel, LLMCredential.id)
            .join(
                LLMRelCredentialModel,
                LLMRelCredentialModel.model_id == LLMModel.id,
            )
            .join(
                LLMCredential,
                LLMRelCredentialModel.credential_id == LLMCredential.id,
            )
            .options(joinedload(LLMModel.provider))
            .filter(
                LLMCredential.is_valid.is_(True),
                LLMRelCredentialModel.is_verified.is_(True),
                LLMModel.is_active.is_(True),
            )
            .order_by(LLMModel.name)
            .all()
        )

        models = []
        seen_model_ids = set()
        for model, credential_id in rows:
            if model.id in seen_model_ids:
                continue
            if has_llm_credential_permission(db, user_id, credential_id, "use"):
                models.append(model)
                seen_model_ids.add(model.id)

        return [LLMModelResponse.model_validate(m) for m in models]

    @staticmethod
    def get_my_embedding_models(
        db: Session, user_id: uuid.UUID
    ) -> List[LLMModelResponse]:
        """
        사용자의 크리덴셜에 기반하여 사용 가능한 임베딩 모델 목록을 반환합니다.
        get_my_available_models와 동일하지만 type='embedding'으로 필터링됩니다.
        """
        rows = (
            db.query(LLMModel, LLMCredential.id)
            .join(
                LLMRelCredentialModel,
                LLMRelCredentialModel.model_id == LLMModel.id,
            )
            .join(
                LLMCredential,
                LLMRelCredentialModel.credential_id == LLMCredential.id,
            )
            .options(joinedload(LLMModel.provider))
            .filter(
                LLMCredential.is_valid.is_(True),
                LLMRelCredentialModel.is_verified.is_(True),
                LLMModel.is_active.is_(True),
                LLMModel.type == "embedding",
            )
            .all()
        )

        models = []
        seen_model_ids = set()
        for model, credential_id in rows:
            if model.id in seen_model_ids:
                continue
            if has_llm_credential_permission(db, user_id, credential_id, "use"):
                models.append(model)
                seen_model_ids.add(model.id)

        return [LLMModelResponse.model_validate(m) for m in models]

    @staticmethod
    def get_agent_answer_options(
        db: Session, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> List[LLMCredentialModelOptionResponse]:
        """
        RAG Agent answer UI에서 바로 제출 가능한 model/credential 조합을 반환합니다.

        Agent answer는 request에서 model과 credential을 모두 필수로 받기 때문에,
        프론트가 서로 검증되지 않은 조합을 만들지 않도록 verified relation과
        credential use 권한을 같은 조회 결과에 묶습니다.
        """
        rows = (
            db.query(LLMModel, LLMCredential, LLMRelCredentialModel.priority)
            .join(
                LLMRelCredentialModel,
                LLMRelCredentialModel.model_id == LLMModel.id,
            )
            .join(
                LLMCredential,
                LLMRelCredentialModel.credential_id == LLMCredential.id,
            )
            .options(joinedload(LLMModel.provider), joinedload(LLMCredential.provider))
            .filter(
                LLMModel.provider_id == LLMCredential.provider_id,
                LLMCredential.organization_id == organization_id,
                LLMCredential.is_valid.is_(True),
                LLMRelCredentialModel.is_verified.is_(True),
                LLMModel.is_active.is_(True),
                LLMModel.type == "chat",
            )
            .order_by(
                LLMRelCredentialModel.priority.asc(),
                LLMModel.name.asc(),
                LLMCredential.credential_name.asc(),
            )
            .all()
        )

        options: list[LLMCredentialModelOptionResponse] = []
        for model, credential, priority in rows:
            if not has_llm_credential_permission(
                db,
                user_id,
                credential.id,
                "use",
                organization_id=organization_id,
            ):
                continue
            options.append(
                LLMCredentialModelOptionResponse(
                    model=LLMModelResponse.model_validate(model),
                    credential=LLMCredentialOptionResponse.model_validate(credential),
                    provider_name=model.provider_name,
                    relation_priority=priority,
                )
            )
        return options

    @staticmethod
    def _order_agent_builder_options(
        options: List[LLMCredentialModelOptionResponse],
    ) -> List[LLMCredentialModelOptionResponse]:
        option_by_identity: dict[
            tuple[str, str],
            LLMCredentialModelOptionResponse,
        ] = {}
        for option in options:
            identity = (str(option.model.id), str(option.credential.id))
            current = option_by_identity.get(identity)
            if current is None or option.relation_priority < current.relation_priority:
                option_by_identity[identity] = option

        candidates: list[ModelRecommendationCandidate] = []
        for identity, option in option_by_identity.items():
            candidates.append(
                ModelRecommendationCandidate(
                    provider_name=option.provider_name,
                    model_id=option.model.model_id_for_api_call,
                    model_name=option.model.name,
                    relation_priority=option.relation_priority,
                    credential_name=option.credential.credential_name,
                    model_stable_id=identity[0],
                    credential_stable_id=identity[1],
                )
            )
        return [
            option_by_identity[
                (candidate.model_stable_id, candidate.credential_stable_id)
            ]
            for candidate in sort_model_candidates(candidates)
        ]

    @staticmethod
    def get_agent_builder_draft_model_recommendation(
        db: Session,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> Optional[LLMCredentialModelOptionResponse]:
        """Return the authorized default for generated Agent Builder LLM nodes."""
        options = LLMService._order_agent_builder_options(
            LLMService.get_agent_answer_options(
                db,
                user_id,
                organization_id,
            )
        )
        if not options:
            return None
        return options[0]

    @staticmethod
    def get_agent_builder_model_option_groups(
        db: Session,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> List[LLMIntentModelProviderResponse]:
        options = LLMService._order_agent_builder_options(
            LLMService.get_agent_answer_options(
                db,
                user_id,
                organization_id,
            )
        )
        by_provider: dict[str, list[LLMCredentialModelOptionResponse]] = {
            provider_name: []
            for provider_name in LLMService.AGENT_BUILDER_PROVIDER_ORDER
        }
        for option in options:
            provider_name = option.provider_name.strip().casefold()
            if provider_name in by_provider:
                by_provider[provider_name].append(option)

        groups: list[LLMIntentModelProviderResponse] = []
        for provider_name in LLMService.AGENT_BUILDER_PROVIDER_ORDER:
            provider_options = by_provider[provider_name]
            unavailable_reason = None
            if not provider_options:
                unavailable_reason = (
                    "chat_model_not_supported"
                    if provider_name == "llamaparse"
                    else "no_authorized_model"
                )
            groups.append(
                LLMIntentModelProviderResponse(
                    provider_name=provider_name,
                    options=provider_options,
                    unavailable_reason=unavailable_reason,
                )
            )
        return groups

    @staticmethod
    def _normalize_model_id(model_id: str) -> str:
        """
        모델 ID를 정규화하여 KNOWN_MODEL_PRICES와 매칭 가능하게 변환합니다.
        예: gpt-4o-2024-11-20 -> gpt-4o, claude-haiku-4-5-20251001 -> claude-haiku-4-5
        """
        return normalize_model_pricing_id(model_id)

    @staticmethod
    def calculate_cost(
        db: Session,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> float:
        """
        모델 가격 정보를 기반으로 비용을 계산합니다.
        DB에 가격 정보가 없으면 KNOWN_MODEL_PRICES로 폴백합니다.
        정규화된 모델 ID로 폴백 시도하여 버전 차이로 인한 매칭 실패를 방지합니다.
        """
        # 1. DB의 관리 가격이 있으면 Judge 후보 프로필과 같은 가격을 사용한다.
        # DB가 없는 lightweight caller는 즉시 catalog fallback으로 간다.
        model = None
        if db is not None:
            model = (
                db.query(LLMModel)
                .filter(LLMModel.model_id_for_api_call == model_id)
                .first()
            )
            normalized_model_id = LLMService._normalize_model_id(model_id)
            if model is None and normalized_model_id != model_id:
                model = (
                    db.query(LLMModel)
                    .filter(LLMModel.model_id_for_api_call == normalized_model_id)
                    .first()
                )

        if (
            model
            and model.input_price_1k is not None
            and model.output_price_1k is not None
        ):
            return calculate_text_token_cost_from_rates(
                input_price_per_1k=float(model.input_price_1k),
                output_price_per_1k=float(model.output_price_1k),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        # 2. DB 값이 없을 때만 shared catalog의 conditional rate를 적용한다.
        return calculate_text_token_cost(
            model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=extract_cached_input_tokens(usage),
        )

    @staticmethod
    def log_usage(
        db: Session,
        user_id: uuid.UUID,
        model_id: str,
        usage: Dict[str, Any],
        cost: float,
        organization_id: Optional[uuid.UUID] = None,
        workflow_id: Optional[uuid.UUID] = None,
        workflow_run_id: Optional[uuid.UUID] = None,
        node_id: Optional[str] = None,
        cost_optimizer_candidate_id: Optional[uuid.UUID] = None,
    ) -> Optional[LLMUsageLog]:
        """
        LLM 사용 로그를 DB에 저장합니다.
        """
        # 모델 DB ID 조회
        model = (
            db.query(LLMModel)
            .filter(LLMModel.model_id_for_api_call == model_id)
            .first()
        )
        if not model:
            if model_id.startswith("models/"):
                alt_id = model_id.replace("models/", "", 1)
            else:
                alt_id = f"models/{model_id}"
            model = (
                db.query(LLMModel)
                .filter(LLMModel.model_id_for_api_call == alt_id)
                .first()
            )
        if not model:
            logger.error(
                f"[LLMService] Usage log skipped: model '{model_id}' not found."
            )
            return None

        usage_context = resolve_llm_usage_context(
            db,
            organization_id=organization_id,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            logger=logger,
        )
        if usage_context is None:
            return None
        organization_uuid = usage_context.organization_id
        workflow_uuid = usage_context.workflow_id
        workflow_run_uuid = usage_context.workflow_run_id

        credential = LLMService._get_valid_credential_for_user(
            db,
            user_id,
            model_db_id=model.id,
            organization_id=organization_uuid,
        )
        if not credential:
            logger.error(
                f"[LLMService] Usage log skipped: no credential for user {user_id}."
            )
            return None

        cost_optimizer_candidate_uuid = None
        if cost_optimizer_candidate_id:
            try:
                cost_optimizer_candidate_uuid = uuid.UUID(
                    str(cost_optimizer_candidate_id)
                )
            except (TypeError, ValueError):
                logger.error(
                    "[LLMService] Usage log skipped: invalid "
                    f"cost_optimizer_candidate_id {cost_optimizer_candidate_id}."
                )
                return None

        log = LLMUsageLog(
            user_id=user_id,
            organization_id=organization_uuid,
            credential_id=credential.id,
            model_id=model.id,
            workflow_id=workflow_uuid,
            workflow_run_id=workflow_run_uuid,
            cost_optimizer_candidate_id=cost_optimizer_candidate_uuid,
            node_id=node_id,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_cost=cost,
            latency_ms=usage.get("latency_ms", 0),
            status="success",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def list_workflow_run_llm_traces(
        db: Session,
        workflow_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        node_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Optional[Dict[str, Any]]:
        run_exists = (
            db.query(WorkflowRun.id)
            .filter(WorkflowRun.id == run_id, WorkflowRun.workflow_id == workflow_id)
            .first()
        )
        if not run_exists:
            return None

        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        query = (
            db.query(LLMUsageLog)
            .options(joinedload(LLMUsageLog.model).joinedload(LLMModel.provider))
            .filter(
                LLMUsageLog.workflow_run_id == run_id,
                or_(
                    LLMUsageLog.workflow_id == workflow_id,
                    LLMUsageLog.workflow_id.is_(None),
                ),
            )
        )
        if node_id:
            query = query.filter(LLMUsageLog.node_id == node_id)

        total = query.count()
        rows = (
            query.order_by(LLMUsageLog.created_at.asc(), LLMUsageLog.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        items = []
        for row in rows:
            model = row.model
            provider = model.provider if model else None
            items.append(
                {
                    "id": row.id,
                    "workflow_id": row.workflow_id or workflow_id,
                    "workflow_run_id": row.workflow_run_id,
                    "node_id": row.node_id,
                    "model_id": row.model_id,
                    "model_name": getattr(model, "name", None),
                    "provider": getattr(provider, "name", None),
                    "credential_id": row.credential_id,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "total_tokens": row.prompt_tokens + row.completion_tokens,
                    "total_cost": float(row.total_cost)
                    if row.total_cost is not None
                    else None,
                    "latency_ms": row.latency_ms,
                    "status": row.status,
                    "created_at": row.created_at,
                }
            )

        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @staticmethod
    def update_model_pricing(
        db: Session, model_id: uuid.UUID, input_price: float, output_price: float
    ) -> LLMModel:
        """
        특정 모델의 가격 정보를 업데이트합니다.
        """
        if input_price < 0 or output_price < 0:
            raise ValueError("Model pricing must be non-negative")

        model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
        if not model:
            raise ValueError(f"Model {model_id} not found")

        model.input_price_1k = input_price
        model.output_price_1k = output_price
        db.commit()
        db.refresh(model)
        return model

    @staticmethod
    def sync_system_prices(db: Session) -> Dict[str, Any]:
        """
        DB에 있는 모든 모델의 가격을 KNOWN_MODEL_PRICES와 동기화합니다.
        알려진 목록에 매칭되면 기존 가격을 덮어씁니다.
        """
        updated_count = 0
        known_prices = LLMService.KNOWN_MODEL_PRICES

        models = db.query(LLMModel).all()
        for m in models:
            # model_id_for_api_call로 매칭 (예: gpt-4o)
            # "models/" 접두사가 있으면 제거 (Google)
            pricing = known_prices.get(LLMService._normalize_model_id(m.model_id_for_api_call))
            if pricing:
                # 다른 값이거나 (또는 기존 값이 None인 경우) 업데이트
                # float 비교는 대략적으로 처리
                current_in = (
                    float(m.input_price_1k) if m.input_price_1k is not None else -1.0
                )
                current_out = (
                    float(m.output_price_1k) if m.output_price_1k is not None else -1.0
                )

                target_in = pricing["input"]
                target_out = pricing["output"]

                if (
                    abs(current_in - target_in) > 0.0000001
                    or abs(current_out - target_out) > 0.0000001
                ):
                    m.input_price_1k = target_in
                    m.output_price_1k = target_out
                    updated_count += 1

        if updated_count > 0:
            db.commit()

        return {"updated_models": updated_count}
