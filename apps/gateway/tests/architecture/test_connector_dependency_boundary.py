import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_gateway_declares_cryptography_version_with_utc_certificate_api() -> None:
    project = tomllib.loads(
        (ROOT / "apps/gateway/pyproject.toml").read_text(encoding="utf-8")
    )
    dependency = next(
        item
        for item in project["project"]["dependencies"]
        if item.startswith("cryptography>=")
    )
    version = re.fullmatch(r"cryptography>=(\d+)\.(\d+)\.(\d+)", dependency)

    assert version is not None
    assert tuple(map(int, version.groups())) >= (42, 0, 0)
