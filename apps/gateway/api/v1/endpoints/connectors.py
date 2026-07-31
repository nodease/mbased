import asyncio
import logging
from enum import Enum
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from apps.gateway.api.deps import get_db
from apps.gateway.application.connectors.errors import (
    ConnectorTestAdmissionUnavailable,
    ConnectorTestBusy,
    ConnectorTestIngressError,
    ConnectorTestPayloadInvalid,
    ConnectorTestPayloadTimeout,
    ConnectorTestRateLimited,
)
from apps.gateway.application.connectors.ingress import (
    DEFAULT_CONNECTOR_TEST_INGRESS_POLICY,
    ConnectorTestIngressMetadata,
    ConnectorTestIngressPolicy,
)
from apps.gateway.application.connectors.models import ConnectorTestCommand
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.composition.authentication import login_network_resolver
from apps.gateway.composition.connectors import get_connector_test_application
from apps.gateway.middleware.webhook_query_redaction import (
    CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY,
)
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import error_detail, raise_api_error
from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleHidden,
    ConnectionLifecycleInUse,
    ConnectionLifecycleService,
    ConnectionLifecycleUnavailable,
)
from apps.gateway.utils.audit import audit
from apps.gateway.utils.encryption import encryption_manager
from apps.shared.audit.actions import AuditAction
from apps.shared.connectors.postgres import PostgresConnector
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.user import User
from apps.shared.db.session import SessionLocal
from apps.shared.schemas.connector import (
    ConnectorTestRequest,
    DBConnectionTestRequest,
    DBConnectionTestResponse,
)
from apps.shared.schemas.connector_detail import DBConnectionDetailResponse
from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshotConfigurationInvalid,
    ConnectionRuntimeSnapshotProvider,
)
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class SupportedDBType(str, Enum):
    POSTGRES = "postgres"
    # MYSQL = "mysql"


CONNECTOR_MAP = {
    SupportedDBType.POSTGRES: PostgresConnector,
    # SupportedDBType.MYSQL: MySQLConnector,
}

_CONNECTOR_TEST_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {"schema": ConnectorTestRequest.model_json_schema()},
    },
}


def _build_workflow_connector(connector_class):
    if connector_class is PostgresConnector or issubclass(connector_class, PostgresConnector):
        # Existing workflow DB connector는 SSH tunnel과 custom DB port를 지원해 왔다.
        # Knowledge source ingestion은 기본 PostgresConnector()를 사용해 더 보수적인 경계를 유지한다.
        return connector_class(allow_ssh_tunnel=True, allowed_db_ports=None)
    return connector_class()


def _raw_header_values(request: Request, expected_name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value
        for name, value in request.scope.get("headers", ())
        if name.lower() == expected_name
    )


def _connector_test_ingress_metadata(request: Request) -> ConnectorTestIngressMetadata:
    state = request.scope.get("state", {})
    return ConnectorTestIngressMetadata(
        query_present=(
            bool(request.scope.get("query_string", b""))
            or state.get(CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY) is True
        ),
        content_type_headers=_raw_header_values(request, b"content-type"),
        content_encoding_headers=_raw_header_values(request, b"content-encoding"),
        content_length_headers=_raw_header_values(request, b"content-length"),
    )


async def _read_connector_test_payload(
    request: Request,
    ingress_policy: ConnectorTestIngressPolicy,
) -> dict[str, Any]:
    deadline = ingress_policy.start_deadline()
    body = bytearray()
    stream = request.stream().__aiter__()

    while True:
        timeout = ingress_policy.remaining_seconds(deadline)
        try:
            chunk = await asyncio.wait_for(anext(stream), timeout=timeout)
        except StopAsyncIteration:
            break
        except TimeoutError:
            raise ConnectorTestPayloadTimeout() from None
        except ClientDisconnect:
            raise ConnectorTestPayloadInvalid() from None
        except Exception:
            raise ConnectorTestPayloadInvalid() from None

        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ConnectorTestPayloadInvalid()
        ingress_policy.validate_actual_size(len(body) + len(chunk))
        body.extend(chunk)

    return ingress_policy.parse_json(bytes(body), deadline)


def _raise_connector_test_ingress_error(
    request: Request,
    error: ConnectorTestIngressError,
) -> NoReturn:
    messages = {
        "connector.test_payload_invalid": "Connection test payload is invalid.",
        "connector.test_payload_timeout": "Connection test payload timed out.",
        "connector.test_payload_too_large": "Connection test payload is too large.",
        "connector.test_media_type_not_supported": (
            "Connection test media type is not supported."
        ),
    }
    raise_api_error(
        request,
        error.status_code,
        error.code,
        messages[error.code],
    )


