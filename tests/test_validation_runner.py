from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from local_swe_controller.config import PatchPolicyConfig, PatchRejectByDefaultConfig
from local_swe_controller.models import (
    CommandResult,
    CommandSeverity,
    CommandSpec,
    FailureClass,
    RunStatus,
)
from local_swe_controller.policy.compiler import PolicyCompiler
from local_swe_controller.policy.schema import CompiledPolicy
from local_swe_controller.validation.runner import ValidationRunner


def test_validate_passing_fixture_repo(project_root: Path, temp_fixture_repo: Path) -> None:
    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")

    report = runner.validate(temp_fixture_repo)

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert report.failure_class is None
    assert report.target_repo_changed is False
    assert report.commands
    assert report.artifact_dir is not None
    assert (report.artifact_dir / "validation-report.json").exists()
    assert not (temp_fixture_repo / ".local-swe").exists()


def test_validate_uses_selected_python_interpreter(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("selected-python")
    (repo / "README.md").write_text("selected python\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "selected-python-policy.json",
        repo,
        test_commands=[
            CommandSpec(command=["python", "-c", "import sys; print(sys.executable)"])
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path, selected_python=Path(sys.executable))

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert report.commands[0].spec.command == [
        sys.executable,
        "-c",
        "import sys; print(sys.executable)",
    ]
    assert report.commands[0].stdout.strip() == sys.executable


def test_validate_failing_repo(project_root: Path, git_repo_factory, init_git_repo) -> None:
    repo = git_repo_factory("failing-repo")
    (repo / "Makefile").write_text("test:\n\tfalse\n", encoding="utf-8")
    init_git_repo(repo)

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo)

    assert report.status == RunStatus.BASELINE_FAILED
    assert report.failure_class == FailureClass.TEST


def test_lint_failure_classification(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
) -> None:
    repo = git_repo_factory("lint-failure")
    (repo / "Makefile").write_text(
        "lint:\n\tpython -c \"import sys; print('ruff lint failed'); sys.exit(1)\"\n",
        encoding="utf-8",
    )
    init_git_repo(repo)

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo)

    assert report.failure_class == FailureClass.LINT


def test_test_failure_classification(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
) -> None:
    repo = git_repo_factory("test-failure")
    (repo / "Makefile").write_text(
        "test:\n\tpython -c \"import sys; print('FAILED test_example'); sys.exit(1)\"\n",
        encoding="utf-8",
    )
    init_git_repo(repo)

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo)

    assert report.failure_class == FailureClass.TEST


