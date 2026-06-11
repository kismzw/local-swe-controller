from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from local_swe_controller.models import RunStatus
from local_swe_controller.repair.controller import RepairController, RepairResult
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


def test_repair_normalizes_wrapped_patch_artifact_and_keeps_raw_response(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("wrapped-patch"), "test", "FAILED still broken")
    init_git_repo(repo)
    wrapped_patch = (
        "Here is the patch.\n"
        "```diff\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-broken\n"
        "+fixed\n"
        "```\n"
    )
    client = RecordingClient([wrapped_patch])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert result.patch_path is not None
    assert result.patch_path.read_text(encoding="utf-8").startswith("--- a/README.md")
    assert "```diff" not in result.patch_path.read_text(encoding="utf-8")
    assert result.patch_path.with_suffix(".raw.txt").read_text(encoding="utf-8") == wrapped_patch


def test_repair_feeds_git_apply_failure_into_retry_prompt(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("git-apply-retry"), "test", "FAILED still broken")
    init_git_repo(repo)
    client = RecordingClient(
        [
            (
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-does not match\n"
                "+fixed\n"
            ),
            _readme_patch("fixed"),
        ]
    )

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert result.status == RunStatus.STOPPED_BY_BUDGET
    assert len(client.prompts) == 2
    assert "Your previous patch could not be applied by git." in client.prompts[1]
    assert "Previous rejected patch:" in client.prompts[1]
    assert "Current target file content:" in client.prompts[1]
    assert "FILE: README.md" in client.prompts[1]
    assert "Compact repo summary:" not in client.prompts[1]
    assert "Relevant repository files:" not in client.prompts[1]


def test_repair_can_recover_from_git_apply_failure_with_patch_repair_prompt(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = temp_fixture_repo
    target_file = repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Introduce failing source"],
        check=True,
        capture_output=True,
        text=True,
    )
    client = RecordingClient(
        [
            (
                "--- a/src/example_pkg/__init__.py\n"
                "+++ b/src/example_pkg/__init__.py\n"
                "@@ -2,4 +2,4 @@\n"
                " \n"
                " \n"
                " def add(left: int, right: int) -> int:\n"
                "-    return left + right\n"
                "+    return left + left\n"
            ),
            _source_patch_fix_add(),
        ]
    )

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)
    result = controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        max_iters=2,
        max_candidates=1,
    )

    assert result.status == RunStatus.SUCCESS
    assert len(client.prompts) == 2
    assert "Mode: patch_repair" in client.prompts[1]
    assert "Patch repair evidence:" in client.prompts[1]
    assert "FILE: src/example_pkg/__init__.py" in client.prompts[1]
    assert "Relevant repository files:" not in client.prompts[1]


def test_patch_repair_prompt_stays_within_context_window(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("patch-repair-budget"), "test", "FAILED still broken")
    readme = repo / "README.md"
    readme.write_text("example\n" + ("section\n" * 20000), encoding="utf-8")
    init_git_repo(repo)

    controller = _controller(project_root, tmp_path)
    policy = controller.policy_compiler.compile(repo)
    report = controller.validation_runner.validate(repo, policy=policy)
    route = controller.router.resolve("patch_generation", profile_name="fake")
    patch_path = tmp_path / "rejected.patch"
    patch_path.write_text(
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-does not match\n"
        "+fixed\n"
        + ("# extra context\n" * 5000),
        encoding="utf-8",
    )
    last_result = RepairResult(
        run_id="retry-budget",
        repo_root=repo,
        goal="Fix tests",
        status=RunStatus.PATCH_REJECTED,
        summary="git apply failed",
        model_profile=route.profile_name,
        artifact_dir=tmp_path,
        policy_path=project_root / "configs" / "default_policy.yaml",
        trace_path=tmp_path / "trace.jsonl",
        patch_path=patch_path,
        baseline_report=report,
        rejection_reasons=[
            "Your previous patch could not be applied by git. git apply reported: "
            + ("error: corrupt patch at line 22. " * 2000)
        ],
        stop_reason="git_apply_check_failed",
    )

    prompt = controller._build_prompt(
        repo_root=repo,
        goal="Fix tests",
        report=report,
        iteration=2,
        profile=route.profile,
        policy=policy,
        compact_context=True,
        last_result=last_result,
        system_prompt="system",
    )

    selection = controller.router.resolve(
        "patch_generation",
        profile_name="fake",
        prompt_text=prompt,
        system_prompt="system",
    )

    assert "Mode: patch_repair" in prompt
    assert "Patch repair evidence:" in prompt
    assert selection.estimated_input_tokens is not None
    assert route.profile.context_window is not None
    assert (
        selection.estimated_input_tokens + route.profile.max_output_tokens
        <= route.profile.context_window
    )