def _raise_connector_test_admission_error(
    request: Request,
    error: ConnectorTestRateLimited | ConnectorTestBusy,
) -> NoReturn:
    messages = {
        "connector.test_rate_limited": "Too many connection test requests.",
        "connector.test_busy": "Connection test capacity is currently busy.",
    }
    raise HTTPException(
        status_code=429,
        detail=error_detail(request, error.code, messages[error.code]),
        headers={"Retry-After": str(error.retry_after or 1)},
    ) from None


@router.post(
    "/test",
    response_model=DBConnectionTestResponse,
    openapi_extra={"requestBody": _CONNECTOR_TEST_REQUEST_BODY},
)
async def test_db_connection(
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    raw_organization_id: str | None = Header(
        default=None,
        alias="X-Organization-Id",
    ),
) -> Any:
    """Test a bounded public PostgreSQL target without persisting credentials."""

    organization_id = resolve_active_organization_id(
        db,
        http_request,
        raw_organization_id,
        current_user.id,
    )
    network_address = login_network_resolver().resolve(http_request)
    if network_address == "unknown":
        raise_api_error(
            http_request,
            503,
            "connector.admission_unavailable",
            "Connection test admission is unavailable.",
        )
    ingress_policy = DEFAULT_CONNECTOR_TEST_INGRESS_POLICY
    try:
        ingress_policy.validate_metadata(
            _connector_test_ingress_metadata(http_request)
        )
        payload = await _read_connector_test_payload(http_request, ingress_policy)
    except ConnectorTestIngressError as error:
        _raise_connector_test_ingress_error(http_request, error)

    try:
        request_data = ConnectorTestRequest.model_validate(payload)
    except ValidationError:
        raise_api_error(
            http_request,
            422,
            "validation.failed",
            "Request validation failed.",
        )

    command = ConnectorTestCommand(
        organization_id=organization_id,
        actor_id=current_user.id,
        network_address=network_address,
        host=request_data.host,
        port=request_data.port,
        database=request_data.database,
        username=request_data.username,
        password=request_data.password.get_secret_value(),
        ssh_enabled=bool(request_data.ssh and request_data.ssh.enabled),
    )
    try:
        result = await get_connector_test_application().use_case.execute(command)
    except (ConnectorTestRateLimited, ConnectorTestBusy) as error:
        _raise_connector_test_admission_error(http_request, error)
    except ConnectorTestAdmissionUnavailable:
        raise_api_error(
            http_request,
            503,
            "connector.admission_unavailable",
            "Connection test admission is unavailable.",
        )

    return DBConnectionTestResponse(
        success=result.success,
        message=result.message,
        reason_code=result.reason_code,
    )


