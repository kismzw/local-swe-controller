from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from local_swe_controller.models import RunStatus
from local_swe_controller.repair.controller import RepairController
from local_swe_controller.storage import ArtifactStore


class RecordingClient:
    def __init__(self, patches: list[str]) -> None:
        self.patches = patches
        self.prompts: list[str] = []
        self.calls = 0

    def generate_patch(self, *, system_prompt: str, user_prompt: str) -> str:
        del system_prompt
        self.prompts.append(user_prompt)
        patch = self.patches[min(self.calls, len(self.patches) - 1)]
        self.calls += 1
        return patch


def test_repair_controller_fixes_failing_repo(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    target_file = temp_fixture_repo / "src" / "example_pkg" / "__init__.py"
    broken_source = (
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n"
    )
    target_file.write_text(broken_source, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "add", "src/example_pkg/__init__.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "commit", "-m", "Introduce failing bug"],
        check=True,
        capture_output=True,
        text=True,
    )
    before_status = _git_status(temp_fixture_repo)

    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )

    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=1,
    )

    after_status = _git_status(temp_fixture_repo)

    assert result.status == RunStatus.SUCCESS
    assert result.target_repo_changed is False
    assert result.validation_report is not None
    assert result.validation_report.status == RunStatus.NO_ACTION_NEEDED
    assert result.validation_report.target_repo_changed is False
    assert result.patch_path is not None and result.patch_path.exists()
    assert before_status == after_status
    assert not (temp_fixture_repo / ".local-swe").exists()
    assert target_file.read_text(encoding="utf-8").endswith("return left - right\n")


def test_repair_controller_generates_tests_and_repairs_in_worktrees_only(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    target_file = temp_fixture_repo / "src" / "example_pkg" / "__init__.py"
    broken_source = (
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n"
    )
    target_file.write_text(broken_source, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "add", "src/example_pkg/__init__.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "commit", "-m", "Introduce failing bug"],
        check=True,
        capture_output=True,
        text=True,
    )
    before_status = _git_status(temp_fixture_repo)

    controller = _controller(project_root, tmp_path)
    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        generate_tests=True,
        max_iters=1,
    )

    after_status = _git_status(temp_fixture_repo)

    assert result.status == RunStatus.SUCCESS
    assert result.generated_test_patch_path is not None
    assert result.generated_test_patch_path.exists()
    assert result.generated_test_validation_report is not None
    assert "tests/test_regression.py" in result.generated_test_patch_path.read_text(
        encoding="utf-8"
    )
    assert result.generated_test_rejection_reasons == []
    assert before_status == after_status
    assert not (temp_fixture_repo / "tests" / "test_regression.py").exists()
    assert target_file.read_text(encoding="utf-8").endswith("return left - right\n")


def test_repair_controller_returns_no_action_needed(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )

    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=1,
    )

    assert result.status == RunStatus.NO_ACTION_NEEDED
    assert result.target_repo_changed is False
    assert not (temp_fixture_repo / ".local-swe").exists()


def test_generate_tests_disabled_keeps_existing_behavior(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
) -> None:
    target_file = temp_fixture_repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "add", "src/example_pkg/__init__.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "commit", "-m", "Introduce failing bug"],
        check=True,
        capture_output=True,
        text=True,
    )

    controller = _controller(project_root, tmp_path)
    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        generate_tests=False,
        max_iters=1,
    )

    assert result.status == RunStatus.SUCCESS
    assert result.generated_test_patch_path is None
    assert result.generated_test_validation_report is None


