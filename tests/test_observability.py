from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_swe_controller.config import PatchPolicyConfig, PatchRejectByDefaultConfig
from local_swe_controller.models import CommandResult, CommandSpec, RunStatus
from local_swe_controller.policy.schema import CompiledPolicy
from local_swe_controller.repair.controller import RepairController
from local_swe_controller.storage import ArtifactStore
from local_swe_controller.storage.events import EventName, validate_trace_event
from local_swe_controller.storage.telemetry import TelemetryClient
from local_swe_controller.validation.runner import ValidationRunner


def test_summary_md_generated_for_validate(
    project_root: Path,
    temp_fixture_repo: Path,
) -> None:
    before_status = _git_status(temp_fixture_repo)
    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    report, run = runner.validate_and_record(temp_fixture_repo, artifact_store=ArtifactStore())
    after_status = _git_status(temp_fixture_repo)

    assert report.status == RunStatus.NO_ACTION_NEEDED
    assert run.summary_path.exists()
    content = run.summary_path.read_text(encoding="utf-8")
    assert "Run Summary" in content
    assert "Command Type: `validate`" in content
    assert "Policy Hash:" in content
    assert before_status == after_status


def test_summary_md_generated_for_repair_with_generated_tests(
    project_root: Path,
    temp_fixture_repo: Path,
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

    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(),
    )
    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        generate_tests=True,
        max_iters=1,
    )

    assert result.summary_path is not None and result.summary_path.exists()
    content = result.summary_path.read_text(encoding="utf-8")
    assert "Command Type: `repair`" in content
    assert str(result.generated_test_patch_path) in content
    assert str(result.patch_path) in content


def test_summary_includes_security_findings_and_scanner_warnings(
    project_root: Path,
    git_repo_factory,
    init_git_repo,
    tmp_path: Path,
) -> None:
    repo = git_repo_factory("observability-security")
    (repo / "README.md").write_text("security\n", encoding="utf-8")
    init_git_repo(repo)
    spec = CommandSpec(
        command=["reuse", "lint"],
        optional=True,
        tool_name="reuse",
    )
    policy_path = _write_policy(tmp_path / "policy.json", repo, security_commands=[spec])
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

    report, run = runner.validate_and_record(
        repo,
        policy_path=policy_path,
        artifact_store=ArtifactStore(),
    )

    assert report.summary is not None
    content = run.summary_path.read_text(encoding="utf-8")
    assert "Security Findings" in content
    assert "Scanner Warnings" in content
    assert "Optional scanner 'reuse' is unavailable" in content


def test_jsonl_traces_are_valid_and_event_names_are_schema_validated(
    project_root: Path,
    temp_fixture_repo: Path,
) -> None:
    runner = ValidationRunner(project_root / "configs" / "default_policy.yaml")
    _, run = runner.validate_and_record(temp_fixture_repo, artifact_store=ArtifactStore())

    lines = run.trace_path.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines:
        payload = json.loads(line)
        envelope = validate_trace_event(
            event=payload["event"],
            payload=payload["payload"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
        events.append(envelope.event)

    assert EventName.RUN_STARTED in events
    assert EventName.POLICY_COMPILED in events
    assert EventName.VALIDATION_STARTED in events
    assert EventName.VALIDATION_FINISHED in events
    assert EventName.RUN_FINISHED in events

    store = ArtifactStore()
    context = store.create_run(run_type="validate", repo_root=temp_fixture_repo)
    with pytest.raises(ValueError):
        store.append_trace(context, "invalid_event_name", {})


def test_repair_trace_covers_generated_tests_and_patch_events(
    project_root: Path,
    temp_fixture_repo: Path,
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

    controller = RepairController(
        default_policy_path=project_root / "configs" / "default_policy.yaml",
        model_profiles_path=project_root / "configs" / "model_profiles.example.yaml",
        artifact_store=ArtifactStore(),
    )
    result = controller.repair(
        repo_path=temp_fixture_repo,
        goal="Fix failing tests",
        model_profile_name="fake",
        generate_tests=True,
        max_iters=1,
    )

    assert result.trace_path.exists()
    events = []
    for line in result.trace_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        envelope = validate_trace_event(
            event=payload["event"],
            payload=payload["payload"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
        events.append(envelope.event)

    assert EventName.RUN_STARTED in events
    assert EventName.POLICY_COMPILED in events
    assert EventName.VALIDATION_STARTED in events
    assert EventName.VALIDATION_FINISHED in events
    assert EventName.MODEL_CALLED in events
    assert EventName.GENERATED_TESTS_CREATED in events
    assert EventName.PATCH_GENERATED in events
    assert EventName.PATCH_POLICY_CHECKED in events
    assert EventName.PATCH_VALIDATION_STARTED in events
    assert EventName.PATCH_VALIDATION_FINISHED in events
    assert EventName.SECURITY_GATE_STARTED in events
    assert EventName.SECURITY_GATE_FINISHED in events
    assert EventName.RUN_FINISHED in events


def test_telemetry_client_fails_gracefully_when_optional_dependency_missing(monkeypatch) -> None:
    def _import_module(name: str):
        if name == "opentelemetry.trace":
            raise ImportError()
        return None

    monkeypatch.setattr(
        "importlib.import_module",
        _import_module,
    )
    from local_swe_controller.config import OpenTelemetryConfig

    client = TelemetryClient(OpenTelemetryConfig(enabled=True))
    client.record_event("run_started", {"repo_root": "/tmp/repo"})

    assert client.warning is not None


def _write_policy(
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
        test_commands=[],
        security_commands=security_commands,
        hard_gates=["format", "lint", "typecheck", "test", "policy"],
        soft_gates=["security"],
        forbidden_commands=[],
        forbidden_paths=[],
        approval_required_operations=[],
        patch_policy=PatchPolicyConfig(
            max_diff_lines=500,
            dependency_files=["pyproject.toml"],
            lockfiles=["uv.lock"],
            license_files=["LICENSE", "REUSE.toml"],
            ci_files=[".github/workflows/"],
            test_paths=["tests/"],
            security_sensitive_paths=["auth/"],
            secret_patterns=["api_key"],
            reject_by_default=PatchRejectByDefaultConfig(),
        ),
        generated_at=datetime.now(UTC),
    )
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    return path


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
        del cwd
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


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