@router.post("", status_code=201)
@audit(AuditAction.CONNECTION_CREATE)
async def create_connection(
    request: DBConnectionTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    **DB 연결 정보 저장 API**

    연결 테스트한 후, 민감 정보를 암호화하여 저장합니다.
    """

    connector_class = CONNECTOR_MAP.get(request.type)
    if not connector_class:
        raise HTTPException(status_code=400, detail="지원하지 않는 DB타입입니다.")

    config_dict = request.model_dump()
    if config_dict.get("ssh") and not config_dict["ssh"].get("enabled"):
        config_dict["ssh"] = None

    try:
        import asyncio

        from starlette.concurrency import run_in_threadpool

        connector = _build_workflow_connector(connector_class)

        # blocking I/O (SSH connection, DB connection)를 별도 스레드에서 실행
        # 10초 타임아웃 적용
        try:
            is_connected = await asyncio.wait_for(
                run_in_threadpool(connector.check, config_dict), timeout=10.0
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=400,
                detail="연결 시간 초과 (10초). 방화벽(보안그룹)이나 IP 허용 설정을 확인해주세요.",
            )

        if not is_connected:
            raise HTTPException(
                status_code=400,
                detail="DB연결 테스트에 실패했습니다. 정보를 확인해주세요.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("DB connection validation failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "connector.connection_failed"},
        )

    try:
        encrypted_password = encryption_manager.encrypt(request.password)
        encrypted_ssh_password = None
        encrypted_ssh_private_key = None

        if request.ssh and request.ssh.enabled:
            if request.ssh.password:
                encrypted_ssh_password = encryption_manager.encrypt(
                    request.ssh.password
                )
            if request.ssh.private_key:
                encrypted_ssh_private_key = encryption_manager.encrypt(
                    request.ssh.private_key
                )
    except Exception as e:
        logger.error("Connection secret encryption failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "connection_config.encrypt_failed"},
        )

    new_connection = Connection(
        user_id=current_user.id,
        name=request.connection_name,
        type=request.type,
        host=request.host,
        port=request.port,
        database=request.database,
        username=request.username,
        encrypted_password=encrypted_password,
        # SSH
        use_ssh=request.ssh.enabled if request.ssh else False,
        ssh_host=request.ssh.host if request.ssh else None,
        ssh_port=request.ssh.port if request.ssh else None,
        ssh_username=request.ssh.username if request.ssh else None,
        ssh_auth_type=request.ssh.auth_type if request.ssh else None,
        encrypted_ssh_password=encrypted_ssh_password,
        encrypted_ssh_private_key=encrypted_ssh_private_key,
    )

    db.add(new_connection)
    db.commit()
    db.refresh(new_connection)

    return {
        "id": str(new_connection.id),
        "success": True,
        "message": "연결 정보가 안전하게 저장되었습니다.",
    }


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
@audit(AuditAction.CONNECTION_DELETE, target_param="connection_id")
def delete_connection(
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        ConnectionLifecycleService(db).delete_unreferenced_connection(
            connection_id=connection_id,
            owner_id=current_user.id,
        )
    except ConnectionLifecycleHidden:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": "resource.hidden"},
        )
    except ConnectionLifecycleInUse:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "connection.in_use"},
        )
    except ConnectionLifecycleBusy:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "connection.reference_busy"},
        )
    except ConnectionLifecycleUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "connection.delete_unavailable"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{connection_id}", response_model=DBConnectionDetailResponse)
async def get_connection_details(
    connection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    **DB 연결 상세 정보 조회 API**
    저장된 연결 정보(Host, Port 등)를 반환합니다. (비밀번호 제외)
    """
    connection = db.query(Connection).filter(Connection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    ssh_config = None
    if connection.use_ssh:
        ssh_config = {
            "enabled": True,
            "host": connection.ssh_host,
            "port": connection.ssh_port,
            "username": connection.ssh_username,
            "auth_type": connection.ssh_auth_type,
            # Passwords/Keys are not returned
        }

    return DBConnectionDetailResponse(
        id=str(connection.id),
        connection_name=connection.name,
        type=connection.type,
        host=connection.host,
        port=connection.port,
        database=connection.database,
        username=connection.username,
        ssh=ssh_config,
    )


# 스키마 조회 API 추가
@router.get("/{connection_id}/schema")
async def get_connection_schema(
    connection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    **DB 스키마 조회 API**
    독립된 짧은 snapshot transaction에서 연결 정보를 해석한 뒤 DB schema를 조회합니다.
    """
    current_user_id = current_user.id
    normalized_connection_id = _authorize_connection_schema_management(
        db,
        connection_id=connection_id,
        current_user_id=current_user_id,
    )
    try:
        snapshot = ConnectionRuntimeSnapshotProvider(SessionLocal).load(
            normalized_connection_id,
            execution_subject_user_id=current_user_id,
        )
    except ConnectionUseDenied:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": "resource.hidden"},
        )
    except ConnectionUseUnavailable:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "connection.reference_unavailable"},
        )
    except ConnectionRuntimeSnapshotConfigurationInvalid:
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "connection_config.decrypt_failed"},
        )

    connector_class = CONNECTOR_MAP.get(snapshot.adapter_type)
    if not connector_class:
        raise HTTPException(status_code=400, detail="Unsupported DB type")
    try:
        connector = _build_workflow_connector(connector_class)
        tables = connector.get_schema_info(snapshot.to_connector_config())
        return {"tables": tables}
    except Exception as e:
        logger.error("Schema fetch failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "connector.schema_fetch_failed"},
        )


def _authorize_connection_schema_management(
    db: Session,
    *,
    connection_id: str,
    current_user_id: UUID,
) -> UUID:
    try:
        normalized_connection_id = UUID(str(connection_id))
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Connection not found") from None

    try:
        owner_row = (
            db.query(Connection.user_id)
            .filter(Connection.id == normalized_connection_id)
            .one_or_none()
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "connection.reference_unavailable"},
        ) from None

    db.rollback()
    if owner_row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if owner_row[0] != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return normalized_connection_id
