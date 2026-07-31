import pytest

from apps.gateway.application.authentication.models import (
    LoginAdmission,
    LoginLimitDimension,
)
from apps.gateway.application.authentication.policies import (
    normalize_login_account,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  User@Example.COM  ", "user@example.com"),
        ("ＦＯＯ@example.com", "foo@example.com"),
        ("Straße@Example.com", "strasse@example.com"),
    ],
)
def test_login_account_normalization_is_nfkc_trimmed_and_casefolded(raw, expected):
    assert normalize_login_account(raw) == expected


def test_login_account_normalization_rejects_empty_identity():
    with pytest.raises(ValueError, match="account identity is empty"):
        normalize_login_account("  ")


@pytest.mark.parametrize(
    "values",
    [
        {"allowed": True, "retry_after_seconds": 1},
        {"allowed": False},
        {
            "allowed": False,
            "retry_after_seconds": 301,
            "limited_dimensions": (LoginLimitDimension.ACCOUNT,),
        },
        {
            "allowed": False,
            "retry_after_seconds": 1,
            "limited_dimensions": (
                LoginLimitDimension.ACCOUNT,
                LoginLimitDimension.ACCOUNT,
            ),
        },
    ],
)
def test_login_admission_rejects_inconsistent_or_unbounded_state(values):
    with pytest.raises(ValueError):
        LoginAdmission(**values)
