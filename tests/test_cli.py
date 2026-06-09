from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from local_swe_controller import __version__
from local_swe_controller.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    runs_help = runner.invoke(app, ["runs", "--help"])
    bench_help = runner.invoke(app, ["bench", "--help"])

    assert result.exit_code == 0
    assert runs_help.exit_code == 0
    assert bench_help.exit_code == 0
    assert "Local-first deterministic SWE controller" in result.stdout
    assert "compile-policy" in result.stdout
    assert "validate" in result.stdout
    assert "repair" in result.stdout
    assert "runs" in result.stdout
    assert "bench" in result.stdout
    assert "pr-summary" in runs_help.stdout
    assert "create-pr" in runs_help.stdout
    assert "run" in bench_help.stdout
    assert "show" in bench_help.stdout


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_compile_policy_writes_default_output(temp_fixture_repo: Path) -> None:
    before_status = _git_status(temp_fixture_repo)
    result = runner.invoke(app, ["compile-policy", "--repo", str(temp_fixture_repo)])
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    prefix = "Compiled policy written to "
    output_file = Path(result.stdout.strip().split(prefix, maxsplit=1)[1])
    assert output_file.exists()
    assert "/policies/" in str(output_file)
    assert output_file.parent.parent != temp_fixture_repo / ".local-swe"
    assert not (temp_fixture_repo / ".local-swe").exists()
    assert before_status == after_status


def test_compile_policy_invalid_repo() -> None:
    result = runner.invoke(app, ["compile-policy", "--repo", "/does/not/exist"])

    assert result.exit_code == 1
    assert "Repository path does not exist" in result.stderr


def test_validate_fixture_repo(temp_fixture_repo: Path) -> None:
    before_status = _git_status(temp_fixture_repo)
    result = runner.invoke(app, ["validate", "--repo", str(temp_fixture_repo)])
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    assert "Validation status: NO_ACTION_NEEDED" in result.stdout
    assert before_status == after_status


def test_repair_returns_no_action_needed_and_keeps_repo_clean(
    temp_fixture_repo: Path,
) -> None:
    before_status = _git_status(temp_fixture_repo)
    result = runner.invoke(
        app,
        [
            "repair",
            "--repo",
            str(temp_fixture_repo),
            "--goal",
            "Fix failing tests",
            "--model-profile",
            "fake",
            "--json",
        ],
    )
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    assert '"status": "NO_ACTION_NEEDED"' in result.stdout
    assert '"target_repo_changed": false' in result.stdout
    assert before_status == after_status


def test_runs_show_reports_summary_path(temp_fixture_repo: Path) -> None:
    validate_result = runner.invoke(app, ["validate", "--repo", str(temp_fixture_repo)])
    assert validate_result.exit_code == 0

    latest_run_id = validate_result.stdout.split("Run ID: ", maxsplit=1)[1].splitlines()[0]
    result = runner.invoke(app, ["runs", "show", latest_run_id])

    assert result.exit_code == 0
    assert "Summary Path:" in result.stdout


def test_runs_list_empty() -> None:
    result = runner.invoke(app, ["runs", "list"])

    assert result.exit_code == 0
    assert "No runs found." in result.stdout


def _git_status(repo: Path) -> str:
    if not repo.joinpath(".git").exists():
        return ""
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