def test_timeout_classification(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("timeout-repo")
    (repo / "README.md").write_text("timeout\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "timeout-policy.json",
        repo,
        test_commands=[
            CommandSpec(
                command=[sys.executable, "-c", "import time; time.sleep(2)"],
                timeout_seconds=1,
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.RESOURCE_EXHAUSTION
    assert report.status == RunStatus.STOPPED_BY_ENVIRONMENT


def test_timeout_per_command_overrides_policy_default(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("timeout-override")
    (repo / "README.md").write_text("timeout override\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "timeout-override-policy.json",
        repo,
        test_commands=[
            CommandSpec(
                command=[sys.executable, "-c", "import time; time.sleep(2)"],
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path, timeout_per_command=1)

    assert report.failure_class == FailureClass.RESOURCE_EXHAUSTION
    assert report.commands[-1].timed_out is True


def test_policy_violation(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("policy-violation")
    (repo / "README.md").write_text("policy\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "policy-violation.json",
        repo,
        lint_commands=[CommandSpec(command=["rm", "-rf", "/tmp/forbidden"])],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.POLICY_VIOLATION
    assert report.status == RunStatus.STOPPED_BY_POLICY
    assert report.target_repo_changed is False


def test_validation_blocks_command_cwd_escape(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("cwd-escape")
    (repo / "README.md").write_text("escape\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "cwd-escape-policy.json",
        repo,
        lint_commands=[
            CommandSpec(
                command=[sys.executable, "-c", "print('escape')"],
                cwd="/tmp",
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.POLICY_VIOLATION
    assert report.status == RunStatus.STOPPED_BY_POLICY


def test_validation_keeps_target_repo_unchanged(
    project_root: Path,
    temp_fixture_repo: Path,
) -> None:
    before_status = subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(temp_fixture_repo)

    after_status = subprocess.run(
        ["git", "-C", str(temp_fixture_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert report.target_repo_changed is False
    assert before_status == after_status
    assert not (temp_fixture_repo / ".local-swe").exists()


def test_validation_environment_summary_mentions_missing_modules_and_manual_setup(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("environment-diagnosis")
    (repo / "src" / "demo_pkg").mkdir(parents=True)
    (repo / "src" / "demo_pkg" / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "environment-diagnosis-policy.json",
        repo,
        test_commands=[
            CommandSpec(
                command=[
                    "python",
                    "-c",
                    "import missing_dep; import demo_pkg",
                ]
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path, selected_python=Path(sys.executable))

    assert report.failure_class == FailureClass.ENVIRONMENT
    assert report.summary is not None
    assert "Missing modules: missing_dep" in report.summary
    assert "Suggested manual setup:" in report.summary


def test_validate_does_not_write_artifacts_inside_target_repo_when_cwd_is_target_repo(
    project_root: Path,
    temp_fixture_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("LOCAL_SWE_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(temp_fixture_repo)

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(temp_fixture_repo)

    assert report.artifact_dir is not None
    assert report.artifact_dir.is_relative_to(fake_home / ".local-swe")
    assert not (temp_fixture_repo / ".local-swe").exists()


def test_pip_audit_findings_map_to_security(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("pip-audit-findings")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "pip-audit-policy.json",
        repo,
        security_commands=[
            CommandSpec(
                command=["uv", "run", "pip-audit", "--format", "json"],
                optional=True,
                tool_name="pip-audit",
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                CommandSpec(
                    command=["uv", "run", "pip-audit", "--format", "json"],
                    optional=True,
                    tool_name="pip-audit",
                ),
                stdout=(
                    '{"dependencies":[{"name":"demo","vulns":[{"id":"PYSEC-2024-1"}]}]}'
                ),
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.SECURITY
    assert report.status == RunStatus.STOPPED_BY_SECURITY


def test_osv_scanner_findings_map_to_security(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("osv-findings")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "osv-policy.json",
        repo,
        security_commands=[
            CommandSpec(
                command=["osv-scanner", "scan", "-r", ".", "--format", "json"],
                optional=True,
                tool_name="osv-scanner",
            )
        ],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                CommandSpec(
                    command=["osv-scanner", "scan", "-r", ".", "--format", "json"],
                    optional=True,
                    tool_name="osv-scanner",
                ),
                stdout='{"results":[{"packages":[{"package":{"name":"demo"},"vulnerabilities":[{"id":"CVE-2024-1234"}]}]}]}',
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.SECURITY


def test_detect_secrets_findings_map_to_security(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("detect-secrets-findings")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    spec = CommandSpec(
        command=["detect-secrets", "scan", "--all-files"],
        optional=True,
        tool_name="detect-secrets",
    )
    policy_path = _write_policy(
        tmp_path / "detect-secrets-policy.json",
        repo,
        security_commands=[spec],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                spec,
                stdout='{"results":{"src/app.py":[{"type":"Secret Keyword"}]}}',
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.SECURITY


def test_reuse_failure_maps_to_security(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("reuse-failure")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    spec = CommandSpec(command=["reuse", "lint"], optional=True, tool_name="reuse")
    policy_path = _write_policy(
        tmp_path / "reuse-policy.json",
        repo,
        security_commands=[spec],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                spec,
                exit_code=1,
                stderr="Missing license information for src/app.py",
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.SECURITY


def test_optional_scanner_unavailable_hard_gate_fails_environment(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("missing-scanner-hard")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    spec = CommandSpec(command=["reuse", "lint"], optional=True, tool_name="reuse")
    policy_path = _write_policy(
        tmp_path / "missing-scanner-hard-policy.json",
        repo,
        security_commands=[spec],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                spec,
                exit_code=127,
                stderr="reuse: command not found",
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.failure_class == FailureClass.ENVIRONMENT
    assert "unavailable" in (report.summary or "")


def test_optional_scanner_unavailable_soft_gate_warns_and_skips(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("missing-scanner-soft")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    spec = CommandSpec(command=["reuse", "lint"], optional=True, tool_name="reuse")
    policy_path = _write_soft_security_policy(
        tmp_path / "missing-scanner-soft-policy.json",
        repo,
        security_commands=[spec],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                spec,
                exit_code=127,
                stderr="reuse: command not found",
                category="security",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert report.failure_class is None
    assert any("Optional scanner 'reuse' is unavailable" in warning for warning in report.warnings)


def test_validation_runs_build_smoke_and_e2e_categories(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("workflow-categories")
    (repo / "README.md").write_text("workflow\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "workflow-policy.json",
        repo,
        build_commands=[CommandSpec(command=["python", "-c", "print('build')"])],
        smoke_commands=[CommandSpec(command=["python", "-c", "print('smoke')"])],
        e2e_commands=[CommandSpec(command=["python", "-c", "print('e2e')"])],
    )

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo, policy_path=policy_path)

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert [command.category for command in report.commands] == ["build", "smoke", "e2e"]


def test_validate_messy_script_fixture_reports_soft_readability_warnings(
    project_root: Path,
    temp_messy_script_repo: Path,
) -> None:
    before_status = subprocess.run(
        ["git", "-C", str(temp_messy_script_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(temp_messy_script_repo)

    after_status = subprocess.run(
        ["git", "-C", str(temp_messy_script_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert any(
        warning.startswith("Script readability:") for warning in report.warnings
    )
    assert any(
        command.spec.command == ["bash", "-n", "old_script.sh"] for command in report.commands
    )
    assert any(
        command.spec.command[:3] == ["python", "-m", "py_compile"]
        for command in report.commands
    )
    assert any(
        "parse(file='test.R')" in " ".join(command.spec.command)
        for command in report.commands
    )
    assert all(command.spec.command[:2] != ["bash", "old_script.sh"] for command in report.commands)
    assert before_status == after_status


def test_validate_script_workflow_fixture_uses_syntax_and_help_only(
    project_root: Path,
    temp_script_workflow_repo: Path,
) -> None:
    before_status = subprocess.run(
        ["git", "-C", str(temp_script_workflow_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(temp_script_workflow_repo)

    after_status = subprocess.run(
        ["git", "-C", str(temp_script_workflow_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert any(
        warning.startswith("Workflow planning:") for warning in report.warnings
    )
    assert any(
        command.spec.command[:3] == ["python", "-m", "py_compile"]
        for command in report.commands
    )
    assert any(command.spec.command[-1] == "--help" for command in report.commands)
    assert all(
        not (
            command.spec.command[0] == "python"
            and command.spec.command[1].endswith("_Train.py")
            and "--help" not in command.spec.command
        )
        for command in report.commands
    )
    assert all(
        not (
            command.spec.command[0] == "python"
            and command.spec.command[1].endswith("_Test.py")
            and "--help" not in command.spec.command
        )
        for command in report.commands
    )
    assert before_status == after_status


def test_implicit_workflow_missing_dependency_help_is_soft_warning(
    project_root: Path,
    temp_script_workflow_repo: Path,
) -> None:
    before_status = subprocess.run(
        ["git", "-C", str(temp_script_workflow_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    compiler = PolicyCompiler(project_root / "configs" / "default_policy.yaml")
    policy = compiler.compile(temp_script_workflow_repo)
    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")

    scripted_results = [
        _result(policy.build_commands[0], category="build"),
        _result(
            policy.smoke_commands[0],
            exit_code=1,
            stderr="ModuleNotFoundError: No module named 'h5py'",
            category="smoke",
        ),
        *[_result(spec, category="smoke") for spec in policy.smoke_commands[1:]],
    ]
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=scripted_results
    )

    report = runner.validate(temp_script_workflow_repo, policy=policy)

    after_status = subprocess.run(
        ["git", "-C", str(temp_script_workflow_repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert report.failure_class is None
    assert any("dependency 'h5py' is missing" in warning for warning in report.warnings)
    assert before_status == after_status


def test_implicit_workflow_syntax_error_remains_hard_failure(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
) -> None:
    repo = git_repo_factory("script-workflow-syntax-error")
    (repo / "DataPipe").mkdir(parents=True)
    (repo / "DownStream").mkdir(parents=True)
    (repo / "README.md").write_text("workflow\n", encoding="utf-8")
    (repo / "DataPipe" / "Build_embedded_dataset.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )
    (repo / "DownStream" / "MTL_Train.py").write_text(
        "import argparse\n"
        "def main() -> None:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.parse_args()\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    init_git_repo(repo)
    before_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report = runner.validate(repo)

    after_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert report.status == RunStatus.BASELINE_FAILED
    assert report.failure_class == FailureClass.BUILD
    assert before_status == after_status


def test_package_repo_environment_failure_remains_hard(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("package-smoke-environment")
    (repo / "README.md").write_text("package\n", encoding="utf-8")
    init_git_repo(repo)
    policy_path = _write_policy(
        tmp_path / "package-smoke-environment-policy.json",
        repo,
        repo_kind="package_repo",
        smoke_commands=[
            CommandSpec(
                command=["python", "tool.py", "--help"],
                severity=CommandSeverity.HARD,
            )
        ],
    )
    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    runner.command_runner_factory = lambda **kwargs: FakeCommandRunner(  # type: ignore[assignment]
        results=[
            _result(
                CommandSpec(command=["python", "tool.py", "--help"]),
                exit_code=1,
                stderr="ModuleNotFoundError: No module named 'h5py'",
                category="smoke",
            )
        ]
    )

    report = runner.validate(repo, policy_path=policy_path)

    assert report.status == RunStatus.STOPPED_BY_ENVIRONMENT
    assert report.failure_class == FailureClass.ENVIRONMENT


def _write_policy(
    path: Path,
    repo_root: Path,
    *,
    repo_kind: str = "unknown",
    format_commands: list[CommandSpec] | None = None,
    lint_commands: list[CommandSpec] | None = None,
    typecheck_commands: list[CommandSpec] | None = None,
    build_commands: list[CommandSpec] | None = None,
    test_commands: list[CommandSpec] | None = None,
    smoke_commands: list[CommandSpec] | None = None,
    e2e_commands: list[CommandSpec] | None = None,
    security_commands: list[CommandSpec] | None = None,
) -> Path:
    policy = CompiledPolicy(
        repo_root=repo_root.resolve(),
        repo_kind=repo_kind,
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=format_commands or [],
        lint_commands=lint_commands or [],
        typecheck_commands=typecheck_commands or [],
        build_commands=build_commands or [],
        test_commands=test_commands or [],
        smoke_commands=smoke_commands or [],
        e2e_commands=e2e_commands or [],
        security_commands=security_commands or [],
        hard_gates=[
            "format",
            "lint",
            "typecheck",
            "build",
            "test",
            "smoke",
            "e2e",
            "security",
            "policy",
        ],
        soft_gates=[],
        forbidden_commands=[
            "rm -rf",
            "git push --force",
            "sudo",
            "curl | bash",
            "wget | bash",
        ],
        forbidden_paths=[],
        approval_required_operations=[],
        patch_policy=_patch_policy(),
        generated_at=datetime.now(UTC),
    )
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    return path


def _write_soft_security_policy(
    path: Path,
    repo_root: Path,
    *,
    security_commands: list[CommandSpec],
) -> Path:
    policy = CompiledPolicy(
        repo_root=repo_root.resolve(),
        policy_version="0.1",
        source_files=[],
        setup_commands=[],
        format_commands=[],
        lint_commands=[],
        typecheck_commands=[],
        build_commands=[],
        test_commands=[],
        smoke_commands=[],
        e2e_commands=[],
        security_commands=security_commands,
        hard_gates=["format", "lint", "typecheck", "build", "test", "smoke", "e2e", "policy"],
        soft_gates=["security"],
        forbidden_commands=[],
        forbidden_paths=[],
        approval_required_operations=[],
        patch_policy=_patch_policy(),
        generated_at=datetime.now(UTC),
    )
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    return path


def _patch_policy() -> PatchPolicyConfig:
    return PatchPolicyConfig(
        max_diff_lines=500,
        dependency_files=["pyproject.toml", "requirements.txt", "package.json"],
        lockfiles=["uv.lock"],
        license_files=["LICENSE", "REUSE.toml"],
        ci_files=[".github/workflows/"],
        test_paths=["tests/"],
        security_sensitive_paths=["auth/", ".secrets.baseline"],
        secret_patterns=["api_key", "BEGIN PRIVATE KEY", "ghp_"],
        reject_by_default=PatchRejectByDefaultConfig(),
    )


class FakeCommandRunner:
    def __init__(self, *, results: list[CommandResult], **_kwargs: object) -> None:
        self._results = results

    def run(
        self,
        spec: CommandSpec,
        *,
        cwd: Path | None = None,
        category: str | None = None,
    ) -> CommandResult:
        result = self._results.pop(0)
        return result.model_copy(update={"spec": spec, "category": category})


def _result(
    spec: CommandSpec,
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    category: str | None = None,
) -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(
        spec=spec,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
        started_at=now,
        finished_at=now,
        timed_out=False,
        stdout_artifact=None,
        stderr_artifact=None,
        category=category,
    )
