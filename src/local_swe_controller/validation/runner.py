"""Validation runner for deterministic policy execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pydantic import ValidationError as PydanticValidationError

from local_swe_controller.config import DefaultPolicyConfig, load_config
from local_swe_controller.exceptions import CommandSafetyError, ValidationError
from local_swe_controller.models import (
    CommandResult,
    CommandSeverity,
    CommandSpec,
    FailureClass,
    MissingToolBehavior,
    RunStatus,
    ValidationReport,
)
from local_swe_controller.policy.compiler import PolicyCompiler
from local_swe_controller.policy.profiler import RepoProfiler
from local_swe_controller.policy.schema import CompiledPolicy
from local_swe_controller.sandbox.commands import CommandRunner, PythonExecutionConfig
from local_swe_controller.sandbox.worktree import ManagedWorktree, WorktreeManager
from local_swe_controller.storage import ArtifactStore, RunContext
from local_swe_controller.storage.manager import default_artifact_root
from local_swe_controller.validation.classifier import FailureClassifier


class ValidationRunner:
    """Run compiled validation policy inside an ephemeral git worktree."""

    def __init__(self, default_policy_path: Path) -> None:
        self.default_policy_path = default_policy_path
        self.default_policy = load_config(default_policy_path, DefaultPolicyConfig)
        self.policy_compiler = PolicyCompiler(default_policy_path)
        self.repo_profiler = RepoProfiler()
        self.classifier = FailureClassifier(self.default_policy.failure_classification)
        base_root = default_artifact_root()
        self.default_artifact_root = base_root / "logs"
        self.command_runner_factory = CommandRunner

    def validate(
        self,
        repo_path: Path,
        *,
        policy_path: Path | None = None,
        policy: CompiledPolicy | None = None,
        selected_python: Path | None = None,
        artifact_dir: Path | None = None,
        keep_worktree: bool = False,
        timeout_per_command: int | None = None,
        trace_callback: Callable[[str, dict[str, object]], None] | None = None,
        phase: str = "direct",
    ) -> ValidationReport:
        policy = self._load_policy(repo_path, policy_path, policy)
        worktree_manager = WorktreeManager(keep_worktree=keep_worktree)

        with worktree_manager.session(repo_path) as managed:
            return self._run_policy(
                policy=policy,
                worktree_path=managed.path,
                selected_python=selected_python,
                artifact_dir=artifact_dir,
                target_repo_dirty=managed.target_repo_dirty,
                target_repo_state=lambda: self._target_repo_changed(worktree_manager, managed),
                warning=managed.warning,
                timeout_per_command=timeout_per_command,
                trace_callback=trace_callback,
                phase=phase,
            )

    def validate_worktree(
        self,
        *,
        repo_root: Path,
        policy: CompiledPolicy,
        worktree_path: Path,
        selected_python: Path | None = None,
        artifact_dir: Path | None = None,
        timeout_per_command: int | None = None,
        trace_callback: Callable[[str, dict[str, object]], None] | None = None,
        phase: str = "patch",
    ) -> ValidationReport:
        return self._run_policy(
            policy=policy,
            worktree_path=worktree_path,
            selected_python=selected_python,
            artifact_dir=artifact_dir,
            target_repo_dirty=self._target_repo_dirty(repo_root),
            target_repo_state=lambda: False,
            warning=None,
            timeout_per_command=timeout_per_command,
            trace_callback=trace_callback,
            phase=phase,
        )

    def validate_and_record(
        self,
        repo_path: Path,
        *,
        policy_path: Path | None = None,
        selected_python: Path | None = None,
        keep_worktree: bool = False,
        timeout_per_command: int | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> tuple[ValidationReport, RunContext]:
        repo_root = repo_path.expanduser().resolve()
        store = artifact_store or ArtifactStore()
        run = store.create_run(run_type="validate", repo_root=repo_root)
        store.append_trace(
            run,
            "run_started",
            {
                "repo_root": str(repo_root),
                "goal": None,
                "command_type": "validate",
                "generate_tests": False,
                "timeout_per_command": timeout_per_command
                or self.default_policy.defaults.timeout_seconds,
            },
        )
        policy = self._load_policy(repo_root, policy_path, None)
        policy_json = policy.model_dump_json(indent=2)
        written_policy = store.write_policy_snapshot(run, policy_json)
        store.append_trace(
            run,
            "policy_compiled",
            {
                "policy_path": str(written_policy),
                "policy_hash": self._policy_hash(policy_json),
            },
        )
        report = self.validate(
            repo_root,
            policy=policy,
            selected_python=selected_python,
            artifact_dir=run.log_dir / "validation",
            keep_worktree=keep_worktree,
            timeout_per_command=timeout_per_command,
            trace_callback=lambda event, payload: store.append_trace(run, event, payload),
            phase="direct",
        )
        payload = {
            "run_id": run.run_id,
            "command_type": "validate",
            "repo_root": str(repo_root),
            "status": report.status.value,
            "failure_class": report.failure_class.value if report.failure_class else None,
            "policy_path": str(written_policy),
            "trace_path": str(run.trace_path),
            "artifact_dir": str(run.artifact_dir),
            "report": report.model_dump(mode="json"),
        }
        store.write_report(run, payload)
        summary = store.build_validate_summary(
            context=run,
            report=report,
            policy_path=written_policy,
        )
        summary_path = store.write_summary(run, summary)
        payload["summary_path"] = str(summary_path)
        store.write_report(run, payload)
        duration_seconds = max((datetime.now(UTC) - run.created_at).total_seconds(), 0.0)
        store.append_trace(
            run,
            "run_finished",
            {
                "status": report.status.value,
                "summary": report.summary or "Validation completed.",
                "stop_reason": None,
                "failure_class": report.failure_class.value if report.failure_class else None,
                "target_repo_changed": report.target_repo_changed,
                "duration_seconds": duration_seconds,
                "summary_path": str(summary_path),
            },
        )
        store.finalize_run(
            run,
            status=report.status,
            summary=report.summary,
            metadata={
                "failure_class": report.failure_class.value if report.failure_class else None,
                "target_repo_changed": report.target_repo_changed,
                "summary_path": str(summary_path),
            },
        )
        return report, run

    def _load_policy(
        self,
        repo_path: Path,
        policy_path: Path | None,
        policy: CompiledPolicy | None,
    ) -> CompiledPolicy:
        repo_root = repo_path.expanduser().resolve()
        if policy is not None:
            if policy.repo_root.resolve() != repo_root:
                raise ValidationError(
                    "Compiled policy repo_root does not match the requested repository."
                )
            return policy
        if policy_path is None:
            return self.policy_compiler.compile(repo_root)

        if not policy_path.exists():
            raise ValidationError(f"Policy file does not exist: {policy_path}")
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid policy JSON in {policy_path}: {exc}") from exc

        try:
            policy = CompiledPolicy.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValidationError(f"Invalid compiled policy in {policy_path}: {exc}") from exc
        if policy.repo_root.resolve() != repo_root:
            raise ValidationError(
                "Compiled policy repo_root does not match the requested repository."
            )
        return policy

    def _run_policy(
        self,
        *,
        policy: CompiledPolicy,
        worktree_path: Path,
        selected_python: Path | None,
        artifact_dir: Path | None,
        target_repo_dirty: bool,
        target_repo_state: Callable[[], bool],
        warning: str | None,
        timeout_per_command: int | None,
        trace_callback: Callable[[str, dict[str, object]], None] | None,
        phase: str,
    ) -> ValidationReport:
        resolved_artifact_dir = self._prepare_artifact_dir(artifact_dir, worktree_path.name)
        runner = self.command_runner_factory(
            forbidden_commands=policy.forbidden_commands,
            artifact_dir=resolved_artifact_dir,
            default_timeout_seconds=(
                timeout_per_command or self.default_policy.defaults.timeout_seconds
            ),
            allowed_cwd_root=worktree_path,
            python_config=(
                PythonExecutionConfig(
                    selected_python=selected_python,
                    target_repo_root=policy.repo_root.resolve(),
                )
                if selected_python is not None
                else None
            ),
        )
        warnings = [warning] if warning else []
        warnings.extend(self._profile_warnings(worktree_path, policy))
        if (
            policy.setup_commands
            and not self.default_policy.command_policy.allow_network_by_default
        ):
            warnings.append(
                "Setup commands were skipped because the default policy disallows "
                "network/bootstrap steps during validation."
            )
        commands: list[CommandResult] = []
        self._trace(
            trace_callback,
            "validation_started",
            {
                "phase": phase,
                "artifact_dir": str(resolved_artifact_dir),
                "worktree_path": str(worktree_path),
            },
        )

        try:
            for category, specs in self._iter_policy_commands(policy):
                for spec in specs:
                    if category == "security":
                        self._trace(
                            trace_callback,
                            "security_gate_started",
                            {
                                "command": spec.command,
                                "tool_name": spec.tool_name,
                                "optional": spec.optional,
                            },
                        )
                    result = runner.run(spec, cwd=worktree_path, category=category)
                    commands.append(result)
                    decision = self._evaluate_result(
                        category=category,
                        spec=spec,
                        result=result,
                        policy=policy,
                        worktree_path=worktree_path,
                        selected_python=selected_python,
                    )
                    if category == "security":
                        self._trace(
                            trace_callback,
                            "security_gate_finished",
                            {
                                "command": spec.command,
                                "tool_name": spec.tool_name,
                                "status": decision["action"],
                                "finding_summary": str(decision.get("summary"))
                                if decision.get("summary")
                                else None,
                                "warnings": (
                                    [str(decision["summary"])]
                                    if decision["action"] == "warn"
                                    else []
                                ),
                            },
                        )
                    if decision["action"] == "continue":
                        continue
                    if decision["action"] == "warn":
                        warnings.append(str(decision["summary"]))
                        continue
                    report = ValidationReport(
                        repo_root=policy.repo_root,
                        status=self._status_for_failure(decision["failure_class"]),
                        failure_class=decision["failure_class"],
                        commands=commands,
                        summary=str(decision["summary"]),
                        worktree_path=worktree_path,
                        artifact_dir=resolved_artifact_dir,
                        warnings=warnings,
                        target_repo_dirty=target_repo_dirty,
                        target_repo_changed=target_repo_state(),
                    )
                    self._write_report_artifact(report)
                    self._trace_validation_finished(trace_callback, phase, report)
                    return report
        except CommandSafetyError as exc:
            failure = self._policy_violation_result(exc)
            commands.append(failure)
            report = ValidationReport(
                repo_root=policy.repo_root,
                status=RunStatus.STOPPED_BY_POLICY,
                failure_class=FailureClass.POLICY_VIOLATION,
                commands=commands,
                summary=str(exc),
                worktree_path=worktree_path,
                artifact_dir=resolved_artifact_dir,
                warnings=warnings,
                target_repo_dirty=target_repo_dirty,
                target_repo_changed=target_repo_state(),
            )
            self._write_report_artifact(report)
            self._trace_validation_finished(trace_callback, phase, report)
            return report

        report = ValidationReport(
            repo_root=policy.repo_root,
            status=RunStatus.NO_ACTION_NEEDED,
            failure_class=None,
            commands=commands,
            summary="Validation passed.",
            worktree_path=worktree_path,
            artifact_dir=resolved_artifact_dir,
            warnings=warnings,
            target_repo_dirty=target_repo_dirty,
            target_repo_changed=target_repo_state(),
        )
        self._write_report_artifact(report)
        self._trace_validation_finished(trace_callback, phase, report)
        return report

    def _profile_warnings(self, repo_root: Path, policy: CompiledPolicy) -> list[str]:
        profile = self.repo_profiler.profile(repo_root)
        return [*profile.readability_warnings, *profile.planning_warnings]

    def _iter_policy_commands(
        self,
        policy: CompiledPolicy,
    ) -> list[tuple[str, list[CommandSpec]]]:
        return [
            ("format", policy.format_commands),
            ("lint", policy.lint_commands),
            ("typecheck", policy.typecheck_commands),
            ("build", policy.build_commands),
            ("test", policy.test_commands),
            ("smoke", policy.smoke_commands),
            ("e2e", policy.e2e_commands),
            ("security", policy.security_commands),
        ]

    def _status_for_failure(self, failure_class: FailureClass) -> RunStatus:
        if failure_class == FailureClass.POLICY_VIOLATION:
            return RunStatus.STOPPED_BY_POLICY
        if failure_class == FailureClass.SECURITY:
            return RunStatus.STOPPED_BY_SECURITY
        if failure_class in {FailureClass.ENVIRONMENT, FailureClass.RESOURCE_EXHAUSTION}:
            return RunStatus.STOPPED_BY_ENVIRONMENT
        return RunStatus.BASELINE_FAILED

    def _build_summary(
        self,
        category: str,
        result: CommandResult,
        failure_class: FailureClass,
        worktree_path: Path,
        selected_python: Path | None,
    ) -> str:
        if result.timed_out:
            return (
                f"{category} command timed out after {result.duration_seconds:.2f}s "
                f"and was classified as {failure_class.value}."
            )
        if failure_class == FailureClass.ENVIRONMENT:
            diagnosis = self._environment_diagnosis(
                result=result,
                worktree_path=worktree_path,
                selected_python=selected_python,
            )
            if diagnosis is not None:
                return (
                    f"{category} command exited with code {result.exit_code} and was classified "
                    f"as {failure_class.value}. {diagnosis}"
                )
        return (
            f"{category} command exited with code {result.exit_code} and was classified as "
            f"{failure_class.value}."
        )

    def _evaluate_result(
        self,
        *,
        category: str,
        spec: CommandSpec,
        result: CommandResult,
        policy: CompiledPolicy,
        worktree_path: Path,
        selected_python: Path | None,
    ) -> dict[str, object]:
        gate_mode = self._gate_mode(category, policy)
        if spec.optional and self._is_missing_tool(result):
            summary = self._missing_tool_summary(spec, result)
            if spec.missing_tool_behavior == MissingToolBehavior.SKIP or gate_mode == "soft":
                return {"action": "warn", "summary": summary}
            return {
                "action": "fail",
                "summary": summary,
                "failure_class": FailureClass.ENVIRONMENT,
            }

        security_summary = self._security_finding_summary(spec, result)
        if security_summary is not None:
            if gate_mode == "soft":
                return {"action": "warn", "summary": security_summary}
            return {
                "action": "fail",
                "summary": security_summary,
                "failure_class": FailureClass.SECURITY,
            }

        if result.exit_code == 0 and not result.timed_out:
            return {"action": "continue"}

        failure_class = self.classifier.classify(result)
        if category == "build" and failure_class == FailureClass.UNKNOWN:
            failure_class = FailureClass.BUILD
        if category in {"smoke", "e2e"} and failure_class == FailureClass.UNKNOWN:
            failure_class = FailureClass.TEST
        summary = self._build_summary(
            category,
            result,
            failure_class,
            worktree_path,
            selected_python,
        )
        soft_environment_warning = self._soft_environment_warning(
            category=category,
            spec=spec,
            result=result,
            failure_class=failure_class,
            policy=policy,
            worktree_path=worktree_path,
            selected_python=selected_python,
        )
        if soft_environment_warning is not None:
            return {"action": "warn", "summary": soft_environment_warning}
        if gate_mode == "soft":
            return {"action": "warn", "summary": summary}
        return {"action": "fail", "summary": summary, "failure_class": failure_class}

    def _soft_environment_warning(
        self,
        *,
        category: str,
        spec: CommandSpec,
        result: CommandResult,
        failure_class: FailureClass,
        policy: CompiledPolicy,
        worktree_path: Path,
        selected_python: Path | None,
    ) -> str | None:
        if spec.severity != CommandSeverity.SOFT:
            return None
        if category != "smoke" or policy.repo_kind != "workflow_repo":
            return None
        profile = self.repo_profiler.profile(worktree_path)
        if "implicit_script_chain" not in profile.workflow_sources:
            return None
        if failure_class != FailureClass.ENVIRONMENT:
            return None
        missing_modules = sorted(self._missing_modules(f"{result.stdout}\n{result.stderr}"))
        if not missing_modules:
            return None
        dependency = missing_modules[0]
        interpreter_hint = (
            f"the selected interpreter ({selected_python})"
            if selected_python is not None
            else "a dependency-aware project interpreter"
        )
        return (
            "Soft smoke check could not run because dependency "
            f"'{dependency}' is missing. Syntax checks passed; provide --python or --venv "
            f"for dependency-aware smoke validation using {interpreter_hint}."
        )

    def _environment_diagnosis(
        self,
        *,
        result: CommandResult,
        worktree_path: Path,
        selected_python: Path | None,
    ) -> str | None:
        output = f"{result.stdout}\n{result.stderr}"
        lowered = output.casefold()
        missing_modules = self._missing_modules(output)
        details: list[str] = []
        if missing_modules:
            details.append(
                "Missing modules: " + ", ".join(sorted(missing_modules))
            )
        if "modulenotfounderror" in lowered or "no module named" in lowered:
            if self._target_package_not_importable(output, worktree_path):
                details.append(
                    "The target package may not be importable from the selected interpreter."
                )
            interpreter_hint = (
                f"the selected interpreter ({selected_python})"
                if selected_python is not None
                else "a prepared project interpreter"
            )
            details.append(
                "Suggested manual setup: pass --python or --venv pointing to "
                f"{interpreter_hint} with this repo's dependencies already installed. "
                "local-swe does not install dependencies automatically."
            )
        if not details:
            return None
        return " ".join(details)

    def _missing_modules(self, output: str) -> set[str]:
        import re

        patterns = (
            re.compile(r"No module named ['\"]([^'\"]+)['\"]"),
            re.compile(r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]"),
        )
        found: set[str] = set()
        for pattern in patterns:
            for match in pattern.findall(output):
                if match:
                    found.add(match)
        return found

    def _target_package_not_importable(self, output: str, worktree_path: Path) -> bool:
        repo_packages = set()
        src_dir = worktree_path / "src"
        if src_dir.is_dir():
            for child in src_dir.iterdir():
                if child.is_dir() and (child / "__init__.py").exists():
                    repo_packages.add(child.name)
        for child in worktree_path.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                repo_packages.add(child.name)
        missing = self._missing_modules(output)
        return any(module.split(".", 1)[0] in repo_packages for module in missing)

    def _policy_violation_result(self, exc: CommandSafetyError) -> CommandResult:
        return CommandResult(
            spec=CommandSpec(
                command=["policy-violation"],
                description="Policy violation placeholder",
            ),
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration_seconds=0,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            timed_out=False,
            stdout_artifact=None,
            stderr_artifact=None,
            category="policy",
        )

    def _gate_mode(self, category: str, policy: CompiledPolicy) -> str:
        if category in policy.soft_gates and category not in policy.hard_gates:
            return "soft"
        return "hard"

    def _is_missing_tool(self, result: CommandResult) -> bool:
        if result.timed_out:
            return False
        output = f"{result.stdout}\n{result.stderr}".casefold()
        missing_patterns = (
            "command not found",
            "no such file or directory",
            "module not found",
            "modulenotfounderror",
            "no module named",
            "is not installed",
            "could not find executable",
            "package not found",
        )
        return result.exit_code != 0 and any(pattern in output for pattern in missing_patterns)

    def _missing_tool_summary(self, spec: CommandSpec, result: CommandResult) -> str:
        tool_name = spec.tool_name or spec.command[-1]
        detail = result.stderr.strip() or result.stdout.strip() or "tool is unavailable"
        return f"Optional scanner '{tool_name}' is unavailable: {detail}"

    def _security_finding_summary(
        self,
        spec: CommandSpec,
        result: CommandResult,
    ) -> str | None:
        if result.category != "security":
            return None
        output = f"{result.stdout}\n{result.stderr}".strip()
        if not output:
            return None

        tool_name = (spec.tool_name or spec.command[-1]).casefold()
        if tool_name == "pip-audit" and self._pip_audit_has_findings(result.stdout):
            return "pip-audit reported vulnerabilities."
        if tool_name == "osv-scanner" and self._osv_scanner_has_findings(result.stdout):
            return "osv-scanner reported vulnerabilities."
        if tool_name == "detect-secrets" and self._detect_secrets_has_findings(result.stdout):
            return "detect-secrets reported secret-looking content."
        if tool_name == "reuse" and result.exit_code != 0 and not self._is_missing_tool(result):
            return "reuse reported a license compliance failure."

        lowered = output.casefold()
        patterns = self.default_policy.failure_classification
        if patterns is not None and self.classifier._matches(lowered, patterns.security_patterns):
            return f"{spec.tool_name or spec.command[-1]} reported a security finding."
        return None

    def _pip_audit_has_findings(self, stdout: str) -> bool:
        payload = self._load_json(stdout)
        if isinstance(payload, dict):
            dependencies = payload.get("dependencies", [])
            if isinstance(dependencies, list):
                return any(
                    isinstance(item, dict) and item.get("vulns") for item in dependencies
                )
        if isinstance(payload, list):
            return any(isinstance(item, dict) and item.get("vulns") for item in payload)
        lowered = stdout.casefold()
        return "vulnerability" in lowered or "cve-" in lowered

    def _osv_scanner_has_findings(self, stdout: str) -> bool:
        payload = self._load_json(stdout)
        if payload is not None:
            return self._json_has_security_findings(payload)
        return "vulnerability" in stdout.casefold() or "cve-" in stdout.casefold()

    def _detect_secrets_has_findings(self, stdout: str) -> bool:
        payload = self._load_json(stdout)
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, dict):
                return any(isinstance(entries, list) and entries for entries in results.values())
        lowered = stdout.casefold()
        return "secret" in lowered and ("result" in lowered or "finding" in lowered)

    def _json_has_security_findings(self, payload: object) -> bool:
        if isinstance(payload, dict):
            for key, value in payload.items():
                lowered_key = str(key).casefold()
                if lowered_key in {"vulns", "vulnerabilities", "findings"}:
                    if isinstance(value, list):
                        return bool(value)
                    if self._json_has_security_findings(value):
                        return True
                    continue
                if self._json_has_security_findings(value):
                    return True
            return False
        if isinstance(payload, list):
            return any(self._json_has_security_findings(item) for item in payload)
        return False

    def _load_json(self, text: str) -> object | None:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _target_repo_changed(
        self,
        manager: WorktreeManager,
        managed: ManagedWorktree,
    ) -> bool:
        if managed.sandbox_kind != "git_worktree":
            return False
        return manager.status(managed.repo_root) != managed.target_repo_status_before

    def _target_repo_dirty(self, repo_root: Path) -> bool:
        try:
            return bool(WorktreeManager(keep_worktree=True).status(repo_root))
        except Exception:
            return False

    def _prepare_artifact_dir(self, artifact_dir: Path | None, slug: str) -> Path:
        if artifact_dir is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            return artifact_dir.resolve()
        path = self.default_artifact_root / slug
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    def _write_report_artifact(self, report: ValidationReport) -> None:
        if report.artifact_dir is None:
            return
        output_path = report.artifact_dir / "validation-report.json"
        output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def _trace(
        self,
        trace_callback: Callable[[str, dict[str, object]], None] | None,
        event: str,
        payload: dict[str, object],
    ) -> None:
        if trace_callback is None:
            return
        trace_callback(event, payload)

    def _trace_validation_finished(
        self,
        trace_callback: Callable[[str, dict[str, object]], None] | None,
        phase: str,
        report: ValidationReport,
    ) -> None:
        failed_command = None
        for command in reversed(report.commands):
            if command.exit_code != 0 or command.timed_out:
                failed_command = command.spec.command
                break
        self._trace(
            trace_callback,
            "validation_finished",
            {
                "phase": phase,
                "status": report.status.value,
                "failure_class": report.failure_class.value if report.failure_class else None,
                "warnings": report.warnings,
                "target_repo_changed": report.target_repo_changed,
                "failed_command": failed_command,
            },
        )

    def _policy_hash(self, policy_json: str) -> str:
        import hashlib

        return hashlib.sha256(policy_json.encode("utf-8")).hexdigest()
