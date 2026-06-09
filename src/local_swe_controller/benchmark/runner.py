"""Local-only benchmark runner for validate and repair workflows."""

from __future__ import annotations

import glob
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from local_swe_controller.benchmark.models import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkMode,
    BenchmarkRunResult,
)
from local_swe_controller.config import DefaultPolicyConfig, load_config, load_yaml_file
from local_swe_controller.exceptions import BenchmarkError, ConfigError, OrchestrationError
from local_swe_controller.llm import load_model_profiles
from local_swe_controller.models import CommandResult, FailureClass
from local_swe_controller.orchestration.langgraph_adapter import LangGraphRepairAdapter
from local_swe_controller.orchestration.models import RepairGraphState
from local_swe_controller.repair import RepairController, RepairResult
from local_swe_controller.sandbox.commands import CommandRunner
from local_swe_controller.storage import ArtifactStore
from local_swe_controller.validation.runner import ValidationRunner


class BenchmarkRunner:
    """Execute benchmark cases against local repositories only."""

    def __init__(
        self,
        *,
        default_policy_path: Path,
        model_profiles_path: Path,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.default_policy_path = default_policy_path
        self.model_profiles_path = model_profiles_path
        self.artifact_store = artifact_store or ArtifactStore()
        self.default_policy = load_config(default_policy_path, DefaultPolicyConfig)
        self.model_profiles = load_model_profiles(model_profiles_path)
        self.bench_root = self.artifact_store.root / "bench"
        self.bench_root.mkdir(parents=True, exist_ok=True)

    def run(self, *, cases_glob: str) -> BenchmarkRunResult:
        case_paths = self._expand_cases(cases_glob)
        bench_run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        artifact_dir = self.bench_root / bench_run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        started_at = datetime.now(UTC)
        started = monotonic()
        case_results = [self._run_case(path, artifact_dir) for path in case_paths]
        finished_at = datetime.now(UTC)
        result = BenchmarkRunResult(
            bench_run_id=bench_run_id,
            created_at=started_at,
            finished_at=finished_at,
            artifact_dir=artifact_dir,
            total=len(case_results),
            passed=sum(1 for case in case_results if case.passed),
            failed=sum(1 for case in case_results if not case.passed),
            status_counts=self._status_counts(case_results),
            failure_class_counts=self._failure_class_counts(case_results),
            duration_seconds=max(monotonic() - started, 0.0),
            cases=case_results,
        )
        self._write_run_artifacts(result)
        return result

    def load_run(self, bench_run_id: str) -> BenchmarkRunResult:
        result_path = self.bench_root / bench_run_id / "result.json"
        if not result_path.exists():
            raise BenchmarkError(f"Benchmark run not found: {bench_run_id}")
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"Invalid benchmark result JSON: {result_path}") from exc
        return BenchmarkRunResult.model_validate(payload)

    def _run_case(self, case_path: Path, artifact_dir: Path) -> BenchmarkCaseResult:
        case_dir = artifact_dir / case_path.stem
        case_dir.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        try:
            case = self._load_case(case_path)
            self._enforce_case_safety(case)
            before_status = self._repo_status(case.repo)
            workflow_result = self._run_workflow(case)
            acceptance_results = self._run_acceptance_commands(case, case_dir)
            after_status = self._repo_status(case.repo)
            repo_status_changed = before_status is not None and after_status != before_status
            workflow_errors: list[str] = []
            if repo_status_changed:
                workflow_errors.append("Benchmark case modified the target repository git state.")
            if workflow_result["target_repo_changed"]:
                workflow_errors.append("Workflow reported target_repo_changed=true.")
            acceptance_passed = all(
                result.exit_code == 0 and not result.timed_out for result in acceptance_results
            )
            expected_status_matched = (
                workflow_result["status"] == case.expected_status
            )
            if not acceptance_passed:
                workflow_errors.append("One or more acceptance commands failed.")
            if not expected_status_matched:
                workflow_errors.append(
                    "Expected status "
                    f"{case.expected_status.value} but observed {workflow_result['status'].value}."
                )
            case_result = BenchmarkCaseResult(
                name=case.name,
                case_path=case_path,
                repo=case.repo,
                mode=case.mode,
                expected_status=case.expected_status,
                actual_status=workflow_result["status"],
                passed=not workflow_errors,
                expected_status_matched=expected_status_matched,
                acceptance_passed=acceptance_passed,
                controller_run_id=workflow_result["run_id"],
                controller_artifact_dir=workflow_result["artifact_dir"],
                failure_class=workflow_result["failure_class"],
                duration_seconds=max(monotonic() - started, 0.0),
                target_repo_changed=workflow_result["target_repo_changed"],
                repo_status_changed=repo_status_changed,
                acceptance_results=acceptance_results,
                errors=workflow_errors,
                notes=case.notes,
            )
        except (BenchmarkError, ConfigError, OrchestrationError, PydanticValidationError) as exc:
            case_result = BenchmarkCaseResult(
                name=case_path.stem,
                case_path=case_path,
                passed=False,
                duration_seconds=max(monotonic() - started, 0.0),
                acceptance_passed=False,
                errors=[str(exc)],
            )
        self._write_case_artifacts(case_dir=case_dir, result=case_result)
        return case_result

    def _load_case(self, case_path: Path) -> BenchmarkCase:
        data = load_yaml_file(case_path)
        try:
            case = BenchmarkCase.model_validate(data)
        except PydanticValidationError as exc:
            raise BenchmarkError(f"Invalid benchmark case in {case_path}: {exc}") from exc
        resolved_repo = (case_path.parent / case.repo).resolve()
        if not resolved_repo.exists():
            raise BenchmarkError(f"Benchmark repo does not exist: {resolved_repo}")
        if not resolved_repo.is_dir():
            raise BenchmarkError(f"Benchmark repo is not a directory: {resolved_repo}")
        return case.model_copy(update={"repo": resolved_repo})

    def _enforce_case_safety(self, case: BenchmarkCase) -> None:
        if case.mode == BenchmarkMode.REPAIR:
            if case.model_profile is None:
                raise BenchmarkError(
                    "Repair benchmark cases must set model_profile explicitly; "
                    "use 'fake' for offline runs."
                )
            profile = self.model_profiles.models.get(case.model_profile)
            if profile is None:
                raise BenchmarkError(
                    f"Unknown benchmark model profile: {case.model_profile}"
                )
            if profile.provider != "fake":
                raise BenchmarkError(
                    "Benchmark repair cases must use a fake model profile because "
                    "benchmark mode forbids network access."
                )

    def _run_workflow(self, case: BenchmarkCase) -> dict[str, Any]:
        store = ArtifactStore(root=self.artifact_store.root)
        if case.mode == BenchmarkMode.VALIDATE:
            runner = ValidationRunner(self.default_policy_path)
            report, run = runner.validate_and_record(case.repo, artifact_store=store)
            return {
                "status": report.status,
                "failure_class": report.failure_class,
                "target_repo_changed": report.target_repo_changed,
                "run_id": run.run_id,
                "artifact_dir": run.artifact_dir,
            }

        controller = RepairController(
            default_policy_path=self.default_policy_path,
            model_profiles_path=self.model_profiles_path,
            artifact_store=store,
        )
        if case.orchestrator == "deterministic":
            result = controller.repair(
                repo_path=case.repo,
                goal=case.goal or "",
                model_profile_name=case.model_profile,
                generate_tests=case.generate_tests,
                max_iters=case.max_iters,
                max_candidates=case.max_candidates,
            )
        elif case.orchestrator == "langgraph":
            result = LangGraphRepairAdapter(controller).execute(
                RepairGraphState(
                    repo_path=case.repo,
                    goal=case.goal or "",
                    model_profile_name=case.model_profile,
                    generate_tests=case.generate_tests,
                    max_iters=case.max_iters,
                    max_candidates=case.max_candidates,
                )
            )
        else:
            raise OrchestrationError(
                f"Unsupported orchestrator '{case.orchestrator}'. "
                "Use 'deterministic' or 'langgraph'."
            )
        return {
            "status": result.status,
            "failure_class": self._repair_failure_class(result),
            "target_repo_changed": result.target_repo_changed,
            "run_id": result.run_id,
            "artifact_dir": result.artifact_dir,
        }

    def _run_acceptance_commands(
        self,
        case: BenchmarkCase,
        case_dir: Path,
    ) -> list[CommandResult]:
        if not case.acceptance_commands:
            return []
        artifact_dir = case_dir / "acceptance"
        runner = CommandRunner(
            forbidden_commands=self.default_policy.forbidden_commands,
            artifact_dir=artifact_dir,
            default_timeout_seconds=self.default_policy.defaults.timeout_seconds,
            allowed_cwd_root=case.repo,
        )
        return [
            runner.run(command, cwd=case.repo, category="benchmark_acceptance")
            for command in case.acceptance_commands
        ]

    def _expand_cases(self, cases_glob: str) -> list[Path]:
        matches = [Path(path).resolve() for path in sorted(glob.glob(cases_glob))]
        if not matches:
            raise BenchmarkError(f"No benchmark case files matched: {cases_glob}")
        return matches

    def _status_counts(self, case_results: list[BenchmarkCaseResult]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in case_results:
            key = case.actual_status.value if case.actual_status is not None else "ERROR"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _failure_class_counts(self, case_results: list[BenchmarkCaseResult]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in case_results:
            key = case.failure_class.value if case.failure_class is not None else "NONE"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _write_case_artifacts(self, *, case_dir: Path, result: BenchmarkCaseResult) -> None:
        (case_dir / "result.json").write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _write_run_artifacts(self, result: BenchmarkRunResult) -> None:
        (result.artifact_dir / "result.json").write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (result.artifact_dir / "summary.md").write_text(
            self._build_summary(result),
            encoding="utf-8",
        )

    def _build_summary(self, result: BenchmarkRunResult) -> str:
        case_lines = [
            (
                f"- `{case.name}` passed=`{str(case.passed).lower()}` "
                f"expected=`{case.expected_status.value if case.expected_status else 'n/a'}` "
                f"actual=`{case.actual_status.value if case.actual_status else 'ERROR'}` "
                f"failure_class=`{case.failure_class.value if case.failure_class else 'NONE'}` "
                f"run_id=`{case.controller_run_id or 'n/a'}`"
            )
            for case in result.cases
        ] or ["- none"]
        return "\n".join(
            [
                f"# Benchmark Summary: {result.bench_run_id}",
                "",
                f"- Total: `{result.total}`",
                f"- Passed: `{result.passed}`",
                f"- Failed: `{result.failed}`",
                f"- Duration Seconds: `{result.duration_seconds:.3f}`",
                f"- Status Counts: `{json.dumps(result.status_counts, sort_keys=True)}`",
                "- Failure Class Counts: "
                f"`{json.dumps(result.failure_class_counts, sort_keys=True)}`",
                "",
                "## Cases",
                *case_lines,
            ]
        )

    def _repo_status(self, repo: Path) -> str | None:
        if not repo.joinpath(".git").exists():
            return None
        completed = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def _repair_failure_class(self, result: RepairResult) -> FailureClass | None:
        if (
            result.validation_report is not None
            and result.validation_report.failure_class is not None
        ):
            return result.validation_report.failure_class
        return result.baseline_report.failure_class


__all__ = [
    "BenchmarkCase",
    "BenchmarkCaseResult",
    "BenchmarkMode",
    "BenchmarkRunResult",
    "BenchmarkRunner",
]
