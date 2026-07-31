import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict

from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshotConfigurationInvalid,
    ConnectionRuntimeSnapshotProvider,
)
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseUnavailable,
)
from apps.shared.services.ingestion.chunkers.adaptive_db_chunker import (
    AdaptiveDbChunker,
)
from apps.shared.services.ingestion.processors.base import (
    BaseProcessor,
    ProcessingResult,
)
from apps.shared.services.ingestion.transformers.db_nl_transformer import (
    DbNlTransformer,
)
from apps.shared.utils.encryption import encryption_manager
from apps.shared.utils.join_query_utils import (
    convert_to_namespace,
    generate_join_query,
    normalize_query_limit,
    quote_postgres_identifier,
)

logger = logging.getLogger(__name__)


class DbProcessor(BaseProcessor):
    """
    [DbProcessor]
    외부 DB 연결 정보를 사용하여 SQL을 실행하고,
    결과 Row를 자연어로 변환하여 청킹합니다.
    """

    def __init__(
        self,
        db_session=None,
        user_id=None,
        organization_id=None,
        *,
        connection_snapshot_provider: ConnectionRuntimeSnapshotProvider | None = None,
    ) -> None:
        super().__init__(db_session, user_id, organization_id)
        self.connection_snapshot_provider = connection_snapshot_provider

    @staticmethod
    def _convert_to_json_serializable(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decimal, datetime 등 JSON 직렬화 불가능한 타입을 변환

        Args:
            data: 원본 row dictionary

        Returns:
            JSON 직렬화 가능한 dictionary
        """
        result = {}
        for key, value in data.items():
            if value is None:
                result[key] = None
            elif isinstance(value, Decimal):
                # Decimal -> float or int
                result[key] = float(value) if value % 1 else int(value)
            elif isinstance(value, (datetime, date)):
                # datetime/date -> ISO format string
                result[key] = value.isoformat()
            elif isinstance(value, uuid.UUID):
                # UUID -> str
                result[key] = str(value)
            elif isinstance(value, (list, dict)):
                # 중첩된 구조는 그대로 (PostgreSQL JSON 타입 등)
                result[key] = value
            else:
                # str, int, float, bool 등은 그대로
                result[key] = value
        return result

    def process(self, source_config: Dict[str, Any]) -> ProcessingResult:
        """
        source_config: {
            "connection_id": "...",
            "sql": "SELECT * FROM ...",
            # 또는 meta_info에서 필요한 정보 전달
        }
        """
        if self.connection_snapshot_provider is None:
            return self._connection_lookup_unavailable_result()

        try:
            connection_snapshot = self.connection_snapshot_provider.load(
                source_config.get("connection_id"),
                execution_subject_user_id=self.user_id,
            )
        except ConnectionUseDenied:
            return self._connection_unavailable_result()
        except ConnectionUseUnavailable:
            return self._connection_lookup_unavailable_result()
        except ConnectionRuntimeSnapshotConfigurationInvalid:
            return self._connection_configuration_invalid_result()

        # Connector 인스턴스 생성
        connector = self._get_connector(connection_snapshot.adapter_type)
        if not connector:
            return self._connection_configuration_invalid_result()

        config_dict = connection_snapshot.to_connector_config()

        # 3. 데이터 패칭
        chunks = []
        try:
            # 사용자가 선택한 테이블/컬럼 정보(selections)를 기반으로 데이터 조회
            selections = source_config.get("selections", [])

            transformer = DbNlTransformer()

            # 자동 Chunker 초기화
            chunk_settings = source_config.get("chunk_settings", {})

            chunker = AdaptiveDbChunker(
                chunk_size=chunk_settings.get("chunk_size", 1000),
                chunk_overlap=chunk_settings.get("overlap", 150),
            )

            # JOIN 모드 체크(2개까지만 허용)
            join_config = source_config.get("join_config", {})
            
            # 2개 테이블 선택 시 FK 관계 필수
            if len(selections) == 2:
                if not join_config.get("enabled", False):
                    raise ValueError(
                        "선택한 테이블 간 FK 관계가 없습니다."
                    )
                
                logger.info("[DB처리] JOIN 모드")
                join_chunks = self._process_with_join(
                    connector,
                    config_dict,
                    selections,
                    join_config,
                    source_config,
                    transformer,
                    chunker,
                )
                chunks.extend(join_chunks)
            # 단일 테이블인 경우
            else:
                chunks.extend(
                    self._process_single_table(
                        connector,
                        config_dict,
                        selections,
                        source_config,
                        transformer,
                        chunker,
                    )
                )
        except Exception as exc:
            error_code = (
                "configuration_invalid"
                if isinstance(exc, (KeyError, TypeError, ValueError))
                else "temporarily_unavailable"
            )
            return ProcessingResult(
                chunks=[],
                metadata={
                    "error": "DB source processing failed",
                    "error_code": error_code,
                    "reason_code": (
                        "configuration.invalid"
                        if error_code == "configuration_invalid"
                        else "source.temporarily_unavailable"
                    ),
                },
            )
        return ProcessingResult(chunks=chunks, metadata={"source_type": "DB"})

    @staticmethod
    def _connection_unavailable_result() -> ProcessingResult:
        return ProcessingResult(
            chunks=[],
            metadata={
                "error": "Resource unavailable",
                "error_code": "configuration_invalid",
                "reason_code": "resource.hidden",
            },
        )

    @staticmethod
    def _connection_lookup_unavailable_result() -> ProcessingResult:
        return ProcessingResult(
            chunks=[],
            metadata={
                "error": "Connection lookup unavailable",
                "error_code": "temporarily_unavailable",
                "reason_code": "source.temporarily_unavailable",
            },
        )

    @staticmethod
    def _connection_configuration_invalid_result() -> ProcessingResult:
        return ProcessingResult(
            chunks=[],
            metadata={
                "error": "Connection configuration unavailable",
                "error_code": "configuration_invalid",
                "reason_code": "configuration.invalid",
            },
        )

    def _get_connector(self, db_type: str):
        if db_type == "postgres":
            from apps.shared.connectors.postgres import PostgresConnector

            # Knowledge ingestion은 기존 workflow connector와 달리 SSH tunnel을 기본 허용하지 않는다.
            return PostgresConnector()
        # 추후 mysql, oracle 등 추가
        return None

    def _process_single_table(
        self,
        connector,
        config_dict,
        selections,
        source_config,
        transformer,
        chunker,
    ):
        """단일 테이블 모드 처리"""
        if not selections:
            logger.warning("No tables selected for processing")
            return []

        selection = selections[0]
        table_name = selection["table_name"]
        logger.info("[DB처리] 단일 테이블 처리")

        columns = selection.get("columns", ["*"])
        if not isinstance(columns, list) or not columns:
            raise ValueError("columns must be a non-empty list")
        if "*" in columns and columns != ["*"]:
            raise ValueError("wildcard must be the only selected column")
        limit = normalize_query_limit(source_config.get("limit", 1000))

        req_cols = ", ".join(
            quote_postgres_identifier(column, allow_wildcard=True)
            for column in columns
        )
        query = (
            f"SELECT {req_cols} FROM {quote_postgres_identifier(table_name)} "
            f"LIMIT {limit}"
        )

        # Strategies
        def transform_strategy(row_dict):
            # 선택된 컬럼만 포함 (중복 출력 방지)
            return transformer.transform(
                row_dict,
                template_str=source_config.get("template") or selection.get("template"),
                table_name=table_name,
            )

        def encryption_key_strategy(table, col):
            return col

        return self._process_common_logic(
            connector,
            query,
            config_dict,
            selections,
            transformer,
            chunker,
            source_config,
            transform_strategy,
            encryption_key_strategy,
        )

    def _process_with_join(
        self,
        connector,
        config_dict,
        selections,
        join_config,
        source_config,
        transformer,
        chunker,
    ):
        """2테이블 JOIN 모드 처리"""
        limit = source_config.get("limit", 1000)
        query = generate_join_query(selections, join_config, limit)
        logger.info("DB JOIN query generated")

        # 템플릿 (전역 템플릿 사용)
        template_str = source_config.get("template", None)
        if not template_str and selections:
            for sel in selections:
                if sel.get("template"):
                    template_str = sel.get("template")
                    break

        # Strategies
        def transform_strategy(row_dict):
            # 네임스페이스 변환: {table__col: val} → {table: {col: val}}
            namespaced_data = convert_to_namespace(row_dict)

            # JSON 직렬화 가능한 형태로 변환 (Decimal, datetime 등)
            serialized_data = {}
            for table, cols in namespaced_data.items():
                if isinstance(cols, dict):
                    serialized_data[table] = self._convert_to_json_serializable(cols)
                else:
                    serialized_data[table] = cols

            # DbNlTransformer 사용하여 일관된 처리
            return transformer.transform(
                serialized_data,
                template_str=template_str,
            )

        def encryption_key_strategy(table, col):
            # JOIN 쿼리는 table__col 형식으로 키가 생성됨
            return f"{table}__{col}"

        return self._process_common_logic(
            connector,
            query,
            config_dict,
            selections,
            transformer,
            chunker,
            source_config,
            transform_strategy,
            encryption_key_strategy,
        )

    def _process_common_logic(
        self,
        connector,
        query,
        config_dict,
        selections,
        transformer,
        chunker,
        source_config,
        transform_strategy,
        encryption_key_strategy,
    ):
        """
        JOIN 모드와 단일 테이블 모드의 공통 처리 로직
        """
        chunks = []
        enable_chunking = source_config.get("enable_auto_chunking", True)

        row_count = 0
        logger.info("[DB처리] 쿼리 실행 중...")

        for row_dict in connector.fetch_data(config_dict, query):
            row_count += 1
            if row_count % 100 == 0:
                logger.info(f"[DB처리] 처리 중: {row_count}개 행")

            # 1. 텍스트 변환 (Strategy)
            nl_text = transform_strategy(row_dict)

            # 2. 원본 데이터 직렬화
            original_data = self._convert_to_json_serializable(row_dict)

            # 3. 암호화 (Strategy)
            for sel in selections:
                table_name = sel["table_name"]
                sensitive_cols = sel.get("sensitive_columns", [])
                for col in sensitive_cols:
                    # 키 매핑 전략: (table_name, col) -> data_key
                    key = encryption_key_strategy(table_name, col)
                    if key in original_data and original_data[key] is not None:
                        original_data[key] = encryption_manager.encrypt(
                            str(original_data[key])
                        )

            # 4. 메타데이터 구성
            metadata = {
                "source": f"DB:{'JOIN' if len(selections) > 1 else selections[0]['table_name']}",
                "tables": [s["table_name"] for s in selections],
                "row_index": row_count,
                "original_data": original_data,
                "sensitive_columns": [
                    c for s in selections for c in s.get("sensitive_columns", [])
                ],
            }

            # 5. 청킹
            try:
                row_chunks = chunker.chunk_if_needed(
                    text=nl_text,
                    metadata=metadata,
                    enable_chunking=enable_chunking,
                )
                chunks.extend(row_chunks)
            except ValueError as exc:
                logger.error(
                    "DB row chunking failed: error_type=%s",
                    type(exc).__name__,
                )
                continue

        logger.info(f"[DB처리] 완료: {row_count}개 행, {len(chunks)}개 청크")
        return chunks
