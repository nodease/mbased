from unittest.mock import MagicMock

import pytest
from apps.shared.services.ingestion.processors.db_processor import DbProcessor
from apps.shared.utils.join_query_utils import (
    generate_join_query,
    normalize_query_limit,
    quote_postgres_identifier,
)


def test_quote_postgres_identifier_preserves_name_without_sql_interpretation():
    assert quote_postgres_identifier('order "archive"') == '"order ""archive"""'
    assert quote_postgres_identifier("*", allow_wildcard=True) == "*"


@pytest.mark.parametrize(
    "value",
    [None, "", "bad\x00name", "가" * 22],
)
def test_quote_postgres_identifier_rejects_invalid_identifier(value):
    with pytest.raises(ValueError, match="identifier"):
        quote_postgres_identifier(value)


@pytest.mark.parametrize(
    "value",
    [True, 0, 10_001, "1 UNION SELECT secret", 1.5],
)
def test_normalize_query_limit_rejects_non_bounded_integer(value):
    with pytest.raises(ValueError, match="limit"):
        normalize_query_limit(value)


def test_single_table_query_quotes_stored_table_and_column_values():
    processor = DbProcessor()
    processor._process_common_logic = MagicMock(return_value=[])

    processor._process_single_table(
        connector=object(),
        config_dict={},
        selections=[
            {
                "table_name": "orders CROSS JOIN secrets",
                "columns": ["id", "(SELECT secret FROM vault)"],
            }
        ],
        source_config={"limit": "1000"},
        transformer=object(),
        chunker=object(),
    )

    query = processor._process_common_logic.call_args.args[1]
    assert query == (
        'SELECT "id", "(SELECT secret FROM vault)" '
        'FROM "orders CROSS JOIN secrets" LIMIT 1000'
    )


def test_join_query_quotes_identifiers_and_uses_only_selected_tables():
    query = generate_join_query(
        [
            {"table_name": "orders", "columns": ["id", "user_id"]},
            {"table_name": "user profile", "columns": ["id", "name"]},
        ],
        {
            "base_table": "orders",
            "joins": [
                {
                    "from_table": "orders",
                    "to_table": "user profile",
                    "from_column": "user_id",
                    "to_column": "id",
                }
            ],
        },
        1000,
    )

    assert 'FROM "orders"' in query
    assert 'LEFT JOIN "user profile"' in query
    assert '"orders"."user_id" = "user profile"."id"' in query
    assert '"user profile"."name" AS "user profile__name"' in query
    assert query.endswith("LIMIT 1000")


def test_join_query_rejects_unselected_table_reference():
    with pytest.raises(ValueError, match="selected tables"):
        generate_join_query(
            [
                {"table_name": "orders", "columns": ["id"]},
                {"table_name": "users", "columns": ["id"]},
            ],
            {
                "base_table": "orders",
                "joins": [
                    {
                        "from_table": "orders",
                        "to_table": "secrets",
                        "from_column": "user_id",
                        "to_column": "id",
                    }
                ],
            },
        )
