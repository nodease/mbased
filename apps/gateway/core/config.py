from typing import Literal, Optional

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    pydantic-settings의 BaseSettings 클래스를 상속합니다.
    이 클래스를 상속받아 설정 모델을 정의하면, 환경 변수나 .env 파일의 값을 자동으로 읽어와서 Python 타입으로 변환 및 검증을 수행합니다.
    """

    # RAG Ingestion Mode
    STORAGE_TYPE: Literal["LOCAL", "CLOUD"] = "LOCAL"

    # App auth secret lifecycle rollout gate. Keep disabled until every Gateway
    # pod runs the verifier-aware revision.
    APP_AUTH_SECRET_LIFECYCLE_MODE: Literal["disabled", "active"] = "disabled"

    # Keep query-embedding policy writes dormant until all purpose-unaware
    # Gateway and Worker processes have drained.
    QUERY_EMBEDDING_POLICY_WRITE_MODE: Literal["disabled", "active"] = "disabled"

    # Compatibility keeps old public chatbot clients available as stateless
    # requests while mixed Gateway/Frontend revisions drain.
    PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE: Literal["compatibility", "strict"] = (
        "compatibility"
    )

    # AWS Settings
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = None
    S3_BUCKET_NAME: Optional[str] = None

    @field_validator("STORAGE_TYPE", mode="before")
    @classmethod
    def normalize_storage_type(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("AWS_REGION", "S3_BUCKET_NAME", mode="before")
    @classmethod
    def normalize_storage_coordinate(cls, value):
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("S3_BUCKET_NAME")
    @classmethod
    def require_complete_cloud_storage(
        cls,
        bucket_name: Optional[str],
        info: ValidationInfo,
    ) -> Optional[str]:
        if info.data.get("STORAGE_TYPE") == "CLOUD" and (
            not bucket_name or not info.data.get("AWS_REGION")
        ):
            # Keep the error stable and free of configuration values. A field
            # validator also prevents Pydantic from including the full Settings
            # input (which may contain credentials) in this validation error.
            raise ValueError("cloud_storage_configuration_incomplete")
        return bucket_name

    # Load from .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        validate_default=True,
    )


settings = Settings()
