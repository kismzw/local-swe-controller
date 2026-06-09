from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_swe_controller.benchmark import BenchmarkCase, BenchmarkRunner
from local_swe_controller.cli import app

runner = CliRunner()


def test_benchmark_schema_validation_requires_goal_for_repair() -> None:
    with pytest.raises(ValueError, match="repair benchmark cases require a goal"):
        BenchmarkCase.model_validate(
            {
                "name": "broken-repair",
                "repo": ".",
                "mode": "repair",
                "model_profile": "fake",
                "expected_status": "NO_ACTION_NEEDED",
            }
        )


def test_fixture_benchmark_passes(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    case_path = _write_case(
        tmp_path / "fixture-bench.yaml",
        repo=temp_fixture_repo,
        mode="validate",
        expected_status="NO_ACTION_NEEDED",
    )
    before_status = _git_status(temp_fixture_repo)

    result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    payload = _load_benchmark_result(result.stdout)
    assert payload["passed"] == 1
    assert payload["failed"] == 0
    assert payload["total"] == 1
    assert before_status == after_status


def test_failed_expectation_is_reported(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    case_path = _write_case(
        tmp_path / "wrong-expectation.yaml",
        repo=temp_fixture_repo,
        mode="validate",
        expected_status="SUCCESS",
    )

    result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])

    assert result.exit_code == 1
    payload = _load_benchmark_result(result.stdout)
    assert payload["failed"] == 1
    assert payload["cases"][0]["expected_status_matched"] is False


def test_invalid_case_is_handled_clearly(project_root: Path, tmp_path: Path) -> None:
    case_path = tmp_path / "invalid.yaml"
    case_path.write_text(
        "name: invalid\nrepo: ./missing\nmode: repair\nexpected_status: NO_ACTION_NEEDED\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])

    assert result.exit_code == 1
    payload = _load_benchmark_result(result.stdout)
    assert payload["failed"] == 1
    assert "repair benchmark cases require a goal" in payload["cases"][0]["errors"][0]


def test_benchmark_does_not_require_network(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = _write_case(
        tmp_path / "offline.yaml",
        repo=temp_fixture_repo,
        mode="validate",
        expected_status="NO_ACTION_NEEDED",
    )

    def _unexpected_network(*_args, **_kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr("urllib.request.urlopen", _unexpected_network)

    result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])

    assert result.exit_code == 0


def test_benchmark_keeps_target_repo_unchanged(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    case_path = _write_case(
        tmp_path / "repair-case.yaml",
        repo=temp_fixture_repo,
        mode="repair",
        expected_status="NO_ACTION_NEEDED",
        goal="Fix failing tests",
        model_profile="fake",
    )
    before_status = _git_status(temp_fixture_repo)

    result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    payload = _load_benchmark_result(result.stdout)
    case = payload["cases"][0]
    assert case["target_repo_changed"] is False
    assert case["repo_status_changed"] is False
    assert before_status == after_status


def test_bench_show_reads_saved_result(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    case_path = _write_case(
        tmp_path / "show-case.yaml",
        repo=temp_fixture_repo,
        mode="validate",
        expected_status="NO_ACTION_NEEDED",
    )
    run_result = runner.invoke(app, ["bench", "run", "--cases", str(case_path)])
    payload = _load_benchmark_result(run_result.stdout)

    show_result = runner.invoke(app, ["bench", "show", payload["bench_run_id"]])

    assert show_result.exit_code == 0
    assert payload["bench_run_id"] in show_result.stdout
    assert "Status Counts:" in show_result.stdout


def test_non_fake_repair_benchmark_is_rejected(project_root: Path, tmp_path: Path) -> None:
    repo = project_root / "examples" / "fixture_python_repo"
    case_path = _write_case(
        tmp_path / "networked-repair.yaml",
        repo=repo,
        mode="repair",
        expected_status="NO_ACTION_NEEDED",
        goal="Fix failing tests",
        model_profile="main_patch_model",
    )
    benchmark_runner = BenchmarkRunner(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
    )
    result = benchmark_runner.run(cases_glob=str(case_path))

    assert result.failed == 1
    assert "must use a fake model profile" in result.cases[0].errors[0]


def _write_case(
    path: Path,
    *,
    repo: Path,
    mode: str,
    expected_status: str,
    goal: str | None = None,
    model_profile: str | None = None,
) -> Path:
    lines = [
        "name: test-case",
        f"repo: {repo}",
        f"mode: {mode}",
        f"expected_status: {expected_status}",
    ]
    if goal is not None:
        lines.append(f"goal: {goal}")
    if model_profile is not None:
        lines.append(f"model_profile: {model_profile}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_benchmark_result(stdout: str) -> dict[str, object]:
    artifact_prefix = "Artifacts: "
    artifact_dir = Path(stdout.split(artifact_prefix, maxsplit=1)[1].splitlines()[0].strip())
    return json.loads((artifact_dir / "result.json").read_text(encoding="utf-8"))


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
