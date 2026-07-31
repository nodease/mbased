from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

MailProvider = Literal["gmail", "naver", "daum", "outlook", "custom"]
MailAuthType = Literal["app_password", "password", "oauth2"]
MailManualAuthType = Literal["app_password", "password"]
MailCredentialStatus = Literal["active", "revoked"]


def _reject_imap_control_characters(value: str, field_name: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError(f"{field_name} contains a forbidden control character")
    return value


class MailCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_name: str = Field(min_length=1, max_length=255)
    provider: MailProvider
    email_address: str = Field(min_length=3, max_length=320)
    auth_type: MailManualAuthType = "app_password"
    secret: SecretStr = Field(min_length=1)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True

    @field_validator("credential_name", "email_address", "imap_host")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("email_address")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        _reject_imap_control_characters(value, "email_address")
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("email_address must be a valid mailbox address")
        return value.lower()

    @field_validator("imap_host")
    @classmethod
    def validate_imap_host(cls, value: str) -> str:
        if "://" in value or any(char.isspace() for char in value):
            raise ValueError("imap_host must be a hostname without scheme")
        return value.lower()

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        _reject_imap_control_characters(value.get_secret_value(), "secret")
        return value


class MailCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_name: str | None = Field(default=None, min_length=1, max_length=255)
    secret: SecretStr | None = Field(default=None, min_length=1)

    @field_validator("credential_name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("secret")
    @classmethod
    def validate_optional_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            _reject_imap_control_characters(value.get_secret_value(), "secret")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "MailCredentialUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if any(
            getattr(self, field_name) is None for field_name in self.model_fields_set
        ):
            raise ValueError("updated fields must not be null")
        return self


class MailCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    organization_id: UUID
    credential_name: str
    provider: MailProvider
    email_preview: str
    auth_type: MailAuthType
    imap_host: str
    imap_port: int
    use_ssl: bool
    status: MailCredentialStatus
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class MailCredentialOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    credential_name: str
    provider: MailProvider
    auth_type: MailAuthType
    email_preview: str
    status: MailCredentialStatus


class MailCredentialPermissionGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_state: Literal["viewer", "operator", "builder", "manager"]


class MailCredentialPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime


class GmailOAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_name: str = Field(min_length=1, max_length=255)

    @field_validator("credential_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("credential_name must not be blank")
        return stripped


class GmailOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str
