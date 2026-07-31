from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def _strip_connection_name(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


# SSH 설정 스키마
class SSHConfig(BaseModel):
    enabled: bool = False
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    auth_type: Literal["password", "key"] = "password"
    password: Optional[str] = None
    private_key: Optional[str] = None


# DB연결 테스트 요청 본문 스키마
class DBConnectionTestRequest(BaseModel):
    connection_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="연결 식별을 위한 별칭",
    )
    type: str = Field(..., description="데이터베이스 타입 (예: postgres)")

    host: str
    port: int = 5432
    database: str
    username: str
    password: str
    ssh: Optional[SSHConfig] = None

    @field_validator("connection_name", mode="before")
    @classmethod
    def normalize_connection_name(cls, value: object) -> object:
        return _strip_connection_name(value)


class ConnectorTestSSHConfig(BaseModel):
    """Bounded legacy SSH shape for the strict connection-test endpoint."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: Optional[str] = Field(default=None, min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=128)
    auth_type: Literal["password", "key"] = "password"
    password: Optional[SecretStr] = Field(default=None, min_length=1, max_length=1024)
    private_key: Optional[SecretStr] = Field(
        default=None,
        min_length=1,
        max_length=16 * 1024,
    )


class ConnectorTestRequest(BaseModel):
    """Strict, non-persistent contract for ``POST /connectors/test``."""

    model_config = ConfigDict(extra="forbid")

    connection_name: str = Field(min_length=1, max_length=100)
    type: Literal["postgres"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535, strict=True)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)
    ssh: Optional[ConnectorTestSSHConfig] = None

    @field_validator("connection_name", mode="before")
    @classmethod
    def normalize_connection_name(cls, value: object) -> object:
        return _strip_connection_name(value)


# 테스트 결과 응답 스키마
class DBConnectionTestResponse(BaseModel):
    success: bool
    message: str
    reason_code: Optional[str] = None


class DBConnectionDetailResponse(BaseModel):
    id: str
    connection_name: str
    type: str
    host: str
    port: int
    database: str
    username: str
    # Password is explicitly excluded for security

    ssh: Optional[dict] = None  # Or define a specific SSH schema