def test_repeated_git_apply_rejection_drops_previous_invalid_patch_anchor(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = _make_repair_repo(git_repo_factory("patch-repeat-anchor"), "test", "FAILED still broken")
    init_git_repo(repo)

    controller = _controller(project_root, tmp_path)
    policy = controller.policy_compiler.compile(repo)
    report = controller.validation_runner.validate(repo, policy=policy)
    route = controller.router.resolve("patch_generation", profile_name="fake")
    patch_text = (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-does not match\n"
        "+fixed\n"
    )
    patch_path = tmp_path / "repeat.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    last_result = RepairResult(
        run_id="repeat-anchor",
        repo_root=repo,
        goal="Fix tests",
        status=RunStatus.PATCH_REJECTED,
        summary="git apply failed",
        model_profile=route.profile_name,
        artifact_dir=tmp_path,
        policy_path=project_root / "configs" / "default_policy.yaml",
        trace_path=tmp_path / "trace.jsonl",
        patch_path=patch_path,
        baseline_report=report,
        rejection_reasons=[
            "Your previous patch could not be applied by git. git apply reported: "
            "error: corrupt patch at line 4."
        ],
        stop_reason="git_apply_check_failed",
    )

    prompt = controller._build_prompt(
        repo_root=repo,
        goal="Fix tests",
        report=report,
        iteration=3,
        profile=route.profile,
        policy=policy,
        compact_context=True,
        last_result=last_result,
        system_prompt="system",
        repeated_rejected_patch_count=2,
    )

    assert "The same rejected patch pattern has repeated 2 times" in prompt
    assert "The previous invalid patch has already been repeated. Do not reuse it." in prompt
    assert "Previous rejected patch:" not in prompt
    assert "FILE: README.md" in prompt


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
    assert result.patch_path.read_text(encoding="utf-8") == (
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-example\n"
        "+second-attempt\n"
    )


def test_repair_until_success_ignores_iteration_budget(
    project_root: Path,
    temp_fixture_repo: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = temp_fixture_repo
    target_file = repo / "src" / "example_pkg" / "__init__.py"
    target_file.write_text(
        '"""Example package for fixture repo."""\n\n\n'
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Introduce failing source"],
        check=True,
        capture_output=True,
        text=True,
    )
    client = RecordingClient(["not a patch", _source_patch_fix_add()])

    controller = _controller(project_root, tmp_path)
    monkeypatch.setattr(controller, "_build_client", lambda *_args, **_kwargs: client)

    result = controller.repair(
        repo_path=repo,
        goal="Fix tests",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
        until_success=True,
    )

    assert result.status == RunStatus.SUCCESS
    assert result.iterations_attempted == 2
    assert result.candidates_attempted == 2
    assert client.calls == 2


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


def test_implicit_workflow_prompt_prefers_readme_only_and_forbids_unsafe_cli_boilerplate(
    project_root: Path,
    temp_script_workflow_repo: Path,
    tmp_path: Path,
) -> None:
    controller = _controller(project_root, tmp_path)
    policy = controller.policy_compiler.compile(temp_script_workflow_repo)
    report = controller.validation_runner.validate(temp_script_workflow_repo, policy=policy)
    route = controller.router.resolve("patch_generation", profile_name="fake")

    prompt = controller._build_prompt(
        repo_root=temp_script_workflow_repo,
        goal="Improve workflow readability without changing behavior",
        report=report,
        iteration=1,
        profile=route.profile,
        policy=policy,
    )

    assert "First candidate should prefer README or documentation-only workflow fixes" in prompt
    assert "Do not add argparse to library, helper, or model modules" in prompt
    assert "Do not add parse_args() at module import time" in prompt
    assert 'parser.add_argument("--help"' in prompt


def test_implicit_workflow_repo_uses_deterministic_readme_repair_first(
    project_root: Path,
    temp_script_workflow_repo: Path,
    tmp_path: Path,
) -> None:
    controller = _controller(project_root, tmp_path)

    result = controller.repair(
        repo_path=temp_script_workflow_repo,
        goal="Improve workflow readability without changing behavior",
        model_profile_name="fake",
        max_iters=1,
        max_candidates=1,
    )

    assert result.status == RunStatus.SUCCESS
    assert result.stop_reason == "workflow_readme_validation_passed"
    assert result.patch_path is not None
    patch_text = result.patch_path.read_text(encoding="utf-8")
    assert "Local-SWE Workflow Notes" in patch_text
    assert "Inferred stage order" in patch_text
    assert "- Build:" in patch_text
    assert "- Train:" in patch_text


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
