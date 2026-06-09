"""Bounded repair controller."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field

from local_swe_controller.exceptions import RepairError, ValidationError
from local_swe_controller.llm import (
    FakeLLMClient,
    LLMClient,
    ModelProfile,
    ModelRouter,
    OpenAICompatibleClient,
    load_model_profiles,
)
from local_swe_controller.models import (
    CommandResult,
    CommandSpec,
    FailureClass,
    RunStatus,
    ValidationReport,
)
from local_swe_controller.policy.compiler import PolicyCompiler
from local_swe_controller.policy.schema import CompiledPolicy
from local_swe_controller.repair.patch_parser import ParsedPatch, PatchParseError, PatchParser
from local_swe_controller.sandbox.commands import CommandRunner, PythonExecutionConfig
from local_swe_controller.sandbox.worktree import WorktreeManager
from local_swe_controller.storage import ArtifactStore, RunContext
from local_swe_controller.validation.runner import ValidationRunner

_PROMPT_SAFETY_MARGIN_TOKENS = 512
_PROMPT_DEFAULT_INPUT_TOKENS = 6_000
_PROMPT_MAX_COMMAND_TAIL_CHARS = 2_000
_PROMPT_MAX_FILE_CHARS = 8_000
_PROMPT_MAX_FILES = 12
_PROMPT_MAX_SUMMARY_FILES = 40
_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".local-swe",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".cache",
    "node_modules",
    "dist",
    "build",
    "outputs",
    "output",
    "logs",
    "log",
    "data",
    "artifacts",
    "checkpoints",
}
_EXCLUDED_SUFFIXES = {
    ".ipynb",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".xz",
    ".bz2",
    ".7z",
    ".parquet",
    ".feather",
    ".arrow",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".pt",
    ".pth",
    ".ckpt",
    ".bin",
    ".so",
    ".dylib",
    ".dll",
    ".pyc",
}
_TEXT_FILE_SUFFIXES = {
    ".py",
    ".pyi",
    ".toml",
    ".md",
    ".rst",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".html",
    ".xml",
    ".sql",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
}
_TRACEBACK_FILE_PATTERN = re.compile(
    r'File "([^"]+)"|'
    r'([\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|toml|ya?ml|json|md|rs|go|java|kt|c|cc|cpp|h|hpp))'
)


class RepairResult(BaseModel):
    """Persisted result for a repair run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    repo_root: Path
    goal: str
    status: RunStatus
    summary: str
    model_profile: str
    artifact_dir: Path
    policy_path: Path
    trace_path: Path
    summary_path: Path | None = None
    patch_path: Path | None = None
    generated_test_patch_path: Path | None = None
    baseline_report: ValidationReport
    generated_test_validation_report: ValidationReport | None = None
    validation_report: ValidationReport | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    generated_test_rejection_reasons: list[str] = Field(default_factory=list)
    worktree_path: Path | None = None
    target_repo_changed: bool = False
    iterations_attempted: int = 0
    candidates_attempted: int = 0
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FailureSignature:
    """Stable signature for repeated failure detection."""

    failure_class: FailureClass
    failed_command: tuple[str, ...]
    stderr_hash: str
    changed_files: tuple[str, ...]


@dataclass(slots=True)
class GeneratedTestStage:
    accepted: bool
    patch_path: Path | None = None
    validation_report: ValidationReport | None = None
    rejection_reasons: list[str] | None = None
    model_profile: str | None = None


