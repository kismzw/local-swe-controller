from __future__ import annotations

import json
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
    assert "inspect" in result.stdout
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


def test_inspect_messy_script_fixture_reports_script_collection_and_no_mutation(
    temp_messy_script_repo: Path,
) -> None:
    before_status = _git_status(temp_messy_script_repo)

    result = runner.invoke(app, ["inspect", "--repo", str(temp_messy_script_repo)])

    after_status = _git_status(temp_messy_script_repo)
    assert result.exit_code == 0
    assert "Repo Kind: script_collection" in result.stdout
    assert "Readability Warnings:" in result.stdout
    assert "Script readability:" in result.stdout
    assert "Command Plan:" in result.stdout
    assert "bash -n old_script.sh" in result.stdout
    assert "Rscript -e parse(file='test.R')" in result.stdout
    assert before_status == after_status


def test_inspect_json_is_valid_for_messy_fixture(temp_messy_script_repo: Path) -> None:
    result = runner.invoke(app, ["inspect", "--repo", str(temp_messy_script_repo), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_kind"] == "script_collection"
    assert payload["repair_actionable_if_validation_passes"] is True
    assert payload["readability_warnings"]


def test_inspect_does_not_execute_scripts(tmp_path: Path) -> None:
    repo = tmp_path / "inspect-no-exec"
    repo.mkdir()
    marker = repo / "executed.txt"
    (repo / "run1.py").write_text(
        "from pathlib import Path\n"
        "def main() -> None:\n"
        f"    Path({str(marker)!r}).write_text('python')\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    (repo / "old_script.sh").write_text(
        f"echo shell > {marker}\n",
        encoding="utf-8",
    )
    (repo / "test.R").write_text(
        "write('r', 'executed.txt')\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", "--repo", str(repo)])

    assert result.exit_code == 0
    assert not marker.exists()


def test_inspect_package_fixture_still_works(temp_fixture_repo: Path) -> None:
    result = runner.invoke(app, ["inspect", "--repo", str(temp_fixture_repo), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command_plan"]["test"]
    assert payload["command_plan"]["lint"]
    assert payload["detected_languages"]


def test_inspect_workflow_fixture_shows_build_smoke_and_e2e(tmp_path: Path) -> None:
    repo = tmp_path / "inspect-workflow"
    repo.mkdir()
    (repo / "Makefile").write_text(
        "build:\n\t@echo build\nsmoke:\n\t@echo smoke\ne2e:\n\t@echo e2e\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", "--repo", str(repo), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_kind"] == "workflow_repo"
    assert payload["workflow_sources"]
    assert ["make", "build"] in payload["command_plan"]["build"]
    assert ["make", "smoke"] in payload["command_plan"]["smoke"]
    assert ["make", "e2e"] in payload["command_plan"]["e2e"]


def test_inspect_script_workflow_fixture_shows_ordered_stages(
    temp_script_workflow_repo: Path,
) -> None:
    result = runner.invoke(app, ["inspect", "--repo", str(temp_script_workflow_repo), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_kind"] == "workflow_repo"
    assert "implicit_script_chain" in payload["workflow_sources"]
    assert payload["ordered_script_stages"] == ["build", "train", "test"]
    assert payload["command_plan"]["build"]
    assert payload["command_plan"]["smoke"]
    assert payload["command_plan_details"]["build"][0]["severity"] == "hard"
    assert all(
        command["severity"] == "soft"
        for command in payload["command_plan_details"]["smoke"]
    )
    assert payload["planning_warnings"]


def test_inspect_text_shows_command_severity(
    temp_script_workflow_repo: Path,
) -> None:
    result = runner.invoke(app, ["inspect", "--repo", str(temp_script_workflow_repo)])

    assert result.exit_code == 0
    assert "[hard] python -m py_compile" in result.stdout
    assert "[soft] python DataPipe/Build_tiles_dataset.py --help" in result.stdout


def test_validate_missing_python_fails_clearly(temp_fixture_repo: Path) -> None:
    result = runner.invoke(
        app,
        ["validate", "--repo", str(temp_fixture_repo), "--python", "/does/not/exist/python"],
    )

    assert result.exit_code == 1
    assert "Selected Python interpreter does not exist" in result.stderr


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
