import uuid

import pytest

from apps.gateway.application.knowledge_administration.delegation_subjects import (
    DelegationSubjectPageInvalid,
    decode_subject_cursor,
    encode_subject_cursor,
    escape_like_prefix,
    normalize_subject_query,
    normalize_subject_type,
    validate_subject_page_size,
)


def test_subject_query_normalizes_whitespace_without_interpreting_wildcards():
    assert normalize_subject_query("  지식\t  Team  ") == "지식 Team"
    assert escape_like_prefix(r"A%_\B") == r"A\%\_\\B"


@pytest.mark.parametrize("value", ["x" * 101, "name\x00value"])
def test_subject_query_rejects_oversized_or_control_text(value):
    with pytest.raises(DelegationSubjectPageInvalid):
        normalize_subject_query(value)


def test_subject_cursor_is_bound_to_type_and_normalized_query():
    subject_id = uuid.uuid4()
    cursor = encode_subject_cursor(
        subject_type="team",
        query="지식 Team",
        last_subject_id=subject_id,
    )

    decoded = decode_subject_cursor(
        cursor,
        subject_type="team",
        query="지식 Team",
    )

    assert decoded is not None
    assert decoded.last_subject_id == subject_id
    assert str(subject_id) not in cursor
    with pytest.raises(DelegationSubjectPageInvalid):
        decode_subject_cursor(cursor, subject_type="user", query="지식 Team")
    with pytest.raises(DelegationSubjectPageInvalid):
        decode_subject_cursor(cursor, subject_type="team", query="다른 검색")


@pytest.mark.parametrize("cursor", ["", "not-base64!", "a" * 129])
def test_subject_cursor_rejects_malformed_or_oversized_values(cursor):
    with pytest.raises(DelegationSubjectPageInvalid):
        decode_subject_cursor(cursor, subject_type="team", query="")


@pytest.mark.parametrize("limit", [0, 51])
def test_subject_page_size_is_bounded(limit):
    with pytest.raises(DelegationSubjectPageInvalid):
        validate_subject_page_size(limit)


def test_subject_page_size_accepts_boundaries():
    assert validate_subject_page_size(1) == 1
    assert validate_subject_page_size("50") == 50


def test_subject_type_rejects_values_outside_the_closed_union():
    assert normalize_subject_type("team") == "team"
    with pytest.raises(DelegationSubjectPageInvalid):
        normalize_subject_type("organization")
