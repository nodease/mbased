import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "pr-ci-control-guard.yml"


def _ci_control_job_lines(workflow_lines: list[str]) -> list[str]:
    start = workflow_lines.index("  ci_control_review:")
    end = next(
        (
            index
            for index in range(start + 1, len(workflow_lines))
            if workflow_lines[index].startswith("  ")
            and not workflow_lines[index].startswith("    ")
        ),
        len(workflow_lines),
    )
    return workflow_lines[start:end]


def _github_script_source() -> str:
    workflow_lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    start = workflow_lines.index("          script: |") + 1
    script_lines = [
        line[12:] if line.startswith("            ") else ""
        for line in workflow_lines[start:]
    ]
    return "\n".join(script_lines)


def test_github_script_is_valid_async_javascript():
    script = f"(async () => {{\n{_github_script_source()}\n}})();\n"
    result = subprocess.run(
        ["node", "--check", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_unrelated_issue_comments_cannot_cancel_ci_control_review():
    workflow_lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    job_lines = _ci_control_job_lines(workflow_lines)

    assert "concurrency:" not in workflow_lines
    assert "    concurrency:" in job_lines
    assert (
        "      group: trusted-ci-control-${{ github.event.pull_request.number || "
        "github.event.issue.number || github.run_id }}"
        in job_lines
    )
    assert "      cancel-in-progress: true" in job_lines
    assert "       github.event.comment.body == '/recheck-ci-control')" in job_lines


def test_review_events_cannot_run_base_trusted_guard():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    trigger_block = workflow.split("permissions:", maxsplit=1)[0]

    assert "pull_request_target:" in trigger_block
    assert "issue_comment:" in trigger_block
    assert "pull_request_review:" not in trigger_block


def test_transient_github_api_failures_use_bounded_retries():
    workflow_lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    job_lines = _ci_control_job_lines(workflow_lines)

    assert "          retries: 3" in job_lines
    assert (
        "          retry-exempt-status-codes: 400,401,403,404,422" in job_lines
    )


def test_operational_errors_use_replaceable_authoritative_status():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    report_error = workflow.split(
        "const reportOperationalError = async (error) => {", maxsplit=1
    )[1].split("};", maxsplit=1)[0]

    assert "await setStatus(" in report_error
    assert '"error",' in report_error
    assert "core.warning(" in report_error
    assert "core.setFailed(" in report_error
    assert "GitHub API unavailable; retry CI control evaluation." in report_error
    assert "x-github-request-id" in workflow


def test_reviewer_permission_lookup_distinguishes_policy_from_api_failure():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "if (error?.status === 404)" in workflow
    assert "throw error;" in workflow


def test_policy_denial_uses_replaceable_head_status_without_failing_job():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deny_policy = workflow.split(
        "const denyPolicy = async (description, warningMessage) => {", maxsplit=1
    )[1].split("};", maxsplit=1)[0]

    assert 'await setStatus("failure", description);' in deny_policy
    assert "core.warning(warningMessage);" in deny_policy
    assert "core.setFailed(" not in deny_policy
    assert workflow.count("await denyPolicy(") == 2
    assert "Could not enumerate every changed file; review is required." in workflow
    assert "Fresh approval from a write maintainer is required." in workflow
