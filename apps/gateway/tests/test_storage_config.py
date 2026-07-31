import secrets

import pytest
from pydantic import ValidationError

from apps.gateway.core.config import Settings


def test_storage_configuration_defaults_to_local_without_cloud_coordinates():
    settings = Settings(_env_file=None)

    assert settings.STORAGE_TYPE == "LOCAL"
    assert settings.S3_BUCKET_NAME is None
    assert settings.AWS_REGION is None


def test_cloud_storage_accepts_complete_normalized_configuration():
    settings = Settings(
        _env_file=None,
        STORAGE_TYPE=" cloud ",
        S3_BUCKET_NAME=" nodease-documents ",
        AWS_REGION=" region-1 ",
    )

    assert settings.STORAGE_TYPE == "CLOUD"
    assert settings.S3_BUCKET_NAME == "nodease-documents"
    assert settings.AWS_REGION == "region-1"


@pytest.mark.parametrize(
    ("bucket", "region"),
    [
        (None, "region-1"),
        ("", "region-1"),
        ("   ", "region-1"),
        ("nodease-documents", None),
        ("nodease-documents", ""),
        ("nodease-documents", "   "),
    ],
)
def test_cloud_storage_rejects_missing_bucket_or_region(bucket, region):
    with pytest.raises(ValidationError, match="cloud_storage_configuration_incomplete"):
        Settings(
            _env_file=None,
            STORAGE_TYPE="CLOUD",
            S3_BUCKET_NAME=bucket,
            AWS_REGION=region,
        )


def test_storage_configuration_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, STORAGE_TYPE="REMOTE")


def test_incomplete_cloud_storage_error_does_not_include_credential_input():
    credential_sentinel = secrets.token_hex(32)

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            STORAGE_TYPE="CLOUD",
            AWS_SECRET_ACCESS_KEY=credential_sentinel,
            AWS_REGION="region-1",
            S3_BUCKET_NAME=None,
        )

    assert "cloud_storage_configuration_incomplete" in str(exc_info.value)
    assert credential_sentinel not in str(exc_info.value)