def test_format_failure_uses_deterministic_formatter_path_without_model(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    makefile = temp_fixture_repo / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8") + "\nformat:\n\truff format .\n",
        encoding="utf-8",
    )
    target_file = temp_fixture_repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add( left: int, right: int ) -> int:\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "add", "-A"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "commit", "-m", "Introduce formatting issue"],
        check=True,
        capture_output=True,
        text=True,
    )
    before_status = _git_status(temp_fixture_repo)
    client = RecordingClient([_source_patch_fix_add()])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix formatting",
        model_profile_name="fake",
        max_iters=1,
        selected_python=Path(sys.executable),
    )

    assert result.status == RunStatus.SUCCESS
    assert result.stop_reason == "formatter_validation_passed"
    assert result.patch_path is not None and result.patch_path.exists()
    assert "def add(left: int, right: int) -> int:" in result.patch_path.read_text(
        encoding="utf-8"
    )
    trace_events = [
        json.loads(line)
        for line in result.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    formatter_start = next(
        event for event in trace_events if event["event"] == "formatter_repair_started"
    )
    assert formatter_start["payload"]["command"] == ["make", "format"]
    assert client.calls == 0
    assert result.target_repo_changed is False
    assert _git_status(temp_fixture_repo) == before_status
    assert target_file.read_text(encoding="utf-8").startswith(
        '"""Example package for fixture repo."""'
    )


def test_repair_formatter_path_uses_selected_interpreter_for_direct_ruff_repo(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("direct-ruff-format")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n[tool.ruff]\nline-length = 88\n",
        encoding="utf-8",
    )
    (repo / "src" / "demo_pkg").mkdir(parents=True)
    (repo / "src" / "demo_pkg" / "__init__.py").write_text(
        "def add( left: int, right: int ) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_demo.py").write_text(
        "from demo_pkg import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    init_git_repo(repo)
    client = RecordingClient([_source_patch_fix_add()])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix formatting",
        model_profile_name="fake",
        max_iters=1,
        selected_python=Path(sys.executable),
    )

    assert result.status == RunStatus.SUCCESS
    trace_events = [
        json.loads(line)
        for line in result.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    formatter_start = next(
        event for event in trace_events if event["event"] == "formatter_repair_started"
    )
    assert formatter_start["payload"]["command"][:3] == [sys.executable, "-m", "ruff"]
    assert client.calls == 0
    assert _git_status(repo) == ""


def test_repair_stops_at_max_iterations(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("max-iters"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient([_readme_patch("one"), _readme_patch("two")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)

    result = controller.repair(
        repo_path=repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.STOPPED_BY_BUDGET
    assert result.stop_reason == "max_iterations_reached"
    assert result.iterations_attempted == 1
    assert result.candidates_attempted == 1
    assert _git_status(repo) == ""
    assert client.calls == 1


def test_repair_stops_on_repeated_identical_failure(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("repeat-failure"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient([_readme_patch("same"), _readme_patch("same")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)

    result = controller.repair(
        repo_path=repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert result.status == RunStatus.STOPPED_BY_REPEATED_FAILURE
    assert result.stop_reason == "repeated_identical_failure"
    assert result.iterations_attempted == 2
    assert result.candidates_attempted == 2
    assert _git_status(repo) == ""


def test_repair_stops_on_security_failure(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("security-failure"), "security", "security issue")
    init_git_repo(repo)
    controller = _controller(project_root, tmp_path)

    result = controller.repair(
        repo_path=repo,
        goal="Fix failing checks",
        model_profile_name="fake",
        max_iters=2,
    )

    assert result.status == RunStatus.STOPPED_BY_SECURITY
    assert result.stop_reason == "security_failure"
    assert result.candidates_attempted == 0
    assert _git_status(repo) == ""


def test_repair_stops_on_environment_failure(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(
        git_repo_factory("environment-failure"),
        "test",
        "ModuleNotFoundError: missing_dependency",
    )
    init_git_repo(repo)
    controller = _controller(project_root, tmp_path)

    result = controller.repair(
        repo_path=repo,
        goal="Fix failing checks",
        model_profile_name="fake",
        max_iters=2,
    )

    assert result.status == RunStatus.STOPPED_BY_ENVIRONMENT
    assert result.stop_reason == "environment_failure"
    assert result.candidates_attempted == 0
    assert _git_status(repo) == ""


def test_retry_prompt_differs_for_lint(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("lint-retry"), "lint", "ruff lint failed")
    init_git_repo(repo)
    client = RecordingClient([_readme_patch("first"), _readme_patch("second")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    controller.repair(
        repo_path=repo,
        goal="Fix lint",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert len(client.prompts) == 2
    assert "minimal lint-only patch" in client.prompts[1]


def test_retry_prompt_differs_for_typecheck(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("typecheck-retry"), "typecheck", "pyright type error")
    init_git_repo(repo)
    client = RecordingClient([_readme_patch("first"), _readme_patch("second")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    controller.repair(
        repo_path=repo,
        goal="Fix types",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert len(client.prompts) == 2
    assert "focus on the type errors" in client.prompts[1]


def test_retry_prompt_differs_for_test_failures(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("test-retry"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient([_readme_patch("first"), _readme_patch("second")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert len(client.prompts) == 2
    assert "focus on the semantic defect" in client.prompts[1]


def test_repair_tries_next_candidate_after_invalid_patch(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("second-candidate"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient(["not a patch", _readme_patch("second-attempt")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)

    result = controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=2,
    )

    assert result.status == RunStatus.STOPPED_BY_BUDGET
    assert result.candidates_attempted == 2
    assert client.calls == 2
    assert result.patch_path is not None
    assert result.patch_path.read_text(encoding="utf-8") == _readme_patch("second-attempt")


def test_test_failure_fallback_still_uses_model_with_bounded_prompt(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    target_file = temp_fixture_repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    (temp_fixture_repo / "data").mkdir()
    (temp_fixture_repo / "outputs").mkdir()
    (temp_fixture_repo / ".local-swe").mkdir()
    (temp_fixture_repo / ".venv").mkdir()
    (temp_fixture_repo / "analysis.ipynb").write_text("NOTEBOOK_MARKER", encoding="utf-8")
    (temp_fixture_repo / "data" / "huge.csv").write_text("CSV_MARKER\n" * 20000, encoding="utf-8")
    (temp_fixture_repo / "outputs" / "huge.txt").write_text(
        "OUTPUT_MARKER = 1\n" * 20000,
        encoding="utf-8",
    )
    (temp_fixture_repo / ".local-swe" / "trace.log").write_text(
        "LOCAL_SWE_MARKER\n" * 20000,
        encoding="utf-8",
    )
    (temp_fixture_repo / ".venv" / "site.py").write_text("VENV_MARKER\n" * 20000, encoding="utf-8")
    (temp_fixture_repo / "README.md").write_text("README_MARKER\n" * 20000, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(temp_fixture_repo),
            "commit",
            "-m",
            "Introduce failing bug with noisy repo",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    client = RecordingClient([_source_patch_fix_add()])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert client.calls == 1
    assert client.prompts
    prompt = client.prompts[0]
    assert "OUTPUT_MARKER" not in prompt
    assert "CSV_MARKER" not in prompt
    assert "LOCAL_SWE_MARKER" not in prompt
    assert "VENV_MARKER" not in prompt
    assert "NOTEBOOK_MARKER" not in prompt
    assert (len(prompt) + 3) // 4 < 8192
    assert _git_status(temp_fixture_repo) == ""


def test_generated_test_patch_rejected_if_it_skips_or_weakens_tests(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("generated-skip"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient([_generated_skip_test_patch(), _readme_patch("fixed")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        generate_tests=True,
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.STOPPED_BY_BUDGET
    assert result.generated_test_patch_path is not None
    assert result.generated_test_rejection_reasons
    assert any("skip/xfail" in reason for reason in result.generated_test_rejection_reasons)
    assert not (repo / "tests" / "test_regression.py").exists()
    assert _git_status(repo) == ""


def test_generated_test_patch_rejected_if_it_touches_forbidden_paths(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("generated-forbidden"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient([_generated_forbidden_test_patch(), _readme_patch("fixed")])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        generate_tests=True,
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.STOPPED_BY_BUDGET
    assert result.generated_test_patch_path is not None
    assert any("forbidden path" in reason for reason in result.generated_test_rejection_reasons)
    assert _git_status(repo) == ""


def _controller(project_root: Path, tmp_path: Path) -> RepairController:
    return RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(root=tmp_path / ".local-swe"),
    )


def _make_repair_repo(repo: Path, target: str, output: str) -> Path:
    (repo / "README.md").write_text("example\n", encoding="utf-8")
    makefile = (
        f"{target}:\n\tpython -c \"import sys; print({output!r}); sys.exit(1)\"\n"
    )
    (repo / "Makefile").write_text(makefile, encoding="utf-8")
    return repo


def _readme_patch(text: str) -> str:
    return (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-example\n"
        f"+{text}\n"
    )


def _source_patch_fix_add() -> str:
    return (
        "--- a/src/example_pkg/__init__.py\n"
        "+++ b/src/example_pkg/__init__.py\n"
        "@@ -2,4 +2,4 @@\n"
        " \n"
        " \n"
        " def add(left: int, right: int) -> int:\n"
        "-    return left - right\n"
        "+    return left + right\n"
    )


def _generated_skip_test_patch() -> str:
    return (
        "--- /dev/null\n"
        "+++ b/tests/test_regression.py\n"
        "@@ -0,0 +1,5 @@\n"
        "+import pytest\n"
        "+\n"
        "+\n"
        "+@pytest.mark.skip(reason='unsafe')\n"
        "+def test_regression() -> None:\n"
    )


def _generated_forbidden_test_patch() -> str:
    return (
        "--- /dev/null\n"
        "+++ b/.local-swe/policies/generated.py\n"
        "@@ -0,0 +1 @@\n"
        "+print('unsafe')\n"
    )


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
