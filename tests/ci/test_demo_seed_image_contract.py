import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_gateway_image_packages_scripts_executed_by_local_demo_guide():
    guide = _read("docs/demo/local-demo-db.md")
    dockerfile = _read("docker/gateway/Dockerfile")

    documented_scripts = set(
        re.findall(r"python\s+(/app/scripts/[A-Za-z0-9_.-]+)", guide)
    )
    packaged_scripts = set(
        re.findall(r"^COPY\s+\S+\s+(/app/scripts/\S+)\s*$", dockerfile, re.MULTILINE)
    )

    assert documented_scripts
    assert documented_scripts <= packaged_scripts
