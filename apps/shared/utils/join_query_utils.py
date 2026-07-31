"""
JOIN 쿼리 생성 유틸리티
2개 테이블 JOIN 쿼리를 자동 생성합니다.
"""

from typing import Any, Dict, List

MAX_POSTGRES_IDENTIFIER_BYTES = 63
MAX_QUERY_LIMIT = 10_000


def quote_postgres_identifier(value: Any, *, allow_wildcard: bool = False) -> str:
    """PostgreSQL identifier 한 개를 안전하게 인용한다.

    Table/column 값은 schema inspector가 반환한 단일 identifier여야 하며, schema-qualified
    expression이나 SQL fragment로 해석하지 않는다.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid PostgreSQL identifier")
    if allow_wildcard and value == "*":
        return "*"
    if len(value.encode("utf-8")) > MAX_POSTGRES_IDENTIFIER_BYTES:
        raise ValueError("PostgreSQL identifier is too long")
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def normalize_query_limit(value: Any, *, maximum: int = MAX_QUERY_LIMIT) -> int:
    """LIMIT 값을 SQL fragment가 아닌 bounded integer로 정규화한다."""
    if isinstance(value, bool):
        raise ValueError("invalid query limit")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
    else:
        raise ValueError("invalid query limit")
    if parsed < 1 or parsed > maximum:
        raise ValueError("query limit is out of range")
    return parsed


def generate_join_query(
    selections: List[Dict[str, Any]], join_config: Dict[str, Any], limit: int = 1000
) -> str:
    """
    2테이블 LEFT JOIN 쿼리 생성

    Args:
        selections: [
            {"table_name": "orders", "columns": ["id", "user_id", "price"]},
            {"table_name": "users", "columns": ["id", "name"]}
        ]
        join_config: {
            "base_table": "orders",
            "joins": [{
                "from_table": "orders",
                "to_table": "users",
                "from_column": "user_id",
                "to_column": "id"
            }]
        }
        limit: LIMIT 절 값

    Returns:
        SELECT orders.id AS orders__id, ...
        FROM orders
        LEFT JOIN users ON orders.user_id = users.id
        LIMIT 1000
    """
    if len(selections) != 2:
        raise ValueError("JOIN query requires exactly two table selections")

    selected_tables = [selection["table_name"] for selection in selections]
    if len(set(selected_tables)) != 2:
        raise ValueError("JOIN query requires two distinct tables")

    base_table = join_config["base_table"]
    if base_table not in selected_tables:
        raise ValueError("JOIN base table must be selected")

    joins = join_config.get("joins", [])
    if not isinstance(joins, list) or len(joins) != 1:
        raise ValueError("JOIN query requires exactly one join edge")

    safe_limit = normalize_query_limit(limit)

    # SELECT 절: 테이블명__컬럼명 형식으로 alias
    select_parts = []
    for sel in selections:
        table = sel["table_name"]
        columns = sel.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError("JOIN columns must be a non-empty list")
        quoted_table = quote_postgres_identifier(table)
        for col in columns:
            if col == "*":
                raise ValueError("JOIN wildcard columns are not supported")
            quoted_column = quote_postgres_identifier(col)
            quoted_alias = quote_postgres_identifier(f"{table}__{col}")
            select_parts.append(
                f"{quoted_table}.{quoted_column} AS {quoted_alias}"
            )

    select_clause = ",\n        ".join(select_parts)

    # JOIN 절
    join_clauses = []
    for join in joins:
        from_table = join["from_table"]
        to_table = join["to_table"]
        if (
            from_table != base_table
            or to_table not in selected_tables
            or to_table == base_table
        ):
            raise ValueError("JOIN edge must reference the selected tables")
        join_clauses.append(
            f"LEFT JOIN {quote_postgres_identifier(to_table)} "
            f"ON {quote_postgres_identifier(from_table)}."
            f"{quote_postgres_identifier(join['from_column'])} = "
            f"{quote_postgres_identifier(to_table)}."
            f"{quote_postgres_identifier(join['to_column'])}"
        )

    join_clause = "\n    ".join(join_clauses)

    query = f"""
        SELECT 
            {select_clause}
        FROM {quote_postgres_identifier(base_table)}
        {join_clause}
        LIMIT {safe_limit}
    """

    return query.strip()


def convert_to_namespace(row_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Flat dictionary를 네임스페이스 구조로 변환

    Args:
        row_dict: {"orders__id": 1, "users__name": "홍길동"}

    Returns:
        {"orders": {"id": 1}, "users": {"name": "홍길동"}}
    """
    result = {}
    for key, value in row_dict.items():
        if "__" in key:
            table, col = key.split("__", 1)
            if table not in result:
                result[table] = {}
            result[table][col] = value
        else:
            # __ 없는 경우는 그대로 (일반 모드와의 호환성)
            result[key] = value
    return result
