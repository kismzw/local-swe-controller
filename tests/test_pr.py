from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from local_swe_controller.cli import app
from local_swe_controller.pr import GitHubClient

runner = CliRunner()


def test_pr_summary_generated_for_repair_run(
    project_root: Path,
    temp_fixture_repo: Path,
) -> None:
    before_status = _git_status(temp_fixture_repo)
    run_id = _create_patch_run(project_root, temp_fixture_repo)

    result = runner.invoke(app, ["runs", "pr-summary", run_id])
    after_status = _git_status(temp_fixture_repo)

    assert result.exit_code == 0
    prefix = "PR summary written to "
    summary_path = Path(result.stdout.strip().split(prefix, maxsplit=1)[1])
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "## Overview" in content
    assert "## Final Status" in content
    assert "## Failure Class" in content
    assert "## Commands Run" in content
    assert "## Validation Evidence" in content
    assert "## Patch Path" in content
    assert "## Generated Test Path" in content
    assert "## Security Findings" in content
    assert "## Target Repo Changed" in content
    assert "## Manual Apply Instructions" in content
    assert "`false`" in content
    assert before_status == after_status


def test_pr_summary_missing_run_id_is_handled() -> None:
    result = runner.invoke(app, ["runs", "pr-summary", "missing-run-id"])

    assert result.exit_code == 1
    assert "Run not found: missing-run-id" in result.stderr


def test_pr_summary_requires_explicit_create_pr_command_and_makes_no_network_calls(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
) -> None:
    run_id = _create_patch_run(project_root, temp_fixture_repo)

    def _unexpected_network(*_args, **_kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr("local_swe_controller.pr.request.urlopen", _unexpected_network)

    result = runner.invoke(app, ["runs", "pr-summary", run_id])

    assert result.exit_code == 0


def test_create_pr_requires_token(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
) -> None:
    run_id = _create_patch_run(project_root, temp_fixture_repo)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = runner.invoke(
        app,
        [
            "runs",
            "create-pr",
            run_id,
            "--repo-owner",
            "octocat",
            "--repo-name",
            "hello-world",
            "--base",
            "main",
            "--head",
            "repair/fix-tests",
        ],
    )

    assert result.exit_code == 1
    assert "GitHub token environment variable 'GITHUB_TOKEN' is required" in result.stderr


def test_create_pr_requires_head_branch_when_patch_exists(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
) -> None:
    run_id = _create_patch_run(project_root, temp_fixture_repo)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    result = runner.invoke(
        app,
        [
            "runs",
            "create-pr",
            run_id,
            "--repo-owner",
            "octocat",
            "--repo-name",
            "hello-world",
            "--base",
            "main",
        ],
    )

    assert result.exit_code == 1
    assert "Head branch must be provided explicitly" in result.stderr


def test_create_pr_uses_mocked_github_api(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
) -> None:
    run_id = _create_patch_run(project_root, temp_fixture_repo)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: dict[str, object] = {}

    def _mock_request_json(self, *, method: str, path: str, payload: dict[str, object]):
        del self
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {
            "number": 42,
            "html_url": "https://github.com/octocat/hello-world/pull/42",
            "title": payload["title"],
        }

    monkeypatch.setattr(GitHubClient, "_request_json", _mock_request_json)

    result = runner.invoke(
        app,
        [
            "runs",
            "create-pr",
            run_id,
            "--repo-owner",
            "octocat",
            "--repo-name",
            "hello-world",
            "--base",
            "main",
            "--head",
            "repair/fix-tests",
        ],
    )

    assert result.exit_code == 0
    assert "PR created: #42" in result.stdout
    assert captured["method"] == "POST"
    assert captured["path"] == "/repos/octocat/hello-world/pulls"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["head"] == "repair/fix-tests"
    assert payload["base"] == "main"
    assert "local-swe" in str(payload["title"])


def test_create_pr_records_github_response_artifact(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
) -> None:
    run_id = _create_patch_run(project_root, temp_fixture_repo)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    def _mock_request_json(self, *, method: str, path: str, payload: dict[str, object]):
        del self, method, path, payload
        return {
            "number": 7,
            "html_url": "https://github.com/octocat/hello-world/pull/7",
            "title": "local-swe: Fix failing tests",
        }

    monkeypatch.setattr(GitHubClient, "_request_json", _mock_request_json)

    result = runner.invoke(
        app,
        [
            "runs",
            "create-pr",
            run_id,
            "--repo-owner",
            "octocat",
            "--repo-name",
            "hello-world",
            "--base",
            "main",
            "--head",
            "repair/fix-tests",
        ],
    )

    assert result.exit_code == 0
    response_prefix = "GitHub response: "
    response_path = Path(
        result.stdout.strip().split(response_prefix, maxsplit=1)[1].splitlines()[0]
    )
    assert response_path.exists()
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert payload["number"] == 7


def _create_patch_run(project_root: Path, repo: Path) -> str:
    target_file = repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "src/example_pkg/__init__.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Introduce failing bug"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = runner.invoke(
        app,
        [
            "repair",
            "--repo",
            str(repo),
            "--goal",
            "Fix failing tests",
            "--model-profile",
            "fake",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    return str(payload["run_id"])


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