@dataclass
class RepairController:
    """Verifier-first bounded repair orchestration."""

    default_policy_path: Path
    model_profiles_path: Path
    artifact_store: ArtifactStore

    def __post_init__(self) -> None:
        self.policy_compiler = PolicyCompiler(self.default_policy_path)
        self.validation_runner = ValidationRunner(self.default_policy_path)
        self.patch_parser = PatchParser()
        self.model_profiles = load_model_profiles(self.model_profiles_path)
        self.router = ModelRouter(self.model_profiles)

    def repair(
        self,
        *,
        repo_path: Path,
        goal: str,
        model_profile_name: str | None,
        generate_tests: bool = False,
        max_iters: int = 1,
        max_candidates: int | None = None,
        max_diff_lines: int | None = None,
        timeout_per_command: int | None = None,
        max_total_runtime_seconds: int | None = None,
        keep_worktree: bool = False,
        selected_python: Path | None = None,
    ) -> RepairResult:
        repo_root = repo_path.expanduser().resolve()
        budget_defaults = self.validation_runner.default_policy.defaults
        max_candidates = max_candidates or budget_defaults.max_candidates
        max_diff_lines = max_diff_lines or budget_defaults.max_diff_lines
        timeout_per_command = timeout_per_command or budget_defaults.timeout_seconds
        max_total_runtime_seconds = (
            max_total_runtime_seconds or budget_defaults.max_total_runtime_seconds
        )
        if max_iters < 1:
            raise RepairError("max_iters must be at least 1.")
        if max_candidates < 1:
            raise RepairError("max_candidates must be at least 1.")

        started = monotonic()
        run = self.artifact_store.create_run(run_type="repair", repo_root=repo_root, goal=goal)
        self.artifact_store.append_trace(
            run,
            "run_started",
            {
                "repo_root": str(repo_root),
                "goal": goal,
                "command_type": "repair",
                "generate_tests": generate_tests,
                "max_iters": max_iters,
                "max_candidates": max_candidates,
                "max_diff_lines": max_diff_lines,
                "timeout_per_command": timeout_per_command,
                "max_total_runtime_seconds": max_total_runtime_seconds,
            },
        )

        policy = self.policy_compiler.compile(repo_root)
        policy_path = self.artifact_store.write_policy_snapshot(
            run,
            policy.model_dump_json(indent=2),
        )
        self.artifact_store.append_trace(
            run,
            "policy_compiled",
            {
                "policy_path": str(policy_path),
                "policy_hash": self._policy_hash(policy_path),
            },
        )

        baseline_report = self.validation_runner.validate(
            repo_root,
            policy=policy,
            selected_python=selected_python,
            artifact_dir=run.log_dir / "baseline",
            keep_worktree=keep_worktree,
            timeout_per_command=timeout_per_command,
            trace_callback=lambda event, payload: self.artifact_store.append_trace(
                run, event, payload
            ),
            phase="baseline",
        )

        resolved_profile_name = self.router.resolve(
            "patch_generation",
            profile_name=model_profile_name,
        ).profile_name
        if baseline_report.status == RunStatus.NO_ACTION_NEEDED and not (
            self._has_actionable_repo_readability_warnings(policy, baseline_report)
        ):
            result = RepairResult(
                run_id=run.run_id,
                repo_root=repo_root,
                goal=goal,
                status=RunStatus.NO_ACTION_NEEDED,
                summary="Baseline validation already passes; no repair was attempted.",
                model_profile=resolved_profile_name,
                artifact_dir=run.artifact_dir,
                policy_path=policy_path,
                trace_path=run.trace_path,
                summary_path=run.summary_path,
                baseline_report=baseline_report,
                generated_test_rejection_reasons=[],
                target_repo_changed=False,
                stop_reason="baseline_passed",
            )
            return self._finalize(run, result)

        stop_result = self._stop_for_terminal_failure(
            run=run,
            repo_root=repo_root,
            goal=goal,
            policy_path=policy_path,
            baseline_report=baseline_report,
            model_profile=resolved_profile_name,
        )
        if stop_result is not None:
            return self._finalize(run, stop_result)

        generated_test_stage = GeneratedTestStage(accepted=False, rejection_reasons=[])
        pre_patch_paths: list[Path] = []
        if generate_tests:
            generated_test_stage = self._generate_tests(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy=policy,
                baseline_report=baseline_report,
                requested_profile_name=model_profile_name,
                max_diff_lines=max_diff_lines,
                timeout_per_command=timeout_per_command,
                keep_worktree=keep_worktree,
                selected_python=selected_python,
            )
            if generated_test_stage.accepted and generated_test_stage.patch_path is not None:
                pre_patch_paths.append(generated_test_stage.patch_path)

        patch_route = self.router.resolve(
            "patch_generation",
            profile_name=resolved_profile_name,
        )
        client = self._build_client(
            patch_route.profile_name,
            patch_route.profile,
        )
        formatter_result = self._attempt_formatter_repair(
            run=run,
            repo_root=repo_root,
            goal=goal,
            policy=policy,
            policy_path=policy_path,
            baseline_report=baseline_report,
            model_profile=patch_route.profile_name,
            keep_worktree=keep_worktree,
            timeout_per_command=timeout_per_command,
            max_diff_lines=max_diff_lines,
            selected_python=selected_python,
            generated_test_stage=generated_test_stage,
        )
        if formatter_result is not None:
            return self._finalize(run, formatter_result)
        seen_failures: set[FailureSignature] = set()
        last_failure_report = generated_test_stage.validation_report or baseline_report
        last_result: RepairResult | None = None
        candidates_attempted = 0

        for iteration in range(1, max_iters + 1):
            runtime_stop = self._runtime_budget_stop(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                model_profile=resolved_profile_name,
                iterations_attempted=iteration - 1,
                candidates_attempted=candidates_attempted,
                started=started,
                max_total_runtime_seconds=max_total_runtime_seconds,
            )
            if runtime_stop is not None:
                return self._finalize(run, runtime_stop)

            prompt = self._build_prompt(
                repo_root=repo_root,
                goal=goal,
                report=last_failure_report,
                iteration=iteration,
                profile=patch_route.profile,
                policy=policy,
            )
            route = self.router.resolve(
                "patch_generation",
                profile_name=resolved_profile_name,
                prompt_text=prompt,
                system_prompt=(
                    "You are a deterministic patch proposer. Return only a unified diff patch."
                ),
            )
            self.artifact_store.append_trace(
                run,
                "model_called",
                {
                    "purpose": "patch_generation",
                    "model_profile": route.profile_name,
                    "estimated_input_tokens": route.estimated_input_tokens,
                },
            )

            for candidate_index in range(1, max_candidates + 1):
                runtime_stop = self._runtime_budget_stop(
                    run=run,
                    repo_root=repo_root,
                    goal=goal,
                    policy_path=policy_path,
                    baseline_report=baseline_report,
                    model_profile=resolved_profile_name,
                    validation_report=last_result.validation_report if last_result else None,
                    patch_path=last_result.patch_path if last_result else None,
                    worktree_path=last_result.worktree_path if last_result else None,
                    rejection_reasons=last_result.rejection_reasons if last_result else None,
                    iterations_attempted=iteration - 1,
                    candidates_attempted=candidates_attempted,
                    started=started,
                    max_total_runtime_seconds=max_total_runtime_seconds,
                    stop_reason="max_total_runtime_reached",
                )
                if runtime_stop is not None:
                    return self._finalize(run, runtime_stop)
                candidates_attempted += 1
                patch_text = client.generate_patch(
                    system_prompt=(
                        "You are a deterministic patch proposer. Return only a unified diff patch."
                    ),
                    user_prompt=prompt,
                )
                patch_path = self.artifact_store.write_patch(run, patch_text)
                self.artifact_store.append_trace(
                    run,
                    "patch_generated",
                    {
                        "iteration": iteration,
                        "candidate": candidate_index,
                        "model_profile": route.profile_name,
                        "patch_path": str(patch_path),
                    },
                )

                try:
                    parsed_patch = self.patch_parser.parse(patch_text)
                except PatchParseError as exc:
                    last_result = RepairResult(
                        run_id=run.run_id,
                        repo_root=repo_root,
                        goal=goal,
                        status=RunStatus.PATCH_REJECTED,
                        summary=str(exc),
                        model_profile=route.profile_name,
                        artifact_dir=run.artifact_dir,
                        policy_path=policy_path,
                        trace_path=run.trace_path,
                        patch_path=patch_path,
                        generated_test_patch_path=generated_test_stage.patch_path,
                        baseline_report=baseline_report,
                        generated_test_validation_report=generated_test_stage.validation_report,
                        rejection_reasons=[str(exc)],
                        generated_test_rejection_reasons=(
                            generated_test_stage.rejection_reasons or []
                        ),
                        target_repo_changed=False,
                        iterations_attempted=iteration,
                        candidates_attempted=candidates_attempted,
                        stop_reason="malformed_patch",
                    )
                    continue

                check = self.patch_parser.check(
                    parsed_patch,
                    policy=policy,
                    max_diff_lines=max_diff_lines,
                )
                self.artifact_store.append_trace(
                    run,
                    "patch_policy_checked",
                    {
                        "iteration": iteration,
                        "candidate": candidate_index,
                        "accepted": check.accepted,
                        "reasons": check.reasons,
                    },
                )
                if not check.accepted:
                    last_result = RepairResult(
                        run_id=run.run_id,
                        repo_root=repo_root,
                        goal=goal,
                        status=RunStatus.PATCH_REJECTED,
                        summary="Patch rejected by static safety checks.",
                        model_profile=route.profile_name,
                        artifact_dir=run.artifact_dir,
                        policy_path=policy_path,
                        trace_path=run.trace_path,
                        summary_path=run.summary_path,
                        patch_path=patch_path,
                        generated_test_patch_path=generated_test_stage.patch_path,
                        baseline_report=baseline_report,
                        generated_test_validation_report=generated_test_stage.validation_report,
                        rejection_reasons=check.reasons,
                        generated_test_rejection_reasons=(
                            generated_test_stage.rejection_reasons or []
                        ),
                        target_repo_changed=False,
                        iterations_attempted=iteration,
                        candidates_attempted=candidates_attempted,
                        stop_reason="patch_static_rejection",
                    )
                    continue

                self.artifact_store.append_trace(
                    run,
                    "patch_validation_started",
                    {
                        "iteration": iteration,
                        "candidate": candidate_index,
                        "patch_path": str(patch_path),
                        "worktree_path": None,
                    },
                )
                validation_report, worktree_path = self._apply_and_validate(
                    repo_root=repo_root,
                    policy=policy,
                    patch_path=patch_path,
                    artifact_dir=run.log_dir / f"candidate-{iteration}-{candidate_index}",
                    keep_worktree=keep_worktree,
                    timeout_per_command=timeout_per_command,
                    selected_python=selected_python,
                    pre_patch_paths=pre_patch_paths,
                    trace_callback=lambda event, payload: self.artifact_store.append_trace(
                        run, event, payload
                    ),
                    phase=f"patch-{iteration}-{candidate_index}",
                )
                self.artifact_store.append_trace(
                    run,
                    "patch_validation_finished",
                    {
                        "iteration": iteration,
                        "candidate": candidate_index,
                        "status": validation_report.status.value,
                        "failure_class": (
                            validation_report.failure_class.value
                            if validation_report.failure_class
                            else None
                        ),
                        "worktree_path": str(worktree_path),
                        "target_repo_changed": validation_report.target_repo_changed,
                    },
                )

                if validation_report.status == RunStatus.NO_ACTION_NEEDED:
                    result = RepairResult(
                        run_id=run.run_id,
                        repo_root=repo_root,
                        goal=goal,
                        status=RunStatus.SUCCESS,
                        summary="Patch applied in a fresh worktree and validation passed.",
                        model_profile=route.profile_name,
                        artifact_dir=run.artifact_dir,
                        policy_path=policy_path,
                        trace_path=run.trace_path,
                        summary_path=run.summary_path,
                        patch_path=patch_path,
                        generated_test_patch_path=generated_test_stage.patch_path,
                        baseline_report=baseline_report,
                        generated_test_validation_report=generated_test_stage.validation_report,
                        validation_report=validation_report,
                        generated_test_rejection_reasons=(
                            generated_test_stage.rejection_reasons or []
                        ),
                        worktree_path=worktree_path,
                        target_repo_changed=False,
                        iterations_attempted=iteration,
                        candidates_attempted=candidates_attempted,
                        stop_reason="validation_passed",
                    )
                    return self._finalize(run, result)

                signature = self._failure_signature(validation_report, parsed_patch)
                if signature in seen_failures:
                    result = self._stopped_result(
                        run=run,
                        repo_root=repo_root,
                        goal=goal,
                        policy_path=policy_path,
                        baseline_report=baseline_report,
                        validation_report=validation_report,
                        model_profile=route.profile_name,
                        status=RunStatus.STOPPED_BY_REPEATED_FAILURE,
                        summary="Stopped after repeated identical failure.",
                        patch_path=patch_path,
                        generated_test_patch_path=generated_test_stage.patch_path,
                        worktree_path=worktree_path,
                        rejection_reasons=[
                            validation_report.summary
                            or "Candidate patch did not change the failure."
                        ],
                        generated_test_rejection_reasons=(
                            generated_test_stage.rejection_reasons or []
                        ),
                        iterations_attempted=iteration,
                        candidates_attempted=candidates_attempted,
                        stop_reason="repeated_identical_failure",
                    )
                    return self._finalize(run, result)
                seen_failures.add(signature)
                last_failure_report = validation_report

                stop_result = self._stop_for_terminal_failure(
                    run=run,
                    repo_root=repo_root,
                    goal=goal,
                    policy_path=policy_path,
                    baseline_report=baseline_report,
                    model_profile=route.profile_name,
                    validation_report=validation_report,
                    patch_path=patch_path,
                    generated_test_patch_path=generated_test_stage.patch_path,
                    worktree_path=worktree_path,
                    iterations_attempted=iteration,
                    candidates_attempted=candidates_attempted,
                    generated_test_rejection_reasons=(
                        generated_test_stage.rejection_reasons or []
                    ),
                )
                if stop_result is not None:
                    return self._finalize(run, stop_result)

                last_result = RepairResult(
                    run_id=run.run_id,
                    repo_root=repo_root,
                    goal=goal,
                    status=RunStatus.PATCH_REJECTED,
                    summary="Candidate patch did not pass validation.",
                    model_profile=route.profile_name,
                    artifact_dir=run.artifact_dir,
                    policy_path=policy_path,
                    trace_path=run.trace_path,
                    patch_path=patch_path,
                    generated_test_patch_path=generated_test_stage.patch_path,
                    baseline_report=baseline_report,
                    generated_test_validation_report=generated_test_stage.validation_report,
                    validation_report=validation_report,
                    rejection_reasons=[validation_report.summary or "Validation failed."],
                    generated_test_rejection_reasons=(
                        generated_test_stage.rejection_reasons or []
                    ),
                    worktree_path=worktree_path,
                    target_repo_changed=validation_report.target_repo_changed,
                    iterations_attempted=iteration,
                    candidates_attempted=candidates_attempted,
                    stop_reason="candidate_failed_validation",
                )

        return self._finalize(
            run,
            self._stopped_result(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                validation_report=last_result.validation_report if last_result else None,
                model_profile=resolved_profile_name,
                status=RunStatus.STOPPED_BY_BUDGET,
                summary="Stopped after reaching max iterations without a passing candidate.",
                    patch_path=last_result.patch_path if last_result else None,
                    generated_test_patch_path=generated_test_stage.patch_path,
                worktree_path=last_result.worktree_path if last_result else None,
                generated_test_validation_report=generated_test_stage.validation_report,
                rejection_reasons=last_result.rejection_reasons if last_result else [],
                generated_test_rejection_reasons=generated_test_stage.rejection_reasons or [],
                iterations_attempted=max_iters,
                candidates_attempted=candidates_attempted,
                stop_reason="max_iterations_reached",
                target_repo_changed=last_result.target_repo_changed if last_result else False,
            ),
        )

    def _build_client(self, profile_name: str, profile: ModelProfile) -> LLMClient:
        if profile.provider == "fake":
            return FakeLLMClient(profile_name, profile)
        if profile.provider == "openai_compatible":
            return OpenAICompatibleClient(profile_name, profile)
        raise RepairError(f"Unsupported model provider: {profile.provider}")

    def _build_prompt(
        self,
        *,
        repo_root: Path,
        goal: str,
        report: ValidationReport,
        iteration: int,
        profile: ModelProfile,
        policy: CompiledPolicy,
    ) -> str:
        retry_guidance = self._retry_guidance(report.failure_class, iteration)
        header = (
            f"Goal: {goal}\n"
            f"Repository: {repo_root}\n"
            f"Repository kind: {policy.repo_kind}\n"
            f"Repair iteration: {iteration}\n"
            f"Validation status: {report.status.value}\n"
            f"Failure class: {report.failure_class.value if report.failure_class else 'NONE'}\n"
            "Return exactly one unified diff patch that fixes the failure without changing tests, "
            "dependencies, CI, policies, generated artifacts, or unrelated files.\n"
            f"{self._repo_kind_guidance(policy)}\n"
            f"{retry_guidance}\n"
        )
        return self._build_bounded_prompt(
            repo_root=repo_root,
            report=report,
            profile=profile,
            header=header,
            mode="patch_generation",
        )

    def _build_test_generation_prompt(
        self,
        *,
        repo_root: Path,
        goal: str,
        report: ValidationReport,
        profile: ModelProfile,
        policy: CompiledPolicy,
    ) -> str:
        header = (
            f"Goal: {goal}\n"
            f"Repository: {repo_root}\n"
            f"Repository kind: {policy.repo_kind}\n"
            f"Validation status: {report.status.value}\n"
            f"Failure class: {report.failure_class.value if report.failure_class else 'NONE'}\n"
            "Return exactly one unified diff patch containing only regression tests. "
            "Do not modify application code, dependencies, CI, policies, or existing tests. "
            "Do not skip, xfail, disable, or weaken tests.\n"
        )
        return self._build_bounded_prompt(
            repo_root=repo_root,
            report=report,
            profile=profile,
            header=header,
            mode="test_generation",
        )

    def _repo_kind_guidance(self, policy: CompiledPolicy) -> str:
        if policy.repo_kind == "script_collection":
            return (
                "Prioritize readability, file and folder naming clarity, README usage guidance, "
                "CLI help behavior, and behavior-preserving cleanup. "
                "Small safe renames are allowed only when they improve script organization."
            )
        if policy.repo_kind == "workflow_repo":
            workflow_sources = self.policy_compiler.repo_profiler.profile(
                policy.repo_root
            ).workflow_sources
            if "implicit_script_chain" in workflow_sources:
                return (
                    "Preserve the script workflow behavior. First candidate should prefer "
                    "README or documentation-only workflow fixes. Python code changes are "
                    "allowed only when narrowly targeted to top-level executable scripts and "
                    "behavior-preserving. Do not add argparse to library, helper, or model "
                    "modules. Do not add parser.add_argument('--help', ...) or "
                    "parser.add_argument(\"--help\", ...). Do not add parse_args() at module "
                    "import time. Focus on README workflow documentation, safe CLI help paths, "
                    "example commands, and small dry-run or smoke affordances only when clearly "
                    "safe. Do not invent a package framework or run training by default."
                )
            return (
                "Preserve the workflow shape. Focus on the smallest safe end-to-end smoke path "
                "and avoid broad refactors or dependency changes."
            )
        return "Prefer the smallest safe patch that directly addresses the failing validator."

    def _retry_guidance(self, failure_class: FailureClass | None, iteration: int) -> str:
        if iteration <= 1 or failure_class is None:
            return "Prefer the smallest safe patch that directly addresses the failing validator."
        guidance = {
            FailureClass.LINT: "Retry guidance: produce a minimal lint-only patch.",
            FailureClass.TYPECHECK: (
                "Retry guidance: focus on the type errors and avoid unrelated edits."
            ),
            FailureClass.TEST: (
                "Retry guidance: focus on the semantic defect causing the failing tests."
            ),
            FailureClass.RESOURCE_EXHAUSTION: (
                "Retry guidance: produce a smaller, simpler patch. "
                "Stop rather than broadening scope."
            ),
            FailureClass.ENVIRONMENT: "Retry guidance: stop. This is an environment failure.",
            FailureClass.SECURITY: "Retry guidance: stop. This is a security failure.",
            FailureClass.POLICY_VIOLATION: (
                "Retry guidance: stop or request approval. This patch path violates policy."
            ),
        }
        return guidance.get(
            failure_class,
            "Retry guidance: narrow the patch to the validator failure and avoid unrelated edits.",
        )

    def _generate_tests(
        self,
        *,
        run: RunContext,
        repo_root: Path,
        goal: str,
        policy: CompiledPolicy,
        baseline_report: ValidationReport,
        requested_profile_name: str | None,
        max_diff_lines: int,
        timeout_per_command: int,
        keep_worktree: bool,
        selected_python: Path | None,
    ) -> GeneratedTestStage:
        if baseline_report.failure_class in {
            FailureClass.SECURITY,
            FailureClass.POLICY_VIOLATION,
            FailureClass.ENVIRONMENT,
        }:
            return GeneratedTestStage(
                accepted=False,
                rejection_reasons=[
                    "Generated tests were skipped because the baseline failure is not repairable "
                    "through test generation."
                ],
            )

        route = self.router.resolve("test_generation", profile_name=requested_profile_name)
        prompt = self._build_test_generation_prompt(
            repo_root=repo_root,
            goal=goal,
            report=baseline_report,
            profile=route.profile,
            policy=policy,
        )
        route = self.router.resolve(
            "test_generation",
            profile_name=route.profile_name,
            prompt_text=prompt,
            system_prompt=(
                "You generate deterministic regression test patches only. "
                "Return only a unified diff patch."
            ),
        )
        client = self._build_client(route.profile_name, route.profile)
        self.artifact_store.append_trace(
            run,
            "model_called",
            {
                "purpose": "generated_tests",
                "model_profile": route.profile_name,
                "estimated_input_tokens": route.estimated_input_tokens,
            },
        )
        patch_text = client.generate_patch(
            system_prompt=(
                "You generate deterministic regression test patches only. "
                "Return only a unified diff patch."
            ),
            user_prompt=prompt,
        )
        patch_path = self.artifact_store.write_generated_test_patch(run, patch_text)
        try:
            parsed_patch = self.patch_parser.parse(patch_text)
        except PatchParseError as exc:
            self.artifact_store.append_trace(
                run,
                "generated_tests_created",
                {
                    "model_profile": route.profile_name,
                    "patch_path": str(patch_path),
                    "accepted": False,
                    "reasons": [str(exc)],
                },
            )
            return GeneratedTestStage(
                accepted=False,
                patch_path=patch_path,
                rejection_reasons=[str(exc)],
                model_profile=route.profile_name,
            )

        general_check = self.patch_parser.check(
            parsed_patch,
            policy=policy,
            max_diff_lines=max_diff_lines,
        )
        test_check_reasons = self._generated_test_patch_rejection_reasons(parsed_patch)
        reasons = [*general_check.reasons, *test_check_reasons]
        if reasons:
            self.artifact_store.append_trace(
                run,
                "generated_tests_created",
                {
                    "model_profile": route.profile_name,
                    "patch_path": str(patch_path),
                    "accepted": False,
                    "reasons": reasons,
                },
            )
            return GeneratedTestStage(
                accepted=False,
                patch_path=patch_path,
                rejection_reasons=reasons,
                model_profile=route.profile_name,
            )

        self.artifact_store.append_trace(
            run,
            "patch_validation_started",
            {
                "iteration": 0,
                "candidate": 0,
                "patch_path": str(patch_path),
                "worktree_path": None,
            },
        )
        validation_report, worktree_path = self._apply_and_validate(
            repo_root=repo_root,
            policy=policy,
            patch_path=patch_path,
            artifact_dir=run.log_dir / "generated-tests",
            keep_worktree=keep_worktree,
            timeout_per_command=timeout_per_command,
            selected_python=selected_python,
            trace_callback=lambda event, payload: self.artifact_store.append_trace(
                run, event, payload
            ),
            phase="generated-tests",
        )
        self.artifact_store.append_trace(
            run,
            "patch_validation_finished",
            {
                "iteration": 0,
                "candidate": 0,
                "status": validation_report.status.value,
                "failure_class": (
                    validation_report.failure_class.value
                    if validation_report.failure_class
                    else None
                ),
                "worktree_path": str(worktree_path),
                "target_repo_changed": validation_report.target_repo_changed,
            },
        )
        if validation_report.failure_class in {
            FailureClass.SECURITY,
            FailureClass.POLICY_VIOLATION,
            FailureClass.ENVIRONMENT,
        }:
            reasons = [
                validation_report.summary
                or "Generated tests failed validation in a way that is unsafe to continue."
            ]
            self.artifact_store.append_trace(
                run,
                "generated_tests_created",
                {
                    "patch_path": str(patch_path),
                    "model_profile": route.profile_name,
                    "accepted": False,
                    "reasons": reasons,
                },
            )
            return GeneratedTestStage(
                accepted=False,
                patch_path=patch_path,
                validation_report=validation_report,
                rejection_reasons=reasons,
                model_profile=route.profile_name,
            )
        self.artifact_store.append_trace(
            run,
            "generated_tests_created",
            {
                "patch_path": str(patch_path),
                "model_profile": route.profile_name,
                "accepted": True,
                "reasons": [],
            },
        )
        return GeneratedTestStage(
            accepted=True,
            patch_path=patch_path,
            validation_report=validation_report,
            rejection_reasons=[],
            model_profile=route.profile_name,
        )

    def _build_bounded_prompt(
        self,
        *,
        repo_root: Path,
        report: ValidationReport,
        profile: ModelProfile,
        header: str,
        mode: str,
    ) -> str:
        max_chars = self._prompt_char_budget(profile)
        sections: list[str] = [header.rstrip(), self._command_evidence(report)]
        repo_summary = self._repo_summary(repo_root)
        if repo_summary:
            sections.append(repo_summary)

        body = "\n\n".join(section for section in sections if section)
        remaining = max_chars - len(body) - 2
        if remaining <= 0:
            return body[:max_chars]

        file_sections: list[str] = []
        included = 0
        for path in self._candidate_prompt_files(repo_root, report, mode=mode):
            if included >= _PROMPT_MAX_FILES:
                break
            section = self._file_section(repo_root, path, remaining)
            if section is None:
                continue
            file_sections.append(section)
            remaining -= len(section) + 2
            included += 1
            if remaining <= 0:
                break
        if file_sections:
            body = body + "\n\nRelevant repository files:\n" + "\n\n".join(file_sections)
        return body[:max_chars]

    def _prompt_char_budget(self, profile: ModelProfile) -> int:
        if profile.context_window is None:
            return _PROMPT_DEFAULT_INPUT_TOKENS * 4
        available_tokens = max(
            profile.context_window - profile.max_output_tokens - _PROMPT_SAFETY_MARGIN_TOKENS,
            256,
        )
        return available_tokens * 4

    def _command_evidence(self, report: ValidationReport) -> str:
        sections: list[str] = ["Validation evidence:"]
        failed_command = self._failed_command(report)
        if failed_command is not None:
            sections.append(
                "Failed command: "
                + " ".join(failed_command.spec.command)
                + f" (exit {failed_command.exit_code})"
            )
            if failed_command.category:
                sections.append(f"Failed category: {failed_command.category}")
        start_index = max(len(report.commands) - 2, 1)
        for index, command in enumerate(report.commands[-3:], start=start_index):
            sections.append(
                f"Command {index}: {' '.join(command.spec.command)} (exit {command.exit_code})"
            )
            stdout_tail = self._tail_text(command.stdout, _PROMPT_MAX_COMMAND_TAIL_CHARS)
            stderr_tail = self._tail_text(command.stderr, _PROMPT_MAX_COMMAND_TAIL_CHARS)
            if stdout_tail:
                sections.append(f"stdout tail:\n```text\n{stdout_tail}\n```")
            if stderr_tail:
                sections.append(f"stderr tail:\n```text\n{stderr_tail}\n```")
        if report.summary:
            sections.append(f"Summary: {report.summary}")
        if report.warnings:
            sections.append("Warnings:")
            sections.extend(f"- {warning}" for warning in report.warnings)
        return "\n".join(sections)

    def _has_actionable_repo_readability_warnings(
        self,
        policy: CompiledPolicy,
        report: ValidationReport,
    ) -> bool:
        script_warning = any(
            warning.startswith("Script readability:") for warning in report.warnings
        )
        workflow_warning = any(
            warning.startswith("Workflow planning:") for warning in report.warnings
        )
        if policy.repo_kind == "script_collection":
            return script_warning
        if policy.repo_kind == "workflow_repo":
            workflow_sources = self.policy_compiler.repo_profiler.profile(
                policy.repo_root
            ).workflow_sources
            if "implicit_script_chain" in workflow_sources:
                return script_warning or workflow_warning
        return False

    def _repo_summary(self, repo_root: Path) -> str:
        included_files: list[str] = []
        omitted_count = 0
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file():
                continue
            if self._should_exclude_from_prompt(repo_root, path):
                omitted_count += 1
                continue
            included_files.append(str(path.relative_to(repo_root)))
            if len(included_files) >= _PROMPT_MAX_SUMMARY_FILES:
                break
        lines = ["Compact repo summary:"]
        if included_files:
            lines.append("Candidate text files: " + ", ".join(included_files))
        if omitted_count:
            lines.append(
                "Excluded large or irrelevant paths such as data, outputs, logs, notebooks, "
                "binaries, caches, virtualenvs, and git metadata."
            )
        return "\n".join(lines)

    def _candidate_prompt_files(
        self,
        repo_root: Path,
        report: ValidationReport,
        *,
        mode: str,
    ) -> list[Path]:
        prioritized: list[Path] = []
        seen: set[Path] = set()
        for path in self._traceback_referenced_files(repo_root, report):
            if path not in seen:
                prioritized.append(path)
                seen.add(path)

        priority_prefixes = ["src/", "tests/"] if mode == "patch_generation" else ["tests/", "src/"]
        priority_names = {"Makefile", "pyproject.toml", "package.json", "Cargo.toml", "README.md"}
        all_files = sorted(path for path in repo_root.rglob("*") if path.is_file())
        for prefix in priority_prefixes:
            for path in all_files:
                rel = str(path.relative_to(repo_root))
                if path in seen or self._should_exclude_from_prompt(repo_root, path):
                    continue
                if rel.startswith(prefix):
                    prioritized.append(path)
                    seen.add(path)
        for path in all_files:
            if path in seen or self._should_exclude_from_prompt(repo_root, path):
                continue
            if path.name in priority_names:
                prioritized.append(path)
                seen.add(path)
        for path in all_files:
            if path in seen or self._should_exclude_from_prompt(repo_root, path):
                continue
            prioritized.append(path)
            seen.add(path)
        return prioritized

    def _traceback_referenced_files(
        self,
        repo_root: Path,
        report: ValidationReport,
    ) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()
        for command in report.commands:
            for match in _TRACEBACK_FILE_PATTERN.finditer(f"{command.stdout}\n{command.stderr}"):
                raw = match.group(1) or match.group(2)
                if not raw:
                    continue
                candidate = (
                    (repo_root / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
                )
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(repo_root)
                except (OSError, ValueError):
                    continue
                if resolved.is_file() and resolved not in seen:
                    candidates.append(resolved)
                    seen.add(resolved)
        return candidates

    def _file_section(self, repo_root: Path, path: Path, remaining_chars: int) -> str | None:
        if remaining_chars < 128:
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        rel = path.relative_to(repo_root)
        max_content = min(_PROMPT_MAX_FILE_CHARS, max(64, remaining_chars - len(str(rel)) - 32))
        truncated = content[:max_content]
        if len(content) > max_content:
            truncated = truncated + "\n...[truncated]"
        return f"FILE: {rel}\n```text\n{truncated}\n```"

    def _should_exclude_from_prompt(self, repo_root: Path, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(repo_root).parts
        except ValueError:
            return True
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts[:-1]):
            return True
        if path.suffix.casefold() in _EXCLUDED_SUFFIXES:
            return True
        if path.name.startswith(".") and path.name not in {".env", ".flake8"}:
            return True
        return path.suffix.casefold() not in _TEXT_FILE_SUFFIXES and path.name not in {
            "Makefile",
            "Dockerfile",
            "Justfile",
        }

    def _tail_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return f"...[truncated]\n{text[-max_chars:]}"

    def _failed_command(self, report: ValidationReport):
        if not report.commands:
            return None
        return report.commands[-1]

    def _attempt_formatter_repair(
        self,
        *,
        run: RunContext,
        repo_root: Path,
        goal: str,
        policy: CompiledPolicy,
        policy_path: Path,
        baseline_report: ValidationReport,
        model_profile: str,
        keep_worktree: bool,
        timeout_per_command: int,
        max_diff_lines: int,
        selected_python: Path | None,
        generated_test_stage: GeneratedTestStage,
    ) -> RepairResult | None:
        if baseline_report.failure_class != FailureClass.FORMAT:
            return None
        failed_command = self._failed_command(baseline_report)
        if failed_command is None:
            return None
        write_command = self._derive_formatter_write_command(failed_command.spec.command)
        if write_command is None:
            self.artifact_store.append_trace(
                run,
                "formatter_repair_skipped",
                {"reason": "unsupported_formatter_command", "command": failed_command.spec.command},
            )
            return None

        manager = WorktreeManager(keep_worktree=keep_worktree)
        with manager.session(repo_root) as managed:
            self.artifact_store.append_trace(
                run,
                "formatter_repair_started",
                {"command": write_command, "worktree_path": str(managed.path)},
            )
            try:
                result = self._run_formatter_command(
                    worktree_path=managed.path,
                    repo_root=repo_root,
                    command=write_command,
                    cwd=failed_command.spec.cwd,
                    env=failed_command.spec.env,
                    timeout_seconds=failed_command.spec.timeout_seconds or timeout_per_command,
                    selected_python=selected_python,
                    forbidden_commands=policy.forbidden_commands,
                    artifact_dir=run.log_dir / "formatter-command",
                )
            except subprocess.TimeoutExpired:
                self.artifact_store.append_trace(
                    run,
                    "formatter_repair_skipped",
                    {"reason": "formatter_write_timed_out", "command": write_command},
                )
                return None
            self.artifact_store.append_trace(
                run,
                "formatter_repair_finished",
                {
                    "command": write_command,
                    "exit_code": result.exit_code,
                    "stdout_tail": self._tail_text(result.stdout, 500),
                    "stderr_tail": self._tail_text(result.stderr, 500),
                },
            )
            if result.exit_code != 0:
                return None

            diff_text = self._git_diff(managed.path)
            if not diff_text.strip():
                self.artifact_store.append_trace(
                    run,
                    "formatter_repair_skipped",
                    {"reason": "no_diff_after_formatter", "command": write_command},
                )
                return None

            patch_path = self.artifact_store.write_patch(run, diff_text)
            try:
                parsed_patch = self.patch_parser.parse(diff_text)
            except PatchParseError:
                return None

            check = self.patch_parser.check(
                parsed_patch,
                policy=policy,
                max_diff_lines=max_diff_lines,
            )
            self.artifact_store.append_trace(
                run,
                "patch_policy_checked",
                {
                    "iteration": 0,
                    "candidate": 0,
                    "accepted": check.accepted,
                    "reasons": check.reasons,
                    "source": "formatter_repair",
                },
            )
            if not check.accepted:
                return None

            validation_report = self.validation_runner.validate_worktree(
                repo_root=repo_root,
                policy=policy,
                worktree_path=managed.path,
                selected_python=selected_python,
                artifact_dir=run.log_dir / "formatter-repair",
                timeout_per_command=timeout_per_command,
                trace_callback=lambda event, payload: self.artifact_store.append_trace(
                    run, event, payload
                ),
                phase="formatter-repair",
            )
            self.artifact_store.append_trace(
                run,
                "patch_validation_finished",
                {
                    "iteration": 0,
                    "candidate": 0,
                    "status": validation_report.status.value,
                    "failure_class": (
                        validation_report.failure_class.value
                        if validation_report.failure_class
                        else None
                    ),
                    "worktree_path": str(managed.path),
                    "target_repo_changed": validation_report.target_repo_changed,
                    "source": "formatter_repair",
                },
            )
            if validation_report.status == RunStatus.NO_ACTION_NEEDED:
                return RepairResult(
                    run_id=run.run_id,
                    repo_root=repo_root,
                    goal=goal,
                    status=RunStatus.SUCCESS,
                    summary="Formatter auto-repair succeeded in an isolated worktree.",
                    model_profile=model_profile,
                    artifact_dir=run.artifact_dir,
                    policy_path=policy_path,
                    trace_path=run.trace_path,
                    summary_path=run.summary_path,
                    patch_path=patch_path,
                    generated_test_patch_path=generated_test_stage.patch_path,
                    baseline_report=baseline_report,
                    generated_test_validation_report=generated_test_stage.validation_report,
                    validation_report=validation_report,
                    generated_test_rejection_reasons=(
                        generated_test_stage.rejection_reasons or []
                    ),
                    worktree_path=managed.path,
                    target_repo_changed=False,
                    iterations_attempted=0,
                    candidates_attempted=0,
                    stop_reason="formatter_validation_passed",
                )
            return self._stop_for_terminal_failure(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                model_profile=model_profile,
                validation_report=validation_report,
                patch_path=patch_path,
                generated_test_patch_path=generated_test_stage.patch_path,
                worktree_path=managed.path,
                iterations_attempted=0,
                candidates_attempted=0,
                generated_test_validation_report=generated_test_stage.validation_report,
                generated_test_rejection_reasons=generated_test_stage.rejection_reasons or [],
            )

    def _derive_formatter_write_command(self, command: list[str]) -> list[str] | None:
        normalized = list(command)
        if len(normalized) >= 5 and normalized[1:4] == ["-m", "ruff", "format"]:
            return [normalized[0], "-m", "ruff", "format", *normalized[5:]]
        if len(normalized) >= 4 and normalized[1:3] == ["-m", "black"] and "--check" in normalized:
            return [normalized[0], "-m", *[part for part in normalized[2:] if part != "--check"]]
        if normalized[:2] == ["uv", "run"]:
            prefix: list[str] = []
            tool = normalized[2:]
        else:
            prefix = []
            tool = normalized
        if tool in (["make", "format-check"], ["gmake", "format-check"]):
            return [tool[0], "format"]
        if tool[:3] == ["ruff", "format", "--check"]:
            return [*prefix, "ruff", "format", *tool[3:]]
        if tool and tool[0] == "black" and "--check" in tool:
            return [*prefix, *[part for part in tool if part != "--check"]]
        if tool and tool[0] == "prettier" and "--check" in tool:
            replaced = ["--write" if part == "--check" else part for part in tool]
            return [*prefix, *replaced]
        if len(tool) >= 2 and tool[:2] == ["biome", "format"] and "--check" in tool:
            if "--write" in tool:
                return [*prefix, *[part for part in tool if part != "--check"]]
            return [*prefix, *["--write" if part == "--check" else part for part in tool]]
        return None

    def _run_formatter_command(
        self,
        *,
        worktree_path: Path,
        repo_root: Path,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout_seconds: int,
        selected_python: Path | None,
        forbidden_commands: list[str],
        artifact_dir: Path,
    ) -> CommandResult:
        runner = CommandRunner(
            forbidden_commands=forbidden_commands,
            artifact_dir=artifact_dir,
            default_timeout_seconds=timeout_seconds,
            allowed_cwd_root=worktree_path,
            python_config=(
                PythonExecutionConfig(
                    selected_python=selected_python,
                    target_repo_root=repo_root.resolve(),
                )
                if selected_python is not None
                else None
            ),
        )
        return runner.run(
            CommandSpec(
                command=command,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
                description="Formatter auto-repair",
            ),
            cwd=worktree_path,
            category="format",
        )

    def _git_diff(self, worktree_path: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "diff", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValidationError(
                result.stderr.strip() or result.stdout.strip() or "git diff failed"
            )
        return result.stdout

    def _generated_test_patch_rejection_reasons(
        self,
        parsed_patch: ParsedPatch,
    ) -> list[str]:
        reasons: list[str] = []
        weak_test_patterns = (
            "@pytest.mark.skip",
            "@pytest.mark.skipif",
            "@pytest.mark.xfail",
            "pytest.skip(",
            "pytest.xfail(",
            "self.skipTest(",
            "@unittest.skip",
            "@unittest.skipIf",
            "@unittest.expectedFailure",
        )
        restricted_runtime_patterns = (
            "http://",
            "https://",
            "requests.",
            "urllib.",
            "socket.",
            "docker",
            "sudo",
            "torch.cuda",
            "CUDA_VISIBLE_DEVICES",
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
        )
        for file_change in parsed_patch.files:
            changed_path = file_change.changed_path
            if not self.patch_parser._is_test_path(changed_path):
                reasons.append(
                    f"Generated test patch must touch only test files: {changed_path}"
                )
            if any(line.strip() for line in file_change.removed_lines):
                reasons.append(
                    f"Generated test patch must not remove or weaken existing tests: {changed_path}"
                )
            added_text = "\n".join(file_change.added_lines)
            for pattern in weak_test_patterns:
                if pattern in added_text:
                    reasons.append(
                        f"Generated test patch adds skip/xfail behavior in {changed_path}: "
                        f"{pattern}"
                    )
            for pattern in restricted_runtime_patterns:
                if pattern in added_text:
                    reasons.append(
                        f"Generated test patch depends on restricted runtime behavior in "
                        f"{changed_path}: {pattern}"
                    )
        return reasons

    def _apply_and_validate(
        self,
        *,
        repo_root: Path,
        policy: CompiledPolicy,
        patch_path: Path,
        artifact_dir: Path,
        keep_worktree: bool,
        timeout_per_command: int,
        selected_python: Path | None = None,
        pre_patch_paths: list[Path] | None = None,
        trace_callback=None,
        phase: str = "patch",
    ) -> tuple[ValidationReport, Path]:
        manager = WorktreeManager(keep_worktree=keep_worktree)
        with manager.session(repo_root) as managed:
            for pre_patch_path in pre_patch_paths or []:
                self._apply_patch(managed.path, pre_patch_path)
            self._apply_patch(managed.path, patch_path)
            patched_policy = self.policy_compiler.compile(managed.path).model_copy(
                update={"repo_root": repo_root.resolve()}
            )
            report = self.validation_runner.validate_worktree(
                repo_root=repo_root,
                policy=patched_policy,
                worktree_path=managed.path,
                selected_python=selected_python,
                artifact_dir=artifact_dir,
                timeout_per_command=timeout_per_command,
                trace_callback=trace_callback,
                phase=phase,
            )
            return report, managed.path

    def _apply_patch(self, worktree_path: Path, patch_path: Path) -> None:
        for args in (["apply", "--check", str(patch_path)], ["apply", str(patch_path)]):
            result = subprocess.run(
                ["git", "-C", str(worktree_path), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "git apply failed"
                raise ValidationError(message)

    def _failure_signature(
        self,
        report: ValidationReport,
        parsed_patch: ParsedPatch | None,
    ) -> FailureSignature:
        failed_command = ()
        stderr = ""
        if report.commands:
            failed = report.commands[-1]
            failed_command = tuple(failed.spec.command)
            stderr = failed.stderr
        changed_files = (
            tuple(sorted(parsed_patch.changed_files)) if parsed_patch is not None else ()
        )
        return FailureSignature(
            failure_class=report.failure_class or FailureClass.UNKNOWN,
            failed_command=failed_command,
            stderr_hash=hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
            changed_files=changed_files,
        )

    def _runtime_budget_stop(
        self,
        *,
        run: RunContext,
        repo_root: Path,
        goal: str,
        policy_path: Path,
        baseline_report: ValidationReport,
        model_profile: str,
        iterations_attempted: int,
        candidates_attempted: int,
        started: float,
        max_total_runtime_seconds: int,
        validation_report: ValidationReport | None = None,
        patch_path: Path | None = None,
        generated_test_patch_path: Path | None = None,
        generated_test_validation_report: ValidationReport | None = None,
        worktree_path: Path | None = None,
        rejection_reasons: list[str] | None = None,
        generated_test_rejection_reasons: list[str] | None = None,
        stop_reason: str = "max_total_runtime_reached",
    ) -> RepairResult | None:
        elapsed = monotonic() - started
        if elapsed <= max_total_runtime_seconds:
            return None
        return self._stopped_result(
            run=run,
            repo_root=repo_root,
            goal=goal,
            policy_path=policy_path,
            baseline_report=baseline_report,
            validation_report=validation_report,
            model_profile=model_profile,
            status=RunStatus.STOPPED_BY_BUDGET,
            summary="Stopped after reaching the max total runtime budget.",
            patch_path=patch_path,
            generated_test_patch_path=generated_test_patch_path,
            worktree_path=worktree_path,
            generated_test_validation_report=generated_test_validation_report,
            rejection_reasons=rejection_reasons,
            generated_test_rejection_reasons=generated_test_rejection_reasons,
            iterations_attempted=iterations_attempted,
            candidates_attempted=candidates_attempted,
            stop_reason=stop_reason,
        )

    def _stop_for_terminal_failure(
        self,
        *,
        run: RunContext,
        repo_root: Path,
        goal: str,
        policy_path: Path,
        baseline_report: ValidationReport,
        model_profile: str,
        validation_report: ValidationReport | None = None,
        patch_path: Path | None = None,
        generated_test_patch_path: Path | None = None,
        worktree_path: Path | None = None,
        iterations_attempted: int = 0,
        candidates_attempted: int = 0,
        generated_test_validation_report: ValidationReport | None = None,
        generated_test_rejection_reasons: list[str] | None = None,
    ) -> RepairResult | None:
        report = validation_report or baseline_report
        failure_class = report.failure_class
        if failure_class == FailureClass.SECURITY:
            return self._stopped_result(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                validation_report=validation_report,
                model_profile=model_profile,
                status=RunStatus.STOPPED_BY_SECURITY,
                summary="Stopped after security failure.",
                patch_path=patch_path,
                generated_test_patch_path=generated_test_patch_path,
                worktree_path=worktree_path,
                generated_test_validation_report=generated_test_validation_report,
                rejection_reasons=[report.summary or "Security validation failed."],
                generated_test_rejection_reasons=generated_test_rejection_reasons,
                iterations_attempted=iterations_attempted,
                candidates_attempted=candidates_attempted,
                stop_reason="security_failure",
                target_repo_changed=report.target_repo_changed,
            )
        if failure_class == FailureClass.POLICY_VIOLATION:
            return self._stopped_result(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                validation_report=validation_report,
                model_profile=model_profile,
                status=RunStatus.STOPPED_BY_POLICY,
                summary="Stopped after policy violation.",
                patch_path=patch_path,
                generated_test_patch_path=generated_test_patch_path,
                worktree_path=worktree_path,
                generated_test_validation_report=generated_test_validation_report,
                rejection_reasons=[report.summary or "Policy validation failed."],
                generated_test_rejection_reasons=generated_test_rejection_reasons,
                iterations_attempted=iterations_attempted,
                candidates_attempted=candidates_attempted,
                stop_reason="policy_violation",
                target_repo_changed=report.target_repo_changed,
            )
        if failure_class == FailureClass.ENVIRONMENT:
            return self._stopped_result(
                run=run,
                repo_root=repo_root,
                goal=goal,
                policy_path=policy_path,
                baseline_report=baseline_report,
                validation_report=validation_report,
                model_profile=model_profile,
                status=RunStatus.STOPPED_BY_ENVIRONMENT,
                summary="Stopped after environment failure.",
                patch_path=patch_path,
                generated_test_patch_path=generated_test_patch_path,
                worktree_path=worktree_path,
                generated_test_validation_report=generated_test_validation_report,
                rejection_reasons=[report.summary or "Environment validation failed."],
                generated_test_rejection_reasons=generated_test_rejection_reasons,
                iterations_attempted=iterations_attempted,
                candidates_attempted=candidates_attempted,
                stop_reason="environment_failure",
                target_repo_changed=report.target_repo_changed,
            )
        return None

    def _stopped_result(
        self,
        *,
        run: RunContext,
        repo_root: Path,
        goal: str,
        policy_path: Path,
        baseline_report: ValidationReport,
        model_profile: str,
        status: RunStatus,
        summary: str,
        validation_report: ValidationReport | None = None,
        patch_path: Path | None = None,
        generated_test_patch_path: Path | None = None,
        worktree_path: Path | None = None,
        generated_test_validation_report: ValidationReport | None = None,
        rejection_reasons: list[str] | None = None,
        generated_test_rejection_reasons: list[str] | None = None,
        iterations_attempted: int,
        candidates_attempted: int,
        stop_reason: str,
        target_repo_changed: bool = False,
    ) -> RepairResult:
        return RepairResult(
            run_id=run.run_id,
            repo_root=repo_root,
            goal=goal,
            status=status,
            summary=summary,
            model_profile=model_profile,
            artifact_dir=run.artifact_dir,
            policy_path=policy_path,
            trace_path=run.trace_path,
            patch_path=patch_path,
            generated_test_patch_path=generated_test_patch_path,
            baseline_report=baseline_report,
            generated_test_validation_report=generated_test_validation_report,
            validation_report=validation_report,
            rejection_reasons=rejection_reasons or [],
            generated_test_rejection_reasons=generated_test_rejection_reasons or [],
            worktree_path=worktree_path,
            target_repo_changed=target_repo_changed,
            iterations_attempted=iterations_attempted,
            candidates_attempted=candidates_attempted,
            stop_reason=stop_reason,
        )

    def _report_trace_payload(self, report: ValidationReport) -> dict[str, object]:
        return {
            "status": report.status.value,
            "failure_class": report.failure_class.value if report.failure_class else None,
        }

    def _finalize(self, run: RunContext, result: RepairResult) -> RepairResult:
        payload = result.model_dump(mode="json")
        summary = self.artifact_store.build_repair_summary(context=run, result_payload=payload)
        summary_path = self.artifact_store.write_summary(run, summary)
        result.summary_path = summary_path
        payload = result.model_dump(mode="json")
        self.artifact_store.write_report(run, payload)
        duration_seconds = max((datetime.now(UTC) - run.created_at).total_seconds(), 0.0)
        self.artifact_store.append_trace(
            run,
            "run_finished",
            {
                "status": result.status.value,
                "summary": result.summary,
                "stop_reason": result.stop_reason,
                "failure_class": (
                    result.validation_report.failure_class.value
                    if result.validation_report and result.validation_report.failure_class
                    else result.baseline_report.failure_class.value
                    if result.baseline_report.failure_class
                    else None
                ),
                "target_repo_changed": result.target_repo_changed,
                "duration_seconds": duration_seconds,
                "summary_path": str(summary_path),
            },
        )
        self.artifact_store.finalize_run(
            run,
            status=result.status,
            summary=result.summary,
            metadata={
                "goal": result.goal,
                "model_profile": result.model_profile,
                "target_repo_changed": result.target_repo_changed,
                "stop_reason": result.stop_reason,
                "iterations_attempted": result.iterations_attempted,
                "candidates_attempted": result.candidates_attempted,
                "summary_path": str(summary_path),
                "failure_class": (
                    result.validation_report.failure_class.value
                    if result.validation_report and result.validation_report.failure_class
                    else result.baseline_report.failure_class.value
                    if result.baseline_report.failure_class
                    else None
                ),
                "generated_test_patch_path": (
                    str(result.generated_test_patch_path)
                    if result.generated_test_patch_path is not None
                    else None
                ),
            },
        )
        return result

    def _policy_hash(self, policy_path: Path) -> str:
        return hashlib.sha256(policy_path.read_bytes()).hexdigest()
